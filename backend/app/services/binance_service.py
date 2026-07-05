import pandas as pd
from binance.client import Client
from app.db.database import SessionLocal
from app.models.candle_model import Candle
from datetime import datetime, timedelta

client = Client(
    ping=False,
    requests_params={"timeout": 20}
)

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
    klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return klines_to_dataframe(klines)


def get_multi_timeframe_data(symbol="BTCUSDT"):
    """
    Returns candle data for 1m, 5m, 15m and 1h timeframes.
    5m is used for trendline detection on scalping setups.
    """
    timeframes = {
        "1m":  200,
        "5m":  220,   # ← added for trendline + structure on scalping TF
        "15m": 220,
        "1h":  220
    }
    data = {}
    for tf, limit in timeframes.items():
        klines   = client.get_klines(symbol=symbol, interval=tf, limit=limit)
        data[tf] = klines_to_dataframe(klines)
    return data


def get_historical_multi_timeframe_data(symbol="BTCUSDT", days=90, intervals=None):
    """
    Fetch historical candles. Pass `intervals` (e.g. ["1h"]) to download ONLY the
    timeframes a strategy actually needs — 30 days of unneeded 1m data alone is
    ~44 paginated requests and was the main cause of backtest timeouts.
    """
    start_time = datetime.utcnow() - timedelta(days=days)
    start_str  = start_time.strftime("%d %b %Y %H:%M:%S")
    timeframes = intervals or ["1m", "5m", "15m", "1h"]
    data       = {}
    for tf in timeframes:
        klines   = client.get_historical_klines(symbol=symbol, interval=tf, start_str=start_str)
        data[tf] = klines_to_dataframe(klines)
    return data