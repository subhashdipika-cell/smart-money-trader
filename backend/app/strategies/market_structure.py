import pandas as pd


def candle_body_size(open_price, close_price):
    return abs(close_price - open_price)


def upper_wick_size(high, open_price, close_price):
    return high - max(open_price, close_price)


def lower_wick_size(low, open_price, close_price):
    return min(open_price, close_price) - low


def detect_swings(df, lookback=3):
    swing_highs = []
    swing_lows  = []

    for i in range(lookback, len(df) - lookback):
        high = df["high"].iloc[i]
        low  = df["low"].iloc[i]

        is_swing_high = True
        is_swing_low  = True

        for j in range(1, lookback + 1):
            if high <= df["high"].iloc[i - j]: is_swing_high = False
            if high <= df["high"].iloc[i + j]: is_swing_high = False
            if low  >= df["low"].iloc[i - j]:  is_swing_low  = False
            if low  >= df["low"].iloc[i + j]:  is_swing_low  = False

        if is_swing_high: swing_highs.append(i)
        if is_swing_low:  swing_lows.append(i)

    return swing_highs, swing_lows


def detect_bos_choch(df, swing_highs, swing_lows):
    signals = []

    # Bullish BOS
    for i in range(1, len(swing_highs)):
        prev_high_idx    = swing_highs[i - 1]
        current_high_idx = swing_highs[i]

        prev_high    = df["high"].iloc[prev_high_idx]
        current_high = df["high"].iloc[current_high_idx]
        open_price   = df["open"].iloc[current_high_idx]
        close_price  = df["close"].iloc[current_high_idx]
        high         = df["high"].iloc[current_high_idx]
        low          = df["low"].iloc[current_high_idx]

        body_size  = candle_body_size(open_price, close_price)
        range_size = high - low

        if range_size == 0:
            continue

        body_ratio = body_size / range_size

        if (
            current_high > prev_high
            and close_price > open_price
            and body_ratio > 0.6
        ):
            signals.append({
                "type":  "bullish_bos",
                "index": current_high_idx,
                "price": current_high
            })

    # Bearish BOS
    for i in range(1, len(swing_lows)):
        prev_low_idx    = swing_lows[i - 1]
        current_low_idx = swing_lows[i]

        prev_low    = df["low"].iloc[prev_low_idx]
        current_low = df["low"].iloc[current_low_idx]
        open_price  = df["open"].iloc[current_low_idx]
        close_price = df["close"].iloc[current_low_idx]
        high        = df["high"].iloc[current_low_idx]
        low         = df["low"].iloc[current_low_idx]

        body_size  = candle_body_size(open_price, close_price)
        range_size = high - low

        if range_size == 0:
            continue

        body_ratio = body_size / range_size

        if (
            current_low < prev_low
            and close_price < open_price
            and body_ratio > 0.6
        ):
            signals.append({
                "type":  "bearish_bos",
                "index": current_low_idx,
                "price": current_low
            })

    return signals


def detect_fvg(df):
    fvgs = []

    for i in range(2, len(df)):
        candle1_high = df["high"].iloc[i - 2]
        candle1_low  = df["low"].iloc[i - 2]
        candle3_high = df["high"].iloc[i]
        candle3_low  = df["low"].iloc[i]

        if candle1_high < candle3_low:
            fvgs.append({
                "type":   "bullish_fvg",
                "index":  i,
                "top":    candle3_low,
                "bottom": candle1_high
            })
        elif candle1_low > candle3_high:
            fvgs.append({
                "type":   "bearish_fvg",
                "index":  i,
                "top":    candle1_low,
                "bottom": candle3_high
            })

    return fvgs


def detect_liquidity_sweeps(df, swing_highs, swing_lows):
    sweeps = []

    min_sweep_percent = 0.00010   # slightly more sensitive
    max_sweep_percent = 0.008     # allow wider sweeps (BTC/Gold need more room)

    # High sweeps
    for i in range(1, len(swing_highs)):
        prev_idx    = swing_highs[i - 1]
        current_idx = swing_highs[i]

        prev_high    = df["high"].iloc[prev_idx]
        current_high = df["high"].iloc[current_idx]
        open_price   = df["open"].iloc[current_idx]
        close_price  = df["close"].iloc[current_idx]
        low          = df["low"].iloc[current_idx]
        range_size   = current_high - low

        if range_size == 0:
            continue

        sweep_percent = (current_high - prev_high) / prev_high
        wick_ratio    = upper_wick_size(current_high, open_price, close_price) / range_size

        # Allow close up to 0.1% above prev_high (liquidity grab and return)
        close_ok = close_price < prev_high * 1.001
        if (
            current_high > prev_high
            and min_sweep_percent <= sweep_percent <= max_sweep_percent
            and close_ok
            and wick_ratio > 0.2
        ):
            sweeps.append({
                "type":  "bearish_liquidity_sweep",
                "index": current_idx,
                "price": current_high
            })

    # Low sweeps
    for i in range(1, len(swing_lows)):
        prev_idx    = swing_lows[i - 1]
        current_idx = swing_lows[i]

        prev_low    = df["low"].iloc[prev_idx]
        current_low = df["low"].iloc[current_idx]
        open_price  = df["open"].iloc[current_idx]
        close_price = df["close"].iloc[current_idx]
        high        = df["high"].iloc[current_idx]
        range_size  = high - current_low

        if range_size == 0:
            continue

        sweep_percent = (prev_low - current_low) / prev_low
        wick_ratio    = lower_wick_size(current_low, open_price, close_price) / range_size

        # Allow close up to 0.1% below prev_low (liquidity grab and return)
        close_ok = close_price > prev_low * 0.999
        if (
            current_low < prev_low
            and min_sweep_percent <= sweep_percent <= max_sweep_percent
            and close_ok
            and wick_ratio > 0.2
        ):
            sweeps.append({
                "type":  "bullish_liquidity_sweep",
                "index": current_idx,
                "price": current_low
            })

    return sweeps


def detect_order_blocks(df):
    order_blocks = []

    for i in range(1, len(df) - 1):
        current_open  = df["open"].iloc[i]
        current_close = df["close"].iloc[i]
        next_open     = df["open"].iloc[i + 1]
        next_close    = df["close"].iloc[i + 1]

        if current_close < current_open and next_close > next_open:
            order_blocks.append({
                "type":   "bullish_ob",
                "index":  i,
                "top":    df["high"].iloc[i],
                "bottom": df["low"].iloc[i]
            })
        elif current_close > current_open and next_close < next_open:
            order_blocks.append({
                "type":   "bearish_ob",
                "index":  i,
                "top":    df["high"].iloc[i],
                "bottom": df["low"].iloc[i]
            })

    return order_blocks


# ── HTF Support & Resistance ──────────────────────────────────────────────────

def detect_htf_support_resistance(df, lookback=5, cluster_pct=0.003, min_touches=2):
    """
    Detects high-timeframe support and resistance levels from a DataFrame.

    Algorithm:
      1. Find all swing highs and lows with a wider lookback (default 5)
      2. Cluster nearby levels within cluster_pct of each other
      3. Count how many times price returned to each cluster (touches)
      4. Only return levels touched min_touches or more times

    Returns a list of level dicts:
      {
        "type":     "resistance" | "support",
        "price":    float,          # average price of the cluster
        "touches":  int,            # number of times price visited
        "strength": "strong" | "moderate",
        "index":    int             # most recent candle index in cluster
      }
    """
    if df is None or len(df) < lookback * 2 + 1:
        return []

    swing_highs, swing_lows = detect_swings(df, lookback=lookback)

    # Collect all raw levels: (price, index, kind)
    raw = []
    for idx in swing_highs:
        raw.append((df["high"].iloc[idx], idx, "resistance"))
    for idx in swing_lows:
        raw.append((df["low"].iloc[idx],  idx, "support"))

    if not raw:
        return []

    # Sort by price so nearby levels are adjacent
    raw.sort(key=lambda x: x[0])

    # Cluster levels within cluster_pct of each other
    clusters = []
    used     = [False] * len(raw)

    for i, (price_i, idx_i, kind_i) in enumerate(raw):
        if used[i]:
            continue

        group_prices  = [price_i]
        group_indices = [idx_i]
        group_kinds   = [kind_i]
        used[i]       = True

        for j in range(i + 1, len(raw)):
            if used[j]:
                continue
            price_j = raw[j][0]
            # Within cluster_pct of the first price in this group
            if abs(price_j - price_i) / price_i <= cluster_pct:
                group_prices.append(price_j)
                group_indices.append(raw[j][1])
                group_kinds.append(raw[j][2])
                used[j] = True

        avg_price  = sum(group_prices) / len(group_prices)
        touches    = len(group_prices)
        # Determine type by majority vote
        resistance_votes = group_kinds.count("resistance")
        level_type = "resistance" if resistance_votes >= len(group_kinds) / 2 else "support"
        most_recent_idx  = max(group_indices)

        clusters.append({
            "type":     level_type,
            "price":    round(avg_price, 4),
            "touches":  touches,
            "strength": "strong" if touches >= 3 else "moderate",
            "index":    most_recent_idx
        })

    # Filter by minimum touches
    confirmed = [c for c in clusters if c["touches"] >= min_touches]

    # Sort by most recent first
    confirmed.sort(key=lambda c: c["index"], reverse=True)

    return confirmed


def find_nearest_htf_level(entry_price, htf_levels, signal_type, proximity_pct=0.005):
    """
    Checks if entry_price is within proximity_pct of any HTF level
    that ALIGNS with the signal direction:

      BUY  signal → look for nearby HTF SUPPORT  (buying at support)
      SELL signal → look for nearby HTF RESISTANCE (selling at resistance)

    Returns the nearest aligned level dict, or None.
    """
    aligned_type = "support" if signal_type == "BUY" else "resistance"

    best       = None
    best_dist  = float("inf")

    for level in htf_levels:
        if level["type"] != aligned_type:
            continue

        dist = abs(entry_price - level["price"]) / entry_price

        if dist <= proximity_pct and dist < best_dist:
            best      = level
            best_dist = dist

    return best


# ── Trendline Support & Resistance ────────────────────────────────────────────

def _fit_trendline(indices, prices):
    """
    Fit a straight line through a set of (index, price) points
    using least squares. Returns (slope, intercept).
    """
    n = len(indices)
    if n < 2:
        return None, None
    sum_x  = sum(indices)
    sum_y  = sum(prices)
    sum_xy = sum(x * y for x, y in zip(indices, prices))
    sum_xx = sum(x * x for x in indices)
    denom  = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return None, None
    slope     = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def _price_at(slope, intercept, index):
    return slope * index + intercept


def detect_trendlines(df, lookback=5, min_touches=2, tolerance_pct=0.002):
    """
    Detects ascending support trendlines (connecting swing lows)
    and descending resistance trendlines (connecting swing highs).

    Returns a list of trendline dicts:
    {
        "type":        "support_trendline" | "resistance_trendline",
        "slope":       float,      # positive = ascending, negative = descending
        "intercept":   float,
        "touches":     int,        # number of swing points on/near the line
        "strength":    "strong" | "moderate",
        "start_index": int,
        "end_index":   int,
        "start_price": float,      # price at start_index
        "end_price":   float,      # price at end_index (current value)
        "direction":   "ascending" | "descending" | "flat"
    }
    """
    if df is None or len(df) < lookback * 2 + 4:
        return []

    swing_highs, swing_lows = detect_swings(df, lookback=lookback)
    trendlines = []

    # ── Resistance trendlines from swing highs ────────────────────────────────
    if len(swing_highs) >= min_touches:
        high_prices = [df["high"].iloc[i] for i in swing_highs]

        # Try connecting each pair of swing highs and count how many others lie on/near the line
        for i in range(len(swing_highs) - 1):
            for j in range(i + 1, len(swing_highs)):
                idx_a, idx_b = swing_highs[i], swing_highs[j]
                p_a,   p_b   = df["high"].iloc[idx_a], df["high"].iloc[idx_b]

                slope, intercept = _fit_trendline([idx_a, idx_b], [p_a, p_b])
                if slope is None or slope > 0.05 * p_a:
                    continue   # skip lines with extreme slope

                # Count touches: swing highs within tolerance of the line
                touches     = 0
                touch_idxs  = []
                for k, sh_idx in enumerate(swing_highs):
                    expected = _price_at(slope, intercept, sh_idx)
                    actual   = df["high"].iloc[sh_idx]
                    if abs(actual - expected) / expected <= tolerance_pct:
                        touches += 1
                        touch_idxs.append(sh_idx)

                if touches >= min_touches:
                    end_idx   = swing_highs[-1]
                    end_price = _price_at(slope, intercept, end_idx)
                    direction = "descending" if slope < -0.001 else "ascending" if slope > 0.001 else "flat"
                    trendlines.append({
                        "type":        "resistance_trendline",
                        "slope":       round(slope, 6),
                        "intercept":   round(intercept, 4),
                        "touches":     touches,
                        "strength":    "strong" if touches >= 3 else "moderate",
                        "start_index": touch_idxs[0],
                        "end_index":   end_idx,
                        "start_price": round(_price_at(slope, intercept, touch_idxs[0]), 4),
                        "end_price":   round(end_price, 4),
                        "direction":   direction
                    })

    # ── Support trendlines from swing lows ────────────────────────────────────
    if len(swing_lows) >= min_touches:
        for i in range(len(swing_lows) - 1):
            for j in range(i + 1, len(swing_lows)):
                idx_a, idx_b = swing_lows[i], swing_lows[j]
                p_a,   p_b   = df["low"].iloc[idx_a], df["low"].iloc[idx_b]

                slope, intercept = _fit_trendline([idx_a, idx_b], [p_a, p_b])
                if slope is None or abs(slope) > 0.05 * p_a:
                    continue

                touches    = 0
                touch_idxs = []
                for sl_idx in swing_lows:
                    expected = _price_at(slope, intercept, sl_idx)
                    actual   = df["low"].iloc[sl_idx]
                    if abs(actual - expected) / expected <= tolerance_pct:
                        touches += 1
                        touch_idxs.append(sl_idx)

                if touches >= min_touches:
                    end_idx   = swing_lows[-1]
                    end_price = _price_at(slope, intercept, end_idx)
                    direction = "ascending" if slope > 0.001 else "descending" if slope < -0.001 else "flat"
                    trendlines.append({
                        "type":        "support_trendline",
                        "slope":       round(slope, 6),
                        "intercept":   round(intercept, 4),
                        "touches":     touches,
                        "strength":    "strong" if touches >= 3 else "moderate",
                        "start_index": touch_idxs[0],
                        "end_index":   end_idx,
                        "start_price": round(_price_at(slope, intercept, touch_idxs[0]), 4),
                        "end_price":   round(end_price, 4),
                        "direction":   direction
                    })

    # Deduplicate — keep strongest trendline per type/direction pair
    seen     = {}
    unique   = []
    for tl in sorted(trendlines, key=lambda t: -t["touches"]):
        key = (tl["type"], tl["direction"], round(tl["end_price"] / 10) * 10)
        if key not in seen:
            seen[key] = True
            unique.append(tl)

    return unique[:6]   # max 6 trendlines


def price_near_trendline(entry_price, trendlines, signal_type, current_index, proximity_pct=0.004):
    """
    Returns the nearest relevant trendline if entry is within proximity_pct.
    BUY  → looks for support trendline
    SELL → looks for resistance trendline
    """
    if not trendlines or not isinstance(trendlines, list):
        return None

    target_type = "support_trendline" if signal_type == "BUY" else "resistance_trendline"
    best        = None
    best_dist   = float("inf")

    for tl in trendlines:
        # Safety check — must be a dict with expected keys
        if not isinstance(tl, dict):
            continue
        if tl.get("type") != target_type:
            continue
        slope     = tl.get("slope")
        intercept = tl.get("intercept")
        if slope is None or intercept is None:
            continue
        try:
            tl_price = _price_at(slope, intercept, current_index)
            dist     = abs(entry_price - tl_price) / entry_price
            if dist <= proximity_pct and dist < best_dist:
                best      = {**tl, "trendline_price": round(tl_price, 4)}
                best_dist = dist
        except Exception:
            continue

    return best
