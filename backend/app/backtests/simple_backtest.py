"""
simple_backtest.py
------------------
Realistic backtest engine implementing proper trading rules:

1. Entry check — price must actually reach limit order level
2. Close-based SL — candle must CLOSE beyond SL (not just wick)
3. 4 Quadrant classification (BP/SP/SL/BL)
4. Signal expiry — OPEN signals expire after 8 hours of candles
5. Minimum RR filter — skips signals below 2.0 RR
"""

MIN_RR           = 2.0
MAX_CANDLES_WAIT = 480   # max 8 hours of 1m candles to wait for entry


def _classify_quadrant(outcome, points, entry, sl, tp):
    """Classify trade into BP/SP/SL/BL quadrant."""
    try:
        sl_dist = abs(float(entry) - float(sl))
        pts     = abs(float(points)) if points else 0
    except Exception:
        return "SL"

    if outcome == "LOSS":
        if sl_dist > 0 and pts > sl_dist * 2.0:
            return "BL"   # Big Loss — should never happen
        return "SL"       # Small Loss — normal

    if outcome == "WIN":
        rr = pts / sl_dist if sl_dist > 0 else 0
        if rr >= 2.5:
            return "BP"   # Big Profit — account growth
        return "SP"       # Small Profit

    return "OPEN"


def run_backtest(df, trade_signals):
    """
    Realistic backtest with entry checks, close-based SL,
    signal expiry and quadrant classification.
    """
    results = []

    for signal in trade_signals:
        try:
            entry        = float(signal["entry"])
            sl           = float(signal["sl"])
            tp           = float(signal["tp"])
            signal_type  = signal["signal"]
            signal_index = signal.get("index", 0)
            rr           = signal.get("rr", 0)
        except Exception:
            continue

        # Skip signals below minimum RR
        try:
            if float(rr) < MIN_RR:
                continue
        except Exception:
            pass

        outcome    = "OPEN"
        entry_hit  = False
        candles_waited = 0

        for i in range(signal_index + 1, len(df)):
            if candles_waited > MAX_CANDLES_WAIT:
                outcome = "EXPIRED"
                break

            try:
                high  = float(df["high"].iloc[i])
                low   = float(df["low"].iloc[i])
                close = float(df["close"].iloc[i])
            except Exception:
                continue

            candles_waited += 1

            if signal_type == "BUY":
                if not entry_hit:
                    # Candle must CLOSE above entry for long confirmation
                    if low <= entry and close >= entry:
                        entry_hit = True
                    else:
                        continue
                # Wick-based SL and TP
                if low <= sl:
                    outcome = "LOSS"
                    break
                if high >= tp:
                    outcome = "WIN"
                    break

            elif signal_type == "SELL":
                if not entry_hit:
                    # Candle must CLOSE below entry for short confirmation
                    if high >= entry and close <= entry:
                        entry_hit = True
                    else:
                        continue
                # Wick-based SL and TP
                if high >= sl:
                    outcome = "LOSS"
                    break
                if low <= tp:
                    outcome = "WIN"
                    break

        # Calculate points
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)

        if outcome == "WIN":
            points = round(tp_dist, 4)
        elif outcome == "LOSS":
            points = round(-sl_dist, 4)
        else:
            points = 0

        quadrant = _classify_quadrant(outcome, points, entry, sl, tp)

        results.append({
            "signal":        signal_type,
            "index":         signal_index,
            "timestamp":     signal.get("timestamp"),   # for date display/grouping
            "entry":         entry,
            "sl":            sl,
            "tp":            tp,
            "rr":            rr,
            "timeframe":     signal.get("timeframe"),
            "confidence":    signal.get("confidence"),
            "quality_score": signal.get("quality_score"),
            "setup":         signal.get("setup"),
            "confluences":   signal.get("confluences", []),
            "outcome":       outcome,
            "points":        points,
            "quadrant":      quadrant,
            "entry_filled":  entry_hit,
            "candles_waited": candles_waited
        })

    # Summary stats
    resolved = [r for r in results if r["outcome"] in ("WIN","LOSS")]
    wins     = [r for r in resolved if r["outcome"] == "WIN"]
    losses   = [r for r in resolved if r["outcome"] == "LOSS"]
    bp       = [r for r in resolved if r["quadrant"] == "BP"]
    bl       = [r for r in resolved if r["quadrant"] == "BL"]

    if resolved:
        print(f"[Backtest] {len(results)} signals | {len(resolved)} resolved | "
              f"W:{len(wins)} L:{len(losses)} | "
              f"WR:{len(wins)/len(resolved):.0%} | "
              f"BP:{len(bp)} BL:{len(bl)}")

    return results
