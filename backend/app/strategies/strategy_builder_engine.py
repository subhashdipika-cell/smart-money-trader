"""
strategy_builder_engine.py
--------------------------
Generic rule-based strategy engine for the Strategy Builder page.

A strategy "definition" is a plain dict (stored in custom_strategies.json):

{
  "id":        "custom_1718000000",
  "name":      "My EMA + Sweep",
  "asset":     "Gold",              # BTC | ETH | Gold
  "timeframe": "15m",               # 5m | 15m | 1h | 4h
  "direction": "both",              # long | short | both
  "conditions": [
      {"type": "ema_cross",   "fast": 20, "slow": 50},
      {"type": "rsi",         "period": 14, "buy_below": 35, "sell_above": 65},
      {"type": "session",     "session": "london"}
  ],
  "risk": {
      "sl_type":     "atr",         # atr | fixed
      "sl_value":    1.5,           # ATR multiple OR fixed points
      "rr_ratio":    2.0
  }
}

The same condition checks power both backtesting and live signal generation,
so a deployed strategy behaves exactly like its backtest.
"""

import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────

_REGISTRY_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "custom_strategies.json")
)


def load_registry() -> dict:
    try:
        with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"strategies": [], "results": {}}


def save_registry(reg: dict):
    with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, default=str)


def load_custom_strategy(strategy_id: str):
    """Return a saved definition by id, or None."""
    for s in load_registry().get("strategies", []):
        if s.get("id") == strategy_id:
            return s
    return None


# USD P&L per 1.0 lot per 1 point of movement (Vantage CFD contract sizes)
CONTRACT_SIZES = {"BTC": 1.0, "ETH": 1.0, "Gold": 100.0}


def get_configured_lot(asset: str) -> float:
    """Configured trading lot for the asset from mt4_config.json (default 0.01)."""
    try:
        cfg_path = os.path.join(os.path.dirname(_REGISTRY_FILE), "mt4_config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            sizes = (json.load(f) or {}).get("lot_sizes", {})
        sym = {"BTC": "BTCUSD", "ETH": "ETHUSD", "Gold": "XAUUSD+"}.get(asset, "")
        return float(sizes.get(sym) or 0.01)
    except Exception:
        return 0.01


# ──────────────────────────────────────────────────────────────────────────────
# Condition catalogue (served to the frontend for dropdowns)
# ──────────────────────────────────────────────────────────────────────────────

CONDITION_CATALOGUE = [
    # Classic indicators
    {"type": "ema_cross",     "label": "EMA crossover (fast crosses slow)",   "category": "Indicators",
     "params": [{"name": "fast", "label": "Fast EMA", "default": 20},
                {"name": "slow", "label": "Slow EMA", "default": 50}]},
    {"type": "price_vs_ma",   "label": "Price above/below moving average",    "category": "Indicators",
     "params": [{"name": "period", "label": "MA period", "default": 200},
                {"name": "ma", "label": "MA type (ema/sma)", "default": "ema", "kind": "choice", "choices": ["ema", "sma"]}]},
    {"type": "rsi",           "label": "RSI oversold / overbought",           "category": "Indicators",
     "params": [{"name": "period", "label": "RSI period", "default": 14},
                {"name": "buy_below", "label": "Buy when RSI below", "default": 30},
                {"name": "sell_above", "label": "Sell when RSI above", "default": 70}]},
    {"type": "macd_cross",    "label": "MACD line crosses signal line",       "category": "Indicators",
     "params": []},
    {"type": "atr_volatility","label": "Volatility filter (ATR above average)","category": "Indicators",
     "params": [{"name": "period", "label": "ATR period", "default": 14},
                {"name": "mult", "label": "Min. x average ATR", "default": 1.0}]},

    # Price action / breakout
    {"type": "breakout",      "label": "Breakout of N-bar high/low",          "category": "Price Action",
     "params": [{"name": "lookback", "label": "Bars to look back", "default": 20}]},
    {"type": "engulfing",     "label": "Engulfing candle",                    "category": "Price Action",
     "params": []},
    {"type": "pin_bar",       "label": "Pin bar / rejection wick",            "category": "Price Action",
     "params": [{"name": "wick_ratio", "label": "Wick vs body (x)", "default": 2.0}]},
    {"type": "big_candle",    "label": "Momentum candle (body > ATR x)",      "category": "Price Action",
     "params": [{"name": "atr_mult", "label": "Body vs ATR (x)", "default": 1.5}]},

    # ICT / SMC
    {"type": "liquidity_sweep","label": "Liquidity sweep of prev. day high/low","category": "ICT / SMC",
     "params": []},
    {"type": "fvg",           "label": "Fair value gap (recent, price inside)","category": "ICT / SMC",
     "params": [{"name": "lookback", "label": "Bars to look back", "default": 10}]},
    {"type": "order_block",   "label": "Order block retest",                  "category": "ICT / SMC",
     "params": [{"name": "lookback", "label": "Bars to look back", "default": 30},
                {"name": "impulse_mult", "label": "Impulse vs ATR (x)", "default": 2.0}]},
    {"type": "choch",         "label": "Change of character (CHoCH)",         "category": "ICT / SMC",
     "params": [{"name": "swing", "label": "Swing strength (bars)", "default": 3}]},
    {"type": "bos",           "label": "Break of structure (BOS)",            "category": "ICT / SMC",
     "params": [{"name": "swing", "label": "Swing strength (bars)", "default": 3}]},

    # Filters
    {"type": "session",       "label": "Trading session filter",              "category": "Filters",
     "params": [{"name": "session", "label": "Session", "default": "london", "kind": "choice",
                 "choices": ["london", "newyork", "asia"]}]},
]

# Exit rules: a trade closes when the OPPOSITE side of one of these fires
# (e.g. in a long trade, "EMA crossover" exits when fast crosses BELOW slow).
# Stop loss always applies; take profit is optional.
EXIT_CATALOGUE = [
    {"type": "ema_cross",   "label": "Opposite EMA crossover",                "category": "Exits",
     "params": [{"name": "fast", "label": "Fast EMA", "default": 20},
                {"name": "slow", "label": "Slow EMA", "default": 50}]},
    {"type": "macd_cross",  "label": "Opposite MACD cross",                   "category": "Exits",
     "params": []},
    {"type": "rsi",         "label": "RSI reaches opposite extreme",          "category": "Exits",
     "params": [{"name": "period", "label": "RSI period", "default": 14},
                {"name": "buy_below", "label": "Exit short below", "default": 30},
                {"name": "sell_above", "label": "Exit long above", "default": 70}]},
    {"type": "price_vs_ma", "label": "Price crosses to other side of MA",     "category": "Exits",
     "params": [{"name": "period", "label": "MA period", "default": 50},
                {"name": "ma", "label": "MA type (ema/sma)", "default": "ema", "kind": "choice", "choices": ["ema", "sma"]}]},
    {"type": "breakout",    "label": "Opposite N-bar breakout",               "category": "Exits",
     "params": [{"name": "lookback", "label": "Bars to look back", "default": 20}]},
    {"type": "engulfing",   "label": "Opposite engulfing candle",             "category": "Exits",
     "params": []},
    {"type": "time_stop",   "label": "Time stop (exit after N bars)",         "category": "Exits",
     "params": [{"name": "bars", "label": "Max bars in trade", "default": 24}]},
]


def _evaluate_exits(definition: dict, df: pd.DataFrame, ctx: dict):
    """
    Returns (exit_long, exit_short, time_stop_bars).
    exit_long[i]  → close an open LONG at bar i  (bearish side of any exit rule)
    exit_short[i] → close an open SHORT at bar i (bullish side of any exit rule)
    Exit rules are OR-ed: any one firing closes the trade.
    """
    n          = len(df)
    exit_long  = np.zeros(n, dtype=bool)
    exit_short = np.zeros(n, dtype=bool)
    time_stop  = None
    for cond in definition.get("exit_conditions", []):
        if cond.get("type") == "time_stop":
            try:
                time_stop = max(int(cond.get("bars", 24)), 1)
            except Exception:
                time_stop = 24
            continue
        bull, bear  = _eval_condition(cond, df, ctx)
        exit_long  |= bear      # bearish trigger closes longs
        exit_short |= bull      # bullish trigger closes shorts
    return exit_long, exit_short, time_stop


# ──────────────────────────────────────────────────────────────────────────────
# Indicators (vectorised, computed once per backtest)
# ──────────────────────────────────────────────────────────────────────────────

def _ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=max(int(period), 1), adjust=False).mean()


def _sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(max(int(period), 1)).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _macd(close: pd.Series):
    macd_line = _ema(close, 12) - _ema(close, 26)
    signal    = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal


def _hours_utc(df: pd.DataFrame) -> pd.Series:
    """Hour-of-day (UTC) regardless of timestamp format (ms int or datetime)."""
    ts = df["timestamp"]
    if np.issubdtype(ts.dtype, np.number):
        return pd.to_datetime(ts, unit="ms").dt.hour
    return pd.to_datetime(ts).dt.hour


def _dates_utc(df: pd.DataFrame) -> pd.Series:
    ts = df["timestamp"]
    if np.issubdtype(ts.dtype, np.number):
        return pd.to_datetime(ts, unit="ms").dt.date
    return pd.to_datetime(ts).dt.date


def _swings(df: pd.DataFrame, strength: int = 3):
    """Pivot highs/lows (fractals). Returns two boolean arrays."""
    n     = len(df)
    highs = df["high"].values
    lows  = df["low"].values
    ph    = np.zeros(n, dtype=bool)
    pl    = np.zeros(n, dtype=bool)
    s     = max(int(strength), 1)
    for i in range(s, n - s):
        if highs[i] == max(highs[i - s:i + s + 1]):
            ph[i] = True
        if lows[i] == min(lows[i - s:i + s + 1]):
            pl[i] = True
    return ph, pl


SESSIONS_UTC = {"london": (7, 12), "newyork": (12, 18), "asia": (0, 7)}


# ──────────────────────────────────────────────────────────────────────────────
# Condition evaluation
# Each evaluator returns two boolean numpy arrays: (long_ok, short_ok)
# ──────────────────────────────────────────────────────────────────────────────

def _eval_condition(cond: dict, df: pd.DataFrame, ctx: dict):
    n     = len(df)
    close = df["close"]
    t     = cond.get("type")
    T     = np.zeros(n, dtype=bool)

    if t == "ema_cross":
        fast = _ema(close, cond.get("fast", 20))
        slow = _ema(close, cond.get("slow", 50))
        up   = (fast > slow) & (fast.shift() <= slow.shift())
        dn   = (fast < slow) & (fast.shift() >= slow.shift())
        return up.fillna(False).values, dn.fillna(False).values

    if t == "price_vs_ma":
        fn = _ema if cond.get("ma", "ema") == "ema" else _sma
        ma = fn(close, cond.get("period", 200))
        return (close > ma).fillna(False).values, (close < ma).fillna(False).values

    if t == "rsi":
        r = _rsi(close, int(cond.get("period", 14)))
        return (r < float(cond.get("buy_below", 30))).values, \
               (r > float(cond.get("sell_above", 70))).values

    if t == "macd_cross":
        m, s = _macd(close)
        up = (m > s) & (m.shift() <= s.shift())
        dn = (m < s) & (m.shift() >= s.shift())
        return up.fillna(False).values, dn.fillna(False).values

    if t == "atr_volatility":
        atr  = ctx["atr"]
        avg  = atr.rolling(50).mean()
        ok   = (atr >= avg * float(cond.get("mult", 1.0))).fillna(False).values
        return ok, ok          # non-directional filter

    if t == "breakout":
        lb = max(int(cond.get("lookback", 20)), 2)
        hh = df["high"].rolling(lb).max().shift()
        ll = df["low"].rolling(lb).min().shift()
        return (close > hh).fillna(False).values, (close < ll).fillna(False).values

    if t == "engulfing":
        o, c   = df["open"], df["close"]
        po, pc = o.shift(), c.shift()
        bull = (c > o) & (pc < po) & (c >= po) & (o <= pc)
        bear = (c < o) & (pc > po) & (c <= po) & (o >= pc)
        return bull.fillna(False).values, bear.fillna(False).values

    if t == "pin_bar":
        k    = float(cond.get("wick_ratio", 2.0))
        o, c, h, l = df["open"], df["close"], df["high"], df["low"]
        body  = (c - o).abs().replace(0, np.nan)
        upper = h - pd.concat([o, c], axis=1).max(axis=1)
        lower = pd.concat([o, c], axis=1).min(axis=1) - l
        rng   = (h - l).replace(0, np.nan)
        bull  = (lower >= k * body) & ((c - l) / rng > 0.66)
        bear  = (upper >= k * body) & ((h - c) / rng > 0.66)
        return bull.fillna(False).values, bear.fillna(False).values

    if t == "big_candle":
        atr  = ctx["atr"]
        body = (df["close"] - df["open"])
        big  = body.abs() >= atr * float(cond.get("atr_mult", 1.5))
        return (big & (body > 0)).fillna(False).values, \
               (big & (body < 0)).fillna(False).values

    if t == "liquidity_sweep":
        dates = ctx["dates"]
        daily_hi = df["high"].groupby(dates).transform("max")
        daily_lo = df["low"].groupby(dates).transform("min")
        # previous day's high/low mapped onto each bar
        day_hi = df.groupby(dates)["high"].max()
        day_lo = df.groupby(dates)["low"].min()
        prev_hi = dates.map(day_hi.shift())
        prev_lo = dates.map(day_lo.shift())
        prev_hi = pd.Series(prev_hi.values, index=df.index, dtype=float)
        prev_lo = pd.Series(prev_lo.values, index=df.index, dtype=float)
        bull = (df["low"] < prev_lo) & (close > prev_lo)    # sweep low, reclaim
        bear = (df["high"] > prev_hi) & (close < prev_hi)   # sweep high, reject
        return bull.fillna(False).values, bear.fillna(False).values

    if t == "fvg":
        lb = max(int(cond.get("lookback", 10)), 1)
        h2 = df["high"].shift(2)
        l2 = df["low"].shift(2)
        bull_gap = df["low"] > h2          # gap up: candle i low above i-2 high
        bear_gap = df["high"] < l2
        bull = bull_gap.rolling(lb).max().astype(bool)
        bear = bear_gap.rolling(lb).max().astype(bool)
        return bull.fillna(False).values, bear.fillna(False).values

    if t == "order_block":
        lb   = max(int(cond.get("lookback", 30)), 3)
        atr  = ctx["atr"]
        o, c = df["open"], df["close"]
        move = close.diff(3)
        impulse_up = move > atr * float(cond.get("impulse_mult", 2.0))
        impulse_dn = move < -atr * float(cond.get("impulse_mult", 2.0))
        # OB = last opposite-colour candle before impulse; approximate:
        ob_bull = ((c.shift(3) < o.shift(3)) & impulse_up)
        ob_bear = ((c.shift(3) > o.shift(3)) & impulse_dn)
        bull = ob_bull.rolling(lb).max().astype(bool)
        bear = ob_bear.rolling(lb).max().astype(bool)
        return bull.fillna(False).values, bear.fillna(False).values

    if t in ("choch", "bos"):
        s_str   = int(cond.get("swing", 3))
        ph, pl  = _swings(df, s_str)
        n_      = len(df)
        bull    = np.zeros(n_, dtype=bool)
        bear    = np.zeros(n_, dtype=bool)
        last_ph = np.nan
        last_pl = np.nan
        closes  = close.values
        highs   = df["high"].values
        lows    = df["low"].values
        trend   = 0   # 1 up, -1 down
        for i in range(n_):
            if not np.isnan(last_ph) and closes[i] > last_ph:
                if t == "choch":
                    bull[i] = trend == -1     # reversal break
                else:
                    bull[i] = trend == 1      # continuation break
                trend, last_ph = 1, np.nan
            if not np.isnan(last_pl) and closes[i] < last_pl:
                if t == "choch":
                    bear[i] = trend == 1
                else:
                    bear[i] = trend == -1
                trend, last_pl = -1, np.nan
            if ph[i]:
                last_ph = highs[i]
            if pl[i]:
                last_pl = lows[i]
        return bull, bear

    if t == "session":
        hrs   = ctx["hours"]
        lo, hi = SESSIONS_UTC.get(cond.get("session", "london"), (0, 24))
        ok = ((hrs >= lo) & (hrs < hi)).values
        return ok, ok          # non-directional filter

    # Unknown condition → never true (safe default)
    return T, T.copy()


def evaluate_conditions(definition: dict, df: pd.DataFrame):
    """Returns (long_ok, short_ok) boolean arrays — AND of every condition."""
    n   = len(df)
    ctx = {
        "atr":   _atr(df, 14),
        "hours": _hours_utc(df),
        "dates": _dates_utc(df),
    }
    long_ok  = np.ones(n, dtype=bool)
    short_ok = np.ones(n, dtype=bool)
    for cond in definition.get("conditions", []):
        l, s     = _eval_condition(cond, df, ctx)
        long_ok  &= l
        short_ok &= s

    direction = definition.get("direction", "both")
    if direction == "long":
        short_ok[:] = False
    elif direction == "short":
        long_ok[:] = False
    return long_ok, short_ok, ctx


# ──────────────────────────────────────────────────────────────────────────────
# Data access
# ──────────────────────────────────────────────────────────────────────────────

_ASSET_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "Gold": "XAUUSD"}


def _get_history(asset: str, timeframe: str, days: int) -> pd.DataFrame:
    if asset == "Gold":
        from app.services.gold_mt5_history import fetch_ohlcv
        df = fetch_ohlcv(timeframe=timeframe, days=days)
        if df is None or df.empty:
            raise RuntimeError("Gold history unavailable — is the MT5 terminal running?")
        return df.reset_index(drop=True)

    from app.services.binance_service import get_historical_multi_timeframe_data
    symbol = _ASSET_SYMBOL.get(asset, "BTCUSDT")
    data   = get_historical_multi_timeframe_data(symbol, days=days, intervals=[timeframe])
    df     = data.get(timeframe)
    if df is None or df.empty:
        raise RuntimeError(f"No {timeframe} history for {symbol}")
    return df.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
# Backtest
# ──────────────────────────────────────────────────────────────────────────────

def run_custom_backtest(definition: dict, days: int = 30) -> dict:
    asset     = definition.get("asset", "Gold")
    timeframe = definition.get("timeframe", "15m")
    risk      = definition.get("risk", {})
    sl_type   = risk.get("sl_type", "atr")
    sl_value  = float(risk.get("sl_value", 1.5))
    rr        = float(risk.get("rr_ratio", 2.0))
    use_tp    = bool(risk.get("use_tp", True)) and rr > 0

    df = _get_history(asset, timeframe, days)
    if len(df) < 60:
        raise RuntimeError(f"Not enough data ({len(df)} bars) — try more days.")

    long_ok, short_ok, ctx = evaluate_conditions(definition, df)
    exit_long, exit_short, time_stop = _evaluate_exits(definition, df, ctx)
    atr = ctx["atr"].values

    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    trades   = []
    i        = 50                  # warm-up for indicators
    while i < n - 2:
        direction = 0
        if long_ok[i]:
            direction = 1
        elif short_ok[i]:
            direction = -1
        if direction == 0:
            i += 1
            continue

        entry   = o[i + 1]                       # enter next bar open
        sl_dist = sl_value * atr[i] if sl_type == "atr" else sl_value
        if not np.isfinite(sl_dist) or sl_dist <= 0:
            i += 1
            continue
        sl = entry - direction * sl_dist
        tp = entry + direction * sl_dist * rr if use_tp else None

        exit_px = None
        reason  = None
        j       = i + 1
        while j < n:
            hit_sl = l[j] <= sl if direction == 1 else h[j] >= sl
            hit_tp = use_tp and (h[j] >= tp if direction == 1 else l[j] <= tp)
            if hit_sl:                            # conservative: SL first
                exit_px, reason = sl, "stop_loss"
                break
            if hit_tp:
                exit_px, reason = tp, "take_profit"
                break
            cond_exit = exit_long[j] if direction == 1 else exit_short[j]
            if cond_exit:
                exit_px, reason = c[j], "exit_signal"
                break
            if time_stop and (j - i) >= time_stop:
                exit_px, reason = c[j], "time_stop"
                break
            j += 1
        if exit_px is None:                       # still open at end of data
            exit_px, reason = c[n - 1], "end_of_data"

        pnl     = (exit_px - entry) * direction
        outcome = "win" if pnl > 0 else "loss"
        ts      = df["timestamp"].iloc[i + 1]
        if isinstance(ts, (int, float, np.integer, np.floating)):   # epoch ms → readable
            ts = datetime.utcfromtimestamp(float(ts) / 1000).strftime("%Y-%m-%d %H:%M")
        trades.append({
            "time":      str(ts)[:16],
            "direction": "BUY" if direction == 1 else "SELL",
            "entry":     round(float(entry), 4),
            "sl":        round(float(sl), 4),
            "tp":        round(float(tp), 4) if use_tp else None,
            "exit":      round(float(exit_px), 4),
            "outcome":   outcome,
            "reason":    reason,
            "pnl":       round(float(pnl), 4),
        })
        i = j + 1                                 # one position at a time

    wins     = [t for t in trades if t["outcome"] == "win"]
    losses   = [t for t in trades if t["outcome"] == "loss"]
    total    = len(trades)
    win_rate = round(100 * len(wins) / total, 1) if total else 0.0
    gross_w  = sum(t["pnl"] for t in wins)
    gross_l  = abs(sum(t["pnl"] for t in losses))
    pf       = round(gross_w / gross_l, 2) if gross_l > 0 else (99.0 if gross_w > 0 else 0.0)
    net      = round(gross_w - gross_l, 2)

    # max drawdown on cumulative pnl
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["pnl"]
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    # Best/worst single trade and SL distance range (points risked per trade)
    pnls     = [t["pnl"] for t in trades]
    sl_dists = [abs(t["entry"] - t["sl"]) for t in trades]
    best_trade  = round(max(pnls), 2) if pnls else 0.0
    worst_trade = round(min(pnls), 2) if pnls else 0.0
    max_sl      = round(max(sl_dists), 2) if sl_dists else 0.0
    min_sl      = round(min(sl_dists), 2) if sl_dists else 0.0

    # USD conversion using the configured trading lot for this asset
    lot = get_configured_lot(asset)
    cs  = CONTRACT_SIZES.get(asset, 1.0)
    usd = lambda pts: round(pts * lot * cs, 2)

    if total < 10:
        verdict = "Not enough trades to judge — try more days."
    elif pf >= 1.5 and win_rate * (1 + rr) > 100:
        verdict = "Looks promising ✅ — consider deploying to signals."
    elif pf >= 1.0:
        verdict = "Marginal — tweak conditions or risk settings."
    else:
        verdict = "Losing strategy ❌ — needs rework before deploying."

    return {
        "summary": {
            "asset":         asset,
            "timeframe":     timeframe,
            "days":          days,
            "bars":          n,
            "total_trades":  total,
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      win_rate,
            "net_points":    net,
            "profit_factor": pf,
            "max_drawdown":  round(max_dd, 2),
            "best_trade":    best_trade,
            "worst_trade":   worst_trade,
            "max_sl":        max_sl,
            "min_sl":        min_sl,
            "lot_size":      lot,
            "contract_size": cs,
            "net_usd":       usd(net),
            "best_trade_usd":  usd(best_trade),
            "worst_trade_usd": usd(worst_trade),
            "max_drawdown_usd": usd(round(max_dd, 2)),
            "verdict":       verdict,
            "exit_reasons":  {r: sum(1 for t in trades if t["reason"] == r)
                              for r in ("stop_loss", "take_profit", "exit_signal",
                                        "time_stop", "end_of_data")
                              if any(t["reason"] == r for t in trades)},
        },
        "trades": trades[-100:],
        "ran_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "params": {"days": days, "sl_type": sl_type, "sl_value": sl_value,
                   "rr_ratio": rr, "use_tp": use_tp},
    }


# ──────────────────────────────────────────────────────────────────────────────
# Live signal generation (same rules, latest bar)
# ──────────────────────────────────────────────────────────────────────────────

def generate_live_signals(definition: dict, data: dict, symbol: str) -> list:
    """
    data: dict of timeframe → DataFrame (from the live engine's MTF fetch).
    Checks the most recent closed bar against the strategy's conditions.
    """
    timeframe = definition.get("timeframe", "15m")
    df = data.get(timeframe)

    # 4h isn't in the live feed — resample from 1h
    if (df is None or df.empty) and timeframe == "4h":
        h1 = data.get("1h")
        if h1 is not None and not h1.empty:
            tmp = h1.copy()
            ts  = tmp["timestamp"]
            idx = pd.to_datetime(ts, unit="ms") if np.issubdtype(ts.dtype, np.number) else pd.to_datetime(ts)
            tmp.index = idx
            df = tmp.resample("4h").agg({
                "timestamp": "first", "open": "first", "high": "max",
                "low": "min", "close": "last", "volume": "sum",
            }).dropna().reset_index(drop=True)

    if df is None or df.empty or len(df) < 60:
        return []

    df = df.reset_index(drop=True)
    long_ok, short_ok, ctx = evaluate_conditions(definition, df)
    i = len(df) - 1                       # most recent bar

    direction = 1 if long_ok[i] else (-1 if short_ok[i] else 0)
    if direction == 0:
        return []

    risk     = definition.get("risk", {})
    sl_type  = risk.get("sl_type", "atr")
    sl_value = float(risk.get("sl_value", 1.5))
    rr       = float(risk.get("rr_ratio", 2.0))
    atr_now  = float(ctx["atr"].iloc[i])

    entry   = float(df["close"].iloc[i])
    sl_dist = sl_value * atr_now if sl_type == "atr" else sl_value
    if sl_dist <= 0:
        return []
    sl = entry - direction * sl_dist
    tp = entry + direction * sl_dist * rr

    cond_labels = []
    label_map = {c["type"]: c["label"] for c in CONDITION_CATALOGUE}
    for cnd in definition.get("conditions", []):
        cond_labels.append(label_map.get(cnd.get("type"), cnd.get("type")))

    return [{
        "signal":        "BUY" if direction == 1 else "SELL",
        "index":         i,
        "entry":         round(entry, 4),
        "sl":            round(sl, 4),
        "tp":            round(tp, 4),
        "rr":            rr,
        "timeframe":     timeframe,
        "session":       "Unknown",
        "quality_score": 7,
        "raw_score":     7,
        "confidence":    "Medium",
        "setup":         definition.get("name", "Custom Strategy"),
        "confluences":   cond_labels,
        "strategy_tag":  definition.get("id", "custom"),
        "entries": {
            "e1": {"price": round(entry, 4), "size_pct": 100, "label": "Signal close"},
        },
    }]
