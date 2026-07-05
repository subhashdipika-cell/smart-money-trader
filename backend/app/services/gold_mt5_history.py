"""
gold_mt5_history.py
────────────────────
Fetches historical OHLCV bars from the MetaTrader 5 local database.

Why MT5 instead of Yahoo Finance?
──────────────────────────────────
• MT5 stores bars permanently in AppData — no internet required after first sync.
• No 59-day cap: you can fetch years of history.
• Exact Vantage Markets prices — same source as your live trades.
• Much faster: reading from local disk, not over the internet.

Cache behaviour
───────────────
• Fetched bars are cached to  backend/mt5_cache/<symbol>_<tf>.json
• Cache is considered fresh for CACHE_TTL_HOURS (default 4 h).
• Call clear_cache() to delete all cache files (returns freed bytes).
• If MT5 is unavailable the caller should fall back to Yahoo Finance.

IMPORTANT — never call mt5.login() or mt5.shutdown() here.
Those calls disconnect the terminal session.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger("gold_mt5_history")

# ── Constants ─────────────────────────────────────────────────────────────────

MT5_GOLD_SYMBOL  = "XAUUSD+"          # Vantage Markets symbol (with plus sign)
CACHE_TTL_HOURS  = 4                   # hours before cache is considered stale
IST_OFFSET       = timedelta(hours=5, minutes=30)

# Cache directory: backend/mt5_cache/
_CACHE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mt5_cache")
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cache_path(timeframe: str, days: int) -> str:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    sym_safe = MT5_GOLD_SYMBOL.replace("+", "plus")
    return os.path.join(_CACHE_DIR, f"{sym_safe}_{timeframe}_{days}d.json")


def _is_cache_fresh(path: str) -> bool:
    """Returns True if the cache file exists and is less than CACHE_TTL_HOURS old."""
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    age   = datetime.now(tz=timezone.utc) - mtime
    return age < timedelta(hours=CACHE_TTL_HOURS)


def _load_cache(path: str) -> Optional[pd.DataFrame]:
    try:
        with open(path, "r") as f:
            rows = json.load(f)
        df = pd.DataFrame(rows)
        # Restore datetime columns
        df["timestamp"]     = pd.to_datetime(df["timestamp"])
        df["timestamp_ist"] = pd.to_datetime(df["timestamp_ist"])
        log.info("[MT5Hist] Loaded %d rows from cache: %s", len(df), os.path.basename(path))
        return df
    except Exception as exc:
        log.warning("[MT5Hist] Cache load failed (%s): %s", path, exc)
        return None


def _save_cache(path: str, df: pd.DataFrame):
    try:
        rows = df.copy()
        rows["timestamp"]     = rows["timestamp"].astype(str)
        rows["timestamp_ist"] = rows["timestamp_ist"].astype(str)
        with open(path, "w") as f:
            json.dump(rows.to_dict(orient="records"), f)
        kb = os.path.getsize(path) / 1024
        log.info("[MT5Hist] Saved %d rows to cache (%.1f KB): %s", len(df), kb, os.path.basename(path))
    except Exception as exc:
        log.warning("[MT5Hist] Cache save failed: %s", exc)


# ── MT5 timeframe map ─────────────────────────────────────────────────────────

def _mt5_timeframe(tf: str):
    import MetaTrader5 as mt5
    return {
        "1m":  mt5.TIMEFRAME_M1,
        "5m":  mt5.TIMEFRAME_M5,
        "15m": mt5.TIMEFRAME_M15,
        "30m": mt5.TIMEFRAME_M30,
        "1h":  mt5.TIMEFRAME_H1,
        "4h":  mt5.TIMEFRAME_H4,
        "1d":  mt5.TIMEFRAME_D1,
    }.get(tf, mt5.TIMEFRAME_M5)


# ── Core fetch ────────────────────────────────────────────────────────────────

def fetch_ohlcv(
    timeframe: str = "5m",
    days: int = 30,
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Returns a DataFrame with columns:
        timestamp (UTC naive datetime), timestamp_ist (IST naive datetime),
        open, high, low, close, volume

    Returns None if MT5 is unavailable — caller should fall back to Yahoo Finance.

    Parameters
    ----------
    timeframe : "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d"
    days      : how many calendar days of history to fetch
    use_cache : read/write the on-disk cache (default True)
    """
    cache_path = _cache_path(timeframe, days)

    # 1. Try cache first
    if use_cache and _is_cache_fresh(cache_path):
        cached = _load_cache(cache_path)
        if cached is not None and not cached.empty:
            return cached

    # 2. Fetch from MT5
    try:
        import MetaTrader5 as mt5
    except ImportError:
        log.error("[MT5Hist] MetaTrader5 package not installed.")
        return None

    if not mt5.initialize(path=r"C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe"):
        log.warning("[MT5Hist] MT5 not initialized: %s", mt5.last_error())
        return None

    # Ensure symbol is visible in Market Watch
    mt5.symbol_select(MT5_GOLD_SYMBOL, True)

    utc_to   = datetime.now(tz=timezone.utc)
    utc_from = utc_to - timedelta(days=days + 3)  # +3 buffer for weekends

    rates = mt5.copy_rates_range(
        MT5_GOLD_SYMBOL,
        _mt5_timeframe(timeframe),
        utc_from,
        utc_to,
    )

    if rates is None or len(rates) == 0:
        err = mt5.last_error()
        log.warning("[MT5Hist] copy_rates_range returned None. Error: %s", err)
        return None

    log.info("[MT5Hist] Fetched %d bars from MT5 (%s, %dd)", len(rates), timeframe, days)

    # 3. Convert numpy structured array → DataFrame
    df = pd.DataFrame(rates)

    # ── Broker-time → UTC correction ─────────────────────────────────────────
    # MT5 bar times are in BROKER SERVER time (usually UTC+2 or UTC+3), not UTC.
    # Without this fix, session-window strategies (Asian box, London open, NY)
    # were trading 2–3 hours off. Auto-detect: if the latest bar appears to be
    # "in the future" vs real UTC now, that lead is the broker offset.
    broker_offset_hours = 0
    try:
        last_bar_secs = int(df["time"].iloc[-1])
        now_secs      = int(datetime.now(tz=timezone.utc).timestamp())
        lead_hours    = (last_bar_secs - now_secs) / 3600.0
        # A live feed's last bar should be ≤ 1 bar old; a positive lead of
        # roughly 1–4 h is the broker offset. Round to the nearest hour.
        if 0.5 < lead_hours < 4.5:
            broker_offset_hours = round(lead_hours)
        elif lead_hours <= 0.5:
            broker_offset_hours = 3   # market closed / stale feed → Vantage default (UTC+3)
    except Exception:
        broker_offset_hours = 3       # safe default for Vantage

    times_utc = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=broker_offset_hours)
    log.info("[MT5Hist] Broker offset applied: UTC+%d", broker_offset_hours)

    df["timestamp"] = times_utc.dt.tz_localize(None)
    df["timestamp_ist"] = (
        times_utc
        .dt.tz_convert(timezone(IST_OFFSET))
        .dt.tz_localize(None)
    )

    # MT5 returns tick_volume; use real_volume if available (non-zero)
    if "real_volume" in df.columns and df["real_volume"].sum() > 0:
        df["volume"] = df["real_volume"].astype(float)
    else:
        df["volume"] = df["tick_volume"].astype(float)

    df = df[["timestamp", "timestamp_ist", "open", "high", "low", "close", "volume"]].copy()
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]

    # 4. Save to cache
    if use_cache and not df.empty:
        _save_cache(cache_path, df)

    return df if not df.empty else None


# ── Cache management ──────────────────────────────────────────────────────────

def get_cache_info() -> dict:
    """
    Returns info about all cache files in mt5_cache/.
    {
        "files": [{"name": ..., "size_kb": ..., "age_hours": ...}],
        "total_size_kb": ...,
        "cache_dir": ...
    }
    """
    if not os.path.exists(_CACHE_DIR):
        return {"files": [], "total_size_kb": 0.0, "cache_dir": _CACHE_DIR}

    files = []
    total = 0.0
    now = datetime.now(tz=timezone.utc)

    for fname in sorted(os.listdir(_CACHE_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(_CACHE_DIR, fname)
        size_kb = os.path.getsize(fpath) / 1024
        mtime   = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
        age_h   = round((now - mtime).total_seconds() / 3600, 1)
        files.append({"name": fname, "size_kb": round(size_kb, 1), "age_hours": age_h})
        total += size_kb

    return {
        "files":         files,
        "total_size_kb": round(total, 1),
        "cache_dir":     _CACHE_DIR,
    }


def clear_cache() -> dict:
    """
    Deletes all .json files in mt5_cache/.
    Returns {"deleted_files": N, "freed_kb": X}.
    """
    if not os.path.exists(_CACHE_DIR):
        return {"deleted_files": 0, "freed_kb": 0.0}

    deleted = 0
    freed   = 0.0

    for fname in os.listdir(_CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(_CACHE_DIR, fname)
        try:
            freed += os.path.getsize(fpath) / 1024
            os.remove(fpath)
            deleted += 1
        except Exception as exc:
            log.warning("[MT5Hist] Could not delete %s: %s", fname, exc)

    log.info("[MT5Hist] Cleared cache: %d files, %.1f KB freed", deleted, freed)
    return {"deleted_files": deleted, "freed_kb": round(freed, 1)}
