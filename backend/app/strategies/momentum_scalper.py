"""
momentum_scalper.py — SMT High-Velocity BB Impulse Scalper
-----------------------------------------------------------
Strategy: Bollinger Band (20, 2σ) + RSI-3 momentum breakout on M1/M5 candles.

Signal logic:
  BUY  — candle closes BELOW lower BB AND RSI-3 < 10  (extreme oversold + breakout)
  SELL — candle closes ABOVE upper BB AND RSI-3 > 90  (extreme overbought + breakout)

Duplicate protection (Dual-Layer):
  Layer 1 — STATE_LOCK: saves the candle timestamp the moment a signal fires.
             Even if the loop runs 20 times per second, the same candle can never
             fire twice.
  Layer 2 — mt5.positions_get() cross-check: before every execution, queries the
             live MT5 terminal. If a position with MAGIC_NUMBER=202609 already
             exists for that symbol, the order is blocked at the hardware level.

5-minute auto-close:
  A separate monitor thread checks all open scalper positions every 30 seconds.
  Any position older than MAX_TRADE_DURATION_SECS (300 = 5 min) is closed at
  market regardless of P&L. Scalping = never let a trade run overnight.

Market regime gate:
  Calls detect_market_regime() on last 60 M1 candles before each signal check.
  SIDEWAYS market → no signals fired for that symbol that cycle.

Asset support:
  Works on any MT5 symbol. Default list is SMT's crypto + gold + forex majors.
  Symbols are configurable via /scalper/config endpoint.
"""

import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

MAGIC_NUMBER           = 202609   # Unique ID — all scalper positions tagged with this
MAX_TRADE_DURATION_SECS = 300     # 5 minutes — hard close regardless of P&L
LOOP_SLEEP_SECS        = 1        # Engine checks every 1 second
MONITOR_SLEEP_SECS     = 30       # Auto-close monitor checks every 30 seconds
DEFAULT_SYMBOLS        = ["BTCUSD", "ETHUSD", "XAUUSD+"]
DEFAULT_TIMEFRAME_STR  = "M1"     # M1 or M5
LOT_SIZE               = 0.01
ATR_STOP_MULTIPLIER    = 1.5      # SL = 1.5 × ATR
RR_RATIO               = 1.5      # TP = 1.5 × SL distance (1:1.5 RR)
MIN_STOP_POINTS        = 50       # minimum 50 points stop distance

# ── Global state (set by start/stop API) ─────────────────────────────────────

_running      = False
_engine_thread: Optional[threading.Thread] = None
_monitor_thread: Optional[threading.Thread] = None
_state_lock   = {}   # { symbol: last_fired_candle_timestamp }
_status       = {
    "running":         False,
    "symbols":         list(DEFAULT_SYMBOLS),
    "timeframe":       DEFAULT_TIMEFRAME_STR,
    "signals_fired":   0,
    "positions_closed": 0,
    "last_signal":     None,
    "last_check":      None,
    "errors":          [],
}

# ── Pure-Python indicators (no pandas_ta dependency) ─────────────────────────

def _sma(values: list, period: int) -> list:
    result = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def _std(values: list, period: int) -> list:
    result = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean   = sum(window) / period
        var    = sum((x - mean) ** 2 for x in window) / period
        result.append(var ** 0.5)
    return result


def _bollinger_bands(closes: list, period: int = 20, std_dev: float = 2.0):
    """Returns (upper, mid, lower) as lists."""
    mid   = _sma(closes, period)
    std   = _std(closes, period)
    upper = [m + std_dev * s if m is not None else None for m, s in zip(mid, std)]
    lower = [m - std_dev * s if m is not None else None for m, s in zip(mid, std)]
    return upper, mid, lower


def _rsi(closes: list, period: int = 3) -> list:
    """Wilder-smoothed RSI."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    result = [None] * period
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        if avg_l == 0:
            result.append(100.0)
        else:
            rs  = avg_g / avg_l
            result.append(round(100 - 100 / (1 + rs), 2))
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    return result


def _atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    trs = [None]
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i]  - closes[i - 1]))
        trs.append(tr)
    result = [None] * period
    init   = sum(t for t in trs[1:period + 1] if t is not None) / period
    atr_val = init
    for i in range(period + 1, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        result.append(atr_val)
    return result


# ── MT5 helpers ───────────────────────────────────────────────────────────────

def _get_mt5():
    """Return mt5 module if terminal is connected, else None."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize(path=r"C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe"):
            return None
        if mt5.account_info() is None:
            return None
        return mt5
    except ImportError:
        return None


def _get_timeframe(mt5, tf_str: str):
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
    }
    return tf_map.get(tf_str.upper(), mt5.TIMEFRAME_M1)


def _fetch_bars(mt5, symbol: str, timeframe, count: int = 80):
    """Fetch recent OHLCV bars from MT5. Returns dict of lists, or None."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) < 30:
        return None
    return {
        "time":   [r["time"] for r in rates],
        "open":   [float(r["open"])  for r in rates],
        "high":   [float(r["high"])  for r in rates],
        "low":    [float(r["low"])   for r in rates],
        "close":  [float(r["close"]) for r in rates],
        "volume": [int(r["tick_volume"]) for r in rates],
    }


def _has_open_position(mt5, symbol: str) -> bool:
    """
    Layer 1 anti-duplicate: check if a position with MAGIC_NUMBER exists for symbol.
    Also checks all variations of the symbol name (e.g. XAUUSD vs XAUUSD+).
    """
    positions = mt5.positions_get() or []
    for p in positions:
        if p.magic == MAGIC_NUMBER and p.symbol.startswith(symbol.rstrip("+")):
            return True
    return False


def _close_position(mt5, pos) -> bool:
    """Close a single active position at market."""
    close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
    tick       = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return False
    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
    result = mt5.order_send({
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       pos.symbol,
        "volume":       pos.volume,
        "type":         close_type,
        "position":     pos.ticket,
        "price":        price,
        "deviation":    20,
        "magic":        MAGIC_NUMBER,
        "comment":      "SMT_Scalper_TimeExit",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })
    return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE


# ── 5-minute auto-close monitor ───────────────────────────────────────────────

def _auto_close_monitor():
    """
    Background thread: every 30 seconds, close any scalper position
    that has been open longer than MAX_TRADE_DURATION_SECS (5 minutes).
    """
    global _status
    while _running:
        try:
            mt5 = _get_mt5()
            if mt5:
                now       = datetime.now(timezone.utc)
                positions = mt5.positions_get() or []
                for p in positions:
                    if p.magic != MAGIC_NUMBER:
                        continue
                    open_time = datetime.fromtimestamp(p.time, tz=timezone.utc)
                    age_secs  = (now - open_time).total_seconds()
                    if age_secs >= MAX_TRADE_DURATION_SECS:
                        ok = _close_position(mt5, p)
                        tag = "✅" if ok else "❌"
                        print(f"[Scalper Auto-Close] {tag} #{p.ticket} {p.symbol} "
                              f"age={age_secs:.0f}s (limit={MAX_TRADE_DURATION_SECS}s)")
                        if ok:
                            _status["positions_closed"] = _status.get("positions_closed", 0) + 1
        except Exception as e:
            print(f"[Scalper Monitor] Error: {e}")
        time.sleep(MONITOR_SLEEP_SECS)


# ── Signal execution ──────────────────────────────────────────────────────────

def _execute_scalp(mt5, symbol: str, direction: str, atr_val: float):
    """Fire a market order for the scalper."""
    tick     = mt5.symbol_info_tick(symbol)
    sym_info = mt5.symbol_info(symbol)
    if tick is None or sym_info is None:
        return False

    digits = sym_info.digits
    point  = sym_info.point

    # SL: 1.5 × ATR, minimum 50 points
    stop_dist = max(atr_val * ATR_STOP_MULTIPLIER, MIN_STOP_POINTS * point)

    if direction == "BUY":
        price = tick.ask
        sl    = round(price - stop_dist, digits)
        tp    = round(price + stop_dist * RR_RATIO, digits)
        otype = mt5.ORDER_TYPE_BUY
    else:
        price = tick.bid
        sl    = round(price + stop_dist, digits)
        tp    = round(price - stop_dist * RR_RATIO, digits)
        otype = mt5.ORDER_TYPE_SELL

    result = mt5.order_send({
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       float(LOT_SIZE),
        "type":         otype,
        "price":        float(price),
        "sl":           float(sl),
        "tp":           float(tp),
        "deviation":    10,
        "magic":        MAGIC_NUMBER,
        "comment":      "SMT_M1_Scalper",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })

    ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
    if ok:
        print(f"[Scalper] ✅ {direction} {symbol} @ {price:.5f} | "
              f"SL={sl:.5f} TP={tp:.5f} | #{result.order}")
    else:
        err = result.comment if result else "No response"
        print(f"[Scalper] ❌ {direction} {symbol} failed: {err}")
    return ok


# ── Main engine loop ──────────────────────────────────────────────────────────

def _engine_loop(symbols: list, tf_str: str):
    """
    Core scanning loop. Runs every 1 second.
    For each symbol:
      1. Layer-2 check: skip if position already open (MT5 database query)
      2. Fetch M1/M5 bars from MT5
      3. Calculate BB + RSI-3 + ATR
      4. Check regime — skip if SIDEWAYS
      5. Layer-1 check: skip if STATE_LOCK matches last candle timestamp
      6. Evaluate signal — if match, lock state and fire
    """
    global _running, _status, _state_lock

    try:
        import MetaTrader5 as mt5_lib
    except ImportError:
        print("[Scalper] MetaTrader5 package not installed.")
        _running = False
        return

    if not mt5_lib.initialize(path=r"C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe"):
        print("[Scalper] MT5 initialization failed.")
        _running = False
        return

    if mt5_lib.account_info() is None:
        print("[Scalper] No active MT5 account — login in terminal first.")
        _running = False
        return

    timeframe = _get_timeframe(mt5_lib, tf_str)
    _state_lock = {sym: None for sym in symbols}
    print(f"[Scalper] 🤖 Engine active | Symbols: {symbols} | TF: {tf_str} | "
          f"Magic: {MAGIC_NUMBER} | 5-min auto-close ON")

    try:
        from app.strategies.market_regime import detect_market_regime
        _has_regime = True
    except ImportError:
        _has_regime = False

    while _running:
        _status["last_check"] = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        for symbol in symbols:
            try:
                # ── Layer 2: MT5 position database check ──────────────────
                if _has_open_position(mt5_lib, symbol):
                    continue   # Stand down — position already exists

                # ── Fetch bars ─────────────────────────────────────────────
                bars = _fetch_bars(mt5_lib, symbol, timeframe, count=80)
                if bars is None:
                    continue

                closes = bars["close"]
                highs  = bars["high"]
                lows   = bars["low"]
                times  = bars["time"]

                # ── Indicators ────────────────────────────────────────────
                bb_upper, bb_mid, bb_lower = _bollinger_bands(closes, 20, 2.0)
                rsi3 = _rsi(closes, 3)
                atr  = _atr(highs, lows, closes, 14)

                # Use [-2] = last COMPLETED candle (not the live forming one)
                idx = len(closes) - 2
                if idx < 20:
                    continue

                close_c  = closes[idx]
                upper_c  = bb_upper[idx]
                lower_c  = bb_lower[idx]
                rsi_c    = rsi3[idx]
                atr_c    = atr[idx]
                candle_ts = times[idx]

                if None in (upper_c, lower_c, rsi_c, atr_c):
                    continue

                # ── Market regime gate ────────────────────────────────────
                if _has_regime:
                    regime_info = detect_market_regime(highs[-60:], lows[-60:], closes[-60:])
                    if regime_info["regime"] == "SIDEWAYS":
                        continue   # Skip — choppy market

                # ── Layer 1: State lock (same candle timestamp) ───────────
                if _state_lock.get(symbol) == candle_ts:
                    continue   # Already fired on this candle

                # ── Signal evaluation ─────────────────────────────────────
                direction = None
                if close_c < lower_c and rsi_c < 10:
                    direction = "BUY"   # Extreme oversold breakout below BB
                elif close_c > upper_c and rsi_c > 90:
                    direction = "SELL"  # Extreme overbought breakout above BB

                if direction is None:
                    continue

                print(f"[Scalper] 🎯 {direction} signal | {symbol} | "
                      f"close={close_c:.5f} | RSI={rsi_c:.1f} | "
                      f"BB={'lower' if direction=='BUY' else 'upper'}={lower_c if direction=='BUY' else upper_c:.5f}")

                # ── Lock state BEFORE execution (prevents any gap dupes) ──
                _state_lock[symbol] = candle_ts

                # ── Execute ───────────────────────────────────────────────
                ok = _execute_scalp(mt5_lib, symbol, direction, atr_c)
                if ok:
                    _status["signals_fired"] = _status.get("signals_fired", 0) + 1
                    _status["last_signal"]   = {
                        "symbol":    symbol,
                        "direction": direction,
                        "close":     round(close_c, 5),
                        "rsi":       round(rsi_c, 1),
                        "time":      datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                    }

            except Exception as e:
                print(f"[Scalper] {symbol} error: {e}")
                _status.setdefault("errors", []).append(str(e))
                if len(_status["errors"]) > 10:
                    _status["errors"] = _status["errors"][-10:]

        time.sleep(LOOP_SLEEP_SECS)

    print("[Scalper] 🛑 Engine stopped.")


# ── Public start / stop interface ─────────────────────────────────────────────

def start_scalper(symbols=None, timeframe="M1"):
    global _running, _engine_thread, _monitor_thread, _status

    if _running:
        return {"success": False, "error": "Scalper already running"}

    _running = True
    syms     = symbols or list(DEFAULT_SYMBOLS)
    _status.update({
        "running":          True,
        "symbols":          syms,
        "timeframe":        timeframe,
        "signals_fired":    0,
        "positions_closed": 0,
        "last_signal":      None,
        "errors":           [],
    })

    _engine_thread = threading.Thread(
        target=_engine_loop, args=(syms, timeframe), daemon=True, name="ScalperEngine"
    )
    _monitor_thread = threading.Thread(
        target=_auto_close_monitor, daemon=True, name="ScalperMonitor"
    )
    _engine_thread.start()
    _monitor_thread.start()

    return {"success": True, "symbols": syms, "timeframe": timeframe,
            "magic": MAGIC_NUMBER, "auto_close_secs": MAX_TRADE_DURATION_SECS}


def stop_scalper():
    global _running, _status
    _running = False
    _status["running"] = False
    return {"success": True, "message": "Scalper stop signal sent"}


def get_scalper_status():
    return dict(_status)


# ── Backtest (M1 simulation on historical data) ────────────────────────────────

def run_scalper_backtest(df_m1, symbol: str = "BTCUSD",
                         atr_mult: float = 1.5, rr: float = 1.5) -> list:
    """
    Simulate the BB+RSI scalper on historical M1 data.
    Uses same signal logic as live engine. Returns list of trade results.
    """
    if df_m1 is None or len(df_m1) < 50:
        return []

    closes = [float(x) for x in df_m1["close"].tolist()]
    opens  = [float(x) for x in df_m1["open"].tolist()] if "open" in df_m1.columns else closes
    highs  = [float(x) for x in df_m1["high"].tolist()]
    lows   = [float(x) for x in df_m1["low"].tolist()]
    times  = df_m1["timestamp"].tolist() if "timestamp" in df_m1.columns else list(range(len(closes)))

    bb_upper, _, bb_lower = _bollinger_bands(closes, 20, 2.0)
    rsi3_vals = _rsi(closes, 3)
    atr_vals  = _atr(highs, lows, closes, 14)

    trades     = []
    in_trade   = False
    direction  = None
    entry_px   = 0.0
    sl_px      = 0.0
    tp_px      = 0.0
    entry_idx  = 0
    entry_ts   = None
    MAX_BARS   = 5   # 5 × M1 = 5 minutes max hold

    for i in range(21, len(closes) - 1):
        close  = closes[i]
        upper  = bb_upper[i]
        lower  = bb_lower[i]
        rsi    = rsi3_vals[i]
        atr    = atr_vals[i]

        if None in (upper, lower, rsi, atr):
            continue

        if not in_trade:
            sig = None
            if close < lower and rsi < 10:
                sig = "BUY"
            elif close > upper and rsi > 90:
                sig = "SELL"

            if sig:
                # Stop floor: tiny fraction of price (works for BTC, ETH and Gold alike)
                stop_dist = max(atr * atr_mult, closes[i] * 0.0001)
                entry_px  = opens[i + 1]    # entry at next bar's OPEN (no lookahead)
                if sig == "BUY":
                    sl_px = entry_px - stop_dist
                    tp_px = entry_px + stop_dist * rr
                else:
                    sl_px = entry_px + stop_dist
                    tp_px = entry_px - stop_dist * rr
                in_trade  = True
                direction = sig
                entry_idx = i + 1
                entry_ts  = times[i + 1]
        else:
            # Check SL / TP / 5-min timeout — skip the entry bar itself
            # (part of its range happened before the trade was opened)
            if i <= entry_idx:
                continue
            bars_held = i - entry_idx
            h = highs[i]
            l = lows[i]

            outcome = None
            exit_px = closes[i]

            if direction == "BUY":
                if l <= sl_px:
                    outcome = "LOSS"; exit_px = sl_px
                elif h >= tp_px:
                    outcome = "WIN";  exit_px = tp_px
                elif bars_held >= MAX_BARS:
                    outcome = "TIMEOUT"; exit_px = closes[i]
            else:
                if h >= sl_px:
                    outcome = "LOSS"; exit_px = sl_px
                elif l <= tp_px:
                    outcome = "WIN";  exit_px = tp_px
                elif bars_held >= MAX_BARS:
                    outcome = "TIMEOUT"; exit_px = closes[i]

            if outcome:
                pts = (exit_px - entry_px) if direction == "BUY" else (entry_px - exit_px)
                from app.services.clock import ist_str
                def _ms(ts):
                    """Raw UTC epoch ms — `date` is an IST display string and
                    must never be parsed for time bucketing."""
                    try:
                        v = int(ts)
                        return v if v > 1e12 else v * 1000
                    except (TypeError, ValueError):
                        return None

                trades.append({
                    "entry_ts":     _ms(entry_ts),
                    "signal":       direction,
                    "entry":        round(entry_px, 5),
                    "sl":           round(sl_px, 5),
                    "tp":           round(tp_px, 5),
                    "exit":         round(exit_px, 5),
                    # Timeout exits count as WIN/LOSS by their actual P&L
                    "outcome":      "WIN" if (outcome == "WIN" or (outcome == "TIMEOUT" and pts > 0)) else "LOSS",
                    "points":       round(pts, 5),
                    "bars_held":    bars_held,
                    "timeout":      outcome == "TIMEOUT",
                    "date":         ist_str(entry_ts),   # IST, display only
                    "strategy":     "BB_RSI_Scalper",
                    "quality_score": 7,
                })
                in_trade = False

    return trades
