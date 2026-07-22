"""
gold_service.py
---------------
Live XAU/USD candle data for the signal engine.

Source priority:
  1. MT5 terminal (Vantage XAUUSD+) — the SAME prices your orders fill at.
     Zero delay, no futures-vs-spot premium. Broker-time corrected to UTC.
  2. Yahoo Finance fallback (GC=F gold futures, then XAUUSD=X spot) — used
     only when the MT5 terminal is closed. Futures trade at a premium to
     spot, so MT5 is strongly preferred.

Returns DataFrames in the same format as binance_service.py
(timestamp in epoch milliseconds + OHLCV floats).
"""

import pandas as pd
import yfinance as yf
from datetime import datetime, timezone, timedelta

MT5_GOLD_SYMBOL = "XAUUSD+"   # Vantage Markets symbol; change if your broker differs

# Bars per timeframe for live signal generation.
# 1m gets 600 bars (~10h) so the outcome resolver can cover 8-hour-old signals.
MT5_BAR_COUNTS = {"1m": 600, "5m": 300, "15m": 300, "1h": 300}

GOLD_SYMBOL = "GC=F"        # Gold Futures — most liquid, matches Vantage XAUUSD closely
GOLD_SPOT   = "XAUUSD=X"   # Spot fallback

YF_INTERVAL_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "1h":  "60m"
}

YF_PERIOD_MAP = {
    "1m":  "2d",
    "5m":  "7d",
    "15m": "7d",
    "1h":  "30d"
}


def _mt5_broker_offset_hours(mt5):
    """
    MT5 timestamps are in BROKER time (usually UTC+2/+3), not UTC.
    Detect the offset from the live tick clock; default to 3 (Vantage)
    when the market is closed and the tick is stale.
    """
    import time as _time
    try:
        tick = mt5.symbol_info_tick(MT5_GOLD_SYMBOL)
        if tick is not None and tick.time:
            lead_h = (int(tick.time) - int(_time.time())) / 3600.0
            if 0.5 < lead_h < 4.5:
                return round(lead_h)
            if -0.2 <= lead_h <= 0.5:   # broker clock genuinely on UTC
                return 0
    except Exception:
        pass
    return 3


def _fetch_gold_mt5_all():
    """
    Fetch live 1m/5m/15m/1h Gold candles straight from the MT5 terminal.
    Returns {tf: DataFrame} or None if MT5 is unavailable.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return None
    try:
        # timeout: without it initialize() blocks ~60s when the terminal is
        # unreachable, stalling the whole 3-min scan cycle.
        if not mt5.initialize(path=r"C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe",
                              timeout=10000):
            return None
        mt5.symbol_select(MT5_GOLD_SYMBOL, True)
        tf_map = {"1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5,
                  "15m": mt5.TIMEFRAME_M15, "1h": mt5.TIMEFRAME_H1}
        offset_ms = _mt5_broker_offset_hours(mt5) * 3600 * 1000

        data = {}
        for tf, mt5_tf in tf_map.items():
            rates = mt5.copy_rates_from_pos(MT5_GOLD_SYMBOL, mt5_tf, 0, MT5_BAR_COUNTS[tf])
            if rates is None or len(rates) < 30:
                return None   # incomplete history → let Yahoo handle it
            data[tf] = pd.DataFrame([{
                "timestamp": int(r["time"]) * 1000 - offset_ms,   # broker → UTC ms
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": float(r["tick_volume"]),
            } for r in rates])
        return data
    except Exception as e:
        print(f"[Gold/MT5] live candle fetch failed: {e}")
        return None


def _fetch_gold(interval, period):
    """Yahoo fallback: try GC=F first, fall back to XAUUSD=X."""
    for sym in [GOLD_SYMBOL, GOLD_SPOT]:
        try:
            ticker = yf.Ticker(sym)
            df     = ticker.history(period=period, interval=interval)
            if df is not None and not df.empty:
                return _to_dataframe(df)
        except Exception as e:
            print(f"[Gold] {sym} failed: {e}")
    return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])


def _to_dataframe(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    rows = []
    for ts, row in df.iterrows():
        ts_ms = int(pd.Timestamp(ts).timestamp() * 1000)
        rows.append({
            "timestamp": ts_ms,
            "open":      float(row.get("open",   0)),
            "high":      float(row.get("high",   0)),
            "low":       float(row.get("low",    0)),
            "close":     float(row.get("close",  0)),
            "volume":    float(row.get("volume", 0))
        })

    return pd.DataFrame(rows)


# Simple cache to avoid refetching on every dashboard request
_cache = {}
_cache_time = {}
CACHE_TTL = 60  # seconds

def get_multi_timeframe_data(symbol="XAUUSD"):
    """
    Returns 1m, 5m, 15m, 1h Gold data — same format as binance_service.
    MT5 terminal (Vantage XAUUSD+) first; Yahoo Finance only as fallback.
    """
    import time as _time
    now = _time.time()
    if _cache.get(symbol) and now - _cache_time.get(symbol, 0) < CACHE_TTL:
        return _cache[symbol]  # return cached data silently

    # ── Priority 1: MT5 — same prices your orders fill at ────────────────────
    data = _fetch_gold_mt5_all()
    if data is not None:
        last = data["1m"]["close"].iloc[-1]
        print(f"[Gold/MT5] Live candles from terminal ({MT5_GOLD_SYMBOL}), last close={last:.2f}")
    else:
        # ── Priority 2: Yahoo Finance fallback (terminal closed) ─────────────
        data = {}
        for tf in ["1m", "5m", "15m", "1h"]:
            df = _fetch_gold(YF_INTERVAL_MAP[tf], YF_PERIOD_MAP[tf])
            limit = 600 if tf == "1m" else 200   # resolver needs ~8h of 1m bars
            if len(df) > limit:
                df = df.iloc[-limit:].reset_index(drop=True)
            data[tf] = df
        if any(not v.empty for v in data.values()):
            last = next((v["close"].iloc[-1] for v in data.values() if not v.empty), 0)
            print(f"[Gold/YF] ⚠ Fallback data (MT5 closed), last close={last:.2f}")

    _cache[symbol] = data
    _cache_time[symbol] = _time.time()
    return data


def get_historical_multi_timeframe_data(symbol="XAUUSD", days=90):
    """Returns historical Gold data for backtesting."""
    period_map = {
        "1m":  "7d",
        "5m":  "60d",
        "15m": "60d",
        "1h":  f"{min(days, 730)}d"
    }
    data = {}
    for tf in ["1m", "5m", "15m", "1h"]:
        df = _fetch_gold(YF_INTERVAL_MAP[tf], period_map[tf])
        data[tf] = df
        print(f"[Gold/YF] Historical {tf}: {len(df)} candles")
    return data


def get_current_price():
    """
    Returns current Gold spot price.
    Priority 1 → AllTick live feed (sub-second latency, bid/ask aware).
    Priority 2 → Yahoo Finance last close (fallback, ≤1 min delay on weekdays).
    """
    # ── Try the live WebSocket feed first ─────────────────────────────────────
    try:
        from app.services.gold_realtime_service import get_live_price
        live = get_live_price()
        if live is not None:
            return live
    except Exception:
        pass   # module may not be loaded yet on first import

    # ── Yahoo Finance fallback (5d window so weekends don't return empty) ─────
    for sym in [GOLD_SYMBOL, GOLD_SPOT]:
        try:
            ticker = yf.Ticker(sym)
            df     = ticker.history(period="5d", interval="1m")
            if not df.empty:
                return float(df["Close"].iloc[-1])
        except Exception:
            pass
    return None
