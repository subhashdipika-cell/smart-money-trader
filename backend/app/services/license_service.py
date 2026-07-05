"""
license_service.py
------------------
Offline activation-code licensing.

Code format:  SMT-{6M|12M}-{YYYYMMDD}-{SIGNATURE}
  • 6M / 12M    — license duration from the day of activation
  • YYYYMMDD    — date the code was ISSUED (generated)
  • SIGNATURE   — HMAC-SHA256 over "duration|issue_date", first 10 hex chars

Rules:
  • A code must be activated within 30 days of being issued.
  • Expiry = activation date + duration.
  • A code already used on this machine cannot be activated twice.

Codes are produced by the PRIVATE generator script (smt_license_generator.py)
which shares the secret below. Keep the generator out of the shipped app.
"""

import os
import json
import hmac
import hashlib
from datetime import datetime, timedelta, date

# Must match the secret inside smt_license_generator.py
_SECRET = b"SMT-2026-LICENSE-K3y!-subhash-9d4f7a1c"

LICENSE_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "license.json")
)

ACTIVATION_WINDOW_DAYS = 30          # code must be redeemed within this window
_DURATIONS = {"6M": 6, "12M": 12}    # label → months


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:10].upper()


def _load() -> dict:
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                        else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def parse_code(code: str):
    """Validate format + signature. Returns (months, issued: date). Raises ValueError."""
    parts = (code or "").strip().upper().replace(" ", "").split("-")
    if len(parts) != 4 or parts[0] != "SMT":
        raise ValueError("Invalid code format — expected SMT-6M-YYYYMMDD-XXXXXXXXXX")
    dur_label, issued_str, sig = parts[1], parts[2], parts[3]
    if dur_label not in _DURATIONS:
        raise ValueError("Invalid duration in code (must be 6M or 12M)")
    try:
        issued = datetime.strptime(issued_str, "%Y%m%d").date()
    except ValueError:
        raise ValueError("Invalid issue date in code")
    if not hmac.compare_digest(_sign(f"{dur_label}|{issued_str}"), sig):
        raise ValueError("Invalid activation code (bad signature)")
    return _DURATIONS[dur_label], issued


def get_status() -> dict:
    lic = _load()
    expires = lic.get("expires_on")
    if not expires:
        return {"activated": False, "reason": "not_activated"}
    try:
        exp_date = datetime.strptime(expires, "%Y-%m-%d").date()
    except ValueError:
        return {"activated": False, "reason": "corrupt_license"}
    today = date.today()
    if today > exp_date:
        return {"activated": False, "reason": "expired",
                "expired_on": expires, "activated_on": lic.get("activated_on")}
    return {
        "activated":     True,
        "activated_on":  lic.get("activated_on"),
        "expires_on":    expires,
        "days_left":     (exp_date - today).days,
        "duration":      lic.get("duration"),
    }


def activate(code: str) -> dict:
    months, issued = parse_code(code)

    today = date.today()
    if issued > today + timedelta(days=1):
        raise ValueError("Code issue date is in the future — check your system clock.")
    if today > issued + timedelta(days=ACTIVATION_WINDOW_DAYS):
        raise ValueError(f"This code expired unused — it had to be activated within "
                         f"{ACTIVATION_WINDOW_DAYS} days of {issued.isoformat()}.")

    lic  = _load()
    used = set(lic.get("used_codes", []))
    code_hash = hashlib.sha256(code.strip().upper().encode()).hexdigest()[:16]
    if code_hash in used:
        raise ValueError("This code has already been used on this machine.")

    expires = _add_months(today, months)
    used.add(code_hash)
    _save({
        "activated_on": today.isoformat(),
        "expires_on":   expires.isoformat(),
        "duration":     f"{months} months",
        "used_codes":   sorted(used),
    })
    return get_status()
