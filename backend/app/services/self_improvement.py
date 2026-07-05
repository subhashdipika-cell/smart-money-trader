"""
self_improvement.py
-------------------
After accumulating enough data, this engine analyses performance
and generates specific, actionable suggestions to improve the system.

It thinks like a trading coach reviewing a student's journal:
  - "You keep losing on Asia session BTC scalps — avoid them"
  - "London + NY overlap signals have 80% win rate — prioritise them"
  - "FVG + Trendline combo works better than BOS alone"
  - "You missed 3 big Gold moves — the pattern was X, add it"
  - "Consider adding RSI divergence to filter false sweeps"

Saves suggestions to improvement_suggestions.json
Suggestions are shown in the Learning tab dashboard.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

_BASE           = os.path.join(os.path.dirname(__file__), "..", "..")
SIGNALS_LOG     = os.path.abspath(os.path.join(_BASE, "signals_log.json"))
JOURNAL_FILE    = os.path.abspath(os.path.join(_BASE, "trader_journal.json"))
KNOWLEDGE_FILE  = os.path.abspath(os.path.join(_BASE, "strategy_knowledge.json"))
SUGGESTIONS_FILE = os.path.abspath(os.path.join(_BASE, "improvement_suggestions.json"))

MIN_SIGNALS_FOR_SUGGESTIONS = 8


def _load(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return [] if "log" in path or "journal" in path else {}


def _win_rate(signals):
    resolved = [s for s in signals if s.get("outcome") in ("WIN", "LOSS")]
    if not resolved:
        return None, 0
    wins = sum(1 for s in resolved if s.get("outcome") == "WIN")
    return round(wins / len(resolved), 3), len(resolved)


def _session_from_ts(ts_ms):
    try:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(int(ts_ms)/1000, tz=timezone.utc)
        h  = dt.hour
        if 0  <= h < 7:  return "Asia"
        if 7  <= h < 12: return "London"
        if 12 <= h < 20: return "New York"
        return "Off-session"
    except Exception:
        return "Unknown"


def generate_suggestions():
    """
    Analyse all available data and generate improvement suggestions.
    """
    signals   = _load(SIGNALS_LOG)
    knowledge = _load(KNOWLEDGE_FILE)
    resolved  = [s for s in signals if s.get("outcome") in ("WIN", "LOSS")]

    suggestions = []

    if len(resolved) < MIN_SIGNALS_FOR_SUGGESTIONS:
        return [{
            "type":     "info",
            "priority": "low",
            "title":    "Accumulating data",
            "detail":   f"Need {MIN_SIGNALS_FOR_SUGGESTIONS} resolved signals for suggestions. Currently have {len(resolved)}.",
            "action":   "Keep running — suggestions appear automatically as signals resolve."
        }]

    # ── 1. Session performance analysis ──────────────────────────────────────
    session_groups = defaultdict(list)
    for s in resolved:
        sess = _session_from_ts(s.get("timestamp", 0))
        session_groups[sess].append(s)

    best_session  = None
    worst_session = None
    best_wr       = 0
    worst_wr      = 1.0

    for sess, sigs in session_groups.items():
        wr, count = _win_rate(sigs)
        if count >= 3 and wr:
            if wr > best_wr:
                best_wr, best_session = wr, (sess, count)
            if wr < worst_wr:
                worst_wr, worst_session = wr, (sess, count)

    if best_session and best_wr >= 0.6:
        suggestions.append({
            "type":     "opportunity",
            "priority": "high",
            "title":    f"🏆 {best_session[0]} session is your strongest",
            "detail":   f"{best_session[0]} session has {best_wr:.0%} win rate over {best_session[1]} trades.",
            "action":   f"Consider increasing position size during {best_session[0]} session trades.",
            "metric":   f"{best_wr:.0%} win rate"
        })

    if worst_session and worst_wr <= 0.35:
        suggestions.append({
            "type":     "warning",
            "priority": "high",
            "title":    f"⚠️ Avoid {worst_session[0]} session",
            "detail":   f"{worst_session[0]} session only {worst_wr:.0%} win rate over {worst_session[1]} trades.",
            "action":   f"Add {worst_session[0]} session filter — skip signals generated during this time.",
            "metric":   f"{worst_wr:.0%} win rate"
        })

    # ── 2. Per-asset performance ──────────────────────────────────────────────
    for symbol in ["BTCUSDT", "ETHUSDT", "XAUUSD"]:
        asset_sigs = [s for s in resolved if s.get("symbol") == symbol]
        if len(asset_sigs) < 3:
            continue
        wr, count = _win_rate(asset_sigs)
        if wr and wr < 0.35:
            suggestions.append({
                "type":     "warning",
                "priority": "high",
                "title":    f"⚠️ {symbol} signals underperforming",
                "detail":   f"{symbol} has only {wr:.0%} win rate over {count} trades.",
                "action":   f"Raise quality score threshold for {symbol} by +1. Consider adding volume confirmation.",
                "metric":   f"{wr:.0%} win rate"
            })
        elif wr and wr >= 0.7:
            suggestions.append({
                "type":     "opportunity",
                "priority": "medium",
                "title":    f"✅ {symbol} performing excellently",
                "detail":   f"{symbol} has {wr:.0%} win rate — the strategy fits this asset well.",
                "action":   f"Consider slightly loosening threshold for {symbol} to capture more setups.",
                "metric":   f"{wr:.0%} win rate"
            })

    # ── 3. Confluence combination analysis ───────────────────────────────────
    combo_wins   = defaultdict(int)
    combo_totals = defaultdict(int)
    for s in resolved:
        tags   = sorted(s.get("confluences", []))
        is_win = s.get("outcome") == "WIN"
        for i in range(len(tags)):
            for j in range(i+1, len(tags)):
                combo = f"{tags[i]} + {tags[j]}"
                combo_totals[combo] += 1
                if is_win: combo_wins[combo] += 1

    for combo, total in combo_totals.items():
        if total < 3:
            continue
        wr = combo_wins[combo] / total
        if wr >= 0.75:
            suggestions.append({
                "type":     "opportunity",
                "priority": "medium",
                "title":    f"🎯 High-value confluence combo found",
                "detail":   f'"{combo}" wins {wr:.0%} of the time ({total} trades).',
                "action":   f"Add +1 bonus score when both '{combo.split(' + ')[0]}' and '{combo.split(' + ')[1]}' appear together.",
                "metric":   f"{wr:.0%} win rate"
            })
        elif wr <= 0.25 and total >= 3:
            suggestions.append({
                "type":     "warning",
                "priority": "medium",
                "title":    f"🚫 Weak confluence combo detected",
                "detail":   f'"{combo}" only wins {wr:.0%} of the time ({total} trades).',
                "action":   f"Add -1 penalty when these two confluences appear without stronger confirmation.",
                "metric":   f"{wr:.0%} win rate"
            })

    # ── 4. Timeframe analysis ─────────────────────────────────────────────────
    tf_groups = defaultdict(list)
    for s in resolved:
        tf = s.get("timeframe", "Unknown")
        tf_groups[tf].append(s)

    for tf, sigs in tf_groups.items():
        wr, count = _win_rate(sigs)
        if count < 3 or not wr:
            continue
        if wr < 0.3:
            suggestions.append({
                "type":     "warning",
                "priority": "high",
                "title":    f"⚠️ {tf} signals have poor win rate",
                "detail":   f"{tf} signals only win {wr:.0%} of the time ({count} trades).",
                "action":   f"Reduce daily limit for {tf} signals from current to {max(1, {'Scalping':4,'Intraday':2,'Swing':1}.get(tf,2)-1)}.",
                "metric":   f"{wr:.0%} win rate"
            })

    # ── 5. Feature suggestions based on missed patterns ──────────────────────
    total_losses = len([s for s in resolved if s.get("outcome") == "LOSS"])
    if total_losses >= 5:
        suggestions.append({
            "type":     "feature",
            "priority": "medium",
            "title":    "💡 Consider adding RSI divergence filter",
            "detail":   f"With {total_losses} losses analysed, many false sweeps could be filtered by RSI divergence confirmation.",
            "action":   "Add RSI(14) divergence check: only take BUY signals when RSI is making higher lows, SELL when lower highs.",
            "metric":   "Potential improvement"
        })

    if len(resolved) >= 15:
        suggestions.append({
            "type":     "feature",
            "priority": "low",
            "title":    "💡 Volume confirmation layer",
            "detail":   "With enough data, volume-based confirmation could filter low-probability setups.",
            "action":   "Add volume spike detection: entry sweeps on above-average volume have higher institutional conviction.",
            "metric":   "Enhancement"
        })

    if len(resolved) >= 20:
        suggestions.append({
            "type":     "feature",
            "priority": "medium",
            "title":    "💡 Time-of-day optimisation",
            "detail":   "Consider restricting signals to the best-performing 2-hour windows within each session.",
            "action":   "Analyse sub-session performance and add time-window filter (e.g. only 13:00-15:00 UTC for NY session).",
            "metric":   "Enhancement"
        })

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: priority_order.get(s.get("priority","low"), 2))

    result = {
        "suggestions":    suggestions,
        "total_resolved": len(resolved),
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "summary": f"{len(suggestions)} suggestions based on {len(resolved)} resolved trades."
    }

    with open(SUGGESTIONS_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[Improvement] {len(suggestions)} suggestions generated from {len(resolved)} resolved signals")
    return result


def load_suggestions():
    try:
        with open(SUGGESTIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"suggestions": [], "summary": "No suggestions yet."}
