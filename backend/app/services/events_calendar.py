"""
events_calendar.py
------------------
Fetches ForexFactory economic calendar at day start,
stores today's high-impact events with exact IST times,
and checks if current time is near a market-moving event.

Runs once per day at startup and midnight.
Saves to daily_events.json for use by sentiment service.
"""

import json
import os
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

_BASE             = os.path.join(os.path.dirname(__file__), "..", "..")
EVENTS_FILE       = os.path.abspath(os.path.join(_BASE, "daily_events.json"))
PAUSE_BEFORE_MINS = 15   # pause signals 15 min before event
PAUSE_AFTER_MINS  = 20   # resume signals 20 min after event

# High-impact event keywords to watch
HIGH_IMPACT_KEYWORDS = [
    "powell", "bessent", "lagarde", "fed", "fomc", "boe", "ecb", "rba", "rbnz",
    "rate decision", "rate statement", "interest rate",
    "nfp", "non-farm", "employment",
    "cpi", "inflation", "pce",
    "gdp", "gross domestic",
    "retail sales", "ism",
    "treasury", "secretary",
    "press conference", "speech", "speaks",
    "tokyo cpi", "core cpi",
    "german", "eurozone"
]

# ForexFactory RSS feed
FF_RSS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _parse_ff_xml(raw_xml):
    """Parse ForexFactory XML calendar and extract today's events."""
    events = []
    today_ist = _ist_now().date()

    try:
        root = ET.fromstring(raw_xml)
        for item in root.iter("event"):
            title   = item.findtext("title", "").strip()
            country = item.findtext("country", "").strip()
            impact  = item.findtext("impact", "").strip().lower()
            date_str = item.findtext("date", "").strip()
            time_str = item.findtext("time", "").strip()

            if impact not in ("high", "medium"):
                continue

            # Parse event date/time
            try:
                # ForexFactory uses format like "05-28-2026" and "1:30am"
                dt_str  = f"{date_str} {time_str}"
                # Try different formats
                for fmt in ["%m-%d-%Y %I:%M%p", "%m-%d-%Y %I:%M %p", "%Y-%m-%d %H:%M"]:
                    try:
                        dt_utc  = datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
                        dt_ist  = dt_utc + timedelta(hours=5, minutes=30)
                        if dt_ist.date() == today_ist:
                            title_lower = title.lower()
                            is_high = any(k in title_lower for k in HIGH_IMPACT_KEYWORDS)
                            events.append({
                                "title":      title,
                                "country":    country,
                                "impact":     impact,
                                "time_utc":   dt_utc.strftime("%H:%M"),
                                "time_ist":   dt_ist.strftime("%H:%M"),
                                "timestamp":  int(dt_utc.timestamp()),
                                "is_high_impact": is_high or impact == "high"
                            })
                        break
                    except ValueError:
                        continue
            except Exception:
                continue
    except ET.ParseError:
        pass

    return events


def fetch_and_store_today_events():
    """
    Fetch today's economic calendar events and save to file.
    Called once at startup and once at midnight.
    """
    today_ist = _ist_now().strftime("%Y-%m-%d")
    print(f"[Calendar] Fetching today's economic events ({today_ist} IST)...")

    events = []

    # Try ForexFactory RSS
    try:
        req = urllib.request.Request(
            FF_RSS_URL,
            headers={"User-Agent": "SmartMoneyTrader/1.0 Calendar Bot"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="ignore")
        events = _parse_ff_xml(raw)
        print(f"[Calendar] {len(events)} events fetched for today")
    except Exception as e:
        print(f"[Calendar] RSS fetch failed: {e}")

    # Fallback: use known high-impact events from news if RSS fails
    # (These get populated from sentiment headlines)

    high_impact = [e for e in events if e["is_high_impact"]]
    normal      = [e for e in events if not e["is_high_impact"]]

    if high_impact:
        print(f"[Calendar] ⚠️  {len(high_impact)} HIGH-IMPACT events today:")
        for e in high_impact:
            print(f"  {e['time_ist']} IST — {e['country']} {e['title']}")

    data = {
        "date":         today_ist,
        "events":       events,
        "high_impact":  high_impact,
        "fetched_at":   _ist_now().strftime("%H:%M IST"),
        "total":        len(events)
    }

    with open(EVENTS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return data


def load_today_events():
    """Load today's stored events. Re-fetch if stale (different date)."""
    try:
        with open(EVENTS_FILE) as f:
            data = json.load(f)
        today = _ist_now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            # New day — fetch fresh
            return fetch_and_store_today_events()
        return data
    except Exception:
        return fetch_and_store_today_events()


def is_high_impact_window():
    """
    Returns (is_paused, reason) — True if we're within the pause window
    of a high-impact economic event.
    """
    now_ts  = int(time.time())
    now_ist = _ist_now()

    try:
        data   = load_today_events()
        events = data.get("high_impact", [])
    except Exception:
        return False, None

    for event in events:
        event_ts = event.get("timestamp", 0)
        if not event_ts:
            continue

        secs_to_event   = event_ts - now_ts
        secs_after_event = now_ts - event_ts

        # Before event window
        if 0 < secs_to_event <= PAUSE_BEFORE_MINS * 60:
            mins = int(secs_to_event / 60)
            reason = f"{event['title']} in {mins} min ({event['time_ist']} IST)"
            return True, reason

        # After event window
        if 0 < secs_after_event <= PAUSE_AFTER_MINS * 60:
            mins = int(secs_after_event / 60)
            reason = f"{event['title']} ended {mins} min ago — market settling"
            return True, reason

    return False, None


def add_headline_event(title, time_ist_str=None):
    """
    Manually add a high-impact event detected from news headlines.
    Used as fallback when RSS doesn't have the event.
    """
    try:
        data   = load_today_events()
        today  = _ist_now().strftime("%Y-%m-%d")

        # Avoid duplicates
        existing = [e["title"].lower() for e in data.get("high_impact", [])]
        if title.lower() in existing:
            return

        now_ist = _ist_now()
        event = {
            "title":          title,
            "country":        "US",
            "impact":         "high",
            "time_utc":       (now_ist - timedelta(hours=5, minutes=30)).strftime("%H:%M"),
            "time_ist":       time_ist_str or now_ist.strftime("%H:%M"),
            "timestamp":      int(time.time()),
            "is_high_impact": True,
            "source":         "headline_detection"
        }

        data["high_impact"].append(event)
        data["events"].append(event)

        with open(EVENTS_FILE, "w") as f:
            json.dump(data, f, indent=2)

        print(f"[Calendar] Added from headlines: {title}")
    except Exception as e:
        print(f"[Calendar] Could not add event: {e}")
