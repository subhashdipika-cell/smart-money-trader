"""
telegram_service.py
-------------------
Sends trade signals to Telegram using synchronous requests.
Uses requests library instead of async to avoid event loop issues.
"""

import requests
import os
import json

# ── Config ────────────────────────────────────────────────────────────────────
# Priority: app_settings.json (editable from the UI Settings modal) → env vars.
# No credentials are hardcoded here.
_DEFAULT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_DEFAULT_CHAT  = os.getenv("TELEGRAM_CHAT_ID",   "")

SETTINGS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "app_settings.json")
)


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_telegram_config():
    """Current bot token + chat id (settings file wins, re-read every call)."""
    s = _load_settings()
    token = (s.get("telegram_bot_token") or "").strip() or _DEFAULT_TOKEN
    chat  = str(s.get("telegram_chat_id") or "").strip() or _DEFAULT_CHAT
    return token, chat


# Kept for backwards compatibility with any old imports
BOT_TOKEN, CHAT_ID = get_telegram_config()

# Stamped on every alert so this bot's messages are distinguishable from the
# other MT5 apps that post to the same Telegram chat.
APP_NAME = "Smart Money Trader"
_BANNER  = f"📡 <b>{APP_NAME}</b>"


def send_alert(message: str) -> bool:
    """
    Send a message to Telegram using synchronous requests.
    Every message is prefixed with the app-name banner (idempotent).
    Returns True if successful, False otherwise.
    """
    token, chat_id = get_telegram_config()
    if not message.lstrip().startswith(_BANNER):
        message = f"{_BANNER}\n\n{message}"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML"
            },
            timeout=10
        )
        if response.status_code == 200:
            print(f"[Telegram] ✅ Alert sent successfully")
            return True
        else:
            print(f"[Telegram] ❌ Failed: {response.status_code} — {response.text[:100]}")
            return False
    except requests.exceptions.Timeout:
        print("[Telegram] ❌ Timeout — message not sent")
        return False
    except Exception as e:
        print(f"[Telegram] ❌ Error: {e}")
        return False


def send_test_message() -> bool:
    """Send a test message to verify Telegram connection."""
    return send_alert(
        "✅ Telegram connection test successful!\n"
        "You will receive trade signals here."
    )