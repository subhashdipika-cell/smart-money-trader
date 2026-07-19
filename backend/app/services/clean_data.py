"""
clean_data.py — the single definition of "which signals can be trusted".

Outcomes recorded BEFORE the resolver fix (2026-07-05) are corrupted: the old
2h pending expiry + missing entry_hit persistence zeroed a FILLED trade to
EXPIRED at 8h, so anything slow was written off as a no-fill. Those verdicts
are terminal — nothing re-resolved them after the rules were retuned.

Replaying all 380 logged signals against real 1m candles (2026-07-19) measured
the damage: 83 of the 237 pre-fix EXPIRED signals had actually filled and
resolved (56 losses, 27 wins), and 0 of the 48 post-fix ones were wrong. The
error is MISSING DATA rather than bad values — the 83 sit as EXPIRED/0 pts, so
an `outcome in ("WIN","LOSS")` filter drops them silently. Consumers reading
the raw log see 92 resolved at an 18% win rate where the truth is 168 at 26%
(EMA20_Pullback 9%->17%, HTF_ICT 13%->24%, NONE 50%->31%).

So: anything that learns from `signals_log.json` must read it through here.
The constant lived only in strategy_learner.py, which meant learning_engine.py
and strategy_selector.py could — and did — read the same file unfiltered.

Related: strategy-lab/smt_window_scan.py in the AlphaEdge repo does the replay
and prints the era split.
"""
import json

# 2026-07-05 00:00 UTC — the resolver fix. Signals at or after this are trusted.
CLEAN_DATA_EPOCH_MS = 1783209600000


def signal_ts_ms(sig):
    """Timestamp in ms, tolerating the seconds-vs-ms inconsistency in the log."""
    try:
        t = int(sig.get("timestamp", 0))
    except Exception:
        return 0
    return t if t > 1e12 else t * 1000


def filter_clean(log):
    """Drop signals predating the resolver fix."""
    return [s for s in log if signal_ts_ms(s) >= CLEAN_DATA_EPOCH_MS]


def load_clean_log(path):
    """Read a signals log and return only the trustworthy epoch. Never raises —
    callers treat an unreadable log as 'no data yet' and fall back to defaults."""
    try:
        with open(path, "r") as f:
            log = json.load(f)
    except Exception:
        return []
    return filter_clean(log)
