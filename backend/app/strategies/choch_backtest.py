"""
choch_backtest.py
-----------------
SMC Change of Character (CHoCH) Backtest Engine

Strategy Rules (Multi-Timeframe):
  1. H4 Equilibrium Filter
     - Compute 50% midpoint of last 50 H4 candles
     - Below midpoint = Discount zone → only LONG setups
     - Above midpoint = Premium zone → only SHORT setups

  2. M15 CHoCH Detection (using last 4 candles: c3, c2, c1, c0)
     Bullish CHoCH:
       - c1 closes ABOVE max(c2.high, c3.high)  → structure break
       - c2 closed BELOW that structural high    → prior bearish structure confirmed
       - c1.low > c3.high                         → FVG exists between c3 and c1

     Bearish CHoCH (mirror):
       - c1 closes BELOW min(c2.low, c3.low)
       - c2 closed ABOVE that structural low
       - c1.high < c3.low                         → FVG exists

  3. Entry, SL, TP
     Bullish:
       entry  = c3.high  (top of FVG / OB open)
       sl     = min(c1.low, c2.low, c3.low) - 0.1% padding
       tp     = entry + rr × (entry - sl)

     Bearish:
       entry  = c3.low
       sl     = max(c1.high, c2.high, c3.high) + 0.1% padding
       tp     = entry - rr × (sl - entry)

Styles:
  Scalping  → M15 execution, H1 bias,  1:2 RR
  Intraday  → M15 execution, H4 bias,  1:2 or 1:3 RR
  Swing     → H1 execution,  D1 bias,  1:3 RR
"""

import pandas as pd
from collections import defaultdict
from datetime import datetime, timezone, timedelta


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ensure_dt_index(df):
    """
    Give the DataFrame a UTC DatetimeIndex.
    Handles Binance data where 'timestamp' is a plain epoch number (ms or s) —
    previously this crashed with \"'Index' object has no attribute 'tz'\".
    """
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" not in df.columns:
            raise ValueError("choch_backtest: DataFrame has no 'timestamp' column and no DatetimeIndex")
        ts = df["timestamp"]
        if pd.api.types.is_numeric_dtype(ts):
            unit = "ms" if float(ts.iloc[-1]) > 1e12 else "s"
            df.index = pd.to_datetime(ts, unit=unit, utc=True)
        else:
            df.index = pd.to_datetime(ts)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def _resample_h4(df_h1):
    """Resample H1 DataFrame to H4."""
    df = _ensure_dt_index(df_h1)
    return df.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def _resample_d1(df_h1):
    """Resample H1 DataFrame to Daily (for the Swing style's HTF bias)."""
    df = _ensure_dt_index(df_h1)
    return df.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def _equilibrium(df_htf, lookback=50):
    """50% midpoint of last `lookback` candles."""
    window = df_htf.tail(lookback)
    return (window["high"].max() + window["low"].min()) / 2.0


def _simulate_forward(df, entry, sl, tp, direction, from_idx, max_wait_bars=96):
    """
    Walk candles forward from from_idx.
    The entry is a LIMIT order (BUY below market / SELL above market), so price
    must first return to `entry` before the trade exists. If price never fills
    the order within `max_wait_bars`, the trade is reported as NO_FILL and must
    not be counted in the results.
    Returns ('WIN'|'LOSS'|'OPEN'|'NO_FILL', max_move_r, pnl_r).
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return "NO_FILL", 0.0, 0.0

    filled     = False
    max_move_r = 0.0
    for i in range(from_idx, len(df)):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]

        if not filled:
            if i - from_idx >= max_wait_bars:
                return "NO_FILL", 0.0, 0.0
            # Limit order fill check (touch fill)
            if direction == "BUY" and l <= entry:
                filled = True
                # Worst case: same candle also fell through the SL
                if l <= sl:
                    return "LOSS", 0.0, -1.0
            elif direction == "SELL" and h >= entry:
                filled = True
                if h >= sl:
                    return "LOSS", 0.0, -1.0
            else:
                continue
            continue  # filled this candle; start TP/SL tracking next candle

        if direction == "BUY":
            max_move_r = max(max_move_r, (h - entry) / risk)
            if l <= sl:  return "LOSS", round(max_move_r, 2), -1.0
            if h >= tp:  return "WIN",  round(max_move_r, 2), round((tp - entry) / risk, 2)
        else:
            max_move_r = max(max_move_r, (entry - l) / risk)
            if h >= sl:  return "LOSS", round(max_move_r, 2), -1.0
            if l <= tp:  return "WIN",  round(max_move_r, 2), round((entry - tp) / risk, 2)

    return ("OPEN" if filled else "NO_FILL"), round(max_move_r, 2), 0.0


# ── Main backtest ───────────────────────────────────────────────────────────────

def run_choch_backtest(df_exec, df_htf, rr=2.0, style="Intraday"):
    """
    df_exec : M15 (Intraday/Scalping) or H1 (Swing) DataFrame
    df_htf  : H4 (Intraday) or D1 (Swing) DataFrame
    rr      : Risk-Reward ratio
    style   : "Scalping" | "Intraday" | "Swing"

    Returns list of trade dicts.
    """
    if df_exec is None or df_exec.empty or df_htf is None or df_htf.empty:
        return []

    # Ensure DatetimeIndex with UTC (handles numeric Binance timestamps too)
    df_exec = _ensure_dt_index(df_exec)
    df_htf  = _ensure_dt_index(df_htf)

    trades      = []
    traded_days = set()   # one trade per day (either direction)

    for i in range(4, len(df_exec) - 1):
        c3 = df_exec.iloc[i - 3]
        c2 = df_exec.iloc[i - 2]
        c1 = df_exec.iloc[i - 1]   # CHoCH candle (just closed)
        ts = df_exec.index[i - 1]

        day_key = ts.strftime("%Y-%m-%d")
        if day_key in traded_days:
            continue

        # ── HTF equilibrium at this moment ────────────────────────────────
        htf_prior = df_htf[df_htf.index <= ts].tail(50)
        if len(htf_prior) < 5:
            continue
        eq = _equilibrium(htf_prior)
        in_discount = c1["close"] < eq
        in_premium  = c1["close"] > eq

        # ── Bullish CHoCH ─────────────────────────────────────────────────
        struct_high = max(c2["high"], c3["high"])
        # ICT FVG: use open of displacement candle (c1) vs high of origin (c3)
        # c1.low > c3.high is too strict and filters valid gaps where wick overlaps
        fvg_bull    = c1["open"] > c3["high"]   # gap between c3 top and c1 open

        if (in_discount
                and c1["close"] > struct_high
                and c2["close"] <= struct_high
                and fvg_bull):

            entry  = c3["high"]                                    # FVG top / OB open
            sl     = min(c1["low"], c2["low"], c3["low"]) * (1 - 0.001)
            risk   = entry - sl
            if risk <= 0 or risk > entry * 0.05:                   # skip if SL > 5% away
                continue
            tp = entry + rr * risk

            future = df_exec.iloc[i:]
            outcome, max_r, pnl_r = _simulate_forward(future, entry, sl, tp, "BUY", 0)
            if outcome == "NO_FILL":
                continue   # limit order never filled — no trade happened

            trades.append({
                "date":       day_key,
                "time":       ts.strftime("%H:%M"),
                "direction":  "BUY",
                "style":      style,
                "setup":      "CHoCH + FVG (Bullish)",
                "htf_bias":   "Discount",
                "equilibrium": round(eq, 2),
                "entry":      round(entry, 4),
                "sl":         round(sl, 4),
                "tp":         round(tp, 4),
                "rr":         rr,
                "pnl_pips":   round(pnl_r * (entry - sl), 4),
                "pnl_r":      round(pnl_r, 2),
                "max_move_r": max_r,
                "outcome":    outcome,
            })
            traded_days.add(day_key)
            continue   # one trade per day

        # ── Bearish CHoCH ─────────────────────────────────────────────────
        struct_low = min(c2["low"], c3["low"])
        # ICT FVG: use open of displacement candle (c1) vs low of origin (c3)
        fvg_bear   = c1["open"] < c3["low"]   # gap between c1 open and c3 bottom

        if (in_premium
                and c1["close"] < struct_low
                and c2["close"] >= struct_low
                and fvg_bear):

            entry = c3["low"]
            sl    = max(c1["high"], c2["high"], c3["high"]) * (1 + 0.001)
            risk  = sl - entry
            if risk <= 0 or risk > entry * 0.05:
                continue
            tp = entry - rr * risk

            future = df_exec.iloc[i:]
            outcome, max_r, pnl_r = _simulate_forward(future, entry, sl, tp, "SELL", 0)
            if outcome == "NO_FILL":
                continue   # limit order never filled — no trade happened

            trades.append({
                "date":       day_key,
                "time":       ts.strftime("%H:%M"),
                "direction":  "SELL",
                "style":      style,
                "setup":      "CHoCH + FVG (Bearish)",
                "htf_bias":   "Premium",
                "equilibrium": round(eq, 2),
                "entry":      round(entry, 4),
                "sl":         round(sl, 4),
                "tp":         round(tp, 4),
                "rr":         rr,
                "pnl_pips":   round(pnl_r * (sl - entry), 4),
                "pnl_r":      round(pnl_r, 2),
                "max_move_r": max_r,
                "outcome":    outcome,
            })
            traded_days.add(day_key)


    return trades


def summarise(trades, rr):
    wins    = [t for t in trades if t["outcome"] == "WIN"]
    losses  = [t for t in trades if t["outcome"] == "LOSS"]
    total   = len(wins) + len(losses)
    wr      = round(len(wins) / total * 100, 1) if total else 0
    net_r   = sum(t["pnl_r"] for t in trades)

    day_map = defaultdict(lambda: {"wins":0,"losses":0,"trades":0,"pnl_pips":0.0})
    for t in trades:
        d = t["date"]
        day_map[d]["trades"]   += 1
        day_map[d]["pnl_pips"] += t["pnl_pips"]
        if t["outcome"] == "WIN":   day_map[d]["wins"]   += 1
        if t["outcome"] == "LOSS":  day_map[d]["losses"] += 1

    return {
        "total":        len(trades),
        "wins":         len(wins),
        "losses":       len(losses),
        "open":         len(trades) - total,
        "win_rate":     wr,
        "breakeven_wr": round(100 / (1 + rr), 1),
        "net_r":        round(net_r, 2),
        "net_pips":     round(sum(t["pnl_pips"] for t in trades), 2),
        "best_trade":   round(max((t["pnl_pips"] for t in trades), default=0), 2),
        "worst_trade":  round(min((t["pnl_pips"] for t in trades), default=0), 2),
        "profit_factor": round(
            sum(t["pnl_r"] for t in wins) / abs(sum(t["pnl_r"] for t in losses))
            if losses else 0, 2
        ),
    }, [
        {"date": k, **v,
         "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0}
        for k, v in sorted(day_map.items())
    ]
