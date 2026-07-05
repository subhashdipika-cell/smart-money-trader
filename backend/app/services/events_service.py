"""
events_service.py
-----------------
Fetches this week's economic calendar events from ForexFactory's
public JSON feed (free, no API key needed).

Filters for HIGH impact events relevant to crypto/gold:
  USD, EUR, GBP, JPY, CNY, XAU
"""

import urllib.request
import json
from datetime import datetime, timezone, timedelta

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Currencies that affect our markets
RELEVANT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CNY", "XAU"}
RELEVANT_IMPACTS    = {"High", "Medium"}


def _fetch_raw():
    req = urllib.request.Request(
        FF_URL,
        headers={"User-Agent": "SmartMoneyTrader/1.0"}
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _to_ist(utc_str):
    """Convert ForexFactory UTC datetime string to IST."""
    try:
        dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_ist = dt_utc + timedelta(hours=5, minutes=30)
        return dt_ist.strftime("%d %b %H:%M IST"), dt_ist
    except Exception:
        return utc_str, None


def get_market_events(max_events=20):
    """
    Returns list of upcoming relevant events, sorted by time.
    Each event: { title, currency, impact, datetime_ist, datetime_obj, is_upcoming }
    """
    try:
        raw = _fetch_raw()
    except Exception as e:
        if "429" in str(e):
            print(f"[Events] Rate limited — using cached data")
        else:
            print(f"[Events] Fetch failed: {e}")
        return []

    now_utc = datetime.now(timezone.utc)
    events  = []

    for item in raw:
        currency = item.get("country", "").upper()
        impact   = item.get("impact",  "").capitalize()

        if currency not in RELEVANT_CURRENCIES:
            continue
        if impact not in RELEVANT_IMPACTS:
            continue

        title    = item.get("title", "Unknown event")
        date_str = item.get("date",  "")
        ist_str, dt_obj = _to_ist(date_str)

        is_upcoming = dt_obj is not None and dt_obj.replace(tzinfo=timezone.utc) > now_utc

        events.append({
            "title":        title,
            "currency":     currency,
            "impact":       impact,
            "datetime_ist": ist_str,
            "datetime_raw": date_str,
            "is_upcoming":  is_upcoming,
            "minutes_away": int((dt_obj.replace(tzinfo=timezone.utc) - now_utc).total_seconds() / 60)
                            if dt_obj and is_upcoming else None
        })

    # Sort: upcoming first, then past
    events.sort(key=lambda e: (not e["is_upcoming"], e.get("minutes_away") or 99999))

    return events[:max_events]
