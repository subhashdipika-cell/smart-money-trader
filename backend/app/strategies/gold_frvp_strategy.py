"""
gold_frvp_strategy.py
─────────────────────
Advanced Gold FRVP Liquidity Trap Strategy
==========================================
Core logic:
  1. Every trading day, compute VAH / VAL from the Fixed Range Volume Profile
     using price/volume data from 00:00 → 15:59 IST (10:30 → 16:00 UTC+5:30).
  2. After 16:00 IST, watch for liquidity traps:
       SHORT  → price wicks above VAH then candle closes back below it
       LONG   → price wicks below VAL then candle closes back above it
  3. Max 2 trades per day. Stop new entries after a TP hit.
  4. SL = $8-$10 buffer.  TP = entry ± (SL × RR).

Data source (priority order):
  1. MT5 local history — permanent, offline, no day cap (gold_mt5_history.py)
  2. Yahoo Finance    — fallback when MT5 terminal is closed (GC=F / XAUUSD=X)
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

STRATEGY_ID   = "gold_frvp_liquidity_trap"
STRATEGY_NAME = "Gold FRVP Liquidity Trap"
DESCRIPTION   = (
    "Uses Fixed Range Volume Profile (VAH/VAL) computed before 16:00 IST, "
    "then enters on liquidity traps (false breakouts) after 16:00 IST. "
    "Max 2 trades/day, $8–10 SL, configurable RR."
)

# IST = UTC+5:30
IST_OFFSET_HOURS = 5.5          # hours ahead of UTC
PROFILE_END_HOUR_IST = 16       # 4 PM IST → profile window ends
TRADING_START_HOUR_IST = 16     # entries only after this

# ──────────────────────────────────────────────────────────────────────────────
# Data fetching
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_5m_data(days: int = 30) -> pd.DataFrame:
    """
    Pull 5-minute Gold bars.

    Priority 1: MT5 local history (permanent, offline, no day cap).
    Priority 2: Yahoo Finance (fallback when MT5 terminal is closed).
    """
    # ── 1. Try MT5 local history ──────────────────────────────────────────────
    try:
        from app.services.gold_mt5_history import fetch_ohlcv as mt5_fetch
        df = mt5_fetch(timeframe="5m", days=days, use_cache=True)
        if df is not None and not df.empty:
            print(f"[FRVP] MT5 history: {len(df)} bars ({days}d 5m)")
            return df
    except Exception as exc:
        print(f"[FRVP] MT5 fetch failed, falling back to Yahoo Finance: {exc}")

    # ── 2. Yahoo Finance fallback ─────────────────────────────────────────────
    print("[FRVP] Using Yahoo Finance as data source (MT5 not available)")
    period = f"{min(days + 2, 59)}d"   # YF caps 5m at ~60 days
    for sym in ["GC=F", "XAUUSD=X"]:
        try:
            df = yf.Ticker(sym).history(period=period, interval="5m")
            if df is not None and not df.empty:
                # Flatten MultiIndex if present
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.columns = [c.lower() for c in df.columns]
                df = df.reset_index()

                # Normalise timestamp column name
                ts_col = next(
                    (c for c in df.columns if "datetime" in c.lower() or c == "date"),
                    df.columns[0]
                )
                df = df.rename(columns={ts_col: "timestamp"})

                # Ensure UTC-aware then convert to IST
                if df["timestamp"].dt.tz is None:
                    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
                df["timestamp_ist"] = df["timestamp"].dt.tz_convert(
                    timezone(timedelta(hours=IST_OFFSET_HOURS))
                )
                df["timestamp"] = df["timestamp"].dt.tz_localize(None)
                df["timestamp_ist"] = df["timestamp_ist"].dt.tz_localize(None)

                required = {"open", "high", "low", "close", "volume"}
                if required.issubset(df.columns):
                    print(f"[FRVP] Yahoo Finance ({sym}): {len(df)} bars")
                    return df.dropna(subset=["close"])
        except Exception as exc:
            print(f"[FRVP] {sym} fetch failed: {exc}")

    return pd.DataFrame()


# ──────────────────────────────────────────────────────────────────────────────
# Volume Profile
# ──────────────────────────────────────────────────────────────────────────────

def calculate_value_area(
    df: pd.DataFrame,
    price_col: str = "close",
    vol_col: str = "volume",
    value_area_pct: float = 0.70,
    bin_size: float = 0.5,
) -> tuple[float | None, float | None]:
    """
    Programmatic FRVP: returns (VAH, VAL) for the given candle slice.
    Returns (None, None) if data is insufficient.
    """
    if df is None or df.empty:
        return None, None

    prices = df[price_col].dropna().values
    vols   = df[vol_col].dropna().values
    if len(prices) == 0 or vols.sum() == 0:
        return None, None

    min_p = np.floor(prices.min() / bin_size) * bin_size
    max_p = np.ceil(prices.max()  / bin_size) * bin_size
    bins  = np.arange(min_p, max_p + bin_size, bin_size)

    bin_vols = np.zeros(len(bins))
    for price, vol in zip(df[price_col].values, df[vol_col].values):
        idx = int((price - min_p) / bin_size)
        idx = max(0, min(idx, len(bins) - 1))
        bin_vols[idx] += vol

    total_vol   = bin_vols.sum()
    target_vol  = total_vol * value_area_pct
    poc_idx     = int(np.argmax(bin_vols))
    va_set      = {poc_idx}
    current_vol = bin_vols[poc_idx]
    up_i, dn_i  = poc_idx + 1, poc_idx - 1

    while current_vol < target_vol:
        vol_up = bin_vols[up_i] if up_i < len(bin_vols) else 0.0
        vol_dn = bin_vols[dn_i] if dn_i >= 0          else 0.0
        if vol_up == 0 and vol_dn == 0:
            break
        if vol_up >= vol_dn:
            va_set.add(up_i);  current_vol += vol_up;  up_i += 1
        else:
            va_set.add(dn_i);  current_vol += vol_dn;  dn_i -= 1

    sorted_va = sorted(va_set)
    return float(bins[sorted_va[-1]]), float(bins[sorted_va[0]])  # VAH, VAL


# ──────────────────────────────────────────────────────────────────────────────
# Backtest engine
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest(
    days: int = 30,
    sl_distance: float = 8.0,
    rr_ratio: float = 2.0,
    max_trades_per_day: int = 2,
    value_area_pct: float = 0.70,
) -> dict:
    """
    Full backtest of the Gold FRVP Liquidity Trap strategy.
    Returns a dict with summary stats and individual trade logs.
    """
    t0 = time.time()
    print(f"[FRVP] Fetching {days}d of 5-min Gold data…")
    df = _fetch_5m_data(days)

    if df.empty:
        return {
            "error": "No Gold data available from Yahoo Finance.",
            "trades": [],
            "summary": {},
        }

    # Work in IST time
    df["date_ist"]  = df["timestamp_ist"].dt.date
    df["hour_ist"]  = df["timestamp_ist"].dt.hour

    trade_logs: list[dict] = []
    daily_vah_val: dict    = {}   # date → {vah, val}

    for date, group in df.groupby("date_ist"):
        profile_data = group[group["hour_ist"] < PROFILE_END_HOUR_IST]
        trading_data = group[group["hour_ist"] >= TRADING_START_HOUR_IST]

        if profile_data.empty or trading_data.empty:
            continue

        vah, val = calculate_value_area(
            profile_data, value_area_pct=value_area_pct
        )
        if vah is None or val is None:
            continue

        daily_vah_val[str(date)] = {"vah": round(vah, 2), "val": round(val, 2)}

        trades_today = 0
        position     = None
        just_exited  = False   # prevents re-entry on the same candle as a stop-out
        entry_price  = sl_price = tp_price = 0.0

        candles = trading_data.to_dict("records")

        for i, candle in enumerate(candles):
            just_exited = False   # reset at start of each candle
            # ── Exit check ───────────────────────────────────────────────────
            if position == "SHORT":
                if candle["high"] >= sl_price:
                    trade_logs.append(_make_trade(
                        date, candle, "SHORT", entry_price, sl_price,
                        -sl_distance, "SL", vah, val
                    ))
                    position = None
                    just_exited = True   # block re-entry this candle
                elif candle["low"] <= tp_price:
                    trade_logs.append(_make_trade(
                        date, candle, "SHORT", entry_price, tp_price,
                        round(sl_distance * rr_ratio, 2), "TP", vah, val
                    ))
                    position = None
                    break   # rule: stop after TP

            elif position == "LONG":
                if candle["low"] <= sl_price:
                    trade_logs.append(_make_trade(
                        date, candle, "LONG", entry_price, sl_price,
                        -sl_distance, "SL", vah, val
                    ))
                    position = None
                    just_exited = True   # block re-entry this candle
                elif candle["high"] >= tp_price:
                    trade_logs.append(_make_trade(
                        date, candle, "LONG", entry_price, tp_price,
                        round(sl_distance * rr_ratio, 2), "TP", vah, val
                    ))
                    position = None
                    break   # rule: stop after TP

            # ── Entry check (only when flat and under daily limit) ───────────
            # `just_exited` flag prevents re-entry on the same candle a stop-out occurred
            if position is None and trades_today < max_trades_per_day and not just_exited:
                prev_close = candles[i - 1]["close"] if i > 0 else candle["close"]

                # SHORT trap: wick above VAH, close back below
                short_trap = (
                    (candle["high"] > vah and candle["close"] < vah) or
                    (prev_close > vah and candle["close"] < vah)
                )
                # LONG trap: wick below VAL, close back above
                long_trap = (
                    (candle["low"] < val and candle["close"] > val) or
                    (prev_close < val and candle["close"] > val)
                )

                if short_trap:
                    position    = "SHORT"
                    entry_price = candle["close"]
                    sl_price    = round(entry_price + sl_distance, 2)
                    tp_price    = round(entry_price - sl_distance * rr_ratio, 2)
                    trades_today += 1

                elif long_trap:
                    position    = "LONG"
                    entry_price = candle["close"]
                    sl_price    = round(entry_price - sl_distance, 2)
                    tp_price    = round(entry_price + sl_distance * rr_ratio, 2)
                    trades_today += 1

    # ── Summary analytics ────────────────────────────────────────────────────
    summary = _compute_summary(trade_logs, days, sl_distance, rr_ratio)
    day_breakdown = _day_breakdown(trade_logs)

    elapsed = round(time.time() - t0, 1)
    print(f"[FRVP] Backtest complete in {elapsed}s — {summary['total_trades']} trades")

    return {
        "strategy_id":   STRATEGY_ID,
        "strategy_name": STRATEGY_NAME,
        "days":          days,
        "sl_distance":   sl_distance,
        "rr_ratio":      rr_ratio,
        "ran_at":        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "elapsed_s":     elapsed,
        "summary":       summary,
        "daily_levels":  daily_vah_val,
        "day_breakdown": day_breakdown,
        "trades":        trade_logs,
    }


def _make_trade(date, candle, direction, entry, exit_p, pnl, result, vah, val):
    ts = candle.get("timestamp_ist") or candle.get("timestamp") or ""
    return {
        "date":      str(date),
        "time":      str(ts)[:16] if ts else "",
        "direction": direction,
        "entry":     round(float(entry), 2),
        "exit":      round(float(exit_p), 2),
        "pnl":       round(float(pnl), 2),
        "result":    result,
        "vah":       round(float(vah), 2),
        "val":       round(float(val), 2),
    }


def _compute_summary(trades: list, days: int, sl: float, rr: float) -> dict:
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "net_pnl": 0.0,
            "profit_factor": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "max_drawdown": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
            "breakeven_wr": round(100 / (1 + rr), 1),
        }

    wins   = [t for t in trades if t["result"] == "TP"]
    losses = [t for t in trades if t["result"] == "SL"]
    total  = len(trades)
    wr     = round(len(wins) / total * 100, 1) if total else 0
    net    = round(sum(t["pnl"] for t in trades), 2)

    gross_win  = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf         = round(gross_win / gross_loss, 2) if gross_loss > 0 else 999.0

    avg_win  = round(gross_win  / len(wins),   2) if wins   else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0

    # Max drawdown (running equity curve)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["pnl"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades":  total,
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      wr,
        "net_pnl":       net,
        "profit_factor": pf,
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "max_drawdown":  round(max_dd, 2),
        "best_trade":    round(max(t["pnl"] for t in trades), 2),
        "worst_trade":   round(min(t["pnl"] for t in trades), 2),
        "breakeven_wr":  round(100 / (1 + rr), 1),
        "days_tested":   days,
        "sl_used":       sl,
        "rr_used":       rr,
    }


def _day_breakdown(trades: list) -> list:
    day_map: dict = defaultdict(lambda: {"wins": 0, "losses": 0, "trades": 0, "pnl": 0.0})
    for t in trades:
        d = t["date"]
        day_map[d]["trades"] += 1
        day_map[d]["pnl"]    += t["pnl"]
        if t["result"] == "TP": day_map[d]["wins"]   += 1
        else:                   day_map[d]["losses"]  += 1

    return [
        {
            "date":     k,
            "trades":   v["trades"],
            "wins":     v["wins"],
            "losses":   v["losses"],
            "pnl":      round(v["pnl"], 2),
            "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
        }
        for k, v in sorted(day_map.items())
    ]
