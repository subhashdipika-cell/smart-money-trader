"""
gold_advanced_strategies.py
===========================
Three institutional-grade Gold (XAUUSD) backtesting strategies for SMT.

Strategy 1 — London Session Breakout (Volatility Box)        | M15 / M30
Strategy 2 — M5 Mean Reversion (RSI + Bollinger Bands)       | M5
Strategy 3 — H4 Break-and-Retest Structure Shift             | H4 + Daily

Design principles:
  • NO fixed-pip stop losses — all SL/TP are ATR-scaled
  • Dynamic lot sizing: risk % of account / ATR-based SL distance
  • Session-time filters enforced (GMT)
  • MT5 local history first, yfinance fallback
  • Output format identical to existing SMT strategies so the
    Strategy Tester UI (StrategyTester.jsx + GoldDashboard.jsx)
    can display results without any frontend changes.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime, date as date_type
from typing import Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Data fetching
# ─────────────────────────────────────────────────────────────────────────────

def _fetch(timeframe: str, days: int) -> Optional[pd.DataFrame]:
    """
    Try MT5 local history (permanent, no cap) then yfinance (≤59 d for intraday).
    Returns DataFrame: timestamp(UTC tz-aware), open, high, low, close, volume
    """
    # ── Priority 1: MT5 ──────────────────────────────────────────────────────
    try:
        from app.services.gold_mt5_history import fetch_ohlcv as _mt5
        df = _mt5(timeframe=timeframe, days=days, use_cache=True)
        if df is not None and not df.empty:
            df = _ensure_utc(df)
            print(f"[GOLD ADV] MT5 {timeframe}: {len(df)} bars")
            return df
    except Exception as exc:
        print(f"[GOLD ADV] MT5 {timeframe} failed: {exc} — trying yfinance")

    # ── Priority 2: yfinance ─────────────────────────────────────────────────
    try:
        import yfinance as yf

        _YF_MAP = {"5m":"5m","15m":"15m","30m":"30m","1h":"60m","4h":"60m","1d":"1d"}
        interval = _YF_MAP.get(timeframe, "15m")
        max_days = 59 if "m" in timeframe else days + 10
        period   = f"{min(days + 3, max_days)}d"

        for sym in ("GC=F", "XAUUSD=X"):
            raw = yf.Ticker(sym).history(period=period, interval=interval)
            if not raw.empty:
                break
        if raw.empty:
            return None

        raw = raw.reset_index()
        raw.columns = [c.lower() for c in raw.columns]
        ts = "datetime" if "datetime" in raw.columns else "date"
        raw = raw.rename(columns={ts: "timestamp"})
        df  = raw[["timestamp","open","high","low","close","volume"]].copy()
        df  = _ensure_utc(df)

        # Resample 1h → 4h
        if timeframe == "4h":
            df = (df.set_index("timestamp")
                    .resample("4h")
                    .agg(open=("open","first"), high=("high","max"),
                         low=("low","min"), close=("close","last"),
                         volume=("volume","sum"))
                    .dropna()
                    .reset_index())

        print(f"[GOLD ADV] yfinance {timeframe}: {len(df)} bars")
        return df
    except Exception as exc:
        print(f"[GOLD ADV] yfinance {timeframe} also failed: {exc}")
        return None


def _ensure_utc(df: pd.DataFrame) -> pd.DataFrame:
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert("UTC")
    return df.sort_values("timestamp").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicators (pure pandas — no TA-lib dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    """Returns (lower_band, mid_band, upper_band)."""
    mid  = close.rolling(period).mean()
    std  = close.rolling(period).std()
    return mid - std_dev * std, mid, mid + std_dev * std


def _ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────────────────
# Price Action pattern detection
# ─────────────────────────────────────────────────────────────────────────────

def _pin_bar(row: pd.Series, direction: str) -> bool:
    """
    Bullish pin bar: long lower wick ≥ 2× body and ≥ 60 % of total range.
    Bearish pin bar: long upper wick ≥ 2× body and ≥ 60 % of total range.
    """
    body  = abs(row["close"] - row["open"])
    rng   = row["high"] - row["low"]
    if rng < 1e-6:
        return False
    if direction == "bull":
        wick = min(row["open"], row["close"]) - row["low"]
    else:
        wick = row["high"] - max(row["open"], row["close"])
    return wick >= max(2 * body, 1e-6) and wick >= 0.6 * rng


def _doji(row: pd.Series) -> bool:
    """Body is ≤ 10 % of total range."""
    body = abs(row["close"] - row["open"])
    rng  = row["high"] - row["low"]
    return rng > 0 and body <= 0.10 * rng


def _engulfing(prev: pd.Series, curr: pd.Series, direction: str) -> bool:
    if direction == "bull":
        return (curr["close"] > curr["open"]
                and prev["close"] < prev["open"]
                and curr["close"] > prev["open"]
                and curr["open"]  < prev["close"])
    else:
        return (curr["close"] < curr["open"]
                and prev["close"] > prev["open"]
                and curr["close"] < prev["open"]
                and curr["open"]  > prev["close"])


def _pa_label(row: pd.Series, prev: pd.Series, direction: str) -> str:
    if _pin_bar(row, direction):
        return "Pin Bar"
    if _doji(row):
        return "Doji"
    if _engulfing(prev, row, direction):
        return "Engulfing"
    return "Reversal"


# ─────────────────────────────────────────────────────────────────────────────
# Trade simulation
# ─────────────────────────────────────────────────────────────────────────────

def _sim_trade(
    future: pd.DataFrame,
    entry: float,
    sl: float,
    tp: float,
    direction: str,
    max_candles: int = 300,
) -> Tuple[str, float]:
    """
    Walk forward through future candles; return (result_label, exit_price).
    result_label: "TP" | "SL" | "EOD_WIN" | "EOD_LOSS"
    """
    for _, row in future.head(max_candles).iterrows():
        h, l = row["high"], row["low"]
        if direction == "long":
            if l <= sl:
                return "SL", sl
            if h >= tp:
                return "TP", tp
        else:
            if h >= sl:
                return "SL", sl
            if l <= tp:
                return "TP", tp

    last = future.iloc[-1]["close"] if not future.empty else entry
    result = "EOD_WIN" if (direction == "long" and last > entry) or \
                          (direction == "short" and last < entry) else "EOD_LOSS"
    return result, last


def _pnl(
    entry: float,
    exit_price: float,
    direction: str,
    sl_dist: float,
    account_size: float,
    risk_pct: float,
) -> float:
    """
    Dynamic position sizing.
    Gold: 1 standard lot = 100 troy oz → $1 price move = $100 P&L per lot.
    lot_size = (account × risk_pct) / (sl_dist × $100_per_lot)
    """
    if sl_dist <= 0:
        return 0.0
    risk_amt = account_size * risk_pct
    lot_size = risk_amt / (sl_dist * 100.0)
    lot_size = round(max(0.01, min(lot_size, 10.0)), 2)  # 0.01–10 lot cap
    diff     = (exit_price - entry) if direction == "long" else (entry - exit_price)
    return round(diff * lot_size * 100.0, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Result builders
# ─────────────────────────────────────────────────────────────────────────────

def _summary(trades: list, days: int, strategy: str, extra: dict) -> dict:
    if not trades:
        return dict(strategy=strategy, total_trades=0, wins=0, losses=0,
                    win_rate=0.0, net_pnl=0.0, profit_factor=0.0,
                    avg_win=0.0, avg_loss=0.0, max_drawdown=0.0,
                    best_trade=0.0, worst_trade=0.0,
                    breakeven_wr=0.0, days=days, **extra)

    pnls       = pd.Series([t["pnl"] for t in trades], dtype=float)
    wins       = pnls[pnls > 0]
    loss       = pnls[pnls < 0]    # strict < 0; breakeven (==0) counted separately
    breakevens = int((pnls == 0).sum())

    gross_profit = float(wins.sum())
    gross_loss   = float(loss.abs().sum())
    pf           = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

    cum      = pnls.cumsum()
    peak     = cum.cummax()
    max_dd   = round(float((cum - peak).min()), 2)

    # Breakeven WR: win_rate needed for zero net P&L given avg win/loss
    avg_win_v  = float(wins.mean()) if len(wins) else 0
    avg_loss_v = float(loss.abs().mean()) if len(loss) else 0
    be_wr = round(100 * avg_loss_v / (avg_win_v + avg_loss_v), 1) \
            if (avg_win_v + avg_loss_v) > 0 else 50.0

    return dict(
        strategy      = strategy,
        total_trades  = len(trades),
        wins          = int((pnls > 0).sum()),
        losses        = int((pnls < 0).sum()),
        breakevens    = breakevens,
        win_rate      = round(float((pnls > 0).mean() * 100), 1),
        net_pnl       = round(float(pnls.sum()), 2),
        profit_factor = pf,
        avg_win       = round(avg_win_v, 2),
        avg_loss      = round(-avg_loss_v, 2),
        max_drawdown  = max_dd,
        best_trade    = round(float(pnls.max()), 2),   # highest single-trade profit
        worst_trade   = round(float(pnls.min()), 2),   # highest single-trade loss
        breakeven_wr  = be_wr,
        days          = days,
        **extra,
    )


def _day_breakdown(trades: list) -> list:
    if not trades:
        return []
    df = pd.DataFrame(trades)
    df["pnl"] = df["pnl"].astype(float)
    g = df.groupby("date").agg(
        trades   = ("pnl", "count"),
        wins     = ("pnl", lambda x: int((x > 0).sum())),
        losses   = ("pnl", lambda x: int((x <= 0).sum())),
        pnl      = ("pnl", "sum"),
    ).reset_index()
    g["win_rate"] = (g["wins"] / g["trades"] * 100).round(1)
    g["pnl"]      = g["pnl"].round(2)
    return g.to_dict("records")


def _wrap(strategy_id: str, days: int, trades: list, params: dict) -> dict:
    return {
        "strategy":      strategy_id,
        "summary":       _summary(trades, days, strategy_id, params),
        "trades":        trades,
        "day_breakdown": _day_breakdown(trades),
        "ran_at":        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "params":        {"days": days, **params},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — London Session Breakout (Volatility Box)
# ─────────────────────────────────────────────────────────────────────────────

def run_london_breakout_backtest(
    days: int          = 60,
    timeframe: str     = "15m",
    rr_ratio: float    = 2.0,
    atr_sl_mult: float = 1.5,
    account_size: float = 1000,
    risk_pct: float    = 0.01,
) -> dict:
    """
    London Session Breakout (Volatility Box)

    Asian box  : mark high/low from 00:00–08:00 GMT
    Entry window: 08:00–11:00 GMT
    Long  : candle closes ABOVE box high AND volume > 5-bar vol MA
    Short : candle closes BELOW box low  AND volume > 5-bar vol MA
    SL    : entry ± (ATR(14) × atr_sl_mult)
    TP    : entry ± SL_distance × rr_ratio
    Max 1 trade per day.
    """
    df = _fetch(timeframe, days + 5)
    if df is None or df.empty:
        return {"error": "No data for London Breakout — check MT5 connection or reduce days"}

    df["atr"]    = _atr(df, 14)
    df["vol_ma"] = df["volume"].rolling(5, min_periods=1).mean()
    df["h_gmt"]  = df["timestamp"].dt.hour
    df["date"]   = df["timestamp"].dt.strftime("%Y-%m-%d")

    trades: list = []

    for date_str, day in df.groupby("date", sort=True):
        day = day.reset_index(drop=True)

        # Asian box (00:00 ≤ hour < 08:00 GMT)
        asian = day[day["h_gmt"] < 8]
        if len(asian) < 4:
            continue

        box_high = float(asian["high"].max())
        box_low  = float(asian["low"].min())
        if (box_high - box_low) < 0.5:        # sanity: box must be ≥ 50 cents
            continue

        # London window (08:00 ≤ hour < 11:00 GMT)
        london = day[(day["h_gmt"] >= 8) & (day["h_gmt"] < 11)]
        if london.empty:
            continue

        traded = False

        for idx, row in london.iterrows():
            if traded:
                break
            atr_v = float(row["atr"])
            if pd.isna(atr_v) or atr_v <= 0:
                continue

            vol_ok = (not pd.isna(row["vol_ma"])) and row["volume"] > row["vol_ma"]
            if not vol_ok:
                continue

            sl_dist = atr_sl_mult * atr_v

            # ── Long breakout ──────────────────────────────────────────────
            if float(row["close"]) > box_high:
                entry = float(row["close"])
                sl    = entry - sl_dist
                tp    = entry + sl_dist * rr_ratio
                future = day[day.index > idx]
                result, exit_p = _sim_trade(future, entry, sl, tp, "long")
                pnl_v = _pnl(entry, exit_p, "long", sl_dist, account_size, risk_pct)
                trades.append(dict(
                    date=date_str, time=row["timestamp"].strftime("%H:%M"),
                    direction="LONG", entry=round(entry,2),
                    sl=round(sl,2), tp=round(tp,2),
                    box_high=round(box_high,2), box_low=round(box_low,2),
                    atr=round(atr_v,2), result=result,
                    exit_price=round(exit_p,2), pnl=pnl_v,
                ))
                traded = True

            # ── Short breakout ─────────────────────────────────────────────
            elif float(row["close"]) < box_low:
                entry = float(row["close"])
                sl    = entry + sl_dist
                tp    = entry - sl_dist * rr_ratio
                future = day[day.index > idx]
                result, exit_p = _sim_trade(future, entry, sl, tp, "short")
                pnl_v = _pnl(entry, exit_p, "short", sl_dist, account_size, risk_pct)
                trades.append(dict(
                    date=date_str, time=row["timestamp"].strftime("%H:%M"),
                    direction="SHORT", entry=round(entry,2),
                    sl=round(sl,2), tp=round(tp,2),
                    box_high=round(box_high,2), box_low=round(box_low,2),
                    atr=round(atr_v,2), result=result,
                    exit_price=round(exit_p,2), pnl=pnl_v,
                ))
                traded = True

    return _wrap("london_breakout", days, trades,
                 {"rr_ratio": rr_ratio, "timeframe": timeframe, "atr_sl_mult": atr_sl_mult})


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — M5 Mean Reversion (RSI + Bollinger Bands)
# ─────────────────────────────────────────────────────────────────────────────

def run_m5_mean_reversion_backtest(
    days: int              = 30,
    rr_ratio: float        = 2.0,
    rsi_ob: float          = 80.0,   # overbought threshold for spike detection
    rsi_os: float          = 20.0,   # oversold threshold for spike detection
    rsi_return_hi: float   = 70.0,   # RSI crosses back below → trigger short
    rsi_return_lo: float   = 30.0,   # RSI crosses back above → trigger long
    bb_period: int         = 20,
    bb_std: float          = 2.0,
    atr_sl_mult: float     = 1.0,
    account_size: float    = 1000,
    risk_pct: float        = 0.01,
) -> dict:
    """
    M5 Mean Reversion — RSI extremes + Bollinger Band spike

    Session : 13:00–16:00 GMT (NY / London overlap)
    Spike   : close outside BB upper/lower + RSI > 80 or < 20
    Trigger : RSI crosses back inside 70 / 30 threshold (closed candle)
    SL      : 1 × ATR above spike high (short) or below spike low (long)
    TP1     : 50 % at BB midline (20 SMA)
    TP2     : 50 % at opposite BB band
    Combined TP = average of TP1 and TP2 for single P&L tracking.
    """
    df = _fetch("5m", days + 3)
    if df is None or df.empty:
        return {"error": "No M5 data — check MT5 or reduce days (≤59 for yfinance)"}

    df["atr"]      = _atr(df, 14)
    df["rsi"]      = _rsi(df["close"], 14)
    df["bb_lo"], df["bb_mid"], df["bb_hi"] = _bollinger(df["close"], bb_period, bb_std)
    df["h_gmt"]    = df["timestamp"].dt.hour
    df["date"]     = df["timestamp"].dt.strftime("%Y-%m-%d")

    trades: list = []

    # State: track if we're waiting for RSI to return inside band
    in_short_spike = False
    in_long_spike  = False
    spike_high     = None
    spike_low      = None
    cooldown       = 0      # bars to skip after a trade

    for i in range(bb_period + 15, len(df)):
        if cooldown > 0:
            cooldown -= 1
            continue

        row  = df.iloc[i]
        prev = df.iloc[i - 1]

        # Session filter: 13:00–16:00 GMT
        h = int(row["h_gmt"])
        if not (13 <= h < 16):
            # Reset spike state and cooldown outside session window
            in_short_spike = in_long_spike = False
            spike_high = spike_low = None
            cooldown = 0   # cooldown is session-scoped; carry-over would suppress next session open
            continue

        if pd.isna(row["rsi"]) or pd.isna(row["bb_hi"]) or pd.isna(row["atr"]):
            continue

        atr_v = float(row["atr"])

        # ── Detect spike ───────────────────────────────────────────────────
        if float(row["close"]) > float(row["bb_hi"]) and float(row["rsi"]) > rsi_ob:
            in_short_spike = True
            spike_high     = max(spike_high or row["high"], float(row["high"]))
            # Cancel pending long spike
            in_long_spike = False; spike_low = None

        if float(row["close"]) < float(row["bb_lo"]) and float(row["rsi"]) < rsi_os:
            in_long_spike  = True
            spike_low      = min(spike_low or row["low"], float(row["low"]))
            in_short_spike = False; spike_high = None

        # ── Trigger: RSI crosses back inside band ──────────────────────────

        # SHORT trigger
        if (in_short_spike
                and spike_high is not None
                and float(prev["rsi"]) > rsi_return_hi
                and float(row["rsi"]) <= rsi_return_hi):
            entry   = float(row["close"])
            sl      = spike_high + atr_v * atr_sl_mult
            sl_dist = sl - entry
            if sl_dist <= 0:
                in_short_spike = False; spike_high = None
                continue
            tp1     = float(row["bb_mid"])           # 50 % at midline
            tp2     = float(row["bb_lo"])            # 50 % at lower band
            tp_avg  = (tp1 + tp2) / 2.0

            future  = df.iloc[i + 1:]
            result, exit_p = _sim_trade(future, entry, sl, tp_avg, "short")
            pnl_v  = _pnl(entry, exit_p, "short", sl_dist, account_size, risk_pct)
            trades.append(dict(
                date=row["date"], time=row["timestamp"].strftime("%H:%M"),
                direction="SHORT", entry=round(entry,2),
                sl=round(sl,2), tp1=round(tp1,2), tp2=round(tp2,2),
                bb_mid=round(float(row["bb_mid"]),2),
                rsi_entry=round(float(row["rsi"]),1),
                spike_high=round(spike_high,2),
                atr=round(atr_v,2), result=result,
                exit_price=round(exit_p,2), pnl=pnl_v,
            ))
            in_short_spike = False; spike_high = None
            cooldown = 12   # ~1 hour cooldown on M5

        # LONG trigger
        elif (in_long_spike
                and spike_low is not None
                and float(prev["rsi"]) < rsi_return_lo
                and float(row["rsi"]) >= rsi_return_lo):
            entry   = float(row["close"])
            sl      = spike_low - atr_v * atr_sl_mult
            sl_dist = entry - sl
            if sl_dist <= 0:
                in_long_spike = False; spike_low = None
                continue
            tp1     = float(row["bb_mid"])
            tp2     = float(row["bb_hi"])
            tp_avg  = (tp1 + tp2) / 2.0

            future  = df.iloc[i + 1:]
            result, exit_p = _sim_trade(future, entry, sl, tp_avg, "long")
            pnl_v  = _pnl(entry, exit_p, "long", sl_dist, account_size, risk_pct)
            trades.append(dict(
                date=row["date"], time=row["timestamp"].strftime("%H:%M"),
                direction="LONG", entry=round(entry,2),
                sl=round(sl,2), tp1=round(tp1,2), tp2=round(tp2,2),
                bb_mid=round(float(row["bb_mid"]),2),
                rsi_entry=round(float(row["rsi"]),1),
                spike_low=round(spike_low,2),
                atr=round(atr_v,2), result=result,
                exit_price=round(exit_p,2), pnl=pnl_v,
            ))
            in_long_spike = False; spike_low = None
            cooldown = 12

    return _wrap("m5_mean_reversion", days, trades,
                 {"rr_ratio": rr_ratio, "rsi_ob": rsi_ob, "rsi_os": rsi_os,
                  "bb_period": bb_period, "bb_std": bb_std})


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — H4 Break-and-Retest Structure Shift
# ─────────────────────────────────────────────────────────────────────────────

def run_h4_break_retest_backtest(
    days: int               = 180,
    rr_ratio: float         = 2.5,
    swing_lookback: int     = 20,          # H4 candles to define a structural swing
    min_body_atr: float     = 0.3,         # breakout candle min body (× H4 ATR)
    zone_tolerance: float   = 0.5,         # retest zone width below broken level (× H4 ATR)
    retest_timeout_days: int= 15,          # abandon waiting after N days
    sl_breathing: float     = 0.5,         # extra Daily ATR below pattern low for SL
    account_size: float     = 1000,
    risk_pct: float         = 0.01,
) -> dict:
    """
    H4 Break-and-Retest Structure Shift

    Trend filter : Daily 50 EMA > 200 EMA → longs only; below → shorts only
    Step 1       : H4 swing high/low held for ≥ several days
    Step 2       : Impulsive breakout candle closes beyond structure
    Step 3       : Pullback into the broken level (zone tolerance = 0.5 × H4 ATR)
    Trigger      : Bullish/Bearish PA candle (Pin Bar, Doji, Engulfing)
    SL           : Below pattern low − (Daily ATR × sl_breathing)
    TP           : Entry ± SL_distance × rr_ratio
    """
    df4  = _fetch("4h",  days + 30)
    df1d = _fetch("1d",  days + 90)   # need extra history for EMA warm-up

    if df4 is None or df4.empty:
        return {"error": "No H4 data — check MT5 connection"}
    if df1d is None or df1d.empty:
        return {"error": "No Daily data — check MT5 connection"}

    # ── Daily indicators ───────────────────────────────────────────────────
    df1d["ema50"]  = _ema(df1d["close"], 50)
    df1d["ema200"] = _ema(df1d["close"], 200)
    df1d["atr_d"]  = _atr(df1d, 14)
    df1d["_date"]  = df1d["timestamp"].dt.date

    daily_trend: dict = {}
    daily_atr:   dict = {}
    for _, r in df1d.iterrows():
        d = r["_date"]
        if pd.isna(r["ema50"]) or pd.isna(r["ema200"]):
            daily_trend[d] = "neutral"
        elif r["ema50"] > r["ema200"]:
            daily_trend[d] = "bull"
        else:
            daily_trend[d] = "bear"
        daily_atr[d] = float(r["atr_d"]) if not pd.isna(r["atr_d"]) else 30.0

    # ── H4 indicators ─────────────────────────────────────────────────────
    df4["atr"] = _atr(df4, 14)
    df4["_date"] = df4["timestamp"].dt.date

    trades: list = []

    # State machine
    state       = "scanning"
    zone_hi     = zone_lo = None
    zone_dir    = None
    breakout_dt = None   # date_type when breakout was detected

    for i in range(swing_lookback + 1, len(df4) - 1):
        row  = df4.iloc[i]
        d    = row["_date"]
        atr4 = float(row["atr"]) if not pd.isna(row["atr"]) else 5.0
        trend   = daily_trend.get(d, "neutral")
        d_atr   = daily_atr.get(d, atr4 * 5)

        # ── State: scanning for structural swing + breakout ────────────────
        if state == "scanning":
            if trend == "neutral":
                continue

            window = df4.iloc[i - swing_lookback : i]
            sh_idx = window["high"].idxmax()
            sl_idx = window["low"].idxmin()
            sh_val = float(df4.loc[sh_idx, "high"])
            sl_val = float(df4.loc[sl_idx, "low"])

            body = abs(float(row["close"]) - float(row["open"]))

            # Bullish breakout
            if trend == "bull" and float(row["close"]) > sh_val:
                if body >= min_body_atr * atr4:
                    zone_hi  = sh_val + atr4 * 0.15   # just above old resistance
                    zone_lo  = sh_val - atr4 * zone_tolerance
                    zone_dir = "bull"
                    breakout_dt = d
                    state = "waiting_retest"
                continue

            # Bearish breakout
            if trend == "bear" and float(row["close"]) < sl_val:
                if body >= min_body_atr * atr4:
                    zone_hi  = sl_val + atr4 * zone_tolerance
                    zone_lo  = sl_val - atr4 * 0.15
                    zone_dir = "bear"
                    breakout_dt = d
                    state = "waiting_retest"
                continue

        # ── State: waiting for retest + PA confirmation ────────────────────
        elif state == "waiting_retest":
            # Timeout
            if breakout_dt and (d - breakout_dt).days > retest_timeout_days:
                state = "scanning"
                zone_hi = zone_lo = zone_dir = breakout_dt = None
                continue

            # Check if this candle enters the retest zone
            in_zone = (zone_lo is not None and zone_hi is not None and
                       (zone_lo <= float(row["low"]) <= zone_hi or
                        zone_lo <= float(row["close"]) <= zone_hi))

            if not in_zone:
                continue

            prev = df4.iloc[i - 1]

            if zone_dir == "bull":
                is_rev = (_pin_bar(row, "bull") or
                          _doji(row) or
                          _engulfing(prev, row, "bull"))
                if not is_rev:
                    continue

                entry   = float(row["close"])
                sl_raw  = float(row["low"]) - sl_breathing * d_atr
                sl_dist = entry - sl_raw
                if sl_dist <= 0:
                    continue
                tp      = entry + sl_dist * rr_ratio

                future = df4.iloc[i + 1:]
                result, exit_p = _sim_trade(future, entry, sl_raw, tp, "long", max_candles=120)
                pnl_v = _pnl(entry, exit_p, "long", sl_dist, account_size, risk_pct)

                trades.append(dict(
                    date=d.strftime("%Y-%m-%d"),
                    time=row["timestamp"].strftime("%H:%M"),
                    direction="LONG", entry=round(entry,2),
                    sl=round(sl_raw,2), tp=round(tp,2),
                    zone_high=round(zone_hi,2), zone_low=round(zone_lo,2),
                    atr_h4=round(atr4,2), daily_atr=round(d_atr,2),
                    pattern=_pa_label(row, prev, "bull"),
                    trend_filter="50EMA>200EMA",
                    result=result, exit_price=round(exit_p,2), pnl=pnl_v,
                ))
                state = "scanning"
                zone_hi = zone_lo = zone_dir = breakout_dt = None

            elif zone_dir == "bear":
                is_rev = (_pin_bar(row, "bear") or
                          _doji(row) or
                          _engulfing(prev, row, "bear"))
                if not is_rev:
                    continue

                entry   = float(row["close"])
                sl_raw  = float(row["high"]) + sl_breathing * d_atr
                sl_dist = sl_raw - entry
                if sl_dist <= 0:
                    continue
                tp      = entry - sl_dist * rr_ratio

                future = df4.iloc[i + 1:]
                result, exit_p = _sim_trade(future, entry, sl_raw, tp, "short", max_candles=120)
                pnl_v = _pnl(entry, exit_p, "short", sl_dist, account_size, risk_pct)

                trades.append(dict(
                    date=d.strftime("%Y-%m-%d"),
                    time=row["timestamp"].strftime("%H:%M"),
                    direction="SHORT", entry=round(entry,2),
                    sl=round(sl_raw,2), tp=round(tp,2),
                    zone_high=round(zone_hi,2), zone_low=round(zone_lo,2),
                    atr_h4=round(atr4,2), daily_atr=round(d_atr,2),
                    pattern=_pa_label(row, prev, "bear"),
                    trend_filter="50EMA<200EMA",
                    result=result, exit_price=round(exit_p,2), pnl=pnl_v,
                ))
                state = "scanning"
                zone_hi = zone_lo = zone_dir = breakout_dt = None

    return _wrap("h4_break_retest", days, trades,
                 {"rr_ratio": rr_ratio, "swing_lookback": swing_lookback,
                  "retest_timeout_days": retest_timeout_days})


# ─────────────────────────────────────────────────────────────────────────────
# CLI — run all three and print quick summary
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    SEP = "=" * 64

    def _show(label: str, result: dict) -> None:
        print(f"\n{SEP}")
        print(f"  {label}")
        print(SEP)
        if "error" in result:
            print(f"  ERROR: {result['error']}")
            return
        sm = result["summary"]
        print(f"  Trades       : {sm['total_trades']}")
        print(f"  Win Rate     : {sm['win_rate']} %")
        print(f"  Net P&L      : $ {sm['net_pnl']}")
        print(f"  Profit Factor: {sm['profit_factor']}")
        print(f"  Avg Win / Loss: ${sm['avg_win']} / ${sm['avg_loss']}")
        print(f"  Max Drawdown : $ {sm['max_drawdown']}")
        print(f"  Break-even WR: {sm['breakeven_wr']} %")
        print(f"  Ran at       : {result['ran_at']}")

    print(SEP)
    print("  SMT Gold Advanced Strategies — Backtest Runner")
    print(SEP)

    _show("1/3  London Session Breakout  (60d · M15 · RR 2.0)",
          run_london_breakout_backtest(days=60, timeframe="15m", rr_ratio=2.0))

    _show("2/3  M5 Mean Reversion  (30d · RSI 80/20 · RR 2.0)",
          run_m5_mean_reversion_backtest(days=30, rr_ratio=2.0))

    _show("3/3  H4 Break-and-Retest  (180d · Daily EMA · RR 2.5)",
          run_h4_break_retest_backtest(days=180, rr_ratio=2.5))

    print(f"\n{SEP}")
    print("  Done.")
    print(SEP)
