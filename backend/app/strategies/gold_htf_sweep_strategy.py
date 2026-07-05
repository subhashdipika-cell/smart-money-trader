"""
gold_htf_sweep_strategy.py
--------------------------
Daily BSL/SSL Liquidity Sweep — Gold (XAUUSD)

Strategy Mechanics
------------------
HTF Setup (Daily):
  · Previous Day's High (PDH) = Buy-Side Liquidity (BSL)
  · Previous Day's Low  (PDL) = Sell-Side Liquidity (SSL)

1M Execution (state machine — one trade at a time):

  SHORT setup  ("Judas Swing" above PDH):
    1. Price sweeps above PDH  → state = ABOVE_PDH
    2. A red (bearish) 1M candle closes → record its low as trigger; state = WAIT_SHORT
    3. Next candle's low breaks below trigger → SELL STOP fills at trigger
       SL  = highest wick of the sweep (sweep_extreme)
       TP  = entry − risk × rr_ratio
    4. Reset immediately if SL hit; advance to WAITING if TP hit.

  LONG setup  (mirror — sweep below PDL):
    1. Price sweeps below PDL  → state = BELOW_PDL
    2. A green (bullish) 1M candle closes → record its high as trigger; state = WAIT_LONG
    3. Next candle's high breaks above trigger → BUY STOP fills at trigger
       SL  = sweep_extreme (lowest wick)
       TP  = entry + risk × rr_ratio

Daily Breaker Rule (from the original strategy note):
  If 2 consecutive full SL losses fire for the same direction on the same day,
  trading is halted for that direction for the rest of the day. This prevents
  chasing a genuinely expanding trend that looks like a sweep.

Data Sources:
  · Priority 1 — MT5 local history (full history, no time cap)
  · Priority 2 — yfinance 5m as fallback (≤ 59d; slightly lower resolution)
  The 1D bars for PDH/PDL always use MT5 → yfinance fallback chain.

IMPORTANT — never calls mt5.login() or mt5.shutdown().
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional

# ── Re-use helpers from gold_advanced_strategies ──────────────────────────────
# _fetch(timeframe, days) → tries MT5 then yfinance; returns normalised DataFrame
# _wrap / _summary / _day_breakdown → standard SMT return shape (FRVP format)
from app.strategies.gold_advanced_strategies import (
    _fetch, _ensure_utc, _summary, _day_breakdown, _wrap,
)


# ── 1M / 5M execution data ─────────────────────────────────────────────────────

def _fetch_exec(days: int) -> Optional[pd.DataFrame]:
    """
    Fetch the execution timeframe data.
    Tries 1m from MT5 first (full history).
    Falls back to 5m from yfinance (max 59 days) — slightly coarser but usable.
    Returns (DataFrame, timeframe_label).
    """
    # Try MT5 1m
    try:
        from app.services.gold_mt5_history import fetch_ohlcv as _mt5
        df = _mt5(timeframe="1m", days=days, use_cache=True)
        if df is not None and not df.empty:
            df = _ensure_utc(df)
            print(f"[HTFSweep] MT5 1m: {len(df)} bars")
            return df, "1m"
    except Exception as e:
        print(f"[HTFSweep] MT5 1m failed: {e}")

    # Fallback: yfinance 5m (≤59d cap)
    try:
        import yfinance as yf
        capped = min(days, 59)
        for sym in ("GC=F", "XAUUSD=X"):
            raw = yf.Ticker(sym).history(period=f"{capped}d", interval="5m")
            if not raw.empty:
                break
        if not raw.empty:
            raw = raw.reset_index()
            raw.columns = [c.lower() for c in raw.columns]
            ts = "datetime" if "datetime" in raw.columns else "date"
            raw = raw.rename(columns={ts: "timestamp"})
            df = raw[["timestamp", "open", "high", "low", "close", "volume"]].copy()
            df = _ensure_utc(df)
            print(f"[HTFSweep] yfinance 5m: {len(df)} bars")
            return df, "5m"
    except Exception as e:
        print(f"[HTFSweep] yfinance 5m also failed: {e}")

    return None, None


# ── Timestamp helper ────────────────────────────────────────────────────────────

def _fmt(ts) -> str:
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        return t.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


# ── Main backtest ───────────────────────────────────────────────────────────────

def run_htf_sweep_backtest(
    days:            int   = 30,
    rr_ratio:        float = 5.0,
    max_risk_points: float = 4.0,
) -> dict:
    """
    Run the Daily Liquidity Sweep SMC backtest for Gold.
    Returns an SMT-compatible result dict (FRVP / gold_advanced format).

    Parameters
    ----------
    days            : calendar days of backtest history
    rr_ratio        : Risk-to-Reward for TP (default 5.0 — 1:5)
    max_risk_points : Skip trades where sweep is too violent (SL too wide)
    """
    # ── 1. Fetch data ──────────────────────────────────────────────────────────
    df_exec, tf_label = _fetch_exec(days)
    if df_exec is None or df_exec.empty or len(df_exec) < 50:
        return {"error": "Not enough execution data. Connect MT5 terminal for 1m bars."}

    df_1d = _fetch("1d", days + 5)
    if df_1d is None or df_1d.empty or len(df_1d) < 3:
        return {"error": "Not enough daily data for PDH/PDL levels."}

    # ── 2. Build PDH / PDL mapping ─────────────────────────────────────────────
    # Previous day's high/low: shift(1) on daily bars
    df_1d = df_1d.copy()
    df_1d["_date"]    = df_1d["timestamp"].dt.date
    df_1d["prev_high"] = df_1d["high"].shift(1)
    df_1d["prev_low"]  = df_1d["low"].shift(1)

    pdh_map = df_1d.dropna(subset=["prev_high"]).set_index("_date")["prev_high"]
    pdl_map = df_1d.dropna(subset=["prev_low"]).set_index("_date")["prev_low"]

    df = df_exec.copy()
    df["_date"] = df["timestamp"].dt.date
    df["pdh"]   = df["_date"].map(pdh_map).ffill()
    df["pdl"]   = df["_date"].map(pdl_map).ffill()
    df = df.dropna(subset=["pdh", "pdl"]).reset_index(drop=True)

    print(f"[HTFSweep] {len(df)} {tf_label} bars with PDH/PDL mapped")

    # ── 3. State-machine backtest ──────────────────────────────────────────────
    STATE_WAITING    = "WAITING"
    STATE_ABOVE_PDH  = "ABOVE_PDH"
    STATE_WAIT_SHORT = "WAIT_SHORT"
    STATE_BELOW_PDL  = "BELOW_PDL"
    STATE_WAIT_LONG  = "WAIT_LONG"

    state         = STATE_WAITING
    in_position   = False
    position_type = ""
    entry_price   = 0.0
    stop_loss     = 0.0
    take_profit   = 0.0
    trigger_price = 0.0
    sweep_extreme = 0.0
    entry_ts      = None

    trades = []

    # Daily loss-breaker counters  {date_str: {"SHORT": n, "LONG": n}}
    daily_sl_counts: dict = {}

    prev_date = None

    for i in range(1, len(df)):
        row  = df.iloc[i]
        pdh  = float(row["pdh"])
        pdl  = float(row["pdl"])
        c_ts = row["timestamp"]
        c_date_str = str(row["_date"])

        # ── Day-boundary reset ─────────────────────────────────────────────────
        if c_date_str != prev_date:
            if not in_position:
                state = STATE_WAITING   # fresh day; discard any pending setup
            prev_date = c_date_str
            # Initialise daily breaker counters
            if c_date_str not in daily_sl_counts:
                daily_sl_counts[c_date_str] = {"SHORT": 0, "LONG": 0}

        sl_counts = daily_sl_counts.setdefault(c_date_str, {"SHORT": 0, "LONG": 0})

        # ── Trade management ───────────────────────────────────────────────────
        if in_position:
            if position_type == "SHORT":
                if float(row["high"]) >= stop_loss:          # SL hit
                    pnl = round(entry_price - stop_loss, 2)  # negative
                    trades.append({
                        "signal":        "SELL",
                        "direction":     "SHORT",
                        "entry":         entry_price,
                        "sl":            stop_loss,
                        "tp":            take_profit,
                        "exit":          stop_loss,
                        "outcome":       "LOSS",
                        "pnl":           pnl,
                        "pnl_pips":      pnl,
                        "date":          c_date_str,
                        "time":          _fmt(entry_ts),
                        "exit_date":     _fmt(c_ts),
                        "setup":         "Judas Swing SHORT — SL",
                        "quality_score": 7,
                        "rr_ratio":      rr_ratio,
                        "tf_exec":       tf_label,
                    })
                    sl_counts["SHORT"] = sl_counts.get("SHORT", 0) + 1
                    in_position  = False
                    state        = STATE_WAITING

                elif float(row["low"]) <= take_profit:        # TP hit
                    pnl = round(entry_price - take_profit, 2) # positive
                    trades.append({
                        "signal":        "SELL",
                        "direction":     "SHORT",
                        "entry":         entry_price,
                        "sl":            stop_loss,
                        "tp":            take_profit,
                        "exit":          take_profit,
                        "outcome":       "WIN",
                        "pnl":           pnl,
                        "pnl_pips":      pnl,
                        "date":          c_date_str,
                        "time":          _fmt(entry_ts),
                        "exit_date":     _fmt(c_ts),
                        "setup":         "Judas Swing SHORT — TP",
                        "quality_score": 7,
                        "rr_ratio":      rr_ratio,
                        "tf_exec":       tf_label,
                    })
                    sl_counts["SHORT"] = 0   # reset after a win
                    in_position = False
                    state       = STATE_WAITING

            elif position_type == "LONG":
                if float(row["low"]) <= stop_loss:            # SL hit
                    pnl = round(stop_loss - entry_price, 2)   # negative
                    trades.append({
                        "signal":        "BUY",
                        "direction":     "LONG",
                        "entry":         entry_price,
                        "sl":            stop_loss,
                        "tp":            take_profit,
                        "exit":          stop_loss,
                        "outcome":       "LOSS",
                        "pnl":           pnl,
                        "pnl_pips":      pnl,
                        "date":          c_date_str,
                        "time":          _fmt(entry_ts),
                        "exit_date":     _fmt(c_ts),
                        "setup":         "Judas Swing LONG — SL",
                        "quality_score": 7,
                        "rr_ratio":      rr_ratio,
                        "tf_exec":       tf_label,
                    })
                    sl_counts["LONG"] = sl_counts.get("LONG", 0) + 1
                    in_position = False
                    state       = STATE_WAITING

                elif float(row["high"]) >= take_profit:       # TP hit
                    pnl = round(take_profit - entry_price, 2) # positive
                    trades.append({
                        "signal":        "BUY",
                        "direction":     "LONG",
                        "entry":         entry_price,
                        "sl":            stop_loss,
                        "tp":            take_profit,
                        "exit":          take_profit,
                        "outcome":       "WIN",
                        "pnl":           pnl,
                        "pnl_pips":      pnl,
                        "date":          c_date_str,
                        "time":          _fmt(entry_ts),
                        "exit_date":     _fmt(c_ts),
                        "setup":         "Judas Swing LONG — TP",
                        "quality_score": 7,
                        "rr_ratio":      rr_ratio,
                        "tf_exec":       tf_label,
                    })
                    sl_counts["LONG"] = 0   # reset after a win
                    in_position = False
                    state       = STATE_WAITING

            continue   # don't scan for new setups while in a trade

        # ── Daily breaker check ────────────────────────────────────────────────
        short_blocked = sl_counts.get("SHORT", 0) >= 2
        long_blocked  = sl_counts.get("LONG",  0) >= 2

        # ── Short setup: BSL sweep above PDH ──────────────────────────────────
        if not short_blocked:
            if state == STATE_WAITING and float(row["high"]) > pdh:
                state         = STATE_ABOVE_PDH
                sweep_extreme = float(row["high"])

            elif state in (STATE_ABOVE_PDH, STATE_WAIT_SHORT):
                sweep_extreme = max(sweep_extreme, float(row["high"]))

                is_red = float(row["close"]) < float(row["open"])
                if is_red:
                    trigger_price = float(row["low"])
                    state         = STATE_WAIT_SHORT

                elif state == STATE_WAIT_SHORT and float(row["low"]) < trigger_price:
                    # Entry triggered: sell stop hit
                    risk = sweep_extreme - trigger_price
                    if 0 < risk <= max_risk_points:
                        entry_price   = trigger_price
                        stop_loss     = round(sweep_extreme, 2)
                        take_profit   = round(entry_price - risk * rr_ratio, 2)
                        entry_ts      = c_ts
                        in_position   = True
                        position_type = "SHORT"
                        state         = STATE_WAITING   # will be managed next iter
                    else:
                        state = STATE_WAITING  # risk too wide — abort

        # ── Long setup: SSL sweep below PDL ───────────────────────────────────
        if not long_blocked:
            if state == STATE_WAITING and float(row["low"]) < pdl:
                state         = STATE_BELOW_PDL
                sweep_extreme = float(row["low"])

            elif state in (STATE_BELOW_PDL, STATE_WAIT_LONG):
                sweep_extreme = min(sweep_extreme, float(row["low"]))

                is_green = float(row["close"]) > float(row["open"])
                if is_green:
                    trigger_price = float(row["high"])
                    state         = STATE_WAIT_LONG

                elif state == STATE_WAIT_LONG and float(row["high"]) > trigger_price:
                    # Entry triggered: buy stop hit
                    risk = trigger_price - sweep_extreme
                    if 0 < risk <= max_risk_points:
                        entry_price   = trigger_price
                        stop_loss     = round(sweep_extreme, 2)
                        take_profit   = round(entry_price + risk * rr_ratio, 2)
                        entry_ts      = c_ts
                        in_position   = True
                        position_type = "LONG"
                        state         = STATE_WAITING   # managed on next iterations
                    else:
                        state = STATE_WAITING

    # ── Close any open trade at the last bar ───────────────────────────────────
    if in_position:
        last_price = float(df.iloc[-1]["close"])
        last_ts    = df.iloc[-1]["timestamp"]
        last_date  = str(df.iloc[-1]["_date"])
        if position_type == "SHORT":
            pnl = round(entry_price - last_price, 2)
        else:
            pnl = round(last_price - entry_price, 2)
        trades.append({
            "signal":        "SELL" if position_type == "SHORT" else "BUY",
            "direction":     position_type,
            "entry":         entry_price,
            "sl":            stop_loss,
            "tp":            take_profit,
            "exit":          last_price,
            "outcome":       "OPEN",
            "pnl":           pnl,
            "pnl_pips":      pnl,
            "date":          last_date,
            "time":          _fmt(entry_ts),
            "exit_date":     _fmt(last_ts),
            "setup":         f"Judas Swing {position_type} — OPEN",
            "quality_score": 7,
            "rr_ratio":      rr_ratio,
            "tf_exec":       tf_label,
        })

    print(f"[HTFSweep] done — {len(trades)} trades, "
          f"exec_tf={tf_label}, days={days}")

    return _wrap(
        "htf_liquidity_sweep",
        days,
        trades,
        {
            "rr_ratio":        rr_ratio,
            "max_risk_points": max_risk_points,
            "exec_tf":         tf_label or "1m",
            "strategy_id":     "htf_liquidity_sweep",
        },
    )
