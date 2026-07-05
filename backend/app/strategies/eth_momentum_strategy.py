"""
eth_momentum_strategy.py
------------------------
Dual-Timeframe ETH Momentum Strategy for SMT Strategy Tester.

Strategy Logic
--------------
Macro Trend (Daily):
  · 50 EMA > 200 EMA  →  Bull regime — long setups only
  · 50 EMA < 200 EMA  →  Bear regime — short setups only
  Daily macro state is shifted by 1 day before mapping onto 4H bars to
  prevent lookahead bias (the day's EMA is only known at the daily close).

Entry Trigger (4H MACD 12/26/9):
  · Bull cross  +  macro = Bull  +  RSI in [rsi_low, rsi_high]  → BUY
  · Bear cross  +  macro = Bear  +  RSI in [100-rsi_high, 100-rsi_low]  → SELL

Stop Loss:
  · Long : lowest low of the last 10 4H bars before the crossover − 0.1 %
  · Short: highest high of the last 10 4H bars before the crossover + 0.1 %

Exit (trailing — no fixed TP):
  · Long exits on the FIRST of: SL hit  or  MACD bearish cross
  · Short exits on the FIRST of: SL hit  or  MACD bullish cross

Key Design Notes
----------------
  · EWM (ewm) used throughout instead of simple rolling average — avoids
    artificial "block transitions" when extreme bars roll off the window.
  · RSI zone for shorts mirrors the long zone: (100 − rsi_high … 100 − rsi_low).
  · Quality score: 6 base + 1 if RSI is well inside the zone + 1 if macro
    trend has been consistent for ≥ 4 preceding bars.
"""

import time
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone


# ── Config ─────────────────────────────────────────────────────────────────────

TICKER_MAP = {
    "ETHUSD":  "ETH-USD",
    "ETHUSDT": "ETH-USD",
    "ETH":     "ETH-USD",
    "BTCUSD":  "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "BTC":     "BTC-USD",
}

SL_LOOKBACK = 10      # 4H bars to look back for swing SL
SL_BUFFER   = 0.001   # 0.1 % buffer beyond the swing extreme


# ── Data helpers ────────────────────────────────────────────────────────────────

def _fetch_4h(symbol: str, days: int) -> pd.DataFrame:
    """Fetch 4H OHLCV from yfinance.  Returns normalised DataFrame."""
    ticker = TICKER_MAP.get(symbol.upper().replace("-", ""), "ETH-USD")
    try:
        df = yf.download(
            ticker,
            period=f"{min(days + 30, 729)}d",
            interval="4h",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"[Momentum] yfinance 4H error: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df.columns = [
        c[0].lower() if isinstance(c, tuple) else c.lower()
        for c in df.columns
    ]
    df = df.rename_axis("timestamp").reset_index()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.reset_index(drop=True)


def _fetch_daily(symbol: str) -> pd.DataFrame:
    """Fetch 400 days of daily bars — enough warmup for EMA-200."""
    ticker = TICKER_MAP.get(symbol.upper().replace("-", ""), "ETH-USD")
    try:
        df = yf.download(
            ticker,
            period="400d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"[Momentum] yfinance daily error: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df.columns = [
        c[0].lower() if isinstance(c, tuple) else c.lower()
        for c in df.columns
    ]
    df = df.rename_axis("timestamp").reset_index()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.reset_index(drop=True)


# ── Indicator calculation ───────────────────────────────────────────────────────

def _build_indicators(
    df_4h:    pd.DataFrame,
    df_daily: pd.DataFrame,
    rsi_low:  float = 45.0,
    rsi_high: float = 65.0,
    fast:     int   = 12,
    slow:     int   = 26,
    signal:   int   = 9,
) -> pd.DataFrame:
    """
    Calculate all indicators and merge Daily macro state into the 4H frame.
    The daily macro trend is shifted by 1 day so 4H bars can only use
    confirmed prior-day signals — no intra-day lookahead.
    """
    df_4h    = df_4h.copy()
    df_daily = df_daily.copy()

    # ── Daily: EMA-50, EMA-200, macro_trend ──────────────────────────────────
    df_daily["ema50"]  = df_daily["close"].ewm(span=50,  adjust=False).mean()
    df_daily["ema200"] = df_daily["close"].ewm(span=200, adjust=False).mean()
    df_daily["macro"]  = np.where(
        df_daily["ema50"] > df_daily["ema200"], 1, -1
    )
    # Shift 1 day: today's bars use yesterday's confirmed daily close
    df_daily["macro"] = df_daily["macro"].shift(1).fillna(0).astype(int)

    df_4h["_date"]    = df_4h["timestamp"].dt.date
    df_daily["_date"] = df_daily["timestamp"].dt.date
    macro_map         = df_daily.set_index("_date")["macro"]

    df_4h["macro_trend"] = (
        df_4h["_date"].map(macro_map).ffill().fillna(0)
    ).astype(int)
    df_4h.drop(columns=["_date"], inplace=True)

    # ── 4H MACD (12 / 26 / 9) ────────────────────────────────────────────────
    fast_ema          = df_4h["close"].ewm(span=fast,   adjust=False).mean()
    slow_ema          = df_4h["close"].ewm(span=slow,   adjust=False).mean()
    df_4h["macd"]     = fast_ema - slow_ema
    df_4h["macd_sig"] = df_4h["macd"].ewm(span=signal,  adjust=False).mean()

    # ── 4H RSI-14 (Wilder EWM) ────────────────────────────────────────────────
    delta    = df_4h["close"].diff()
    gain_arr = np.where(delta > 0, delta, 0.0)
    loss_arr = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain_arr, index=df_4h.index).ewm(com=13, adjust=False).mean()
    avg_loss = pd.Series(loss_arr, index=df_4h.index).ewm(com=13, adjust=False).mean()
    rs       = avg_gain / np.where(avg_loss == 0, 1e-9, avg_loss)
    df_4h["rsi"] = 100.0 - (100.0 / (1.0 + rs))

    # ── MACD crossover flags ──────────────────────────────────────────────────
    prev_macd = df_4h["macd"].shift(1)
    prev_sig  = df_4h["macd_sig"].shift(1)
    df_4h["bull_cross"] = (prev_macd <= prev_sig) & (df_4h["macd"] > df_4h["macd_sig"])
    df_4h["bear_cross"] = (prev_macd >= prev_sig) & (df_4h["macd"] < df_4h["macd_sig"])

    return df_4h


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _fmt_ts(ts) -> str:
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        return t.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


# ── Main backtest entry point ───────────────────────────────────────────────────

def run_eth_momentum_backtest(
    symbol:   str   = "ETHUSD",
    days:     int   = 180,
    rsi_low:  float = 45.0,
    rsi_high: float = 65.0,
) -> dict:
    """
    Run the ETH dual-timeframe momentum backtest.
    Returns an SMT-compatible result dict (same shape as smc_swing).

    Parameters
    ----------
    symbol   : "ETHUSD" | "ETHUSDT" | "ETH"
    days     : backtest window in calendar days (90–365 recommended)
    rsi_low  : lower RSI bound for long entries (default 45)
    rsi_high : upper RSI bound for long entries (default 65)
                (short zone is automatically mirrored: 100-rsi_high … 100-rsi_low)
    """
    t0 = time.time()

    df_4h    = _fetch_4h(symbol, days)
    df_daily = _fetch_daily(symbol)

    if df_4h.empty or len(df_4h) < 60:
        return {
            "error": f"Not enough 4H data for {symbol} "
                     f"({len(df_4h)} bars). Try fewer days or check connectivity."
        }
    if df_daily.empty or len(df_daily) < 210:
        return {"error": "Not enough daily data for EMA-200 warmup (need ≥ 210 bars)."}

    df_full = _build_indicators(df_4h, df_daily, rsi_low, rsi_high)

    # Trim to the requested backtest window for trade execution
    cutoff = df_full["timestamp"].max() - pd.Timedelta(days=days)
    df     = df_full[df_full["timestamp"] >= cutoff].reset_index(drop=True)

    print(f"[Momentum] {symbol} {days}d — {len(df)} 4H bars in backtest window")

    trades = []
    i      = 0
    n      = len(df)

    while i < n:
        row   = df.iloc[i]
        trend = int(row["macro_trend"])
        rsi_v = float(row["rsi"])

        entry_signal = None

        # ── Long setup ────────────────────────────────────────────────────────
        if trend == 1 and bool(row["bull_cross"]) and rsi_low <= rsi_v <= rsi_high:
            entry_signal = "BUY"

        # ── Short setup ───────────────────────────────────────────────────────
        elif (trend == -1
              and bool(row["bear_cross"])
              and (100.0 - rsi_high) <= rsi_v <= (100.0 - rsi_low)):
            entry_signal = "SELL"

        if entry_signal is None:
            i += 1
            continue

        # ── Build the trade ───────────────────────────────────────────────────
        entry_price = float(row["close"])
        entry_ts    = row["timestamp"]

        # Stop loss: swing extreme of the last SL_LOOKBACK bars
        lb_start = max(0, i - SL_LOOKBACK)
        if entry_signal == "BUY":
            swing_ext = float(df.iloc[lb_start:i]["low"].min())
            sl        = round(swing_ext * (1.0 - SL_BUFFER), 2)
        else:
            swing_ext = float(df.iloc[lb_start:i]["high"].max())
            sl        = round(swing_ext * (1.0 + SL_BUFFER), 2)

        risk = abs(entry_price - sl)
        if risk <= 0:
            i += 1
            continue

        # ── Simulate: exit on MACD cross-back or SL hit ───────────────────────
        outcome    = "OPEN"
        exit_price = float(df.iloc[-1]["close"])
        exit_ts    = df.iloc[-1]["timestamp"]
        j_end      = i  # track last bar we visited

        for j in range(i + 1, min(i + 300, n)):
            c    = df.iloc[j]
            hi   = float(c["high"])
            lo   = float(c["low"])
            cl   = float(c["close"])
            ts_j = c["timestamp"]

            if entry_signal == "BUY":
                if lo <= sl:                        # SL hit
                    outcome    = "LOSS"
                    exit_price = sl
                    exit_ts    = ts_j
                    j_end      = j
                    break
                if bool(c["bear_cross"]):           # trailing exit
                    outcome    = "WIN" if cl > entry_price else "LOSS"
                    exit_price = cl
                    exit_ts    = ts_j
                    j_end      = j
                    break
            else:
                if hi >= sl:                        # SL hit
                    outcome    = "LOSS"
                    exit_price = sl
                    exit_ts    = ts_j
                    j_end      = j
                    break
                if bool(c["bull_cross"]):           # trailing exit
                    outcome    = "WIN" if cl < entry_price else "LOSS"
                    exit_price = cl
                    exit_ts    = ts_j
                    j_end      = j
                    break

        # P&L in USD price points
        if entry_signal == "BUY":
            pts = round(exit_price - entry_price, 2)
        else:
            pts = round(entry_price - exit_price, 2)

        rr_achieved = round(pts / risk, 2) if risk > 0 else 0.0

        # Quality score (6 base)
        score = 6
        # +1 if RSI is well inside the zone (not at the boundary)
        if rsi_low + 5.0 <= rsi_v <= rsi_high - 5.0:
            score += 1
        # +1 if macro trend has been consistent for ≥ 4 previous bars
        if i >= 4 and all(
            int(df.iloc[i - k]["macro_trend"]) == trend for k in range(1, 5)
        ):
            score += 1

        trades.append({
            "signal":        entry_signal,
            "direction":     "LONG" if entry_signal == "BUY" else "SHORT",
            "entry":         round(entry_price, 2),
            "sl":            round(sl, 2),
            "tp":            None,          # trailing exit — no fixed TP
            "exit":          round(exit_price, 2),
            "outcome":       outcome,
            "points":        pts,
            "pnl_pips":      pts,
            "date":          _fmt_ts(entry_ts),
            "exit_date":     _fmt_ts(exit_ts),
            "rsi_at_entry":  round(rsi_v, 1),
            "macro_trend":   "BULL" if trend == 1 else "BEAR",
            "strategy":      "BTC_Momentum" if symbol.upper().startswith("BTC") else "ETH_Momentum",
            "quality_score": score,
            "rr_achieved":   rr_achieved,
            "setup": (
                f"MACD {'Bull' if entry_signal == 'BUY' else 'Bear'} Cross "
                f"| RSI {rsi_v:.0f} | {('BULL' if trend == 1 else 'BEAR')} macro"
            ),
        })

        # Skip past this trade to avoid re-entering while open
        i = j_end + 1 if j_end > i else i + 1

    # ── Summary statistics ─────────────────────────────────────────────────────
    wins      = [t for t in trades if t["outcome"] == "WIN"]
    losses    = [t for t in trades if t["outcome"] == "LOSS"]
    open_t    = [t for t in trades if t["outcome"] == "OPEN"]
    total     = len(wins) + len(losses)

    wr         = round(len(wins) / total * 100, 1) if total else 0.0
    net        = round(sum(t["points"] for t in trades), 2)
    gross_win  = sum(t["points"] for t in wins)
    gross_loss = abs(sum(t["points"] for t in losses))
    pf         = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0
    avg_win    = round(gross_win / len(wins),    2) if wins   else 0.0
    avg_loss_v = round(-gross_loss / len(losses), 2) if losses else 0.0

    cum, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        cum   += t["points"]
        peak   = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    expectancy = round(
        (wr / 100.0 * avg_win) + ((1.0 - wr / 100.0) * avg_loss_v), 2
    ) if total else 0.0

    # Day breakdown
    day_map: dict = {}
    for t in trades:
        d = t["date"][:10] if t["date"] != "—" else "unknown"
        if d not in day_map:
            day_map[d] = {"date": d, "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        day_map[d]["trades"] += 1
        if t["outcome"] == "WIN":
            day_map[d]["wins"]   += 1
        elif t["outcome"] == "LOSS":
            day_map[d]["losses"] += 1
        day_map[d]["pnl"] = round(day_map[d]["pnl"] + t["points"], 2)

    for d in day_map.values():
        tot_d          = d["wins"] + d["losses"]
        d["win_rate"]  = round(d["wins"] / tot_d * 100, 1) if tot_d else 0.0
        d["pnl_pips"]  = d["pnl"]

    day_breakdown = sorted(day_map.values(), key=lambda x: x["date"])
    elapsed       = round(time.time() - t0, 2)

    print(
        f"[Momentum] {symbol} done — {len(trades)} trades, "
        f"WR={wr}%, PF={pf}, net={net:.2f} pts, {elapsed}s"
    )

    strategy_tag = "BTC_Momentum" if symbol.upper().startswith("BTC") else "ETH_Momentum"
    strategy_id  = "btc_momentum" if symbol.upper().startswith("BTC") else "eth_momentum"

    return {
        "strategy":      strategy_tag,
        "strategy_id":   strategy_id,
        "symbol":        symbol.upper(),
        "days":          days,
        "ran_at":        datetime.now(timezone.utc).isoformat(),
        "elapsed_s":     elapsed,
        "params": {
            "rsi_low":   rsi_low,
            "rsi_high":  rsi_high,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_sig":  9,
            "ema_fast":  50,
            "ema_slow":  200,
            "sl_lookback": SL_LOOKBACK,
        },
        "summary": {
            "total":         len(trades),
            "wins":          len(wins),
            "losses":        len(losses),
            "open":          len(open_t),
            "win_rate":      wr,
            "net_points":    net,
            "profit_factor": pf,
            "avg_win":       avg_win,
            "avg_loss":      avg_loss_v,
            "max_drawdown":  round(max_dd, 2),
            "best_trade":    round(max((t.get("points", 0) for t in trades), default=0), 2),
            "worst_trade":   round(min((t.get("points", 0) for t in trades), default=0), 2),
            "expectancy":    expectancy,
        },
        "trades":        trades,
        "day_breakdown": day_breakdown,
    }
