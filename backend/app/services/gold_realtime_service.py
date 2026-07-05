"""
gold_realtime_service.py
─────────────────────────
Real-time XAUUSD price feed — sourced directly from your MetaTrader 5 terminal.

Why MT5 instead of a third-party WebSocket?
─────────────────────────────────────────────
• 100% free, forever — no API key, no trial, no rate limit.
• Exact Vantage Markets bid/ask — same numbers you see on your chart.
• Zero network latency — data comes from the terminal running on this machine.
• MT5 library is already used in trading_executor.py, so no new dependency.

Requirements
────────────
• MT5 terminal must be open and logged in (it needs to be for trading anyway).
• The symbol "XAUUSD+" must be visible in Market Watch.
  If it isn't: right-click Market Watch → Show All → find XAUUSD+.

Architecture
────────────
• Runs a background thread that polls mt5.symbol_info_tick() every 500 ms.
• Writes to a shared _cache dict — zero-latency reads from any other module.
• get_live_price() / get_latest_tick() are the public read API.
• Starts automatically at FastAPI startup via start_gold_feed().
• Falls back gracefully if MT5 is not initialised (gold_service.py then
  uses Yahoo Finance as the fallback price source).

IMPORTANT — never call mt5.login() or mt5.shutdown() here.
Those calls disconnect the terminal session. We piggy-back on the
account that the user has already logged into in the MT5 app.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger("gold_rt")

# Vantage Markets uses "XAUUSD+" as the Gold symbol in MT5.
# If your broker uses plain "XAUUSD", change this value.
MT5_GOLD_SYMBOL  = "XAUUSD+"
POLL_INTERVAL_S  = 0.5      # seconds between tick fetches (500 ms)


# ── Shared price cache ────────────────────────────────────────────────────────

_cache: dict = {
    "bid":        None,
    "ask":        None,
    "last":       None,
    "spread":     None,
    "updated_at": None,
    "source":     "none",       # "mt5" | "none"
    "connected":  False,
    "status":     "stopped",    # "stopped" | "connecting" | "live" | "error"
    "error":      None,
}
_cache_lock = threading.Lock()


def _update_cache(**kwargs):
    with _cache_lock:
        _cache.update(kwargs)


def get_latest_tick() -> dict:
    """Thread-safe snapshot of the latest Gold tick."""
    with _cache_lock:
        return dict(_cache)


def get_live_price() -> Optional[float]:
    """
    Returns the mid-price if the MT5 feed is live and fresh (< 10 s old).
    Returns None if MT5 is not connected — caller falls back to Yahoo Finance.
    """
    with _cache_lock:
        if _cache["status"] == "live" and _cache["last"] is not None:
            age = time.time() - (_cache.get("updated_at") or 0)
            if age < 10:
                return float(_cache["last"])
    return None


# ── Background polling thread ─────────────────────────────────────────────────

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def _poll_loop():
    """
    Polls mt5.symbol_info_tick(MT5_GOLD_SYMBOL) every POLL_INTERVAL_S seconds.
    Handles MT5 not being ready yet (e.g. app starts before terminal opens)
    by retrying silently every 10 seconds until it connects.
    """
    mt5 = None

    while not _stop_event.is_set():

        # ── Step 1: ensure MT5 is initialised ────────────────────────────────
        if mt5 is None:
            try:
                import MetaTrader5 as _mt5
                mt5 = _mt5
            except ImportError:
                _update_cache(
                    status="error",
                    error="MetaTrader5 package not installed. Run: pip install MetaTrader5",
                )
                log.error("[GoldRT] MetaTrader5 package not installed.")
                _stop_event.wait(30)
                continue

        # ── Step 2: call initialize() if not already done ────────────────────
        #    (Never login/shutdown — reuse the terminal session)
        if not mt5.initialize(path=r"C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe"):
            err = mt5.last_error()
            _update_cache(
                status="connecting",
                connected=False,
                error=f"MT5 not ready: {err}. Open terminal and login.",
            )
            _stop_event.wait(10)   # wait 10 s before retrying
            continue

        # ── Step 3: make sure the symbol is visible in Market Watch ──────────
        if not mt5.symbol_select(MT5_GOLD_SYMBOL, True):
            _update_cache(
                status="error",
                connected=False,
                error=f"Symbol {MT5_GOLD_SYMBOL!r} not found. Check Market Watch.",
            )
            _stop_event.wait(10)
            continue

        # ── Step 4: poll ticks ────────────────────────────────────────────────
        _update_cache(status="live", connected=True, error=None)
        log.info("[GoldRT] MT5 connected. Streaming %s ticks…", MT5_GOLD_SYMBOL)

        consecutive_errors = 0

        while not _stop_event.is_set():
            try:
                tick = mt5.symbol_info_tick(MT5_GOLD_SYMBOL)

                if tick is None:
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        # MT5 terminal probably closed
                        _update_cache(
                            status="connecting",
                            connected=False,
                            error="No tick data. MT5 may have disconnected.",
                        )
                        log.warning("[GoldRT] No tick from MT5 — retrying outer loop.")
                        break   # break inner loop → re-initialise in outer loop
                    _stop_event.wait(POLL_INTERVAL_S)
                    continue

                consecutive_errors = 0
                bid  = float(tick.bid)
                ask  = float(tick.ask)
                last = (bid + ask) / 2   # mid-price

                _update_cache(
                    bid=round(bid, 3),
                    ask=round(ask, 3),
                    last=round(last, 3),
                    spread=round(ask - bid, 3),
                    updated_at=time.time(),
                    source="mt5",
                    connected=True,
                    status="live",
                    error=None,
                )

            except Exception as exc:
                log.warning("[GoldRT] Tick fetch error: %s", exc)
                _update_cache(connected=False, status="connecting", error=str(exc))
                break   # re-enter outer loop to re-initialise

            _stop_event.wait(POLL_INTERVAL_S)

    _update_cache(status="stopped", connected=False)
    log.info("[GoldRT] Feed stopped.")


# ── Public lifecycle API ──────────────────────────────────────────────────────

def start_gold_feed():
    """
    Start the MT5 tick-polling thread.
    Called once at FastAPI startup — safe to call multiple times (idempotent).
    """
    global _thread

    if _thread and _thread.is_alive():
        return   # already running

    _stop_event.clear()
    _thread = threading.Thread(
        target=_poll_loop,
        daemon=True,
        name="gold-mt5-feed",
    )
    _thread.start()
    log.info("[GoldRT] MT5 Gold feed thread started (symbol: %s).", MT5_GOLD_SYMBOL)
    print(f"[Startup] ✅ Gold realtime feed started — polling {MT5_GOLD_SYMBOL} from MT5")


def stop_gold_feed():
    """Signal the polling loop to exit cleanly."""
    _stop_event.set()
    log.info("[GoldRT] Stop signal sent.")
