"""
dhan_service.py
---------------
Fetches Nifty 50 OHLCV candles from the Dhan API and returns
DataFrames in exactly the same format as binance_service.py
so the existing signal engine works without any changes.

Dhan API docs: https://dhanhq.co/docs/v2/
"""

import os
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ── Credentials ───────────────────────────────────────────────────────────────
# Reads from environment variables if set, otherwise uses defaults below.

DHAN_CLIENT_ID   = os.getenv("DHAN_CLIENT_ID",   "1103928764")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc5ODU0MDg2LCJpYXQiOjE3Nzk3Njc2ODYsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAzOTI4NzY0In0.GqfJI_LZn5RUu-nTaHiHWdEts_bJ6tsnWgr3sZn5t_4Ef_cohEoF6KEegyrUjh-LKqt-vdN0Ka5S_PPTKfjrUg")

BASE_URL = "https://api.dhan.co/v2"

# ── Nifty 50 instrument details ───────────────────────────────────────────────
# Security ID for Nifty 50 Index on Dhan
NIFTY_SECURITY_ID   = "13"
NIFTY_EXCHANGE      = "IDX_I"       # NSE Index
NIFTY_INSTRUMENT    = "INDEX"

# ── Market hours (IST = UTC+5:30) ─────────────────────────────────────────────
MARKET_OPEN_HOUR    = 9
MARKET_OPEN_MIN     = 15
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MIN    = 30

# ── Timeframe map — Dhan interval strings ─────────────────────────────────────
DHAN_INTERVAL_MAP = {
    "1m":  "1",
    "15m": "15",
    "1h":  "60",
    "1d":  "D"
}


# ── Market hours guard ────────────────────────────────────────────────────────

def is_market_open():
    """
    Returns True only during NSE trading hours on weekdays.
    9:15 AM – 3:30 PM IST (UTC+5:30), Monday–Friday.
    """
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist    = datetime.now(timezone.utc) + ist_offset
    weekday    = now_ist.weekday()   # 0=Mon … 6=Sun

    if weekday >= 5:   # Saturday or Sunday
        return False

    open_time  = now_ist.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MIN,  second=0)
    close_time = now_ist.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)

    return open_time <= now_ist <= close_time


def market_status_message():
    ist_offset = timedelta(hours=5, minutes=30)
    now_ist    = datetime.now(timezone.utc) + ist_offset
    return (
        f"NSE market is closed. "
        f"Current IST: {now_ist.strftime('%A %H:%M')}. "
        f"Opens Mon–Fri 9:15 AM IST."
    )


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _headers():
    return {
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id":    DHAN_CLIENT_ID,
        "Content-Type": "application/json",
        "Accept":       "application/json"
    }


def _post(endpoint, payload):
    url      = f"{BASE_URL}{endpoint}"
    response = requests.post(url, json=payload, headers=_headers(), timeout=15)
    response.raise_for_status()
    return response.json()


# ── Data conversion ───────────────────────────────────────────────────────────

def _to_dataframe(data):
    """
    Convert Dhan candle response to the same DataFrame format
    as binance_service.klines_to_dataframe():
      columns: timestamp, open, high, low, close, volume
    """
    if not data or "open" not in data:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    opens      = data.get("open",      [])
    highs      = data.get("high",      [])
    lows       = data.get("low",       [])
    closes     = data.get("close",     [])
    volumes    = data.get("volume",    [])
    timestamps = data.get("timestamp", [])

    rows = []
    for i in range(len(closes)):
        # Dhan timestamps are Unix seconds — convert to milliseconds
        # to match binance_service format
        ts_raw = timestamps[i] if i < len(timestamps) else 0
        ts_ms  = int(ts_raw) * 1000 if ts_raw else 0

        rows.append({
            "timestamp": ts_ms,
            "open":      float(opens[i])   if i < len(opens)   else 0.0,
            "high":      float(highs[i])   if i < len(highs)   else 0.0,
            "low":       float(lows[i])    if i < len(lows)    else 0.0,
            "close":     float(closes[i]),
            "volume":    float(volumes[i]) if i < len(volumes) else 0.0
        })

    df = pd.DataFrame(rows)
    return df


# ── Candle fetcher ────────────────────────────────────────────────────────────

def _fetch_candles(interval_str, from_date, to_date):
    """
    Calls Dhan's intraday or daily candle endpoint.
    from_date / to_date: "YYYY-MM-DD" strings
    """
    # Dhan uses different endpoints for intraday vs daily
    if interval_str == "D":
        endpoint = "/charts/historical"
    else:
        endpoint = "/charts/intraday"

    payload = {
        "securityId":   NIFTY_SECURITY_ID,
        "exchangeSegment": NIFTY_EXCHANGE,
        "instrument":   NIFTY_INSTRUMENT,
        "interval":     interval_str,
        "fromDate":     from_date,
        "toDate":       to_date
    }

    return _post(endpoint, payload)


def _date_range_for_limit(interval, limit):
    """
    Calculate fromDate/toDate to get approximately `limit` candles.
    """
    now = datetime.now(timezone.utc)

    if interval == "1m":
        # 1m candles — need enough trading days
        # ~375 candles per trading day (6.25 hrs × 60 min)
        days_needed = max(1, (limit // 375) + 3)
    elif interval == "15m":
        # ~25 candles per trading day
        days_needed = max(1, (limit // 25) + 3)
    elif interval == "1h":
        # ~6-7 candles per trading day
        days_needed = max(5, (limit // 6) + 5)
    else:
        days_needed = 30

    # Add extra days to account for weekends/holidays
    days_needed = int(days_needed * 1.5)

    from_dt  = now - timedelta(days=days_needed)
    to_str   = now.strftime("%Y-%m-%d")
    from_str = from_dt.strftime("%Y-%m-%d")

    return from_str, to_str


# ── Public API (mirrors binance_service) ─────────────────────────────────────

def get_multi_timeframe_data(symbol="NIFTY50"):
    """
    Returns dict of DataFrames for 1m, 15m, 1h — same structure
    as binance_service.get_multi_timeframe_data().
    Raises RuntimeError if market is closed.
    """
    if not is_market_open():
        raise RuntimeError(market_status_message())

    timeframes = {"1m": 200, "15m": 200, "1h": 200}
    data       = {}

    for tf, limit in timeframes.items():
        interval_str       = DHAN_INTERVAL_MAP[tf]
        from_date, to_date = _date_range_for_limit(tf, limit)

        try:
            raw = _fetch_candles(interval_str, from_date, to_date)
            df  = _to_dataframe(raw)

            # Keep only the last `limit` rows (newest)
            if len(df) > limit:
                df = df.iloc[-limit:].reset_index(drop=True)

            data[tf] = df
            print(f"[Dhan] {tf}: {len(df)} candles fetched")

        except Exception as e:
            print(f"[Dhan] Failed to fetch {tf}: {e}")
            data[tf] = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

    return data


def get_historical_multi_timeframe_data(symbol="NIFTY50", days=90):
    """
    Returns dict of DataFrames for backtesting — same structure
    as binance_service.get_historical_multi_timeframe_data().
    """
    now       = datetime.now(timezone.utc)
    from_dt   = now - timedelta(days=days)
    from_date = from_dt.strftime("%Y-%m-%d")
    to_date   = now.strftime("%Y-%m-%d")

    timeframes = ["1m", "15m", "1h"]
    data       = {}

    for tf in timeframes:
        interval_str = DHAN_INTERVAL_MAP[tf]
        try:
            raw = _fetch_candles(interval_str, from_date, to_date)
            df  = _to_dataframe(raw)
            data[tf] = df
            print(f"[Dhan] Historical {tf}: {len(df)} candles fetched")
        except Exception as e:
            print(f"[Dhan] Historical {tf} failed: {e}")
            data[tf] = pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

    return data
