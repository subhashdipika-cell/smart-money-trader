"""
sessions.py — one definition of "which trading session was this?"

There were three near-copies of this logic (strategy_learner, self_improvement,
trading_journal) and they had already drifted: the first two use
Asia/London/New York/Off-session, while trading_journal uses different
boundaries AND different labels (Asian, London/NY overlap, Late US). A fourth
copy in trader_brain is what this module exists to prevent.

The canonical taxonomy here is the strategy_learner/self_improvement one — it
is what the learning stack already scores against, and on the current clean
data it puts 78% of resolved trades into a symbol+direction+session cell with
enough samples to be usable (vs 75% for the journal's 5-bucket split).

trading_journal keeps its own labels deliberately: they are already persisted
inside trader_journal.json, so re-bucketing them would silently reinterpret
historical entries. Its session strings are for display, not for matching.

IMPORTANT — derive, never trust. Signals carry a `session` field set by the
strategy that produced them, but most set the literal "Unknown" and only
london_breakout_live sets a real value. Worse, the logged-record whitelist in
live_signal_service never persisted the field at all, so every historical
signal reads "". Matching a live signal's "Unknown" against a stored "" is why
trader_brain's quadrant block silently never fired. Both sides must be derived
from the timestamp through this module.
"""
from datetime import datetime, timezone

from app.services.clean_data import signal_ts_ms

UNKNOWN = "Unknown"


def session_from_ts(ts_ms):
    """Canonical UTC trading session for a millisecond timestamp.

    Returns UNKNOWN rather than raising — callers use this to bucket data and
    must be able to skip a record whose time is unusable.
    """
    try:
        ts = int(ts_ms)
    except (TypeError, ValueError):
        return UNKNOWN
    # A zero/negative stamp means "missing", not 1970 — without this it would
    # silently bucket as Asia (epoch hour 0) and pollute that session's stats.
    if ts <= 0:
        return UNKNOWN
    try:
        h = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour
    except (TypeError, ValueError, OverflowError, OSError):
        return UNKNOWN
    if h < 7:
        return "Asia"
    if h < 12:
        return "London"
    if h < 20:
        return "New York"
    return "Off-session"


def session_of_signal(sig, now_ms=None):
    """Session for a signal dict, live or logged.

    Uses the signal's own timestamp when it has one. A live signal is often not
    stamped until it is written to the log, so fall back to the current time —
    for a signal being evaluated right now that IS its session. Any `session`
    string already on the dict is ignored on purpose (see the module docstring).
    """
    ts = signal_ts_ms(sig)
    if not ts:
        ts = now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000
    return session_from_ts(ts)
