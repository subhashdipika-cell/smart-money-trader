"""
ema_strategy.py
---------------
20 EMA Pullback & Rejection Strategy

Rules:
  BUY  when: 20 EMA is clearly rising (sloping ~45°)
             AND price pulls back to touch/pierce the 20 EMA
             AND bullish rejection candle (hammer, pin bar, or engulfing)
             AND candle CLOSES above the 20 EMA (not just wick)
             AND 20 EMA > 50 EMA (macro trend confirms)

  SELL when: 20 EMA is clearly falling
             AND price rallies up to touch the 20 EMA
             AND bearish rejection candle (shooting star or engulfing)
             AND candle CLOSES below the 20 EMA
             AND 20 EMA < 50 EMA (macro trend confirms)

SL  = just beyond the wick that tested the 20 EMA (swing high/low)
TP  = previous local high/low, minimum 1:2 RR

Asset notes:
  BTC/ETH: Crypto wicks through EMA to hunt stops — MUST wait for candle close
  Gold:    Highly reliable in London + NY sessions; avoid Asian session flat EMA
"""

import pandas as pd

try:
    from app.strategies.market_regime import detect_regime_from_df as _detect_regime
except ImportError:
    _detect_regime = None


def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def _ema_slope(ema_series, i, lookback=5):
    """
    Calculate EMA slope over last `lookback` candles.
    Returns slope as price-change-per-candle.
    Positive = rising, Negative = falling.
    A 45-degree slope means the EMA is moving meaningfully.
    """
    if i < lookback:
        return 0
    start = float(ema_series.iloc[i - lookback])
    end   = float(ema_series.iloc[i])
    return (end - start) / lookback


def _is_ema_trending(slope, price, min_slope_pct=0.0003):
    """
    EMA must be clearly trending, not flat.
    min_slope_pct: slope must be at least 0.03% of price per candle.
    """
    return abs(slope) >= price * min_slope_pct


def _touched_ema20(df, i, ema20_series, signal_type, lookback=3):
    """
    Price must have touched or slightly pierced the 20 EMA within last candles.
    BUY:  any low <= EMA20 (price dipped to/below the line)
    SELL: any high >= EMA20 (price rallied to/above the line)
    """
    for j in range(max(0, i - lookback + 1), i + 1):
        e20 = float(ema20_series.iloc[j])
        if signal_type == "BUY"  and float(df["low"].iloc[j])  <= e20:
            return True
        if signal_type == "SELL" and float(df["high"].iloc[j]) >= e20:
            return True
    return False


def _bullish_rejection(df, i, ema20):
    """
    Bullish rejection off the 20 EMA:
    - Candle closes ABOVE the EMA (not just wick)
    - Long lower wick (wick >= 60% of candle range) OR bullish engulfing
    - Previous candle was bearish (pullback context)
    """
    if i < 1:
        return False, "none"

    close  = float(df["close"].iloc[i])
    open_  = float(df["open"].iloc[i])
    high   = float(df["high"].iloc[i])
    low    = float(df["low"].iloc[i])
    c_prev = float(df["close"].iloc[i-1])
    o_prev = float(df["open"].iloc[i-1])

    # Must close above EMA
    if close <= ema20:
        return False, "none"

    candle_range = high - low
    if candle_range == 0:
        return False, "none"

    lower_wick = open_ - low if close > open_ else close - low
    body       = abs(close - open_)

    # Hammer / Pin bar: lower wick >= 60% of range, small body
    if lower_wick / candle_range >= 0.6 and body / candle_range <= 0.4:
        return True, "Hammer"

    # Bullish engulfing: current closes above prev open, opens below prev close
    if (close > open_) and (c_prev < o_prev) and (close >= o_prev) and (open_ <= c_prev):
        return True, "Bullish engulfing"

    # Morning star: current is a strong bullish candle closing well above EMA
    if (close > open_) and body / candle_range >= 0.6 and close > ema20 * 1.001:
        return True, "Strong bullish close"

    return False, "none"


def _bearish_rejection(df, i, ema20):
    """
    Bearish rejection off the 20 EMA:
    - Candle closes BELOW the EMA
    - Long upper wick OR bearish engulfing
    """
    if i < 1:
        return False, "none"

    close  = float(df["close"].iloc[i])
    open_  = float(df["open"].iloc[i])
    high   = float(df["high"].iloc[i])
    low    = float(df["low"].iloc[i])
    c_prev = float(df["close"].iloc[i-1])
    o_prev = float(df["open"].iloc[i-1])

    # Must close below EMA
    if close >= ema20:
        return False, "none"

    candle_range = high - low
    if candle_range == 0:
        return False, "none"

    upper_wick = high - open_ if close < open_ else high - close
    body       = abs(close - open_)

    # Shooting star / Pin bar: upper wick >= 60% of range
    if upper_wick / candle_range >= 0.6 and body / candle_range <= 0.4:
        return True, "Shooting star"

    # Bearish engulfing
    if (close < open_) and (c_prev > o_prev) and (close <= o_prev) and (open_ >= c_prev):
        return True, "Bearish engulfing"

    # Strong bearish close below EMA
    if (close < open_) and body / candle_range >= 0.6 and close < ema20 * 0.999:
        return True, "Strong bearish close"

    return False, "none"


def _recent_swing_low(df, i, lookback=8):
    """Swing low = lowest point of the wick that tested the EMA."""
    start = max(0, i - lookback)
    return float(df["low"].iloc[start:i+1].min())


def _recent_swing_high(df, i, lookback=8):
    """Swing high = highest point of the wick that tested the EMA."""
    start = max(0, i - lookback)
    return float(df["high"].iloc[start:i+1].max())


def _is_gold_session(df, i):
    """Gold works best in London and NY sessions."""
    if "timestamp" not in df.columns:
        return True  # assume valid if no timestamp
    ts = df["timestamp"].iloc[i]
    try:
        hour = __import__("datetime").datetime.utcfromtimestamp(int(ts)/1000).hour
        return 7 <= hour < 20  # London open (7 UTC) to NY close (20 UTC)
    except Exception:
        return True


def generate_ema_signals(df, rr_ratio=3.0, lookback=200, symbol=""):
    """
    Run the 20 EMA Pullback & Rejection strategy.
    Skips signal generation when market regime is SIDEWAYS.
    Returns list of signal dicts compatible with existing signal format.
    """
    if df is None or len(df) < 60:
        return []

    # ── Market regime gate ────────────────────────────────────────────────────
    if _detect_regime is not None:
        regime_info = _detect_regime(df.tail(100))
        regime      = regime_info["regime"]
        print(f"[EMA20/{symbol}] Market regime: {regime} — {regime_info['reason']}")
        if regime == "SIDEWAYS":
            print(f"[EMA20/{symbol}] Skipping — sideways market")
            return []
    # ─────────────────────────────────────────────────────────────────────────

    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)

    ema20  = _ema(closes, 20)
    ema50  = _ema(closes, 50)

    signals = []
    start   = max(55, len(df) - lookback)
    is_gold = symbol in ("XAUUSD", "PAXGUSDT") or (len(df) > 0 and float(closes.iloc[-1]) > 3000 and float(closes.iloc[-1]) < 10000)

    for i in range(start, len(df)):
        e20   = float(ema20.iloc[i])
        e50   = float(ema50.iloc[i])
        close = float(closes.iloc[i])
        high  = float(highs.iloc[i])
        low   = float(lows.iloc[i])

        # ── Step 1: EMA must be clearly trending (not flat) ──────────────────
        slope = _ema_slope(ema20, i, lookback=5)
        if not _is_ema_trending(slope, close):
            continue   # flat EMA = ranging market = stay out

        # ── Gold session filter ───────────────────────────────────────────────
        if is_gold and not _is_gold_session(df, i):
            continue   # Gold: avoid Asian session flat EMA

        macro_bullish = e20 > e50   # 20 EMA above 50 EMA = uptrend
        macro_bearish = e20 < e50   # 20 EMA below 50 EMA = downtrend

        # ── BUY signal ────────────────────────────────────────────────────────
        if macro_bullish and slope > 0:
            # Price must have pulled back to touch the rising 20 EMA
            touched = _touched_ema20(df, i, ema20, "BUY", lookback=3)
            if not touched:
                continue

            # Bullish rejection candle — must CLOSE above EMA
            is_rejection, pattern = _bullish_rejection(df, i, e20)
            if not is_rejection:
                continue

            # SL: just below the swing low (wick that tested EMA)
            sl_raw   = _recent_swing_low(df, i, lookback=5)
            sl_raw   = min(sl_raw, e20 - (e20 * 0.001))  # at least 0.1% below EMA

            # Asset-specific SL minimums
            if close > 30000:    # BTC
                min_sl = 100.0
                max_sl = 400.0
            elif 3000 < close <= 10000:  # Gold
                min_sl = 5.0
                max_sl = 30.0
            else:                # ETH
                min_sl = 15.0
                max_sl = 35.0

            risk = close - sl_raw
            risk = max(risk, min_sl)
            if risk > max_sl:
                continue   # SL too wide — skip

            sl = round(close - risk, 4)
            tp = round(close + (risk * rr_ratio), 4)

            # Discrete entries
            e1 = round(close, 4)
            e2 = round(close - risk * 0.3, 4)   # deeper pullback
            e3 = round(close - risk * 0.6, 4)   # best price

            confluences = [
                f"20 EMA rising (slope: {slope:+.3f})",
                "Price pulled back to 20 EMA",
                f"{pattern} — bullish rejection",
                "20 EMA > 50 EMA (macro bullish)"
            ]

            signals.append({
                "signal":        "BUY",
                "index":         i,
                "entry":         e1,
                "sl":            sl,
                "tp":            tp,
                "rr":            rr_ratio,
                "timeframe":     "Intraday",
                "session":       "Unknown",
                "quality_score": 7,
                "raw_score":     7,
                "confidence":    "High",
                "setup":         "20 EMA Pullback + Rejection",
                "confluences":   confluences,
                "strategy_tag":  "EMA20_Intraday",
                "entries": {
                    "e1": {"price": e1, "size_pct": 50, "label": "20 EMA touch"},
                    "e2": {"price": e2, "size_pct": 30, "label": "Deeper pullback"},
                    "e3": {"price": e3, "size_pct": 20, "label": "Best price"}
                },
                "trailing_sl": {
                    "enabled":     True,
                    "activate_at": round(close + risk, 4),
                    "step":        round(risk * 0.5, 4),
                    "current_sl":  sl,
                    "activated":   False
                }
            })

        # ── SELL signal ───────────────────────────────────────────────────────
        elif macro_bearish and slope < 0:
            # Price must have rallied up to touch the falling 20 EMA
            touched = _touched_ema20(df, i, ema20, "SELL", lookback=3)
            if not touched:
                continue

            # Bearish rejection candle — must CLOSE below EMA
            is_rejection, pattern = _bearish_rejection(df, i, e20)
            if not is_rejection:
                continue

            # SL: just above the swing high (wick that tested EMA)
            sl_raw = _recent_swing_high(df, i, lookback=5)
            sl_raw = max(sl_raw, e20 + (e20 * 0.001))

            if close > 30000:    # BTC
                min_sl = 100.0
                max_sl = 400.0
            elif 3000 < close <= 10000:  # Gold
                min_sl = 5.0
                max_sl = 30.0
            else:                # ETH
                min_sl = 15.0
                max_sl = 35.0

            risk = sl_raw - close
            risk = max(risk, min_sl)
            if risk > max_sl:
                continue

            sl = round(close + risk, 4)
            tp = round(close - (risk * rr_ratio), 4)

            e1 = round(close, 4)
            e2 = round(close + risk * 0.3, 4)
            e3 = round(close + risk * 0.6, 4)

            confluences = [
                f"20 EMA falling (slope: {slope:+.3f})",
                "Price rallied to 20 EMA",
                f"{pattern} — bearish rejection",
                "20 EMA < 50 EMA (macro bearish)"
            ]

            signals.append({
                "signal":        "SELL",
                "index":         i,
                "entry":         e1,
                "sl":            sl,
                "tp":            tp,
                "rr":            rr_ratio,
                "timeframe":     "Intraday",
                "session":       "Unknown",
                "quality_score": 7,
                "raw_score":     7,
                "confidence":    "High",
                "setup":         "20 EMA Pullback + Rejection",
                "confluences":   confluences,
                "strategy_tag":  "EMA20_Intraday",
                "entries": {
                    "e1": {"price": e1, "size_pct": 50, "label": "20 EMA touch"},
                    "e2": {"price": e2, "size_pct": 30, "label": "Deeper pullback"},
                    "e3": {"price": e3, "size_pct": 20, "label": "Best price"}
                },
                "trailing_sl": {
                    "enabled":     True,
                    "activate_at": round(close - risk, 4),
                    "step":        round(risk * 0.5, 4),
                    "current_sl":  sl,
                    "activated":   False
                }
            })

    return signals
