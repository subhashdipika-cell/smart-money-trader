"""
strategy_learner.py
-------------------
Advanced self-learning engine that:

1. Analyses WIN/LOSS patterns per asset, session, timeframe and confluence combo
2. Identifies which setups work best in which geopolitical conditions
3. Adjusts per-asset quality score thresholds independently
4. Tracks streak patterns (e.g. 3 losses in a row → raise threshold temporarily)
5. Saves a rich knowledge base to strategy_knowledge.json
6. Exposes learned insights to signal_generator for smarter filtering

Improvements over v1:
- Recency weighting: last 20 trades count 2× so recent form matters more
- Points-weighted confluence scoring: big wins/losses teach more than small ones
- Per-direction win rates (BUY vs SELL tracked separately per asset)
- Score-outcome correlation: verifies higher scores actually win more
- Direction blocking: blocks a direction if win rate falls below 30% (5+ trades)
- Smarter threshold: more granular steps based on win rate severity
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

_BASE = os.path.join(os.path.dirname(__file__), "..", "..")
SIGNALS_LOG_FILE     = os.path.abspath(os.path.join(_BASE, "signals_log.json"))
LEARNED_WEIGHTS_FILE = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))
KNOWLEDGE_FILE       = os.path.abspath(os.path.join(_BASE, "strategy_knowledge.json"))

MIN_SAMPLES            = 8    # minimum resolved signals before learning per asset
MIN_CONFLUENCE_SAMPLES = 3    # minimum appearances before scoring a confluence
MIN_DIRECTION_SAMPLES  = 5    # minimum trades before blocking a direction
RECENCY_N              = 20   # last N trades get extra weight
RECENCY_WEIGHT         = 2.0  # recent trades count this many times vs older ones

# Outcomes recorded BEFORE the resolver fix (2026-07-05) are corrupted: the
# old 2h pending expiry + missing entry_hit persistence erased slow winners
# as EXPIRED and logged a fake ~10% win rate — weights learned from that data
# taught the engine the wrong lessons. The learner only trusts signals from
# this epoch onward; MIN_SAMPLES gates keep defaults until clean data grows.
CLEAN_DATA_EPOCH_MS = 1783209600000   # 2026-07-05 00:00 UTC


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _load_log():
    try:
        with open(SIGNALS_LOG_FILE, "r") as f:
            log = json.load(f)
    except Exception:
        return []
    def _ts(s):
        try:
            t = int(s.get("timestamp", 0))
            return t if t > 1e12 else t * 1000
        except Exception:
            return 0
    return [s for s in log if _ts(s) >= CLEAN_DATA_EPOCH_MS]

def load_weights():
    try:
        with open(LEARNED_WEIGHTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"min_quality_score": 6, "confluence_bonuses": {}, "confluence_win_rates": {}}

def _load_knowledge():
    try:
        with open(KNOWLEDGE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_knowledge(k):
    with open(KNOWLEDGE_FILE, "w") as f:
        json.dump(k, f, indent=2)

def _save_weights(w):
    with open(LEARNED_WEIGHTS_FILE, "w") as f:
        json.dump(w, f, indent=2)


# ── Analysis helpers ──────────────────────────────────────────────────────────

def _win_rate(signals):
    """Simple unweighted win rate."""
    resolved = [s for s in signals if s.get("outcome") in ("WIN", "LOSS")]
    if not resolved:
        return None, 0
    wins = sum(1 for s in resolved if s.get("outcome") == "WIN")
    return round(wins / len(resolved), 3), len(resolved)


def _win_rate_with_recency(signals):
    """
    Win rate with recent trades weighted more heavily.
    Last RECENCY_N resolved trades count RECENCY_WEIGHT times.
    Makes the system more responsive to recent performance changes —
    a losing streak in the last 20 trades overrides a long historical win record.
    """
    resolved = [s for s in signals if s.get("outcome") in ("WIN", "LOSS")]
    if not resolved:
        return None, 0

    recent = resolved[-RECENCY_N:]
    older  = resolved[:-RECENCY_N] if len(resolved) > RECENCY_N else []

    weighted_wins  = sum(RECENCY_WEIGHT for s in recent if s.get("outcome") == "WIN")
    weighted_wins += sum(1.0 for s in older if s.get("outcome") == "WIN")
    weighted_total = len(recent) * RECENCY_WEIGHT + len(older)

    if weighted_total == 0:
        return None, 0

    return round(weighted_wins / weighted_total, 3), len(resolved)


def _session_from_timestamp(ts_ms):
    """Derive trading session from UTC timestamp."""
    try:
        dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
        h  = dt.hour
        if 0  <= h < 7:  return "Asia"
        if 7  <= h < 12: return "London"
        if 12 <= h < 20: return "New York"
        return "Off-session"
    except Exception:
        return "Unknown"


def _confluence_win_rates(resolved):
    """Simple confluence win rates (unweighted)."""
    wins   = defaultdict(int)
    totals = defaultdict(int)
    for s in resolved:
        is_win = s.get("outcome") == "WIN"
        for tag in s.get("confluences", []):
            totals[tag] += 1
            if is_win:
                wins[tag] += 1
    rates = {}
    for tag, total in totals.items():
        if total >= MIN_CONFLUENCE_SAMPLES:
            rates[tag] = {"win_rate": round(wins[tag] / total, 3), "total": total}
    return rates


def _confluence_win_rates_weighted(resolved):
    """
    Points-weighted confluence win rates.
    A +500pt win teaches more than a +10pt win.
    A -200pt loss penalises more than a -20pt loss.
    Uses actual points from signals_log so confluence tags
    that lead to high-profit wins get stronger bonuses.
    """
    wins   = defaultdict(float)
    losses = defaultdict(float)
    totals = defaultdict(int)

    for s in resolved:
        outcome = s.get("outcome")
        pts     = abs(float(s.get("points") or 0))
        weight  = max(1.0, pts)   # minimum weight of 1 even if points not recorded
        tags    = s.get("confluences", [])

        for tag in tags:
            totals[tag] += 1
            if outcome == "WIN":
                wins[tag] += weight
            elif outcome == "LOSS":
                losses[tag] += weight

    rates = {}
    for tag, total in totals.items():
        if total < MIN_CONFLUENCE_SAMPLES:
            continue
        total_weight = wins[tag] + losses[tag]
        wr = wins[tag] / total_weight if total_weight > 0 else 0.5
        rates[tag] = {"win_rate": round(wr, 3), "total": total}
    return rates


def _combo_win_rates(resolved):
    """Find which pairs of confluences together have high win rates."""
    combo_wins   = defaultdict(int)
    combo_totals = defaultdict(int)
    for s in resolved:
        tags   = sorted(s.get("confluences", []))
        is_win = s.get("outcome") == "WIN"
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                combo = f"{tags[i]} + {tags[j]}"
                combo_totals[combo] += 1
                if is_win:
                    combo_wins[combo] += 1
    rates = {}
    for combo, total in combo_totals.items():
        if total >= MIN_CONFLUENCE_SAMPLES:
            rates[combo] = {"win_rate": round(combo_wins[combo] / total, 3), "total": total}
    return dict(sorted(rates.items(), key=lambda x: -x[1]["win_rate"])[:10])


def _direction_win_rates(asset_signals):
    """
    Track BUY vs SELL performance separately per asset.
    The system may be great at catching BUY setups but
    consistently lose on SELL — this detects that.
    """
    result = {}
    for direction in ["BUY", "SELL"]:
        dir_sigs = [
            s for s in asset_signals
            if s.get("signal") == direction and s.get("outcome") in ("WIN", "LOSS")
        ]
        if len(dir_sigs) < 3:
            continue
        wins = sum(1 for s in dir_sigs if s.get("outcome") == "WIN")
        wr   = round(wins / len(dir_sigs), 3)
        result[direction] = {"win_rate": wr, "total": len(dir_sigs)}
    return result


def _score_outcome_correlation(resolved):
    """
    Check if higher quality scores actually win more.
    Returns (score_map, recommended_min_score).

    If score 6 wins at 40% but score 8 wins at 70%,
    the system should raise its minimum threshold to 8.
    """
    score_groups = defaultdict(list)
    for s in resolved:
        score = s.get("quality_score")
        if score is not None:
            score_groups[int(score)].append(s)

    result = {}
    for score, sigs in score_groups.items():
        wins   = sum(1 for s in sigs if s.get("outcome") == "WIN")
        losses = sum(1 for s in sigs if s.get("outcome") == "LOSS")
        total  = wins + losses
        if total >= 3:
            result[score] = {"win_rate": round(wins / total, 3), "total": total}

    # Find the lowest score band that achieves >= 55% win rate
    recommended = None
    for score in sorted(result.keys()):
        if result[score]["win_rate"] >= 0.55:
            recommended = score
            break

    return result, recommended


def _loss_streak(resolved):
    """Count current consecutive real losses (excludes EXPIRED)."""
    streak = 0
    for s in reversed(resolved):
        if s.get("outcome") == "LOSS":
            streak += 1
        elif s.get("outcome") == "WIN":
            break
    return streak


def _geo_condition_performance(resolved):
    """
    Analyse performance grouped by sentiment label at time of signal.
    E.g. do signals sent during 'Bullish' sentiment win more?
    """
    groups = defaultdict(list)
    for s in resolved:
        label = s.get("sentiment", "Unknown")
        groups[label].append(s)
    result = {}
    for label, signals in groups.items():
        wr, count = _win_rate(signals)
        if count >= 2:
            result[label] = {"win_rate": wr, "total": count}
    return result


def _dynamic_threshold(win_rate, loss_streak, current):
    """
    Adjust quality score threshold based on win rate and loss streak.

    Win rate tiers:
      < 35%  → raise by 2 (very poor — tighten hard)
      < 45%  → raise by 1 (poor — tighten moderately)
      > 70%  → lower by 1 (excellent — allow more signals)
      > 60%  → lower by 1 (good — loosen slightly)

    Loss streak overrides:
      5+ consecutive losses → raise by 2 (emergency brake)
      3+ consecutive losses → raise by 1 (caution mode)

    Clamped between 4 and 8.
    """
    threshold = current

    if win_rate is not None:
        if win_rate < 0.35:
            threshold = min(current + 2, 8)   # very poor — raise hard
        elif win_rate < 0.45:
            threshold = min(current + 1, 7)   # poor — raise moderately
        elif win_rate > 0.70:
            threshold = max(current - 1, 4)   # excellent — loosen
        elif win_rate > 0.60:
            threshold = max(current - 1, 5)   # good — loosen slightly

    if loss_streak >= 5:
        threshold = min(threshold + 2, 8)     # emergency brake
    elif loss_streak >= 3:
        threshold = min(threshold + 1, 7)     # caution mode

    return threshold


# ── Main learning function ────────────────────────────────────────────────────

def run_learning():
    """
    Full learning cycle. Returns updated weights dict.
    """
    all_signals = _load_log()
    weights     = load_weights()
    knowledge   = _load_knowledge()

    # EXPIRED signals are excluded from win/loss calculations
    resolved_all = [s for s in all_signals if s.get("outcome") in ("WIN", "LOSS")]

    if not resolved_all:
        print("[Learning] No resolved signals yet.")
        return weights

    # ── Global stats ──────────────────────────────────────────────────────────
    global_wr, global_count = _win_rate_with_recency(resolved_all)
    _, raw_count            = _win_rate(resolved_all)
    global_wins             = sum(1 for s in resolved_all if s.get("outcome") == "WIN")

    weights["total_resolved"]  = raw_count
    weights["total_wins"]      = global_wins
    weights["overall_win_rate"] = global_wr

    print(f"[Learning] Global: {raw_count} resolved, recency-weighted win rate {global_wr:.0%}" if global_wr else f"[Learning] {raw_count} resolved")

    # ── Score-outcome correlation (global) ────────────────────────────────────
    score_corr, recommended_min = _score_outcome_correlation(resolved_all)
    if score_corr:
        weights["score_outcome_correlation"] = score_corr
        corr_str = ", ".join(f"{s}→{d['win_rate']:.0%}" for s, d in sorted(score_corr.items()))
        print(f"[Learning] Score correlation: {corr_str}")
        if recommended_min is not None:
            print(f"[Learning] Score correlation suggests minimum score ≥ {recommended_min} for 55%+ win rate")

    # ── Per-asset learning ────────────────────────────────────────────────────
    asset_knowledge  = {}
    asset_thresholds = {}
    direction_blocks = {}

    for symbol in ["BTCUSDT", "ETHUSDT", "XAUUSD"]:
        asset_signals = [
            s for s in resolved_all
            if s.get("symbol") == symbol and s.get("outcome") in ("WIN", "LOSS")
        ]
        if not asset_signals:
            continue

        # Use recency-weighted win rate for threshold decisions
        wr, count     = _win_rate_with_recency(asset_signals)
        streak        = _loss_streak(asset_signals)
        conf_rates    = _confluence_win_rates_weighted(asset_signals)  # points-weighted
        combo_rates   = _combo_win_rates(asset_signals)
        geo_perf      = _geo_condition_performance(asset_signals)
        dir_perf      = _direction_win_rates(asset_signals)

        # Session performance
        session_groups = defaultdict(list)
        for s in asset_signals:
            sess = _session_from_timestamp(s.get("timestamp", 0))
            session_groups[sess].append(s)
        session_perf = {}
        for sess, sigs in session_groups.items():
            swr, scount = _win_rate(sigs)
            if scount >= 2:
                session_perf[sess] = {"win_rate": swr, "total": scount}

        # Timeframe performance
        tf_groups = defaultdict(list)
        for s in asset_signals:
            tf = s.get("timeframe", "Unknown")
            tf_groups[tf].append(s)
        tf_perf = {}
        for tf, sigs in tf_groups.items():
            twr, tcount = _win_rate(sigs)
            if tcount >= 2:
                tf_perf[tf] = {"win_rate": twr, "total": tcount}

        # Score-outcome correlation per asset
        asset_score_corr, asset_rec_min = _score_outcome_correlation(asset_signals)

        # Dynamic threshold per asset (uses recency-weighted win rate)
        current_threshold = weights.get(f"min_quality_score_{symbol}", 6)
        if count >= MIN_SAMPLES:
            new_threshold = _dynamic_threshold(wr, streak, current_threshold)
            # If score correlation recommends a higher minimum, respect it
            if asset_rec_min and asset_rec_min > new_threshold:
                new_threshold = min(asset_rec_min, 8)
                print(f"[Learning] {symbol}: score correlation raised threshold to {new_threshold}")
        else:
            new_threshold = 6   # not enough data — use default
        asset_thresholds[symbol] = new_threshold

        # ── Direction blocks ──────────────────────────────────────────────────
        # Block a direction if win rate < 30% with 5+ trades
        for direction, data in dir_perf.items():
            dir_wr    = data.get("win_rate", 0.5)
            dir_total = data.get("total", 0)
            key       = f"{symbol}_{direction}"
            if dir_total >= MIN_DIRECTION_SAMPLES and dir_wr < 0.30:
                direction_blocks[key] = {
                    "blocked":  True,
                    "win_rate": dir_wr,
                    "total":    dir_total,
                    "reason":   f"{symbol} {direction} win rate is only {dir_wr:.0%} over {dir_total} trades"
                }
                print(f"[Learning] ⛔ BLOCKING {symbol} {direction} — win rate {dir_wr:.0%} ({dir_total} trades)")
            elif key in direction_blocks:
                # Previously blocked but now recovered — unblock
                direction_blocks.pop(key, None)
                print(f"[Learning] ✅ UNBLOCKED {symbol} {direction} — win rate recovered to {dir_wr:.0%}")

        asset_knowledge[symbol] = {
            "total_resolved":    count,
            "win_rate":          wr,
            "loss_streak":       streak,
            "threshold":         new_threshold,
            "confluence_rates":  conf_rates,
            "best_combos":       combo_rates,
            "session_perf":      session_perf,
            "timeframe_perf":    tf_perf,
            "geo_perf":          geo_perf,
            "direction_perf":    dir_perf,
            "score_correlation": asset_score_corr,
            "last_updated":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        }

        wr_pct = f"{wr:.0%}" if wr is not None else "n/a"
        print(f"[Learning] {symbol}: wr={wr_pct} (recency-weighted) count={count} streak={streak} threshold={new_threshold}")

        if conf_rates:
            best  = max(conf_rates.items(), key=lambda x: x[1]["win_rate"])
            worst = min(conf_rates.items(), key=lambda x: x[1]["win_rate"])
            print(f"[Learning]   Best confluence:  {best[0]} ({best[1]['win_rate']:.0%}, pts-weighted)")
            print(f"[Learning]   Worst confluence: {worst[0]} ({worst[1]['win_rate']:.0%}, pts-weighted)")

        if dir_perf:
            for d, dp in dir_perf.items():
                print(f"[Learning]   {symbol} {d}: {dp['win_rate']:.0%} ({dp['total']} trades)")

    # ── Update weights ────────────────────────────────────────────────────────

    # Global confluence bonuses — use points-weighted rates, need 10+ samples
    global_conf = _confluence_win_rates_weighted(resolved_all)
    bonuses = {}
    MIN_CONFLUENCE_REQUIRED = 10
    for tag, data in global_conf.items():
        wr    = data["win_rate"]
        total = data.get("total", 0)
        if total < MIN_CONFLUENCE_REQUIRED:
            bonuses[tag] = 0
            continue
        if wr >= 0.70:   bonuses[tag] =  2   # very reliable
        elif wr >= 0.60: bonuses[tag] =  1   # reliable
        elif wr <= 0.30: bonuses[tag] = -2   # consistently losing
        elif wr <= 0.40: bonuses[tag] = -1   # poor
        else:            bonuses[tag] =  0

    weights["confluence_bonuses"]   = bonuses
    weights["confluence_win_rates"] = {k: v["win_rate"] for k, v in global_conf.items()}

    # Direction blocks — persisted so is_quality_signal can read them
    weights["direction_blocks"] = direction_blocks

    # Per-asset thresholds
    for symbol, threshold in asset_thresholds.items():
        symbol_resolved = [s for s in resolved_all if s.get("symbol") == symbol]
        if len(symbol_resolved) >= 8:
            weights[f"min_quality_score_{symbol}"] = threshold

    # Global threshold
    global_streak        = _loss_streak(resolved_all)
    old_global_threshold = weights.get("min_quality_score", 6)
    if raw_count >= MIN_SAMPLES:
        new_global_threshold = _dynamic_threshold(global_wr, global_streak, old_global_threshold)
        # Respect score correlation recommendation globally too
        if recommended_min and recommended_min > new_global_threshold:
            new_global_threshold = min(recommended_min, 8)
    else:
        new_global_threshold = 6
    weights["min_quality_score"] = new_global_threshold

    weights["notes"] = (
        f"Learned from {raw_count} resolved signals across all assets. "
        f"Recency-weighted win rate: {global_wr:.0%}. "
        f"Quality threshold: {new_global_threshold}. "
        f"Loss streak: {global_streak}. "
        f"Direction blocks: {len(direction_blocks)}."
    ) if global_wr else f"Accumulating data — {raw_count} resolved signals so far."

    # ── Save knowledge base ───────────────────────────────────────────────────
    knowledge["assets"]           = asset_knowledge
    knowledge["global_win_rate"]  = global_wr
    knowledge["total_resolved"]   = raw_count
    knowledge["direction_blocks"] = direction_blocks
    knowledge["last_updated"]     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Best session across all assets
    all_sessions = defaultdict(list)
    for s in resolved_all:
        sess = _session_from_timestamp(s.get("timestamp", 0))
        all_sessions[sess].append(s)
    best_session    = None
    best_session_wr = 0
    for sess, sigs in all_sessions.items():
        swr, scount = _win_rate(sigs)
        if scount >= 3 and swr and swr > best_session_wr:
            best_session_wr = swr
            best_session    = sess
    knowledge["best_session"] = best_session
    knowledge["best_combos"]  = _combo_win_rates(resolved_all)

    _save_knowledge(knowledge)
    _save_weights(weights)

    print(f"[Learning] Knowledge base updated. Best session: {best_session or 'insufficient data'}")
    if direction_blocks:
        print(f"[Learning] Active direction blocks: {list(direction_blocks.keys())}")
    return weights
