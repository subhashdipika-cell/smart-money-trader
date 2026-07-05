"""
ema_9_20_strategy.py
--------------------
9 & 20 EMA Pullback Strategy — Stock Burner

Rules (from wiki/strategies/9-20-ema-pullback.md):
  LONG  when:
    - 9 EMA fully above 20 EMA
    - Structural uptrend: rolling high > prior high AND rolling low > prior low
    - Price pulls back: candle low touches or pierces 9 or 20 EMA
    - Entry candle: green (close > open) AND closes above previous close
    - Stop: lowest of last 3 candles (with small buffer)
    - Target: minimum 1:3 R:R

  SHORT when:
    - 9 EMA fully below 20 EMA
    - Structural downtrend
    - Price bounces: candle high touches or pierces 9 or 20 EMA
    - Entry candle: red (close < open) AND close within bottom 15% of candle range
    - Stop: highest of last 3 candles (with small buffer)
    - Target: minimum 1:3 R:R

  Two-Strike Rule tracked externally (via TwoStrikeTracker in detector script).
  Ranging market (EMA spread < 0.10%): skip.
"""

import pandas as pd


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _ema_spread_pct(ema9: pd.Series, ema20: pd.Series) -> pd.Series:
    return (ema9 - ema20).abs() / ema20 * 100


def _structural_direction(high: pd.Series, low: pd.Series, window: int = 10) -> pd.Series:
    """
    +1 = higher highs + higher lows (uptrend)
    -1 = lower highs + lower lows (downtrend)
     0 = ambiguous
    """
    roll_high  = high.rolling(window).max()
    roll_low   = low.rolling(window).min()
    prev_high  = high.shift(window).rolling(window).max()
    prev_low   = low.shift(window).rolling(window).min()

    up   = (roll_high > prev_high) & (roll_low > prev_low)
    down = (roll_high < prev_high) & (roll_low < prev_low)

    result = pd.Series(0, index=high.index)
    result[up]   =  1
    result[down] = -1
    return result


def generate_ema_9_20_signals(
    df:          pd.DataFrame,
    rr_ratio:    float = 3.0,
    lookback:    int   = 200,
    symbol:      str   = "",
    stop_bars:   int   = 3,
    swing_window:int   = 10,
    flat_thresh: float = 0.10,   # % spread below which EMAs are "flat" (ranging market)
    near_low_pct:float = 0.15,   # short entry candle: close within bottom X% of range
) -> list[dict]:
    """
    Generate 9/20 EMA pullback signals.
    Returns a list of signal dicts compatible with simple_backtest.run_backtest().
    """
    if df is None or len(df) < max(60, lookback // 2):
        return []

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    opens  = df["open"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)
    closes = df["close"].astype(float)

    ema9  = _ema(closes, 9)
    ema20 = _ema(closes, 20)
    spread = _ema_spread_pct(ema9, ema20)
    struct = _structural_direction(highs, lows, swing_window)

    signals = []
    start   = max(stop_bars + swing_window + 1, len(df) - lookback)

    for i in range(start, len(df)):
        e9    = float(ema9.iloc[i])
        e20   = float(ema20.iloc[i])
        close = float(closes.iloc[i])
        open_ = float(opens.iloc[i])
        high  = float(highs.iloc[i])
        low   = float(lows.iloc[i])
        prev_close = float(closes.iloc[i - 1])
        sp    = float(spread.iloc[i])
        sd    = int(struct.iloc[i])

        # ── Ranging market gate ───────────────────────────────────────────────
        if sp < flat_thresh:
            continue

        # ── LONG setup ────────────────────────────────────────────────────────
        if e9 > e20 and sd == 1:
            # Pullback: candle low touched or pierced either EMA
            touched = low <= e9 or low <= e20
            if not touched:
                continue

            # Entry candle: green AND closes above previous close
            green      = close > open_
            above_prev = close > prev_close
            if not (green and above_prev):
                continue

            # Must close above both EMAs (pullback did not reverse the trend)
            if close <= e9 or close <= e20:
                continue

            # Stop: lowest low of the `stop_bars` candles BEFORE the entry candle
            # Exclude bar i itself — we want the swing low of the pullback, not the entry bar
            sl_window = lows.iloc[max(0, i - stop_bars) : i]
            sl_raw    = float(sl_window.min()) * 0.9995  # 0.05% buffer below wick
            risk      = close - sl_raw
            if risk <= 0:
                continue

            # Entry 5 bps below close so simple_backtest limit-fill logic triggers
            # on the next bar (which virtually always dips to at least the prior close)
            entry_price = round(close * 0.9995, 4)
            tp = round(entry_price + risk * rr_ratio, 4)
            sl = round(sl_raw, 4)

            confluences = [
                "9 EMA above 20 EMA",
                f"Structural uptrend (rolling HH/HL, window={swing_window})",
                "Pullback: low touched 9 or 20 EMA",
                "Entry candle: green + closes above prior close",
                f"Stop below 3-bar swing low ({sl})",
            ]

            signals.append({
                "signal":        "BUY",
                "index":         i,
                "entry":         entry_price,
                "sl":            sl,
                "tp":            tp,
                "rr":            rr_ratio,
                "timeframe":     "1H",
                "session":       "Unknown",
                "quality_score": 7,
                "raw_score":     7,
                "confidence":    "High",
                "setup":         "9/20 EMA Pullback (Stock Burner)",
                "confluences":   confluences,
                "strategy_tag":  "EMA_9_20_Pullback",
            })

        # ── SHORT setup ───────────────────────────────────────────────────────
        elif e9 < e20 and sd == -1:
            # Pullback: candle high touched or pierced either EMA
            touched = high >= e9 or high >= e20
            if not touched:
                continue

            # Entry candle: red AND close near absolute low (bottom near_low_pct of range)
            red = close < open_
            candle_range = high - low
            near_low = candle_range > 0 and (close - low) / candle_range <= near_low_pct
            if not (red and near_low):
                continue

            # Must close below both EMAs
            if close >= e9 or close >= e20:
                continue

            # Stop: highest high of the `stop_bars` candles BEFORE the entry candle
            # Exclude bar i itself — we want the swing high of the bounce, not the entry bar
            sl_window = highs.iloc[max(0, i - stop_bars) : i]
            sl_raw    = float(sl_window.max()) * 1.0005  # 0.05% buffer above wick
            risk      = sl_raw - close
            if risk <= 0:
                continue

            entry_price = round(close * 1.0005, 4)
            tp = round(entry_price - risk * rr_ratio, 4)
            sl = round(sl_raw, 4)

            confluences = [
                "9 EMA below 20 EMA",
                f"Structural downtrend (rolling LH/LL, window={swing_window})",
                "Rally: high touched 9 or 20 EMA",
                "Entry candle: red + closes near absolute low",
                f"Stop above 3-bar swing high ({sl})",
            ]

            signals.append({
                "signal":        "SELL",
                "index":         i,
                "entry":         entry_price,
                "sl":            sl,
                "tp":            tp,
                "rr":            rr_ratio,
                "timeframe":     "1H",
                "session":       "Unknown",
                "quality_score": 7,
                "raw_score":     7,
                "confidence":    "High",
                "setup":         "9/20 EMA Pullback (Stock Burner)",
                "confluences":   confluences,
                "strategy_tag":  "EMA_9_20_Pullback",
            })

    return signals
