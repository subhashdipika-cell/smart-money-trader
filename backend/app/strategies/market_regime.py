"""
market_regime.py
----------------
Shared market regime detector used by ALL strategies as a pre-filter gate.

Returns one of three regimes:
  TRENDING  → Strong directional move. Strategies should fire normally.
  WEAK      → Moderate trend, low conviction. Strategies may fire with tighter filters.
  SIDEWAYS  → Range-bound / choppy. All strategies skip signal generation.

Detection uses three independent signals — all must agree for TRENDING:

  1. ADX (Average Directional Index) > 25
     - ADX measures trend STRENGTH regardless of direction.
     - Below 20 = sideways / choppy. Above 25 = trending. Above 40 = strong.

  2. EMA slope
     - 20 EMA slope over last 10 candles, normalised by price.
     - Below 0.02% per candle = flat / sideways.

  3. Bollinger Band width (volatility proxy)
     - Narrow bands = low volatility = accumulation / sideways.
     - Expanding bands = directional move underway.

Logic:
  - If ADX < 20                       → SIDEWAYS  (definitive flat market)
  - If EMA slope < threshold          → SIDEWAYS  (flat EMA = no trend)
  - If BB width < threshold           → SIDEWAYS  (no volatility = no trend)
  - If ADX 20–25 or slope weak        → WEAK
  - Otherwise                         → TRENDING
"""

from typing import Literal

MarketRegime = Literal["TRENDING", "WEAK", "SIDEWAYS"]


# ── Helper: pure-Python EMA (no pandas needed) ────────────────────────────────

def _ema(values: list, period: int) -> list:
    k, result = 2 / (period + 1), []
    for i, v in enumerate(values):
        result.append(float(v) if i == 0 else float(v) * k + result[-1] * (1 - k))
    return result


# ── ADX calculation ───────────────────────────────────────────────────────────

def _adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Return the most recent ADX value."""
    n = len(closes)
    if n < period * 2 + 1:
        return 0.0

    tr_list, plus_dm, minus_dm = [], [], []

    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        pdm  = up   if up > down and up > 0   else 0.0
        mdm  = down if down > up and down > 0 else 0.0
        tr_list.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    def _smooth(lst):
        s = sum(lst[:period])
        result = [s]
        for v in lst[period:]:
            s = s - s / period + v
            result.append(s)
        return result

    str_  = _smooth(tr_list)
    spdm  = _smooth(plus_dm)
    smdm  = _smooth(minus_dm)

    dx_list = []
    for atr, pdm, mdm in zip(str_, spdm, smdm):
        if atr == 0:
            dx_list.append(0.0)
            continue
        pdi = 100 * pdm / atr
        mdi = 100 * mdm / atr
        dx  = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) != 0 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return 0.0

    adx_val = sum(dx_list[-period:]) / period
    return round(adx_val, 2)


# ── Bollinger Band width ──────────────────────────────────────────────────────

def _bb_width(closes: list, period: int = 20) -> float:
    """Return BB width as fraction of mid: (upper - lower) / mid."""
    if len(closes) < period:
        return 0.0
    window = closes[-period:]
    mid    = sum(window) / period
    std    = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    return round((4 * std) / mid, 6) if mid else 0.0


# ── Main detector ─────────────────────────────────────────────────────────────

def detect_market_regime(
    highs:  list,
    lows:   list,
    closes: list,
    adx_period:      int   = 14,
    ema_period:      int   = 20,
    ema_lookback:    int   = 10,
    min_adx_trend:   float = 25.0,   # ADX above this → trending
    min_adx_weak:    float = 20.0,   # ADX above this → at least weak trend
    min_slope_pct:   float = 0.0002, # EMA must move 0.02% per candle
    min_bb_width:    float = 0.008,  # BB width must be > 0.8% of price
) -> dict:
    """
    Detect market regime from OHLC lists.

    Returns:
      {
        "regime":    "TRENDING" | "WEAK" | "SIDEWAYS",
        "adx":       float,
        "ema_slope": float,
        "bb_width":  float,
        "reason":    str,    # human-readable explanation
      }
    """
    n = len(closes)
    if n < max(adx_period * 2, ema_period, 30):
        return {"regime": "SIDEWAYS", "adx": 0, "ema_slope": 0,
                "bb_width": 0, "reason": "Not enough data"}

    # 1. ADX
    adx = _adx(highs, lows, closes, adx_period)

    # 2. EMA slope (normalised)
    ema20 = _ema(closes, ema_period)
    lb    = min(ema_lookback, len(ema20) - 1)
    slope = (ema20[-1] - ema20[-1 - lb]) / lb if lb > 0 else 0.0
    price = closes[-1] if closes[-1] else 1.0
    slope_pct = abs(slope) / price  # normalised slope per candle

    # 3. BB width
    bb_w = _bb_width(closes)

    # ── Decision logic ─────────────────────────────────────────────────────
    sideways_reasons = []

    if adx < min_adx_weak:
        sideways_reasons.append(f"ADX={adx:.1f} < {min_adx_weak} (flat)")
    if slope_pct < min_slope_pct:
        sideways_reasons.append(f"EMA slope={slope_pct*100:.3f}% < {min_slope_pct*100:.3f}% (flat)")
    if bb_w < min_bb_width:
        sideways_reasons.append(f"BB width={bb_w*100:.2f}% < {min_bb_width*100:.2f}% (tight)")

    # SIDEWAYS: any two signals agree it's flat
    if len(sideways_reasons) >= 2:
        return {
            "regime":    "SIDEWAYS",
            "adx":       adx,
            "ema_slope": round(slope_pct * 100, 4),
            "bb_width":  round(bb_w * 100, 3),
            "reason":    " | ".join(sideways_reasons),
        }

    # WEAK: ADX in the grey zone (20-25)
    if adx < min_adx_trend or slope_pct < min_slope_pct * 1.5:
        return {
            "regime":    "WEAK",
            "adx":       adx,
            "ema_slope": round(slope_pct * 100, 4),
            "bb_width":  round(bb_w * 100, 3),
            "reason":    f"ADX={adx:.1f}, slope={slope_pct*100:.3f}% — weak trend",
        }

    # TRENDING
    direction = "bullish" if ema20[-1] > ema20[-1 - lb] else "bearish"
    return {
        "regime":    "TRENDING",
        "adx":       adx,
        "ema_slope": round(slope_pct * 100, 4),
        "bb_width":  round(bb_w * 100, 3),
        "reason":    f"ADX={adx:.1f} | slope={slope_pct*100:.3f}% | BB={bb_w*100:.2f}% — {direction} trend",
    }


# ── Pandas-friendly wrapper ───────────────────────────────────────────────────

def detect_regime_from_df(df, **kwargs) -> dict:
    """
    Convenience wrapper that accepts a pandas DataFrame with columns:
    high, low, close  (or High, Low, Close).
    """
    try:
        h = [float(x) for x in df.get("high", df.get("High", [])).tolist()]
        l = [float(x) for x in df.get("low",  df.get("Low",  [])).tolist()]
        c = [float(x) for x in df.get("close",df.get("Close",[])).tolist()]
        return detect_market_regime(h, l, c, **kwargs)
    except Exception as e:
        return {"regime": "SIDEWAYS", "adx": 0, "ema_slope": 0,
                "bb_width": 0, "reason": f"Regime detection error: {e}"}
