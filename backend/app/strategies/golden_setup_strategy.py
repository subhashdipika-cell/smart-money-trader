"""
golden_setup_strategy.py
------------------------
Golden Setup — Power of Stocks (Subasish)

Detection rules (from wiki/strategies/golden-setup.md):
  1. Structural direction: HH/HL → bullish; LH/LL → bearish (rolling window)
  2. Round number trigger:
       - Gold/Forex (price < 10k): nearest 50-point multiple
       - Crypto BTC (price ≥ 10k): nearest 1000-point multiple
  3. Entry when price CLOSES ABOVE a round level (long) or BELOW (short)
     aligned with structural direction
  4. Stop: placed `stop_dist` points BELOW round level (long) or ABOVE (short)
     — not below entry; the round level IS the structural support/resistance
  5. Target: minimum 1:5 R:R; aim for 1:10–1:20
  6. Partial exit: 70% at 1:3 R; hold 30% for the runner (modelled in signal)
  7. Time windows (optional filter):
       - US Market Open: 13:00–15:00 UTC  (6:30–7:30 PM IST)
       - Morning volatility: 02:00–04:00 UTC  (7:30–8:30 AM IST)
  8. Max 4-5 iterations per zone (blocked after 4 consecutive losses on same level)
"""

import pandas as pd
import numpy as np
from datetime import timezone, datetime


# ── Config defaults by instrument ──────────────────────────────────────────────

_INSTRUMENT_PROFILE = {
    # symbol_substring → (round_step, stop_dist_pts)
    "XAU":  (50,   5.0),    # Gold: 50-pt levels, 5-pt stop below level
    "BTC":  (1000, 300.0),  # BTC: 1000-pt levels, 300-pt stop below level
    "ETH":  (100,  20.0),   # ETH: 100-pt levels, 20-pt stop below level
    "NAS":  (100,  10.0),   # Nasdaq
    "SPX":  (50,   5.0),    # S&P 500
}
_DEFAULT_PROFILE = (100, 10.0)


def _get_profile(symbol: str):
    sym = (symbol or "").upper()
    for key, profile in _INSTRUMENT_PROFILE.items():
        if key in sym:
            return profile
    return _DEFAULT_PROFILE


def _nearest_round(price: float, step: float) -> float:
    return round(price / step) * step


def _is_time_window(ts_ms: int, apply_filter: bool) -> bool:
    """Return True if the bar is inside the US-open or morning volatility window (UTC)."""
    if not apply_filter:
        return True
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    h = dt.hour
    # US Market Open: 13:00–15:00 UTC | Morning volatility: 02:00–04:00 UTC
    return (13 <= h < 15) or (2 <= h < 4)


def _structural_direction(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    """
    +1 = HH + HL (uptrend)
    -1 = LH + LL (downtrend)
     0 = ambiguous
    """
    roll_high = high.rolling(window).max()
    roll_low  = low.rolling(window).min()
    prev_high = high.shift(window).rolling(window).max()
    prev_low  = low.shift(window).rolling(window).min()

    up   = (roll_high > prev_high) & (roll_low > prev_low)
    down = (roll_high < prev_high) & (roll_low < prev_low)

    result = pd.Series(0, index=high.index)
    result[up]   =  1
    result[down] = -1
    return result


def generate_golden_setup_signals(
    df:           pd.DataFrame,
    symbol:       str   = "XAUUSD",
    rr_ratio:     float = 5.0,
    lookback:     int   = 300,
    swing_window: int   = 20,
    apply_time_filter: bool = True,
) -> list[dict]:
    """
    Generate Golden Setup signals on 1H data.
    Returns signal dicts compatible with simple_backtest.run_backtest().
    """
    if df is None or len(df) < max(60, swing_window * 2 + 5):
        return []

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    round_step, stop_dist = _get_profile(symbol)

    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)
    closes = df["close"].astype(float)
    struct = _structural_direction(highs, lows, swing_window)

    has_ts = "timestamp" in df.columns

    signals  = []
    zone_stops: dict[float, int] = {}   # round_level → consecutive stop count
    start    = max(swing_window * 2 + 1, len(df) - lookback)

    for i in range(start, len(df) - 1):
        close     = float(closes.iloc[i])
        prev_close = float(closes.iloc[i - 1])
        sd        = int(struct.iloc[i])

        # ── Time window gate ──────────────────────────────────────────────────
        if apply_time_filter:
            if not has_ts:
                # Cannot enforce time window without timestamps — skip bar rather than silently open all hours
                continue
            ts_ms = int(df["timestamp"].iloc[i])
            if not _is_time_window(ts_ms, True):
                continue

        # ── Find nearest round level ──────────────────────────────────────────
        round_level = _nearest_round(close, round_step)

        # ── Zone iteration gate (max 4 consecutive stops per level) ──────────
        if zone_stops.get(round_level, 0) >= 4:
            continue

        # ── LONG: close crosses ABOVE round level in uptrend ─────────────────
        if sd == 1 and prev_close < round_level <= close:
            sl        = round(round_level - stop_dist, 4)
            risk      = round(close - sl, 4)
            if risk <= 0:
                continue
            tp        = round(close + risk * rr_ratio, 4)
            # 70/30 partial: record both targets in confluences
            tp_partial = round(close + risk * 3, 4)

            confluences = [
                f"Structural uptrend (HH/HL, window={swing_window})",
                f"Round level crossed: {round_level} (step {round_step})",
                f"Close {close:.2f} above round level {round_level}",
                f"Stop {stop_dist} pts below round level → SL {sl:.2f}",
                f"Partial exit (70%) at 1:3 = {tp_partial:.2f}; runner targets {tp:.2f}",
            ]

            signals.append({
                "signal":        "BUY",
                "index":         i,
                "entry":         round(close * 1.0002, 4),  # limit fill 2bps above close
                "sl":            sl,
                "tp":            tp,
                "tp_partial":    tp_partial,  # 70% exit at 1:3 R (wiki Phase 4)
                "partial_pct":   0.70,
                "rr":            rr_ratio,
                "timeframe":     "1H",
                "quality_score": 8,
                "raw_score":     8,
                "confidence":    "High",
                "setup":         f"Golden Setup LONG @ {round_level}",
                "confluences":   confluences,
                "strategy_tag":  "Golden_Setup",
                "round_level":   round_level,
            })
            zone_stops[round_level] = zone_stops.get(round_level, 0) + 1

        # ── SHORT: close crosses BELOW round level in downtrend ───────────────
        elif sd == -1 and prev_close > round_level >= close:
            sl        = round(round_level + stop_dist, 4)
            risk      = round(sl - close, 4)
            if risk <= 0:
                continue
            tp        = round(close - risk * rr_ratio, 4)
            tp_partial = round(close - risk * 3, 4)

            confluences = [
                f"Structural downtrend (LH/LL, window={swing_window})",
                f"Round level breached: {round_level} (step {round_step})",
                f"Close {close:.2f} below round level {round_level}",
                f"Stop {stop_dist} pts above round level → SL {sl:.2f}",
                f"Partial exit (70%) at 1:3 = {tp_partial:.2f}; runner targets {tp:.2f}",
            ]

            signals.append({
                "signal":        "SELL",
                "index":         i,
                "entry":         round(close * 0.9998, 4),  # limit fill 2bps below close
                "sl":            sl,
                "tp":            tp,
                "tp_partial":    tp_partial,  # 70% exit at 1:3 R (wiki Phase 4)
                "partial_pct":   0.70,
                "rr":            rr_ratio,
                "timeframe":     "1H",
                "quality_score": 8,
                "raw_score":     8,
                "confidence":    "High",
                "setup":         f"Golden Setup SHORT @ {round_level}",
                "confluences":   confluences,
                "strategy_tag":  "Golden_Setup",
                "round_level":   round_level,
            })
            zone_stops[round_level] = zone_stops.get(round_level, 0) + 1

    return signals
