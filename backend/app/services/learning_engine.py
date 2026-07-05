"""
learning_engine.py
------------------
Analyses every resolved signal in signals_log.json and:
  1. Calculates win-rate per confluence tag
  2. Calculates overall win-rate
  3. Raises/lowers the minimum quality-score threshold automatically
  4. Saves everything to learned_weights.json so signal_generator can use it
"""

import json
import os
from collections import defaultdict

# ── File paths ────────────────────────────────────────────────────────────────
_BASE = os.path.join(os.path.dirname(__file__), "..", "..")
SIGNALS_LOG_FILE     = os.path.abspath(os.path.join(_BASE, "signals_log.json"))
LEARNED_WEIGHTS_FILE = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "min_quality_score":    6,       # minimum score to send a signal
    "confluence_win_rates": {},      # tag -> win-rate (0.0 – 1.0)
    "confluence_bonuses":   {},      # tag -> score adjustment (-1 / 0 / +1)
    "overall_win_rate":     None,    # None until enough data
    "total_resolved":       0,
    "total_wins":           0,
    "notes":                "Not enough data yet – using defaults."
}

MIN_SAMPLES_FOR_LEARNING = 10   # don't adjust until we have this many resolved signals
MIN_SAMPLES_PER_CONFLUENCE = 3  # don't score a confluence until it appears this many times


def load_signals_log():
    try:
        with open(SIGNALS_LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def load_weights():
    try:
        with open(LEARNED_WEIGHTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def save_weights(weights):
    with open(LEARNED_WEIGHTS_FILE, "w") as f:
        json.dump(weights, f, indent=2)


def _confluence_stats(resolved_signals):
    """Count wins and appearances for every confluence tag."""
    wins   = defaultdict(int)
    totals = defaultdict(int)

    for signal in resolved_signals:
        outcome     = signal.get("outcome", "OPEN")
        confluences = signal.get("confluences", [])
        is_win      = outcome == "WIN"

        for tag in confluences:
            totals[tag] += 1
            if is_win:
                wins[tag] += 1

    return wins, totals


def _score_bonus(win_rate):
    """
    Return a score adjustment based on how well a confluence performs.
      win_rate >= 0.65  →  +1  (reliable confluence, reward it)
      win_rate <= 0.35  →  -1  (poor confluence, penalise it)
      otherwise         →   0
    """
    if win_rate >= 0.65:
        return 1
    if win_rate <= 0.35:
        return -1
    return 0


def _dynamic_threshold(overall_win_rate, current_threshold):
    """
    Automatically tighten or loosen the quality-score gate.
      overall win-rate < 40%  → raise threshold (be more selective)
      overall win-rate > 65%  → lower threshold slightly (allow more signals)
      otherwise               → keep current threshold
    Clamped between 4 and 8.
    """
    if overall_win_rate < 0.40:
        return min(current_threshold + 1, 8)
    if overall_win_rate > 0.65:
        return max(current_threshold - 1, 4)
    return current_threshold


def run_learning():
    """
    Main entry point. Call this after each signal-check cycle.
    Returns the updated weights dict.
    """
    signals = load_signals_log()
    weights = load_weights()

    # Only look at signals that have a resolved outcome
    resolved = [s for s in signals if s.get("outcome") in ("WIN", "LOSS")]
    total    = len(resolved)
    wins_n   = sum(1 for s in resolved if s.get("outcome") == "WIN")

    weights["total_resolved"] = total
    weights["total_wins"]     = wins_n

    if total < MIN_SAMPLES_FOR_LEARNING:
        weights["notes"] = (
            f"Only {total} resolved signals — need {MIN_SAMPLES_FOR_LEARNING} "
            f"before learning kicks in. Using defaults."
        )
        save_weights(weights)
        print(f"[Learning] {weights['notes']}")
        return weights

    # ── Overall win-rate ──────────────────────────────────────────────────────
    overall_win_rate            = wins_n / total
    weights["overall_win_rate"] = round(overall_win_rate, 3)

    # ── Per-confluence win-rates ───────────────────────────────────────────────
    conf_wins, conf_totals = _confluence_stats(resolved)

    win_rates = {}
    bonuses   = {}

    for tag, count in conf_totals.items():
        if count < MIN_SAMPLES_PER_CONFLUENCE:
            continue   # not enough data for this tag yet
        wr           = conf_wins[tag] / count
        win_rates[tag] = round(wr, 3)
        bonuses[tag]   = _score_bonus(wr)

    weights["confluence_win_rates"] = win_rates
    weights["confluence_bonuses"]   = bonuses

    # ── Dynamic threshold ─────────────────────────────────────────────────────
    old_threshold = weights.get("min_quality_score", DEFAULT_WEIGHTS["min_quality_score"])
    new_threshold = _dynamic_threshold(overall_win_rate, old_threshold)
    weights["min_quality_score"] = new_threshold

    weights["notes"] = (
        f"Learned from {total} resolved signals. "
        f"Win rate: {overall_win_rate:.0%}. "
        f"Quality threshold: {new_threshold}."
    )

    save_weights(weights)
    print(f"[Learning] {weights['notes']}")
    for tag, wr in win_rates.items():
        bonus_str = f"  bonus={bonuses[tag]:+d}" if bonuses[tag] != 0 else ""
        print(f"[Learning]   {tag}: {wr:.0%} win-rate ({conf_totals[tag]} trades){bonus_str}")

    return weights
