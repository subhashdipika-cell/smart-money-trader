"""
chart_observer.py
-----------------
Analyses recent candles the way an experienced trader would.
Spots patterns, momentum shifts, key level tests, volume anomalies.
Returns a structured market observation dict.
"""

import pandas as pd
from collections import namedtuple

Observation = namedtuple("Observation", [
    "pattern", "strength", "direction", "description", "score"
])


# ── Candle pattern recognition ────────────────────────────────────────────────

def _body_ratio(o, h, l, c):
    rng = h - l
    return abs(c - o) / rng if rng > 0 else 0

def _upper_wick_ratio(o, h, l, c):
    rng = h - l
    return (h - max(o, c)) / rng if rng > 0 else 0

def _lower_wick_ratio(o, h, l, c):
    rng = h - l
    return (min(o, c) - l) / rng if rng > 0 else 0

def _is_bullish(o, c): return c > o
def _is_bearish(o, c): return c < o


def detect_candle_patterns(df, lookback=10):
    """
    Detect the most recent significant candle patterns.
    Returns list of Observation objects.
    """
    if df is None or not hasattr(df, '__len__') or len(df) < 5:
        return []

    observations = []
    try:
        recent = df.iloc[-lookback:].reset_index(drop=True)
    except Exception:
        return []
    n = len(recent)
    if n < 3:
        return []

    for i in range(2, n):
        o = float(recent["open"].iloc[i])
        h = float(recent["high"].iloc[i])
        l = float(recent["low"].iloc[i])
        c = float(recent["close"].iloc[i])

        o1 = float(recent["open"].iloc[i-1])
        c1 = float(recent["close"].iloc[i-1])
        h1 = float(recent["high"].iloc[i-1])
        l1 = float(recent["low"].iloc[i-1])

        o2 = float(recent["open"].iloc[i-2])
        c2 = float(recent["close"].iloc[i-2])

        br  = _body_ratio(o, h, l, c)
        uw  = _upper_wick_ratio(o, h, l, c)
        lw  = _lower_wick_ratio(o, h, l, c)

        # ── Bullish patterns ──────────────────────────────────────────────────

        # Hammer (bullish reversal)
        if lw >= 0.55 and br <= 0.35 and _is_bullish(o, c):
            observations.append(Observation(
                "Hammer", "Strong", "BUY",
                "Long lower wick — buyers absorbed selling pressure strongly",
                score=3
            ))

        # Bullish engulfing
        if (_is_bearish(o1, c1) and _is_bullish(o, c)
                and o <= c1 and c >= o1 and br >= 0.6):
            observations.append(Observation(
                "Bullish Engulfing", "Strong", "BUY",
                "Buyers completely engulfed previous bearish candle — momentum shift",
                score=3
            ))

        # Morning star
        if (_is_bearish(o2, c2) and abs(c1-o1)/(h1-l1+0.0001) < 0.3
                and _is_bullish(o, c) and c > (o2+c2)/2):
            observations.append(Observation(
                "Morning Star", "Moderate", "BUY",
                "Three-candle reversal — indecision followed by strong buyers",
                score=2
            ))

        # ── Bearish patterns ──────────────────────────────────────────────────

        # Shooting star (bearish reversal)
        if uw >= 0.55 and br <= 0.35 and _is_bearish(o, c):
            observations.append(Observation(
                "Shooting Star", "Strong", "SELL",
                "Long upper wick — sellers rejected the high aggressively",
                score=3
            ))

        # Bearish engulfing
        if (_is_bullish(o1, c1) and _is_bearish(o, c)
                and o >= c1 and c <= o1 and br >= 0.6):
            observations.append(Observation(
                "Bearish Engulfing", "Strong", "SELL",
                "Sellers completely engulfed previous bullish candle — momentum shift",
                score=3
            ))

        # Displacement candle (strong momentum)
        if br >= 0.75:
            direction = "BUY" if _is_bullish(o, c) else "SELL"
            observations.append(Observation(
                "Displacement Candle", "Strong", direction,
                f"{'Bullish' if direction=='BUY' else 'Bearish'} displacement — institutional order flow",
                score=2
            ))

    return observations[-5:]  # most recent 5 patterns


# ── Market structure observation ──────────────────────────────────────────────

def observe_momentum(df, period=14):
    """Measure recent momentum direction and strength."""
    if df is None or not hasattr(df, '__len__') or len(df) < period + 2:
        return {"direction": "Neutral", "strength": 0, "description": "Insufficient data"}

    recent = df.iloc[-period:]
    closes = recent["close"].astype(float)

    # Simple momentum: % change over period
    pct_change = (closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] * 100

    # Count bullish vs bearish candles
    opens  = recent["open"].astype(float)
    bull_n = sum(1 for o, c in zip(opens, closes) if c > o)
    bear_n = period - bull_n

    if pct_change > 0.5 and bull_n > bear_n:
        direction = "Bullish"
        strength  = min(10, int(abs(pct_change) * 3 + bull_n/period * 5))
    elif pct_change < -0.5 and bear_n > bull_n:
        direction = "Bearish"
        strength  = min(10, int(abs(pct_change) * 3 + bear_n/period * 5))
    else:
        direction = "Neutral"
        strength  = 2

    return {
        "direction":   direction,
        "strength":    strength,
        "pct_change":  round(pct_change, 3),
        "bull_candles": bull_n,
        "bear_candles": bear_n,
        "description": f"{direction} momentum ({pct_change:+.2f}% over {period} candles, {bull_n}/{period} bullish)"
    }


def observe_key_level_test(df, levels, proximity_pct=0.003):
    """Check if price is currently testing a key S&R level."""
    if df is None or not hasattr(df, 'empty') or df.empty or not levels:
        return []

    current_price = float(df["close"].iloc[-1])
    tests = []

    for level in levels:
        price = level.get("price", 0)
        if not price:
            continue
        dist = abs(current_price - price) / price
        if dist <= proximity_pct:
            level_type = level.get("type", "level")
            tests.append({
                "level":       price,
                "type":        level_type,
                "distance_pct": round(dist * 100, 3),
                "description": f"Price testing {level_type} @ {price:.2f} (within {dist*100:.2f}%)"
            })

    return tests


def observe_higher_timeframe_alignment(htf_df, ltf_df):
    """
    Check if 1H trend aligns with 1M entry direction.
    Returns alignment assessment.
    """
    if htf_df is None or ltf_df is None or not hasattr(htf_df,'empty') or htf_df.empty or not hasattr(ltf_df,'empty') or ltf_df.empty:
        return {"aligned": False, "description": "Insufficient data"}

    # 1H trend via last 20 candles
    htf_closes = htf_df["close"].astype(float).iloc[-20:]
    htf_trend  = "Bullish" if htf_closes.iloc[-1] > htf_closes.iloc[0] else "Bearish"

    # 1M recent momentum
    ltf_closes = ltf_df["close"].astype(float).iloc[-10:]
    ltf_trend  = "Bullish" if ltf_closes.iloc[-1] > ltf_closes.iloc[0] else "Bearish"

    aligned = htf_trend == ltf_trend
    return {
        "aligned":     aligned,
        "htf_trend":   htf_trend,
        "ltf_trend":   ltf_trend,
        "description": f"1H is {htf_trend}, 1M is {ltf_trend} — {'✅ Aligned' if aligned else '⚠️ Diverging'}"
    }


# ── Full chart observation ────────────────────────────────────────────────────

def observe_chart(data, htf_sr_levels=None):
    """
    Full chart observation across all timeframes.
    Returns structured observation dict that trader_brain will use.
    """
    ltf_df = data.get("1m") if data else None
    ftf_df = data.get("5m") if data else None
    mid_df = data.get("15m") if data else None
    htf_df = data.get("1h") if data else None

    # Ensure all are valid DataFrames
    import pandas as pd
    ltf_df = ltf_df if ltf_df is not None and not ltf_df.empty else None
    ftf_df = ftf_df if ftf_df is not None and not ftf_df.empty else None
    mid_df = mid_df if mid_df is not None and not mid_df.empty else None
    htf_df = htf_df if htf_df is not None and not htf_df.empty else None

    use_df      = ftf_df if ftf_df is not None else ltf_df
    patterns    = detect_candle_patterns(use_df, lookback=15) if use_df is not None else []
    momentum_1m = observe_momentum(ltf_df, period=14)
    momentum_5m = observe_momentum(ftf_df, period=14)
    momentum_1h = observe_momentum(htf_df, period=20)
    tf_alignment = observe_higher_timeframe_alignment(htf_df, ltf_df)
    level_tests  = observe_key_level_test(ltf_df, htf_sr_levels or [], proximity_pct=0.004)

    # Overall bias score: +ve = bullish, -ve = bearish
    bias_score = 0
    if momentum_1h["direction"] == "Bullish": bias_score += 2
    if momentum_1h["direction"] == "Bearish": bias_score -= 2
    if momentum_5m["direction"] == "Bullish": bias_score += 1
    if momentum_5m["direction"] == "Bearish": bias_score -= 1
    if tf_alignment["aligned"]:
        bias_score += 1 if momentum_1h["direction"] == "Bullish" else -1

    for p in patterns:
        if p.direction == "BUY":  bias_score += p.score * 0.5
        if p.direction == "SELL": bias_score -= p.score * 0.5

    if bias_score >= 2:   overall = "Bullish"
    elif bias_score >= 1: overall = "Mildly Bullish"
    elif bias_score <= -2: overall = "Bearish"
    elif bias_score <= -1: overall = "Mildly Bearish"
    else:                  overall = "Neutral"

    return {
        "overall_bias":   overall,
        "bias_score":     round(bias_score, 2),
        "patterns":       [p._asdict() for p in patterns],
        "momentum_1m":    momentum_1m,
        "momentum_5m":    momentum_5m,
        "momentum_1h":    momentum_1h,
        "tf_alignment":   tf_alignment,
        "level_tests":    level_tests,
        "current_price":  float(ltf_df["close"].iloc[-1]) if ltf_df is not None and not ltf_df.empty else None
    }
