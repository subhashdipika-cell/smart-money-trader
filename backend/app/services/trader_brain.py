"""
trader_brain.py
---------------
The core thinking engine. Combines:
  - Chart observations (patterns, momentum, structure)
  - Geopolitical context (geo_strategy.py)
  - Historical performance (strategy_learner.py)
  - Research insights (web_researcher.py)
  - Current sentiment (sentiment_service.py)

Produces a coherent market narrative and a CONVICTION score
that gates whether a signal is worth sending.

Thinks like a trader: "What is the market trying to do?
Where are the institutions positioned? Does this setup make sense
given what's happening in the world right now?"
"""

import json
import os
from datetime import datetime, timezone, timedelta
from app.services.strategy_learner  import load_weights
from app.services.web_researcher    import get_research_bias
from app.services.geo_strategy      import compute_geo_bias

# Lazy import to avoid circular dependency
def _get_quadrant_log():
    try:
        import json, os
        _BASE = os.path.join(os.path.dirname(__file__), "..", "..")
        with open(os.path.abspath(os.path.join(_BASE, "signals_log.json"))) as f:
            log = json.load(f)
        return [s for s in log if s.get("outcome") in ("WIN","LOSS")]
    except Exception:
        return []

_BASE          = os.path.join(os.path.dirname(__file__), "..", "..")
JOURNAL_FILE   = os.path.abspath(os.path.join(_BASE, "trader_journal.json"))
KNOWLEDGE_FILE = os.path.abspath(os.path.join(_BASE, "strategy_knowledge.json"))


# ── Journal helpers ───────────────────────────────────────────────────────────

def _load_journal():
    try:
        with open(JOURNAL_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_journal(entries):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(entries[-500:], f, indent=2)  # keep last 500 entries


def _load_knowledge():
    try:
        with open(KNOWLEDGE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


# ── Narrative builder ─────────────────────────────────────────────────────────

def _build_narrative(symbol, chart_obs, geo_bias, sentiment, knowledge, signal):
    """
    Build a human-readable market narrative the way a trader thinks.
    """
    lines = []

    # 1. What is the market structure saying?
    tf_align   = chart_obs.get("tf_alignment", {})
    momentum_h = chart_obs.get("momentum_1h", {})
    momentum_5 = chart_obs.get("momentum_5m", {})
    bias       = chart_obs.get("overall_bias", "Neutral")

    lines.append(f"📊 Structure: {bias} bias (score={chart_obs.get('bias_score', 0):+.1f})")
    lines.append(f"   1H: {momentum_h.get('direction','?')} | 5M: {momentum_5.get('direction','?')} | {'✅ Aligned' if tf_align.get('aligned') else '⚠️ Diverging'}")

    # 2. Key level tests
    level_tests = chart_obs.get("level_tests", [])
    if level_tests:
        for t in level_tests[:2]:
            lines.append(f"   🎯 {t['description']}")

    # 3. Recent candle patterns
    patterns = chart_obs.get("patterns", [])
    if patterns:
        latest = patterns[-1]
        lines.append(f"   🕯 {latest['pattern']}: {latest['description']}")

    # 4. Geopolitical context
    geo_label = geo_bias.get("bias_label", "Neutral")
    geo_events = geo_bias.get("key_events", [])
    lines.append(f"\n🌍 Geo context: {geo_label}")
    if geo_events:
        lines.append(f"   Events: {', '.join(geo_events[:3])}")

    # 5. What history says about this setup
    asset_knowledge = knowledge.get("assets", {}).get(symbol, {})
    if asset_knowledge:
        wr     = asset_knowledge.get("win_rate")
        streak = asset_knowledge.get("loss_streak", 0)
        best_sess = max(
            asset_knowledge.get("session_perf", {}).items(),
            key=lambda x: x[1].get("win_rate", 0),
            default=(None, {})
        )
        if wr:
            lines.append(f"\n📈 History: {wr:.0%} win rate on {symbol}")
        if streak >= 2:
            lines.append(f"   ⚠️ Loss streak: {streak} — being cautious")
        if best_sess[0]:
            lines.append(f"   Best session: {best_sess[0]} ({best_sess[1].get('win_rate',0):.0%})")

        # Best confluence combos
        best_combos = asset_knowledge.get("best_combos", {})
        if best_combos:
            top_combo = next(iter(best_combos))
            top_wr    = best_combos[top_combo].get("win_rate", 0)
            if top_wr >= 0.6:
                lines.append(f"   🏆 Best combo: {top_combo} ({top_wr:.0%})")

    # 6. Research insights
    research = get_research_bias(symbol)
    if research.get("label") != "Neutral":
        lines.append(f"\n📚 Research: {research['label']} on {symbol}")

    # 7. Signal assessment
    sig_type = signal.get("signal", "BUY")
    conf     = signal.get("confluences", [])
    score    = signal.get("quality_score", 0)
    lines.append(f"\n⚡ Signal: {sig_type} | Score: {score} | Confluences: {len(conf)}")
    lines.append(f"   {', '.join(conf[:3])}")

    return "\n".join(lines)


# ── Conviction scorer ─────────────────────────────────────────────────────────

def _compute_conviction(signal, chart_obs, geo_bias, sentiment, knowledge, symbol):
    """
    Conviction is a 0-10 score that gates signal quality beyond technical score.
    High conviction = multiple independent reasons to take the trade.
    """
    conviction = 0
    reasons    = []
    warnings   = []

    sig_type = signal.get("signal", "BUY")

    # 1. Chart structure alignment
    # Note: MTF filter already verified 1H bias — brain adds nuance, not hard veto
    bias = chart_obs.get("overall_bias", "Neutral")
    if sig_type == "BUY"  and "Bullish" in bias:
        conviction += 2
        reasons.append("Chart structure bullish")
    elif sig_type == "SELL" and "Bearish" in bias:
        conviction += 2
        reasons.append("Chart structure bearish")
    elif "Neutral" in bias:
        conviction += 1   # neutral = slight positive (MTF already validated)
        reasons.append("Chart structure neutral")
    else:
        # Very mild penalty — MTF already validated structure direction
        conviction -= 0.2
        warnings.append("Chart structure divergence")

    # 2. Timeframe alignment — bonus for alignment, no penalty for divergence
    # MTF analysis already handles timeframe checks
    if chart_obs.get("tf_alignment", {}).get("aligned"):
        conviction += 1.5
        reasons.append("1H and 1M aligned")
    else:
        conviction += 0  # neutral — don't penalise

    # 3. Geopolitical alignment
    geo_align = geo_bias.get("signal_align", {})
    geo_score = geo_bias.get("total_bias", 0)

    if geo_align.get(sig_type, True):
        if abs(geo_score) >= 2:
            conviction += 2
            reasons.append(f"Strong geo alignment ({geo_bias['bias_label']})")
        elif abs(geo_score) >= 1:
            conviction += 1
            reasons.append(f"Mild geo support")
    else:
        conviction -= 2
        warnings.append(f"Geo opposes {sig_type}: {geo_bias['bias_label']}")

    # 4. Candle pattern confirmation
    patterns = chart_obs.get("patterns", [])
    matching = [p for p in patterns if p.get("direction") == sig_type]
    if matching:
        conviction += min(2, len(matching))
        reasons.append(f"{matching[-1]['pattern']} pattern confirms")

    # 5. Key level test
    level_tests = chart_obs.get("level_tests", [])
    if level_tests:
        conviction += 1
        reasons.append("Trading at key level")

    # 6. Historical performance
    asset_knowledge = knowledge.get("assets", {}).get(symbol, {})
    if asset_knowledge:
        wr     = asset_knowledge.get("win_rate")
        streak = asset_knowledge.get("loss_streak", 0)
        if wr and wr >= 0.6:
            conviction += 1
            reasons.append(f"Strong historical win rate ({wr:.0%})")
        if streak >= 3:
            conviction -= 2
            warnings.append(f"Loss streak of {streak} — raising bar")

    # 7. Research bias
    research = get_research_bias(symbol)
    res_score = research.get("score", 0)
    if sig_type == "BUY"  and res_score >= 2:
        conviction += 1
        reasons.append("Research bullish")
    elif sig_type == "SELL" and res_score <= -2:
        conviction += 1
        reasons.append("Research bearish")

    # 8. Quality score boost
    q_score = signal.get("quality_score", 0)
    if q_score >= 8:
        conviction += 1
        reasons.append(f"High quality score ({q_score})")

    # 9. Quadrant history — does this symbol+session combo produce BP or BL?
    try:
        from app.services.trading_journal import classify_quadrant
        resolved = _get_quadrant_log()
        session  = signal.get("session", "")

        # Filter to same symbol + same direction
        similar = [s for s in resolved
                   if s.get("symbol") == symbol
                   and s.get("signal") == sig_type
                   and s.get("session", "") == session]

        if len(similar) >= 3:
            bp_count = sum(1 for s in similar
                          if classify_quadrant(s.get("outcome"), s.get("points"),
                             s.get("entry"), s.get("sl"), s.get("tp"))[0] == "BP")
            bl_count = sum(1 for s in similar
                          if classify_quadrant(s.get("outcome"), s.get("points"),
                             s.get("entry"), s.get("sl"), s.get("tp"))[0] == "BL")
            bp_rate = bp_count / len(similar)
            bl_rate = bl_count / len(similar)

            if bp_rate >= 0.4:
                conviction += 1.5
                reasons.append(f"High BP rate in {session} ({bp_rate:.0%})")
            if bl_rate >= 0.2:
                conviction -= 2
                warnings.append(f"BL risk in {session} ({bl_rate:.0%}) — Rule 2")
    except Exception:
        pass

    # 10. RR quality check — Rule 3: only approve if RR >= 2.5
    rr = float(signal.get("rr", 0))
    if rr >= 3.0:
        conviction += 0.5
        reasons.append(f"Excellent RR ({rr}:1)")
    elif rr < 2.0:
        conviction -= 1
        warnings.append(f"Low RR ({rr}:1) — Rule 3 requires 2.5+")

    conviction = max(0, min(10, conviction))

    return {
        "score":    round(conviction, 1),
        "reasons":  reasons,
        "warnings": warnings,
        "label":    "Strong" if conviction >= 7 else "Moderate" if conviction >= 4 else "Weak"
    }


# ── Journal entry ─────────────────────────────────────────────────────────────

def _journal_entry(symbol, signal, narrative, conviction, outcome="PENDING"):
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return {
        "timestamp":  int(datetime.now(timezone.utc).timestamp() * 1000),
        "time_ist":   now_ist.strftime("%Y-%m-%d %H:%M IST"),
        "symbol":     symbol,
        "signal":     signal.get("signal"),
        "entry":      signal.get("entry"),
        "sl":         signal.get("sl"),
        "tp":         signal.get("tp"),
        "conviction": conviction,
        "narrative":  narrative,
        "outcome":    outcome
    }


# ── Main brain function ───────────────────────────────────────────────────────

def think(symbol, signal, chart_obs, sentiment, geo_headlines=None):
    """
    Main entry point. Processes a signal through the full trader brain.
    Returns:
    {
        "conviction":   dict (score, label, reasons, warnings),
        "narrative":    str  (human-readable market analysis),
        "approved":     bool (True = brain endorses this signal),
        "journal_entry": dict
    }
    """
    knowledge  = _load_knowledge()
    geo_bias   = compute_geo_bias(
        geo_headlines or [],
        symbol,
        sentiment.get("fear_greed_score")
    )

    conviction = _compute_conviction(signal, chart_obs, geo_bias, sentiment, knowledge, symbol)
    narrative  = _build_narrative(symbol, chart_obs, geo_bias, sentiment, knowledge, signal)

    # Brain approves if conviction >= 4 (Moderate or Strong)
    approved   = conviction["score"] >= 4

    entry = _journal_entry(symbol, signal, narrative, conviction)

    # Save to journal
    journal = _load_journal()
    journal.insert(0, entry)
    _save_journal(journal)

    print(f"[Brain] {symbol} {signal.get('signal')} — Conviction: {conviction['score']}/10 ({conviction['label']}) | Approved: {approved}")
    if conviction["reasons"]:
        print(f"[Brain]   ✅ {' | '.join(conviction['reasons'][:3])}")
    if conviction["warnings"]:
        print(f"[Brain]   ⚠️  {' | '.join(conviction['warnings'][:2])}")

    return {
        "conviction":    conviction,
        "narrative":     narrative,
        "approved":      approved,
        "geo_bias":      geo_bias,
        "journal_entry": entry
    }