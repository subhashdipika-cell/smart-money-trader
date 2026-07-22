"""Crypto candle service — MT5 (Vantage) PRIMARY, Binance fallback.

Since 2026-07-05 candles come from the SAME feed trades execute on (Vantage
MT5 via trading_executor's pinned terminal): the strategies place limit
orders at exact price levels (FVG tops, EMA touches), so computing those
levels on Binance spot while filling on Vantage CFD quotes introduced a
basis error of several dollars. Binance remains as an automatic fallback
whenever the MT5 terminal is unreachable, so headless signal generation
still works. All functions keep the original schema:
DataFrame[timestamp(ms, TRUE UTC), open, high, low, close, volume].

NOTE: MT5 bar times are BROKER-SERVER time (UTC+3 for Vantage). They are
normalized to true UTC here — the outcome resolver compares candle
timestamps against signal timestamps, and a 3h skew would corrupt fills.
"""
import threading as _threading
import time as _time

import pandas as pd
from binance.client import Client
from app.db.database import SessionLocal
from app.models.candle_model import Candle
from datetime import datetime, timedelta

client = Client(
    ping=False,
    requests_params={"timeout": 20}
)

# ── MT5 primary feed ─────────────────────────────────────────────────────────
_MT5_SYMBOL = {"BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD", "XAUUSD": "XAUUSD+"}
_MT5_TF = {}          # filled lazily once MetaTrader5 imports
_MT5_OFFSET = {"ts": 0.0, "sec": None}   # broker-server clock vs UTC, cached 1h

# FAIL-FAST guard. mt5.initialize() blocks for its full timeout when the
# terminal is down/not logged in, and this is called on EVERY candle fetch —
# on 2026-07-19 a disconnected Vantage terminal made every request retry it,
# starving the FastAPI workers so the whole backend timed out (UI showed
# OFFLINE while :8000 was still listening). Now: one thread may attempt a
# connect at a time, and after a failure we skip MT5 entirely for
# _MT5_RETRY_SECONDS so callers fall straight through to the Binance
# fallback, which is what the MT5-primary design intended all along.
_MT5_FAIL = {"ts": 0.0}
_MT5_LOCK = _threading.Lock()
# 5 min: a connect attempt against a dead/weekend terminal still costs ~10s
# (initialize timeout), so probe rarely rather than every minute.
_MT5_RETRY_SECONDS = 300.0


def _mt5_lib():
    """Connected MetaTrader5 module via trading_executor's pinned terminal, or
    None. Never blocks longer than one connect attempt, and backs off for
    _MT5_RETRY_SECONDS after a failure so a dead terminal can't hang the app."""
    if _time.time() - _MT5_FAIL["ts"] < _MT5_RETRY_SECONDS:
        return None                      # recently failed — use Binance fallback
    if not _MT5_LOCK.acquire(blocking=False):
        return None                      # another thread is already connecting
    try:
        import sys, os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
        from trading_executor import _connect
        mt5c, _ = _connect()
        if mt5c is None:
            _MT5_FAIL["ts"] = _time.time()
            return None
        import MetaTrader5 as mt5_lib
        # A live handle can still be stale (terminal closed/logged out) — a
        # cheap terminal_info() confirms it before we rely on it.
        try:
            if mt5_lib.terminal_info() is None:
                _MT5_FAIL["ts"] = _time.time()
                return None
        except Exception:
            _MT5_FAIL["ts"] = _time.time()
            return None
        if not _MT5_TF:
            _MT5_TF.update({"1m": mt5_lib.TIMEFRAME_M1, "5m": mt5_lib.TIMEFRAME_M5,
                            "15m": mt5_lib.TIMEFRAME_M15, "1h": mt5_lib.TIMEFRAME_H1,
                            "4h": mt5_lib.TIMEFRAME_H4, "1d": mt5_lib.TIMEFRAME_D1})
        _MT5_FAIL["ts"] = 0.0            # healthy again
        return mt5_lib
    except Exception:
        _MT5_FAIL["ts"] = _time.time()
        return None
    finally:
        _MT5_LOCK.release()


def _server_offset_sec(mt5_lib) -> int:
    """Broker-server clock offset vs UTC in seconds (Vantage: +3h). Measured
    from the newest M1 bar of BTCUSD (trades 24/7 → last bar ~ now)."""
    now = _time.time()
    if _MT5_OFFSET["sec"] is not None and now - _MT5_OFFSET["ts"] < 3600:
        return _MT5_OFFSET["sec"]
    try:
        bars = mt5_lib.copy_rates_from_pos("BTCUSD", mt5_lib.TIMEFRAME_M1, 0, 1)
        if bars is not None and len(bars):
            offset = round((int(bars[-1]["time"]) - now) / 3600.0) * 3600
            _MT5_OFFSET.update(ts=now, sec=int(offset))
            return int(offset)
    except Exception:
        pass
    return _MT5_OFFSET["sec"] or 0


def _mt5_candles_df(symbol: str, interval: str, limit: int):
    """Candles from the Vantage terminal in the Binance schema, or None."""
    mt5_lib = _mt5_lib()
    if mt5_lib is None:
        return None
    sym = _MT5_SYMBOL.get(symbol, symbol)
    tf = _MT5_TF.get(interval)
    if tf is None:
        return None
    try:
        if not mt5_lib.symbol_select(sym, True):
            return None
        bars = mt5_lib.copy_rates_from_pos(sym, tf, 0, int(limit))
        if bars is None or len(bars) == 0:
            return None
        offset = _server_offset_sec(mt5_lib)
        rows = [{
            "timestamp": (int(b["time"]) - offset) * 1000,   # true-UTC ms
            "open": float(b["open"]), "high": float(b["high"]),
            "low": float(b["low"]), "close": float(b["close"]),
            "volume": float(b["tick_volume"]),
        } for b in bars]
        return pd.DataFrame(rows)
    except Exception:
        return None

def klines_to_dataframe(klines):
    rows = []
    for k in klines:
        rows.append({
            "timestamp": k[0],
            "open":      float(k[1]),
            "high":      float(k[2]),
            "low":       float(k[3]),
            "close":     float(k[4]),
            "volume":    float(k[5]),
        })
    return pd.DataFrame(rows)


def download_and_save_data(symbol="BTCUSDT", interval="1h", limit=100):
    db     = SessionLocal()
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    for k in klines:
        candle = Candle(
            symbol=symbol, timeframe=interval,
            timestamp=datetime.fromtimestamp(k[0] / 1000),
            open=float(k[1]), high=float(k[2]),
            low=float(k[3]),  close=float(k[4]), volume=float(k[5])
        )
        db.add(candle)
    db.commit()
    db.close()
    return {"message": f"{limit} candles saved"}


def get_recent_candles_df(symbol="BTCUSDT", interval="1h", limit=200):
    """MT5 (Vantage) primary — same feed trades execute on; Binance fallback."""
    df = _mt5_candles_df(symbol, interval, limit)
    if df is not None and not df.empty:
        return df
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return klines_to_dataframe(klines)


def get_multi_timeframe_data(symbol="BTCUSDT"):
    """
    Returns candle data for 1m, 5m, 15m and 1h timeframes.
    5m is used for trendline detection on scalping setups.
    Each timeframe independently prefers MT5 and falls back to Binance.
    """
    timeframes = {
        "1m":  200,
        "5m":  220,   # ← added for trendline + structure on scalping TF
        "15m": 220,
        "1h":  220
    }
    data = {}
    mt5_used = binance_used = 0
    for tf, limit in timeframes.items():
        df = _mt5_candles_df(symbol, tf, limit)
        if df is not None and not df.empty:
            data[tf] = df
            mt5_used += 1
        else:
            klines   = client.get_klines(symbol=symbol, interval=tf, limit=limit)
            data[tf] = klines_to_dataframe(klines)
            binance_used += 1
    if binance_used and mt5_used == 0:
        print(f"[Candles] {symbol}: MT5 unreachable — served entirely from Binance fallback")
    return data


def get_historical_multi_timeframe_data(symbol="BTCUSDT", days=90, intervals=None):
    """
    Fetch historical candles. Pass `intervals` (e.g. ["1h"]) to download ONLY the
    timeframes a strategy actually needs — 30 days of unneeded 1m data alone is
    ~44 paginated requests and was the main cause of backtest timeouts.
    MT5 first (broker history is deep enough for backtests), Binance fallback.
    """
    _BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}
    start_time = datetime.utcnow() - timedelta(days=days)
    start_str  = start_time.strftime("%d %b %Y %H:%M:%S")
    timeframes = intervals or ["1m", "5m", "15m", "1h"]
    data       = {}
    for tf in timeframes:
        want = days * _BARS_PER_DAY.get(tf, 24)
        df = _mt5_candles_df(symbol, tf, want)
        # Accept MT5 only if it returned a reasonable share of the request —
        # broker history can be shallower than Binance for old 1m data.
        if df is not None and len(df) >= want * 0.7:
            data[tf] = df
            continue
        klines   = client.get_historical_klines(symbol=symbol, interval=tf, start_str=start_str)
        data[tf] = klines_to_dataframe(klines)
    return data