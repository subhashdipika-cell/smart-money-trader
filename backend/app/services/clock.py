"""
clock.py — one clock for the whole app.

THE RULE: UTC internally, IST at the display boundary. Never store, compare or
bucket on IST.

Why not IST throughout, given the desk is in India? Three reasons:

  1. IST is UTC+5:30. Trading sessions (Asia/London/New York) are anchored to
     UTC hours, so in IST every session boundary lands on :30 and an hourly
     bucket straddles two sessions. See sessions.py, which buckets on UTC.
  2. Every feed is UTC-anchored — Binance epoch ms, MT5 normalised from its
     UTC+3 server clock, `sent_at` in signals_log. Reinterpreting stored
     timestamps as IST would silently rewrite history.
  3. IST midnight is 18:30 UTC, i.e. mid-New-York. Grouping "by day" in IST
     splits a NY session across two days.

Before this module there were 24 hand-rolled `timedelta(hours=5, minutes=30)`
conversions across 13 files, and most of them were the naive-shift
anti-pattern:

    datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

That returns a datetime whose tzinfo still says UTC while its wall clock says
IST. strftime() looks correct, so the bug hides — but .timestamp() on the same
object is wrong by 5.5 hours. The helpers here return properly tz-aware IST
datetimes, which format identically and stay correct under arithmetic.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")

DATE_FMT     = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M"


def _to_utc_dt(ts):
    """Coerce epoch seconds, epoch ms, or a datetime into a tz-aware UTC datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    val = float(ts)
    if val > 1e12:          # milliseconds
        val /= 1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc)


def now_utc():
    """Current time, tz-aware UTC. Use this for anything stored or compared."""
    return datetime.now(timezone.utc)


def now_ms():
    """Current time as epoch milliseconds — the canonical internal stamp."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def now_ist():
    """Current time, tz-aware IST. Display only."""
    return datetime.now(timezone.utc).astimezone(IST)


def to_ist(ts):
    """Convert epoch seconds/ms or a datetime to a tz-aware IST datetime."""
    return _to_utc_dt(ts).astimezone(IST)


def ist_str(ts, fmt=DATETIME_FMT, fallback="—"):
    """Format a timestamp as an IST string. Returns `fallback` when the input is
    unusable — callers are formatting for humans and must not raise."""
    if ts is None:
        return fallback
    try:
        # A zero/negative stamp means "missing", not 1970 — matching sessions.py
        # rather than rendering a confident-looking 1970-01-01 05:30.
        if not isinstance(ts, datetime) and float(ts) <= 0:
            return fallback
        return to_ist(ts).strftime(fmt)
    except (TypeError, ValueError, OverflowError, OSError):
        return fallback


def ist_date(ts=None, fallback="—"):
    """IST calendar date (YYYY-MM-DD). Defaults to today in IST."""
    if ts is None:
        return now_ist().strftime(DATE_FMT)
    return ist_str(ts, DATE_FMT, fallback)
