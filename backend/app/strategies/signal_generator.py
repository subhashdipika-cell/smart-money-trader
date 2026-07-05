"""
signal_generator.py
-------------------
Generates ICT trade signals. Now integrates:
  - Learned confluence weights (from learning_engine)
  - HTF Support & Resistance proximity filter (from market_structure)
"""

import json
import os

_BASE                = os.path.join(os.path.dirname(__file__), "..", "..")

# Import trendline helper from market_structure
try:
    from app.strategies.market_structure import price_near_trendline as _price_near_trendline
except ImportError:
    _price_near_trendline = None
LEARNED_WEIGHTS_FILE = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))


def _load_weights():
    try:
        with open(LEARNED_WEIGHTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"confluence_bonuses": {}, "min_quality_score": 6}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _direction_config(signal_type):
    if signal_type == "BUY":
        return {
            "bos":         "bullish_bos",
            "fvg":         "bullish_fvg",
            "order_block": "bullish_ob",
            "sweep":       "bullish_liquidity_sweep"
        }
    return {
        "bos":         "bearish_bos",
        "fvg":         "bearish_fvg",
        "order_block": "bearish_ob",
        "sweep":       "bearish_liquidity_sweep"
    }


def _body_ratio(df, index):
    high         = df["high"].iloc[index]
    low          = df["low"].iloc[index]
    candle_range = high - low
    if candle_range == 0:
        return 0
    return abs(df["close"].iloc[index] - df["open"].iloc[index]) / candle_range


def _timestamp_session(df, index):
    if "timestamp" not in df.columns:
        return "Unmarked"
    timestamp = df["timestamp"].iloc[index]
    try:
        hour = __import__("datetime").datetime.utcfromtimestamp(
            int(timestamp) / 1000
        ).hour
    except (TypeError, ValueError, OverflowError):
        return "Unmarked"
    if 0  <= hour < 6:  return "Asia"
    if 7  <= hour < 11: return "London"
    if 12 <= hour < 17: return "New York"
    return "Off session"


def _find_fvg_after_sweep(fvgs, fvg_type, sweep_index, max_wait=12):
    candidates = [
        fvg for fvg in fvgs
        if fvg["type"] == fvg_type
        and sweep_index <= fvg["index"] <= sweep_index + max_wait
    ]
    return candidates[0] if candidates else None


def _has_recent_bos(bos_signals, bos_type, max_items=6):
    return any(bos["type"] == bos_type for bos in bos_signals[-max_items:])


def _nearest_order_block(order_blocks, order_block_type, signal_index, max_age=40):
    candidates = [
        ob for ob in order_blocks
        if ob["type"] == order_block_type
        and 0 <= signal_index - ob["index"] <= max_age
    ]
    return candidates[-1] if candidates else None


def _premium_discount_confluence(df, index, entry, signal_type, lookback=80):
    start  = max(0, index - lookback)
    window = df.iloc[start:index + 1]
    if window.empty:
        return False
    high        = window["high"].max()
    low         = window["low"].min()
    equilibrium = low + ((high - low) / 2)
    if signal_type == "BUY":
        return entry <= equilibrium
    return entry >= equilibrium


def _calculate_atr(df, period=14):
    """
    Calculate Average True Range for dynamic SL/TP sizing.
    ATR = average of True Range over `period` candles.
    True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
    """
    if df is None or len(df) < period + 1:
        return None
    try:
        highs  = df["high"].astype(float)
        lows   = df["low"].astype(float)
        closes = df["close"].astype(float)
        tr_list = []
        for i in range(1, len(df)):
            tr = max(
                highs.iloc[i]  - lows.iloc[i],
                abs(highs.iloc[i]  - closes.iloc[i-1]),
                abs(lows.iloc[i]   - closes.iloc[i-1])
            )
            tr_list.append(tr)
        atr = sum(tr_list[-period:]) / period
        return round(atr, 4)
    except Exception:
        return None


def _classify_timeframe(entry, stop):
    """
    Classify trade style based on actual SL distance in points.
    Thresholds are calibrated per asset price range:

    Gold  (XAU ~$4500):  Scalp < 8pts  | Intraday < 25pts  | Swing >= 25pts
    BTC   (~$75,000):    Scalp < 200pts | Intraday < 800pts | Swing >= 800pts
    ETH   (~$2,000):     Scalp < 8pts   | Intraday < 30pts  | Swing >= 30pts
    Default (% based):   Scalp < 0.3%  | Intraday < 1.0%   | Swing >= 1.0%
    """
    sl_pts = abs(entry - stop)
    if sl_pts == 0 or not entry:
        return "Scalping"

    # Detect asset by price range
    if entry > 30000:
        # BTC range — per professional trading guide:
        # Scalping: 150-300pts SL | Day: 500-1200pts | Swing: 2500+pts
        if sl_pts < 300:   return "Scalping"
        if sl_pts < 2500:  return "Intraday"
        return "Swing"
    elif entry > 3000:
        # Gold range (XAU/USD ~$4,500)
        # Fixed: 5pt SL = Scalping, 6-20pt = Intraday, 20pt+ = Swing
        if sl_pts <= 6:  return "Scalping"
        if sl_pts < 20:  return "Intraday"
        return "Swing"
    else:
        # ETH range — per professional trading guide:
        # Scalping: <15pt SL | Intraday: 15-89pt SL | Swing: 90pt+ SL
        if sl_pts < 15:  return "Scalping"
        if sl_pts < 90:  return "Intraday"
        return "Swing"


def _confidence_label(score, min_quality_score):
    if score >= min_quality_score:      return "High"
    if score >= min_quality_score - 2:  return "Medium"
    return "Low"


def _apply_learned_bonuses(confluences, score, bonuses):
    adjustment = sum(bonuses.get(tag, 0) for tag in confluences)
    adjustment = max(-2, min(2, adjustment))
    return score + adjustment


# ── HTF S&R proximity check ───────────────────────────────────────────────────

def _find_htf_level(entry_price, signal_type, htf_sr_levels, proximity_pct=0.005):
    """
    Returns the nearest aligned HTF level within proximity_pct, or None.

    BUY  → looks for HTF support  (entry near support = buy the dip)
    SELL → looks for HTF resistance (entry near resistance = sell the top)

    Bonus: if a COUNTER level is very close (BUY near resistance, SELL near
    support), we return it as a WARNING so the caller can reduce the score.
    """
    aligned_type  = "support"    if signal_type == "BUY" else "resistance"
    opposing_type = "resistance" if signal_type == "BUY" else "support"

    best_aligned  = None
    best_opposing = None
    best_a_dist   = float("inf")
    best_o_dist   = float("inf")

    for level in htf_sr_levels:
        dist = abs(entry_price - level["price"]) / entry_price

        if level["type"] == aligned_type and dist <= proximity_pct:
            if dist < best_a_dist:
                best_aligned = level
                best_a_dist  = dist

        if level["type"] == opposing_type and dist <= proximity_pct * 0.6:
            # very close opposing level is a strong warning
            if dist < best_o_dist:
                best_opposing = level
                best_o_dist   = dist

    return best_aligned, best_opposing


# ── Signal builder ────────────────────────────────────────────────────────────

def _build_signal(df, sweep, fvg, order_block, signal_type, weights, htf_sr_levels, htf_trendlines=None):
    signal_index = fvg["index"]
    fvg_mid      = (fvg["top"] + fvg["bottom"]) / 2
    buffer       = fvg_mid * 0.001
    confluences  = [
        "Liquidity sweep",
        "Fair value gap displacement",
        "15m structure break"
    ]
    score = 3

    # ── Asset-specific entry buffer ───────────────────────────────────────────
    # Adds a small buffer to the FVG midpoint entry so limit orders
    # are more likely to fill without chasing price.
    #
    # Gold  (~$4500): 2.0 pt buffer  (0.044% of price)
    # BTC   (~$75k):  50 pt buffer   (0.067% of price)
    # ETH   (~$2000): 2.0 pt buffer  (0.1%   of price)

    is_btc  = fvg_mid > 30000          # BTC ~$73,000
    is_gold = fvg_mid > 3000 and not is_btc  # Gold ~$4,500 but NOT BTC

    if is_gold:
        entry_buffer = 2.0
    elif is_btc:
        entry_buffer = 50.0
    else:
        entry_buffer = 2.0   # ETH and others

    if signal_type == "BUY":
        # Pull entry slightly LOWER — easier for price to reach on pullback
        entry = fvg_mid - entry_buffer
    else:
        # Push entry slightly HIGHER — easier for price to reach on rally
        entry = fvg_mid + entry_buffer

    # ── ATR-based dynamic SL/TP for BTC ─────────────────────────────────────
    # Per the professional trading guide:
    #   SL  = 1.5 × ATR (breathing room for BTC volatility)
    #   TP  = 3.0 × ATR (minimum 2:1 RR)
    # Falls back to structural SL if ATR unavailable

    # Calculate ATR from 1m df — apply strict minimums per professional guide
    atr = _calculate_atr(df, period=14) if not is_gold else None

    # Hard minimum SL distances (per trading guide):
    # BTC:  scalping 150-300pts | intraday 500-1200pts
    # ETH:  intraday 15-35pts   | scalping 15pts minimum
    if is_btc:
        # 1m ATR for BTC — scale ×50 to get 15m equivalent
        # Null and void check happens AFTER sl_distance = 1.5 × atr
        atr = max((atr or 0) * 50, 100.0)   # minimum 100pts
    elif not is_gold:
        # 1m ATR for ETH ~1-2pts → scale ×10 to get 10m equivalent
        atr = max((atr or 0) * 10, 15.0)    # minimum 15pts SL for ETH

    if signal_type == "BUY":
        if is_gold:
            # Gold: fixed 5pt SL, 12pt TP
            stop         = entry - 5.0
            target       = entry + 12.0
            reward_ratio = round(12.0 / 5.0, 2)
        elif is_btc:
            # BTC: ATR-based SL/TP — use 15m ATR with 150pt minimum
            sl_distance  = round(1.5 * atr, 2) if atr else round(entry * 0.002, 2)  # 0.2% fallback
            sl_distance  = max(sl_distance, 150.0)  # never less than 150pts
            tp_distance  = round(sl_distance * 3.0, 2)  # 3.0 RR — Rule 3: let winners run
            stop         = entry - sl_distance
            target       = entry + tp_distance
            reward_ratio = round(tp_distance / sl_distance, 2)
            confluences.append(f"ATR-based SL ({sl_distance:.0f} pts)")
        else:
            # ETH: ATR-based SL/TP
            # Guide: intraday SL 15-35pts, TP 40-80pts
            # Use 1.5×ATR for SL, 3×ATR for TP (minimum 2:1 RR)
            # Structural OB still used for score boost
            if order_block:
                confluences.append("Bullish order block")
                score += 1
            if atr and atr > 0:
                sl_distance  = max(round(1.5 * atr, 2), 15.0)  # min 15pt SL
                sl_distance  = min(sl_distance, 35.0)            # max 35pt SL
                tp_distance  = round(sl_distance * 3.0, 2)       # always 3:1 RR
                stop         = entry - sl_distance
                target       = entry + tp_distance
                reward_ratio = 3.0
                confluences.append(f"ATR-based SL ({sl_distance:.1f} pts)")
            else:
                # Fallback to structural
                stop_anchor = sweep["price"]
                stop = stop_anchor - buffer
                risk = entry - stop
                if risk <= 0:
                    return None
                reward_ratio = 2.5
                target = entry + (risk * reward_ratio)

    else:
        if is_gold:
            stop         = entry + 5.0
            target       = entry - 12.0
            reward_ratio = round(12.0 / 5.0, 2)
        elif is_btc:
            sl_distance = round(1.5 * atr, 2) if atr else round(entry * 0.002, 2)
            sl_distance = max(sl_distance, 100.0)
            if sl_distance > 600.0:
                return None   # null and void
            tp_distance  = round(sl_distance * 3.0, 2)
            stop         = entry + sl_distance
            target       = entry - tp_distance
            reward_ratio = 3.0
            confluences.append(f"ATR-based SL ({sl_distance:.0f} pts)")
        else:
            # ETH SELL: ATR-based
            if order_block:
                confluences.append("Bearish order block")
                score += 1
            if atr and atr > 0:
                sl_distance  = max(round(1.5 * atr, 2), 15.0)
                sl_distance  = min(sl_distance, 35.0)
                tp_distance  = round(sl_distance * 3.0, 2)       # always 3:1 RR
                stop         = entry + sl_distance
                target       = entry - tp_distance
                reward_ratio = 3.0
                confluences.append(f"ATR-based SL ({sl_distance:.1f} pts)")
            else:
                stop_anchor = sweep["price"]
                stop = stop_anchor + buffer
                risk = stop - entry
                if risk <= 0:
                    return None
                reward_ratio = 2.5
                target = entry - (risk * reward_ratio)

    session = _timestamp_session(df, signal_index)
    if session in ["London", "New York"]:
        confluences.append(f"{session} session")
        score += 1

    if _premium_discount_confluence(df, signal_index, entry, signal_type):
        confluences.append("Discount entry" if signal_type == "BUY" else "Premium entry")
        score += 1

    if _body_ratio(df, signal_index) >= 0.55:
        confluences.append("Displacement candle")
        score += 1

    # ── HTF S&R confluence ────────────────────────────────────────────────────
    htf_aligned  = None
    htf_opposing = None
    htf_note     = None

    if htf_sr_levels:
        htf_aligned, htf_opposing = _find_htf_level(entry, signal_type, htf_sr_levels)

        if htf_aligned:
            strength = htf_aligned["strength"]
            touches  = htf_aligned["touches"]
            level_label = (
                f"HTF support @ {htf_aligned['price']:,.2f} ({touches} touches)"
                if signal_type == "BUY"
                else f"HTF resistance @ {htf_aligned['price']:,.2f} ({touches} touches)"
            )
            confluences.append(level_label)
            # Strong level (3+ touches) = +2, moderate (2 touches) = +1
            score += 2 if strength == "strong" else 1
            htf_note = level_label

        if htf_opposing:
            # Entry is too close to a counter HTF level — reduce score
            score     -= 1
            warn_label = (
                f"Near HTF resistance @ {htf_opposing['price']:,.2f} — caution"
                if signal_type == "BUY"
                else f"Near HTF support @ {htf_opposing['price']:,.2f} — caution"
            )
            confluences.append(warn_label)
            htf_note = (htf_note or "") + f" | {warn_label}"

    # ── Trendline confluence ─────────────────────────────────────────────────
    if htf_trendlines:
        tl_match = _price_near_trendline(entry, signal_type, htf_trendlines, signal_index) if _price_near_trendline else None
        if tl_match:
            tl_dir   = tl_match.get("direction", "")
            tl_price = tl_match.get("trendline_price", entry)
            tl_type  = "support" if signal_type == "BUY" else "resistance"
            tl_label = (
                f"Trendline {tl_type} @ {tl_price:,.2f} "
                f"({tl_dir}, {tl_match['touches']} touches)"
            )
            confluences.append(tl_label)
            score += 2 if tl_match["strength"] == "strong" else 1
            if htf_note:
                htf_note += f" | {tl_label}"
            else:
                htf_note = tl_label

    # ── Learned bonuses ───────────────────────────────────────────────────────
    bonuses        = weights.get("confluence_bonuses", {})
    min_score      = weights.get("min_quality_score", 6)
    adjusted_score = _apply_learned_bonuses(confluences, score, bonuses)
    raw_score      = score

    timeframe = _classify_timeframe(entry, stop)
    sl_dist   = abs(entry - stop)

    # ── Discrete entries (3 scaled-in levels) ────────────────────────────────
    # Entry 1 (50%): signal entry price
    # Entry 2 (30%): 0.3× SL distance better (deeper pullback)
    # Entry 3 (20%): 0.6× SL distance better (best price)
    if signal_type == "BUY":
        entry2 = round(entry - sl_dist * 0.3, 4)
        entry3 = round(entry - sl_dist * 0.6, 4)
    else:
        entry2 = round(entry + sl_dist * 0.3, 4)
        entry3 = round(entry + sl_dist * 0.6, 4)

    # ── Trailing SL levels ────────────────────────────────────────────────────
    # Activates when price moves 1R (sl_dist) in favour
    # Trail at 50% of move — locks in partial profit
    if signal_type == "BUY":
        trail_activate = round(entry + sl_dist, 4)        # activate at +1R
        trail_step     = round(sl_dist * 0.5, 4)          # trail by 0.5R steps
    else:
        trail_activate = round(entry - sl_dist, 4)        # activate at +1R
        trail_step     = round(sl_dist * 0.5, 4)

    return {
        "signal":        signal_type,
        "index":         signal_index,
        "entry":         round(entry, 4),
        "sl":            round(stop, 4),
        "tp":            round(target, 4),
        "rr":            reward_ratio,
        "timeframe":     timeframe,
        "session":       session,
        "quality_score": adjusted_score,
        "raw_score":     raw_score,
        "confidence":    _confidence_label(adjusted_score, min_score),
        "setup":         "Sweep + FVG + BOS",
        "confluences":   confluences,
        "htf_level":     htf_note,
        "strategy_tag":  f"ICT_{timeframe}",
        # Discrete entry levels
        "entries": {
            "e1": {"price": round(entry, 4),  "size_pct": 50, "label": "Primary entry"},
            "e2": {"price": entry2,            "size_pct": 30, "label": "Pullback entry"},
            "e3": {"price": entry3,            "size_pct": 20, "label": "Best price entry"}
        },
        # Trailing SL config
        "trailing_sl": {
            "enabled":       True,
            "activate_at":   trail_activate,   # price level that activates trailing
            "step":          trail_step,        # how much SL moves per R gained
            "current_sl":    round(stop, 4),   # starts at original SL
            "activated":     False
        }
    }


# ── Public API ────────────────────────────────────────────────────────────────

def generate_trade_signals(
    bos_signals,
    fvgs,
    sweeps,
    order_blocks,
    df,
    htf_sr_levels=None,
    htf_trendlines=None
):
    weights       = _load_weights()
    htf_sr_levels = htf_sr_levels or []
    trade_signals = []

    for sweep in sweeps:
        if sweep["type"] == "bullish_liquidity_sweep":
            signal_type = "BUY"
        elif sweep["type"] == "bearish_liquidity_sweep":
            signal_type = "SELL"
        else:
            continue

        config = _direction_config(signal_type)

        if not _has_recent_bos(bos_signals, config["bos"]):
            continue

        fvg = _find_fvg_after_sweep(fvgs, config["fvg"], sweep["index"])
        if not fvg:
            continue

        order_block = _nearest_order_block(
            order_blocks, config["order_block"], fvg["index"]
        )

        signal = _build_signal(
            df, sweep, fvg, order_block,
            signal_type, weights, htf_sr_levels,
            htf_trendlines=htf_trendlines
        )

        if signal and signal["quality_score"] >= 4:
            trade_signals.append(signal)

    return trade_signals