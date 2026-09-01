from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
import os
import json
from datetime import datetime, timezone, timedelta
from threading import Thread

from app.services.binance_service    import get_multi_timeframe_data as binance_get_mtf
from app.services.binance_service    import get_historical_multi_timeframe_data as binance_get_hist
from app.services.binance_service    import get_recent_candles_df
from app.services.gold_service       import (
    get_multi_timeframe_data       as gold_get_mtf,
    get_historical_multi_timeframe_data as gold_get_hist,
    get_current_price              as gold_get_price
)
from app.services.live_signal_service import start_live_signal_engine
from app.strategies.market_structure  import (
    detect_swings, detect_bos_choch, detect_fvg,
    detect_liquidity_sweeps, detect_order_blocks
)
from app.strategies.mtf_analysis     import analyze_multi_timeframe
from app.backtests.simple_backtest   import run_backtest
from app.services.telegram_service   import send_alert
from app.services.strategy_learner   import load_weights
from app.services.web_researcher      import load_research
from app.services.self_improvement    import load_suggestions
from app.services.events_calendar     import load_today_events, is_high_impact_window
from app.services.trading_journal     import get_quadrant_stats, get_monthly_journal_summary, export_monthly_to_obsidian
from app.services.strategy_selector   import analyse_strategies
from app.services.clock import now_ist as _clock_now_ist, ist_date, ist_str
from app.services.geo_strategy        import get_all_asset_biases
from app.services.sentiment_service  import get_sentiment
from app.services.events_service     import get_market_events

app = FastAPI()

# The dashboard may request a graceful engine stop.  It is deliberately limited
# to the local SMT dashboard origins and a non-simple request header so a web
# page cannot silently stop the trading engine through a browser request.
_DASHBOARD_SHUTDOWN_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
}

SUPPORTED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "XAUUSD"}
BINANCE_SYMBOLS   = {"BTCUSDT", "ETHUSDT"}
GOLD_SYMBOLS      = {"XAUUSD"}

_BASE    = os.path.join(os.path.dirname(__file__), "..")
LOG_FILE = os.path.abspath(os.path.join(_BASE, "signals_log.json"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Cache for Gold data — Yahoo Finance is slow and rate-limited
_gold_cache = {"data": None, "ts": 0}

# Per-symbol cache for root endpoint — frontend refreshes every 10s
# but market data only needs refresh every 30s
_market_cache = {}  # {symbol: {"data": ..., "ts": ...}}
_MARKET_CACHE_TTL = 30  # seconds

def fetch_mtf(symbol):
    """Route to correct data source. Gold has 60s cache."""""
    import time
    if symbol in GOLD_SYMBOLS:
        now = time.time()
        if _gold_cache["data"] and now - _gold_cache["ts"] < 60:
            return _gold_cache["data"]
        data = gold_get_mtf(symbol)
        _gold_cache["data"] = data
        _gold_cache["ts"]   = now
        return data
    return binance_get_mtf(symbol)


def fetch_historical_mtf(symbol, days, intervals=None):
    """intervals: list of timeframes to fetch (e.g. ["1h"]). None = all."""
    if symbol in GOLD_SYMBOLS:
        return gold_get_hist(symbol, days)
    return binance_get_hist(symbol, days, intervals=intervals)


def _ema_exit_loop():
    """
    Background thread: every 5 minutes, check active EMA-strategy positions
    and close any that have had price cross back through the 20 EMA.
    Only fires on trades whose setup tag contains 'EMA'.
    """
    import time as _time
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from trading_executor import run_ema_exit_monitor
    CHECK_INTERVAL = 5 * 60  # 5 minutes
    while True:
        try:
            result = run_ema_exit_monitor()
            if result.get("closed"):
                print(f"[EMAMonitor] Closed {len(result['closed'])} trade(s) on EMA cross: "
                      + str([c["ticket"] for c in result["closed"]]))
        except Exception as e:
            print(f"[EMAMonitor] Error: {e}")
        _time.sleep(CHECK_INTERVAL)


def _order_expiry_loop():
    """
    Background thread: every 10 minutes, cancel pending orders that have
    exceeded their type-specific timeout.

    Timeouts (read from each trade's 'setup' field):
      scalp     → 2 hours
      intraday  → 8 hours  (default)
      swing     → 48 hours
    """
    import time as _time
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from trading_executor import cancel_expired_orders
    CHECK_INTERVAL = 10 * 60  # 10 minutes
    while True:
        try:
            result = cancel_expired_orders()  # None → smart per-trade timeout
            if result.get("cancelled"):
                print(f"[AutoExpiry] Cancelled {len(result['cancelled'])} expired order(s): "
                      + ", ".join(str(c.get("ticket")) for c in result["cancelled"]))
        except Exception as e:
            print(f"[AutoExpiry] Error: {e}")
        _time.sleep(CHECK_INTERVAL)


def _gold_friday_loop():
    """
    Background thread: every 2 minutes, run the Friday gold cutoff — from
    22:15 IST on Fridays it closes SMT's open XAU positions and cancels SMT's
    pending XAU orders (magic-filtered; the Vantage terminal is shared).
    Vantage closes XAUUSD+ at 22:30 IST on US-holiday Fridays without notice.
    """
    import time as _time
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from trading_executor import flatten_gold_friday
    CHECK_INTERVAL = 2 * 60  # 2 minutes — tight enough for a 15-min window
    while True:
        try:
            result = flatten_gold_friday()
            if result.get("closed") or result.get("cancelled"):
                print(f"[FriGoldCutoff] closed {result['closed']} cancelled {result['cancelled']}")
        except Exception as e:
            print(f"[FriGoldCutoff] Error: {e}")
        _time.sleep(CHECK_INTERVAL)


def _keep_awake_loop():
    """Hold the machine out of Modern Standby while the engine runs.

    The 2026-07-21/22 outages: the laptop's on-battery sleep timer (10 min)
    suspended the fleet dozens of times, so 1h-bar cross events closed and
    vanished unseen. Same pattern the AlphaEdge strategy-lab already uses.
    ES_SYSTEM_REQUIRED keeps the system awake; the display may still sleep.
    Process-scoped: released automatically when the backend exits.
    """
    import ctypes
    import time as _time
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    while True:
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception as e:
            print(f"[KeepAwake] failed: {e}")
            return
        _time.sleep(60)   # re-assert — cheap, and survives odd resume states


@app.on_event("startup")
def startup_event():
    if os.getenv("START_LIVE_ENGINE", "true").lower() != "true":
        return
    Thread(target=_keep_awake_loop, daemon=True, name="keep-awake").start()
    print("[Startup] Keep-awake ON — system held out of standby while engine runs.")
    Thread(target=start_live_signal_engine, daemon=True).start()
    Thread(target=_order_expiry_loop, daemon=True).start()
    Thread(target=_ema_exit_loop, daemon=True).start()
    Thread(target=_gold_friday_loop, daemon=True).start()
    print("[Startup] ✅ Order expiry monitor started (checks every 10 min)")
    print("[Startup] ✅ EMA-cross exit monitor started (checks every 5 min)")

    # ── AllTick real-time Gold feed (no-op if token not configured) ───────────
    try:
        from app.services.gold_realtime_service import start_gold_feed
        start_gold_feed()
    except Exception as _e:
        print(f"[Startup] ⚠️  Gold realtime feed skipped: {_e}")


@app.on_event("shutdown")
def shutdown_event():
    try:
        from app.services.gold_realtime_service import stop_gold_feed
        stop_gold_feed()
    except Exception:
        pass


def _gracefully_stop_engine():
    """Disconnect local integrations, then terminate this backend process.

    This intentionally leaves existing MT5 positions untouched and never closes
    the MT5 desktop terminal.  Orders retain their broker-side SL/TP protection.
    """
    import time as _time
    _time.sleep(0.35)  # allow the HTTP 202 response to reach the dashboard
    try:
        from app.services.gold_realtime_service import stop_gold_feed
        stop_gold_feed()
    except Exception:
        pass
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass
    os._exit(0)


@app.post("/system/stop", status_code=202)
def system_stop(request: Request):
    """Gracefully stop the local SMT engine at the user's dashboard request."""
    origin = request.headers.get("origin")
    if origin not in _DASHBOARD_SHUTDOWN_ORIGINS:
        raise HTTPException(status_code=403, detail="Local dashboard origin required")
    if request.headers.get("x-smt-shutdown") != "confirm":
        raise HTTPException(status_code=400, detail="Shutdown confirmation header required")

    Thread(target=_gracefully_stop_engine, daemon=True, name="engine-shutdown").start()
    return {
        "status": "stopping",
        "message": "SMT engine is stopping. MT5 and existing positions are unchanged.",
    }


def normalize_symbol(symbol):
    normalized = symbol.upper()
    if normalized not in SUPPORTED_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail="Supported symbols are BTCUSDT, ETHUSDT, and XAUUSD."
        )
    return normalized


# ── MT4 Trading Endpoints ──────────────────────────────────────────────────

try:
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from trading_executor import (
        get_mode, set_mode, get_account_info,
        get_trades_log, load_all_trades, close_all_positions, load_config, save_config
    )
    _MT4_ENABLED = True
except Exception:
    _MT4_ENABLED = False


@app.get("/mt4/status")
def mt4_status():
    if not _MT4_ENABLED:
        return {"enabled": False, "mode": "paper", "message": "MT4 executor not found"}
    try:
        current_mode = get_mode()
        cfg          = load_config()
        # For status polling — return lightweight response
        # Do NOT connect to MT5 here (called every 15s by frontend)
        # MT5 connection only happens on trade execution or /mt4/refresh
        if current_mode == "paper":
            trades   = get_trades_log()
            open_pos = [t for t in trades if t.get("status") == "open"]
            info = {"mode": "paper", "balance": "Paper",
                    "open_trades": len(open_pos)}
        else:
            # Return cached account info without triggering new MT5 connection
            from trading_executor import _mt5_cache, _mt5_cache_time
            import time
            if _mt5_cache and (time.time() - _mt5_cache_time) < 300:
                info = _mt5_cache  # use cache if < 5 min old
            else:
                info = {
                    "mode":    current_mode,
                    "balance": "—",
                    "equity":  "—",
                    "message": "Click Refresh to connect MT5"
                }
        return {"enabled": True, "mode": current_mode, "account": info}
    except Exception as e:
        return {"enabled": True, "mode": get_mode(),
                "account": {"error": str(e)}}


@app.get("/mt4/refresh")
def mt4_refresh():
    """Manually trigger MT5 connection and cache refresh."""
    if not _MT4_ENABLED:
        return {"error": "MT4 not available"}
    try:
        info = get_account_info(force_refresh=True)
        return {"success": True, "account": info}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/mt4/mode/{mode}")
def mt4_set_mode(mode: str):
    if mode not in ("paper", "demo", "live"):
        return {"error": "Mode must be 'paper', 'demo', or 'live'"}
    if not _MT4_ENABLED:
        return {"error": "MT4 executor not available"}
    set_mode(mode)
    return {"success": True, "mode": mode}


@app.get("/mt4/trades")
def mt4_trades(mode: str = None):
    """
    Return trade log.
    ?mode=demo|live|paper  → trades for that specific mode only
    (no param)             → current-mode trades + live MT5 positions/history injected
    all                    → combined across all modes (used by History page)
    """
    if not _MT4_ENABLED:
        return {"trades": []}
    if mode == "all":
        # Merge local log (all modes) with live MT5 history injection
        local  = load_all_trades()
        live   = get_trades_log()   # enriched: mt5_state + realized P&L + history
        # Overlay live enrichment (state, realized P&L) onto matching local rows
        by_ticket = {str(t["ticket"]): t for t in live if t.get("ticket")}
        for t in local:
            e = by_ticket.get(str(t.get("ticket", "")))
            if e:
                for k in ("mt5_state", "realized_usd", "unreal_pts",
                          "unreal_usd", "current_price"):
                    if t.get(k) is None and e.get(k) is not None:
                        t[k] = e[k]
        # Deduplicate by ticket — keep local entries first (richer metadata)
        seen   = {str(t["ticket"]) for t in local if t.get("ticket")}
        extra  = [t for t in live if str(t.get("ticket", "")) not in seen]
        return {"trades": local + extra}
    return {"trades": get_trades_log()}


@app.delete("/mt4/cancel/{ticket}")
def cancel_order(ticket: str):
    """Cancel or close any trade by ticket — works for pending orders AND active positions."""
    if not _MT4_ENABLED:
        return {"error": "MT4 executor not available"}
    try:
        from trading_executor import close_or_cancel_order
        return close_or_cancel_order(ticket)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/mt4/cancel-expired")
def cancel_expired(max_age_hours: float = None):
    """
    Cancel all pending orders older than max_age_hours.
    If max_age_hours is omitted, uses smart per-trade timeouts:
      scalp=2h  intraday=8h  swing=48h
    """
    if not _MT4_ENABLED:
        return {"error": "MT4 executor not available"}
    try:
        from trading_executor import cancel_expired_orders
        return cancel_expired_orders(max_age_hours)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/mt4/ema-exit-check")
def ema_exit_check():
    """
    Manually trigger the EMA-cross exit monitor.
    Closes any active EMA-strategy positions where price crossed the 20 EMA.
    Runs automatically every 5 min in background; use this to trigger on demand.
    """
    if not _MT4_ENABLED:
        return {"error": "MT4 executor not available"}
    try:
        from trading_executor import run_ema_exit_monitor
        return run_ema_exit_monitor()
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/mt4/cancel-duplicates")
def cancel_duplicates():
    """
    Scan all pending orders for duplicates (same symbol + direction + similar price).
    Keeps the OLDEST order (first placed), cancels all newer duplicates.
    Safe to call any time — won't touch active positions or unique pending orders.
    """
    if not _MT4_ENABLED:
        return {"error": "MT4 not available"}
    try:
        from trading_executor import cancel_duplicate_pending_orders
        return cancel_duplicate_pending_orders()
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/mt4/close-all")
def mt4_close_all():
    if not _MT4_ENABLED:
        return {"error": "MT4 not available"}
    success = close_all_positions()
    return {"success": success}


# ── P&L Summary (real MT5 data) ───────────────────────────────────────────────

def _build_pnl_metrics(closed_list, open_positions=None):
    """Compute metrics, equity curve, by_symbol, by_strategy from a closed_list."""
    import datetime as _dt
    from collections import defaultdict

    open_positions = open_positions or []

    # Equity curve
    cumulative = 0.0
    curve = []
    for t in closed_list:
        cumulative = round(cumulative + t["realized"], 2)
        ts = t.get("open_time")
        if ts:
            try:
                date_str = _dt.datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d")
            except Exception:
                date_str = str(ts)[:10]
        else:
            date_str = "unknown"
        if curve and curve[-1]["date"] == date_str:
            curve[-1]["pnl"] = cumulative
        else:
            curve.append({"date": date_str, "pnl": cumulative})

    # Core metrics
    wins   = [t for t in closed_list if t["realized"] > 0]
    losses = [t for t in closed_list if t["realized"] < 0]
    gross_profit  = sum(t["realized"] for t in wins)
    gross_loss    = abs(sum(t["realized"] for t in losses))
    net_total     = round(sum(t["realized"] for t in closed_list), 2)
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    avg_win  = round(gross_profit / len(wins),   2) if wins   else 0
    avg_loss = round(gross_loss   / len(losses), 2) if losses else 0
    win_rate = round(len(wins) / len(closed_list) * 100, 1) if closed_list else 0

    peak, max_dd, running = 0.0, 0.0, 0.0
    for t in closed_list:
        running += t["realized"]
        if running > peak: peak = running
        dd = peak - running
        if dd > max_dd: max_dd = dd

    best_trade  = max((t["realized"] for t in closed_list), default=0)
    worst_trade = min((t["realized"] for t in closed_list), default=0)

    metrics = {
        "total_trades":    len(closed_list),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate":        win_rate,
        "net_pnl":         net_total,
        "gross_profit":    round(gross_profit, 2),
        "gross_loss":      round(gross_loss, 2),
        "profit_factor":   profit_factor,
        "avg_win":         avg_win,
        "avg_loss":        avg_loss,
        "max_drawdown":    round(max_dd, 2),
        "best_trade":      round(best_trade, 2),
        "worst_trade":     round(worst_trade, 2),
        "open_unrealized": round(sum(p.get("unrealized", 0) for p in open_positions), 2),
    }

    by_sym = defaultdict(lambda: {"trades":0,"wins":0,"gross_profit":0,"gross_loss":0,"net":0})
    for t in closed_list:
        s = by_sym[t.get("symbol","?")]
        s["trades"] += 1
        if t["realized"] > 0:
            s["wins"] += 1
            s["gross_profit"] = round(s["gross_profit"] + t["realized"], 2)
        else:
            s["gross_loss"] = round(s["gross_loss"] + abs(t["realized"]), 2)
        s["net"] = round(s["net"] + t["realized"], 2)

    by_strat = defaultdict(lambda: {"trades":0,"wins":0,"gross_profit":0,"gross_loss":0,"net":0})
    for t in closed_list:
        s = by_strat[t.get("strategy","Unknown")]
        s["trades"] += 1
        if t["realized"] > 0:
            s["wins"] += 1
            s["gross_profit"] = round(s["gross_profit"] + t["realized"], 2)
        else:
            s["gross_loss"] = round(s["gross_loss"] + abs(t["realized"]), 2)
        s["net"] = round(s["net"] + t["realized"], 2)

    return metrics, curve, dict(by_sym), dict(by_strat)


def _pnl_from_local_file(mode: str, days: int):
    """Build P&L summary from a local trades JSON file (live or paper mode)."""
    import datetime as _dt
    from trading_executor import _load_trades
    trades = _load_trades(mode=mode)

    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=days)

    closed_list = []
    for t in trades:
        if t.get("mt5_state") not in ("closed", "completed"):
            continue
        realized = t.get("realized_usd") or t.get("pnl_usd") or 0
        try:
            realized = float(realized)
        except Exception:
            continue

        # Parse open_time
        ts = None
        if t.get("timestamp"):
            try: ts = float(t["timestamp"])
            except Exception: pass
        if ts is None and t.get("time_ist"):
            try:
                ts = _dt.datetime.strptime(
                    t["time_ist"].replace(" IST","").strip(), "%Y-%m-%d %H:%M:%S"
                ).timestamp()
            except Exception:
                pass

        if ts and _dt.datetime.utcfromtimestamp(ts) < cutoff:
            continue

        closed_list.append({
            "ticket":    t.get("ticket",""),
            "symbol":    t.get("symbol","?"),
            "direction": t.get("direction","?"),
            "entry":     t.get("entry_price", 0),
            "lot":       t.get("lot", 0),
            "realized":  realized,
            "open_time": ts or 0,
            "strategy":  t.get("strategy_tag") or t.get("setup") or "Unknown",
        })

    closed_list.sort(key=lambda x: x["open_time"])
    metrics, curve, by_sym, by_strat = _build_pnl_metrics(closed_list)
    return {
        "account":        None,
        "open_positions": [],
        "closed_trades":  closed_list,
        "equity_curve":   curve,
        "by_symbol":      by_sym,
        "by_strategy":    by_strat,
        "metrics":        metrics,
        "days":           days,
        "mode":           mode,
    }


@app.get("/pnl/summary")
def pnl_summary(days: int = 90, mode: str = "demo"):
    """
    Returns a complete P&L summary.
    mode=demo  → live MT5 data from currently connected (demo) account
    mode=live  → computed from mt5_trades_live.json
    mode=paper → computed from mt5_trades_paper.json
    """
    import datetime as _dt, sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))

    # Live and paper modes: read from local trade files (no MT5 API needed)
    if mode in ("live", "paper"):
        try:
            return _pnl_from_local_file(mode, days)
        except Exception as e:
            return {"error": str(e), "mode": mode}

    # Demo mode: query MT5 directly
    result = {
        "account":        None,
        "open_positions": [],
        "closed_trades":  [],
        "equity_curve":   [],
        "by_symbol":      {},
        "by_strategy":    {},
        "metrics":        {},
        "days":           days,
        "mode":           "demo",
    }

    try:
        import MetaTrader5 as mt5
        from collections import defaultdict

        # Account snapshot
        info = mt5.account_info()
        if info:
            result["account"] = {
                "balance":     round(info.balance, 2),
                "equity":      round(info.equity, 2),
                "free_margin": round(info.margin_free, 2),
                "profit":      round(info.profit, 2),
                "currency":    info.currency,
                "leverage":    info.leverage,
            }

        # Open positions
        positions = mt5.positions_get() or []
        for p in positions:
            result["open_positions"].append({
                "ticket":     p.ticket,
                "symbol":     p.symbol,
                "direction":  "BUY" if p.type == 0 else "SELL",
                "entry":      p.price_open,
                "current":    p.price_current,
                "sl":         p.sl,
                "tp":         p.tp,
                "lot":        p.volume,
                "unrealized": round(p.profit, 2),
                "swap":       round(p.swap, 2),
                "open_time":  p.time,
            })

        # Closed deals.
        # MT5 deal.time is BROKER-SERVER time (can run hours ahead of UTC/local),
        # so a tight upper bound clips just-closed trades out of History. Over-shoot
        # it — there are no real deals in the future, so a wide `to` is safe.
        _from = _dt.datetime.utcnow() - _dt.timedelta(days=days + 1)
        _to   = _dt.datetime.utcnow() + _dt.timedelta(days=1)
        deals = mt5.history_deals_get(_from, _to) or []

        # DEAL_TYPE_BALANCE=2, DEAL_TYPE_CREDIT=3 — these are account operations,
        # not trades. Skip them. Also skip deals with no symbol (safety net).
        # Filter to THIS app's magics — the MT5 account is shared with other bots,
        # so P&L must count only Smart Money Trader's own trades.
        from trading_executor import SMT_MAGICS
        DEAL_TYPE_BALANCE = 2
        DEAL_TYPE_CREDIT  = 3
        trade_deals = [d for d in deals
                       if d.type not in (DEAL_TYPE_BALANCE, DEAL_TYPE_CREDIT)
                       and d.symbol
                       and getattr(d, "magic", 0) in SMT_MAGICS]

        pos_deals   = defaultdict(list)
        pos_profits = defaultdict(float)
        for d in trade_deals:
            pos_deals[d.position_id].append(d)
            pos_profits[d.position_id] = round(pos_profits[d.position_id] + d.profit, 2)

        from trading_executor import load_all_trades
        def _ticket_key(tk):
            try:    return int(tk)
            except: return tk

        local_trades = {_ticket_key(t["ticket"]): t for t in load_all_trades() if t.get("ticket")}

        DEAL_ENTRY_IN  = 0
        DEAL_ENTRY_OUT = 1
        seen_positions = set()
        closed_list = []

        for pos_id, deal_list in pos_deals.items():
            if pos_id in seen_positions:
                continue

            # Require at least one closing deal — positions with only IN deals are still open
            has_out = any(d.entry == DEAL_ENTRY_OUT for d in deal_list)
            if not has_out:
                continue

            # Use the IN deal as anchor; fall back to the earliest deal if IN is missing
            # (can happen when the position was opened before the query window)
            open_deal = next((d for d in deal_list if d.entry == DEAL_ENTRY_IN), None)
            anchor    = open_deal or min(deal_list, key=lambda d: d.time)

            seen_positions.add(pos_id)
            local    = local_trades.get(anchor.order, {})
            strategy = local.get("strategy_tag") or local.get("setup") or "MT5_Manual"

            # Direction: derive from the OUT deal if the IN deal is missing
            out_deal  = next((d for d in deal_list if d.entry == DEAL_ENTRY_OUT), anchor)
            # Out deal type is opposite: 0=BUY close (was SELL open), 1=SELL close (was BUY open)
            if open_deal:
                direction = "BUY" if open_deal.type == 0 else "SELL"
            else:
                direction = "SELL" if out_deal.type == 0 else "BUY"

            closed_list.append({
                "ticket":      anchor.order,
                "position_id": pos_id,
                "symbol":      anchor.symbol,
                "direction":   direction,
                "entry":       anchor.price,
                "lot":         anchor.volume,
                "realized":    pos_profits[pos_id],
                "open_time":   anchor.time,
                "strategy":    strategy,
            })

        closed_list.sort(key=lambda x: x["open_time"])
        result["closed_trades"] = closed_list

        metrics, curve, by_sym, by_strat = _build_pnl_metrics(closed_list, result["open_positions"])
        result["equity_curve"] = curve
        result["by_symbol"]    = by_sym
        result["by_strategy"]  = by_strat
        result["metrics"]      = metrics

    except Exception as e:
        result["error"] = str(e)

    return result


# ── Momentum Scalper API ──────────────────────────────────────────────────────

@app.post("/scalper/start")
def scalper_start(symbols: str = None, timeframe: str = "M1"):
    """
    Start the M1 Momentum Scalper engine.
    symbols: comma-separated MT5 symbol list, e.g. 'BTCUSD,ETHUSD,XAUUSD+'
    timeframe: M1 (default) or M5
    """
    try:
        from app.strategies.momentum_scalper import start_scalper
        sym_list = [s.strip() for s in symbols.split(",")] if symbols else None
        return start_scalper(symbols=sym_list, timeframe=timeframe)
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/scalper/stop")
def scalper_stop():
    """Stop the M1 Momentum Scalper engine (does not close open positions)."""
    try:
        from app.strategies.momentum_scalper import stop_scalper
        return stop_scalper()
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/scalper/status")
def scalper_status():
    """Return current scalper engine state (running, signals fired, last signal, etc.)."""
    try:
        from app.strategies.momentum_scalper import get_scalper_status
        return get_scalper_status()
    except Exception as e:
        return {"running": False, "error": str(e)}


# ── Strategy activation API ───────────────────────────────────────────────────
# Persists which strategies are active for live/demo/paper trading.
# StrategyTester page reads this to show current status and toggle strategies.

_STRATEGY_CONFIG_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "strategy_config.json")
)

def _load_strategy_config():
    try:
        with open(_STRATEGY_CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"active_strategies": ["HTF_ICT_Intraday"]}  # default

def _save_strategy_config(cfg):
    with open(_STRATEGY_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


@app.get("/strategy/config")
def get_strategy_config():
    """Return currently active strategies."""
    return _load_strategy_config()


@app.post("/strategy/activate")
def activate_strategy(strategy_id: str, mode: str = "demo"):
    """
    Activate a strategy for live/demo/paper trading.
    strategy_id: e.g. 'ATR_Trailing', 'HTF_ICT_Intraday', 'EMA20_Intraday'
    mode: 'paper' | 'demo' | 'live'
    """
    cfg = _load_strategy_config()
    key = f"active_strategies_{mode}"
    current = cfg.get(key, cfg.get("active_strategies", []))
    if strategy_id not in current:
        current.append(strategy_id)
    cfg[key] = current
    _save_strategy_config(cfg)
    return {"success": True, "active": current, "mode": mode}


@app.delete("/strategy/activate")
def deactivate_strategy(strategy_id: str, mode: str = "demo"):
    """Remove a strategy from active trading for the given mode."""
    cfg = _load_strategy_config()
    key = f"active_strategies_{mode}"
    current = cfg.get(key, cfg.get("active_strategies", []))
    cfg[key] = [s for s in current if s != strategy_id]
    _save_strategy_config(cfg)
    return {"success": True, "active": cfg[key], "mode": mode}


@app.get("/mt4/config")
def mt4_config():
    if not _MT4_ENABLED:
        return {}
    cfg = load_config()
    # Hide passwords from frontend
    safe = {k: v for k, v in cfg.items() if "password" not in k.lower()}
    return safe


@app.post("/mt4/config")
async def mt4_update_config(request: dict):
    if not _MT4_ENABLED:
        return {"error": "MT4 not available"}
    cfg = load_config()
    # Allow updating lot sizes and mode only from frontend
    if "lot_size" in request:
        cfg["lot_size"] = float(request["lot_size"])
    if "lot_sizes" in request and isinstance(request["lot_sizes"], dict):
        sizes = cfg.get("lot_sizes", {})
        for k, v in request["lot_sizes"].items():
            sizes[k] = float(v)
        cfg["lot_sizes"] = sizes
    if "mode" in request:
        cfg["mode"] = request["mode"]
    save_config(cfg)
    return {"success": True, "lot_sizes": cfg.get("lot_sizes", {})}


@app.get("/test-telegram")
def test_telegram():
    send_alert("""
🚨 TEST SIGNAL - Smart Money Trader

Symbol: BTCUSDT
Signal: LONG
Timeframe: 15m
Confidence: High
Entry: 67500.00
SL: 67000.00
TP: 68500.00
RR: 3.0
Confluence: BOS, FVG, Order Block
""")
    return {"status": "Test message sent to Telegram!"}


# Sentiment cache — avoid hitting external APIs on every frontend refresh
_sentiment_cache = {"data": None, "ts": 0}

@app.get("/sentiment")
def sentiment_endpoint():
    import time
    now = time.time()
    if _sentiment_cache["data"] and now - _sentiment_cache["ts"] < 60:
        return _sentiment_cache["data"]
    result = get_sentiment()
    _sentiment_cache["data"] = result
    _sentiment_cache["ts"]   = now
    return result


@app.get("/market-events")
def market_events():
    """Upcoming high-impact economic events from ForexFactory."""
    events = get_market_events(max_events=30)
    return {"events": events}




@app.get("/calendar")
def calendar_today():
    """Today's economic calendar with high-impact event windows."""
    data   = load_today_events()
    paused, reason = is_high_impact_window()
    return {
        **data,
        "signals_paused": paused,
        "pause_reason":   reason
    }


@app.get("/journal/monthly")
def monthly_summary():
    """End-of-month journal summary with strategy selection."""
    return get_monthly_journal_summary()


@app.post("/journal/monthly-export")
def monthly_export(month: str | None = None):
    """Write the month's trade rollup to the Obsidian vault
    (raw/trades/smart-money-trader/<YYYY-MM>.md). ``month`` is YYYY-MM; defaults
    to the current month."""
    y = m = None
    if month:
        try:
            y, m = (int(x) for x in month.split("-")[:2])
        except (ValueError, TypeError):
            return {"status": "ERROR", "message": "month must be YYYY-MM"}
    return export_monthly_to_obsidian(y, m)


@app.get("/journal/quadrants")
def quadrant_stats():
    """Quadrant breakdown: BP/SP/SL/BL counts."""
    return get_quadrant_stats()


@app.get("/trader/journal")
def trader_journal():
    """Trader brain journal — what the system observed and thought."""
    import json as _json
    jf = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "trader_journal.json"))
    try:
        with open(jf, "r") as f:
            entries = _json.load(f)
        return {"entries": entries[:50]}  # last 50
    except Exception:
        return {"entries": []}


@app.get("/trader/suggestions")
def trader_suggestions():
    """Self-improvement suggestions generated by the system."""
    return load_suggestions()


@app.get("/knowledge")
def knowledge_base():
    """Full strategy knowledge base — per-asset learning + research insights."""
    import json as _json
    _BASE2 = os.path.join(os.path.dirname(__file__), "..")
    kb_file = os.path.abspath(os.path.join(_BASE2, "strategy_knowledge.json"))
    try:
        with open(kb_file, "r") as f:
            knowledge = _json.load(f)
    except Exception:
        knowledge = {}

    research = load_research()
    weights  = load_weights()

    return {
        "knowledge":  knowledge,
        "research":   research,
        "weights":    weights
    }


# ── Obsidian Wiki Brain ───────────────────────────────────────────────────────

import pathlib as _pathlib
import urllib.parse as _urllib_parse

_WIKI_ROOT = _pathlib.Path(r"E:\Obsidian\Trading_Mind\wiki")

_WIKI_STRATEGY_MAP = {
    "ema_9_20":             ["strategies/9-20-ema-pullback", "strategies/pullback-to-ema"],
    "golden_setup":         ["strategies/golden-setup"],
    "smc_swing":            [],
    "london_breakout":      [],
    "choch":                [],
    "htf_liquidity_sweep":  [],
    "atr_trailing":         [],
    "oliver_velez":         [],
    "momentum":             [],
}

_WIKI_ASSET_MAP = {
    "XAUUSD":   "instruments/gold",
    "BTCUSDT":  "instruments/crypto",
    "ETHUSDT":  "instruments/crypto",
    "BTCUSD":   "instruments/crypto",
    "ETHUSD":   "instruments/crypto",
    "NIFTY50":  "instruments/nse-futures-options",
}

_WIKI_ALWAYS = [
    "overview",
    "concepts/four-quadrants-of-trading",
    "concepts/support-resistance",
]


def _read_wiki_page(slug: str) -> dict:
    if not _WIKI_ROOT.exists():
        return {"error": f"Wiki root not found: {_WIKI_ROOT}"}
    safe = slug.replace("..", "").strip("/").replace("\\", "/")
    path = _WIKI_ROOT / (safe if safe.endswith(".md") else safe + ".md")
    try:
        if not path.resolve().is_relative_to(_WIKI_ROOT.resolve()):
            return {"error": "invalid path"}
    except ValueError:
        return {"error": "invalid path"}
    if not path.exists():
        return {"error": f"page not found: {safe}"}
    return {"slug": safe, "content": path.read_text(encoding="utf-8")}


def _build_wiki_context(asset: str, strategy: str) -> str:
    slugs = list(_WIKI_ALWAYS)
    for key, pages in _WIKI_STRATEGY_MAP.items():
        if key in (strategy or "").lower():
            slugs.extend(pages)
            break
    asset_page = _WIKI_ASSET_MAP.get(asset)
    if asset_page:
        slugs.append(asset_page)
    seen, sections = set(), []
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        result = _read_wiki_page(slug)
        if "content" in result:
            sections.append(f"## [{slug}]\n{result['content']}")
    return "\n\n---\n\n".join(sections)


@app.get("/wiki/index")
def wiki_index():
    """List all wiki pages grouped by subfolder."""
    if not _WIKI_ROOT.exists():
        return {"error": f"Wiki root not found: {_WIKI_ROOT}"}
    index: dict = {}
    for md in sorted(_WIKI_ROOT.rglob("*.md")):
        rel    = md.relative_to(_WIKI_ROOT).as_posix().removesuffix(".md")
        folder = rel.split("/")[0] if "/" in rel else "root"
        index.setdefault(folder, []).append(rel)
    return {"wiki_root": str(_WIKI_ROOT), "pages": index}


@app.get("/wiki/context")
def wiki_context(asset: str = "", strategy: str = ""):
    """Return combined wiki context for the given asset + strategy."""
    context = _build_wiki_context(asset, strategy)
    return {"ok": True, "asset": asset, "strategy": strategy, "context": context}


@app.get("/wiki/page/{slug:path}")
def wiki_page(slug: str):
    """Return a single wiki markdown page by slug."""
    return _read_wiki_page(_urllib_parse.unquote(slug))


# ── Manual Trade Journal (Daily Trading Plan Journal) ────────────────────────

MANUAL_JOURNAL_FILE = os.path.abspath(os.path.join(_BASE, "manual_journal.json"))


def _load_manual_journal():
    try:
        with open(MANUAL_JOURNAL_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_manual_journal(entries):
    with open(MANUAL_JOURNAL_FILE, "w") as f:
        json.dump(entries, f, indent=2)


@app.get("/journal/manual")
def manual_journal_list():
    """All manual daily-journal entries, newest first."""
    entries = _load_manual_journal()
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    return {"entries": entries}


@app.post("/journal/manual")
def manual_journal_save(entry: dict = Body(...)):
    """Create or update (by id) a daily journal entry."""
    import time as _t
    entries = _load_manual_journal()
    if not entry.get("id"):
        entry["id"] = str(int(_t.time() * 1000))
        entry["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entries.append(entry)
    else:
        for i, e in enumerate(entries):
            if e.get("id") == entry["id"]:
                entry["created_at"] = e.get("created_at")
                entries[i] = entry
                break
        else:
            entries.append(entry)
    _save_manual_journal(entries)
    return {"success": True, "id": entry["id"], "total": len(entries)}


@app.delete("/journal/manual/{entry_id}")
def manual_journal_delete(entry_id: str):
    entries = _load_manual_journal()
    entries = [e for e in entries if e.get("id") != entry_id]
    _save_manual_journal(entries)
    return {"success": True, "total": len(entries)}


@app.get("/journal/manual/analysis")
def manual_journal_analysis():
    """
    Analyse all journal entries: performance, discipline/FOMO impact,
    bias accuracy, recurring mistakes and lessons.
    """
    entries = _load_manual_journal()
    if not entries:
        return {"days": 0}

    def _num(v):
        try:
            return float(str(v).replace(",", "").replace("$", "").replace("₹", ""))
        except Exception:
            return None

    days        = len(entries)
    total_trades = sum(int(_num(e.get("total_trades")) or 0) for e in entries)
    total_wins   = sum(int(_num(e.get("winning_trades")) or 0) for e in entries)
    total_losses = sum(int(_num(e.get("losing_trades")) or 0) for e in entries)
    pnls         = [(_num(e.get("net_pl")), e) for e in entries]
    pnls         = [(p, e) for p, e in pnls if p is not None]
    net_total    = round(sum(p for p, _ in pnls), 2)
    green_days   = sum(1 for p, _ in pnls if p > 0)
    red_days     = sum(1 for p, _ in pnls if p < 0)

    def _group_pl(key, value_fn=lambda v: (v or "").strip().title() or "Unknown"):
        groups = {}
        for p, e in pnls:
            k = value_fn(e.get(key))
            g = groups.setdefault(k, {"days": 0, "net": 0.0, "green": 0})
            g["days"] += 1
            g["net"]  = round(g["net"] + p, 2)
            if p > 0:
                g["green"] += 1
        return groups

    def _yes(v):
        return "Yes" if str(v).strip().lower() in ("yes", "y", "true") else "No"

    resolved_t = total_wins + total_losses
    best_day  = max(pnls, key=lambda x: x[0]) if pnls else None
    worst_day = min(pnls, key=lambda x: x[0]) if pnls else None

    return {
        "days":          days,
        "total_trades":  total_trades,
        "wins":          total_wins,
        "losses":        total_losses,
        "trade_win_rate": round(total_wins / resolved_t * 100, 1) if resolved_t else None,
        "net_pl":        net_total,
        "green_days":    green_days,
        "red_days":      red_days,
        "day_win_rate":  round(green_days / len(pnls) * 100, 1) if pnls else None,
        "avg_day_pl":    round(net_total / len(pnls), 2) if pnls else None,
        "best_day":      {"date": best_day[1].get("date"), "pl": best_day[0]} if best_day else None,
        "worst_day":     {"date": worst_day[1].get("date"), "pl": worst_day[0]} if worst_day else None,
        "by_bias":       _group_pl("market_bias"),
        "by_market":     _group_pl("market"),
        "by_setup":      _group_pl("setup_name", lambda v: (v or "").strip() or "Unknown"),
        "by_discipline": _group_pl("disciplined", _yes),
        "by_fomo":       _group_pl("fomo", _yes),
        "mistakes":      [{"date": e.get("date"), "text": e.get("biggest_mistake")}
                          for e in entries if (e.get("biggest_mistake") or "").strip()][:20],
        "lessons":       [{"date": e.get("date"), "text": e.get("lesson_learned")}
                          for e in entries if (e.get("lesson_learned") or "").strip()][:20],
    }


@app.get("/learning/insights")
def learning_insights():
    """
    Everything the Learning page needs beyond raw stats:
    per-asset knowledge, improvement suggestions, and Strategy Tuner state.
    """
    import json as _json
    from app.services.strategy_tuner import get_tuning_state
    knowledge = {}
    for fname in ("trading_knowledge.json", "strategy_knowledge.json"):
        try:
            with open(os.path.abspath(os.path.join(_BASE, fname))) as f:
                knowledge = _json.load(f)
                break
        except Exception:
            continue
    try:
        suggestions = load_suggestions()
    except Exception:
        suggestions = {}
    return {
        "knowledge":   knowledge,
        "suggestions": suggestions,
        "tuner":       get_tuning_state(),
    }


@app.post("/learning/optimize")
def learning_optimize(strategy_id: str, symbol: str = None, days: int = 60):
    """Grid-search the strategy's tunable parameter and save the best value."""
    from app.services.strategy_tuner import run_tuning
    result = run_tuning(strategy_id, symbol, days)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/learning/apply-tuning")
def learning_apply_tuning(strategy_id: str, symbol: str = None):
    """Apply the optimizer's best value so the live engine uses it."""
    from app.services.strategy_tuner import apply_tuning
    result = apply_tuning(strategy_id, symbol)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.delete("/learning/apply-tuning")
def learning_remove_tuning(strategy_id: str):
    """Revert a strategy to its built-in default parameters."""
    from app.services.strategy_tuner import remove_tuning
    return remove_tuning(strategy_id)


@app.post("/learning/run")
def learning_run_now():
    """Trigger a full learning cycle on demand (normally runs automatically)."""
    try:
        from app.services.strategy_learner import run_learning as _rl
        weights = _rl()
        return {"success": True, "notes": weights.get("notes", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/learning/stats")
def learning_stats():
    weights = load_weights()
    log     = []
    try:
        with open(LOG_FILE, "r") as f:
            log = json.load(f)
    except Exception:
        pass
    resolved = [s for s in log if s.get("outcome") in ("WIN", "LOSS")]
    open_n   = len([s for s in log if s.get("outcome") == "OPEN"])
    quads    = get_quadrant_stats()
    strategies = analyse_strategies()

    return {
        "total_signals_sent":   len(log),
        "total_resolved":       len(resolved),
        "total_open":           open_n,
        "total_wins":           weights.get("total_wins", 0),
        "overall_win_rate":     weights.get("overall_win_rate"),
        "min_quality_score":    weights.get("min_quality_score", 6),
        "confluence_win_rates": weights.get("confluence_win_rates", {}),
        "confluence_bonuses":   weights.get("confluence_bonuses", {}),
        "notes":                weights.get("notes", "No data yet."),
        "quadrants":            quads,
        "strategies":           strategies,
        "best_strategy":        weights.get("best_strategy"),
        "best_strategy_wr":     weights.get("best_strategy_wr")
    }




@app.get("/pnl")
def pnl_summary():
    """
    Returns realized and unrealized P&L data.
    Fetches live price ONCE per symbol to avoid timeouts.
    """
    try:
        with open(LOG_FILE, "r") as f:
            signals = json.load(f)
    except Exception:
        signals = []

    from app.services.binance_service import get_recent_candles_df

    # Fetch price once per symbol that has OPEN signals
    open_symbols = {s.get("symbol") for s in signals
                    if s.get("outcome") == "OPEN" and s.get("symbol") in SUPPORTED_SYMBOLS}
    live_prices = {}
    for symbol in open_symbols:
        try:
            if symbol in GOLD_SYMBOLS:
                live_prices[symbol] = gold_get_price()
            else:
                df = get_recent_candles_df(symbol=symbol, interval="1m", limit=1)
                if not df.empty:
                    live_prices[symbol] = float(df["close"].iloc[-1])
        except Exception:
            pass

    result = []
    for s in signals:
        symbol  = s.get("symbol", "BTCUSDT")
        outcome = s.get("outcome", "OPEN")
        entry   = float(s.get("entry", 0))
        sl      = float(s.get("sl", 0))
        sig     = s.get("signal", "BUY")

        current_price  = None
        unrealized_pts = None

        if outcome == "OPEN":
            current_price = live_prices.get(symbol)
            if current_price is not None:
                if sig == "BUY":
                    unrealized_pts = round(current_price - entry, 4)
                else:
                    unrealized_pts = round(entry - current_price, 4)

        risk_pts = round(abs(entry - sl), 4) if entry and sl else None
        result.append({
            **s,
            "current_price":  current_price,
            "unrealized_pts": unrealized_pts,
            "risk_pts":       risk_pts
        })

    return {"signals": result}


@app.get("/signals/sent")
def sent_signals():
    try:
        with open(LOG_FILE, "r") as f:
            signals = json.load(f)
    except Exception:
        signals = []
    return {"signals": signals}


@app.get("/")
def root(symbol: str = "BTCUSDT"):
    import time
    symbol = normalize_symbol(symbol)

    # Return cached response if fresh enough
    cached = _market_cache.get(symbol)
    if cached and time.time() - cached["ts"] < _MARKET_CACHE_TTL:
        return cached["data"]

    try:
        data = fetch_mtf(symbol)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Unable to reach Binance market data.") from error

    df = data["1m"]
    swing_highs, swing_lows = detect_swings(df)
    bos = detect_bos_choch(df, swing_highs, swing_lows)

    # No fallback markers — if nothing is detected, leave the list empty
    # so the chart shows no phantom BOS/sweep lines.

    fvgs   = detect_fvg(df)
    sweeps = detect_liquidity_sweeps(df, swing_highs, swing_lows)

    obs     = detect_order_blocks(df)
    signals = analyze_multi_timeframe(data, symbol=symbol)
    candles = []

    for index, row in df.iterrows():
        candles.append({
            "time":      index + 1,
            "timestamp": row.get("timestamp"),
            "open":      row["open"],
            "high":      row["high"],
            "low":       row["low"],
            "close":     row["close"]
        })

    # Stamp each signal with exact detection time (IST)
    now_ist     = _clock_now_ist()
    detected_at = now_ist.strftime("%I:%M:%S %p IST")

    stamped_signals = []
    for sig in signals[-5:]:
        stamped_signals.append({
            **sig,
            "detected_at": detected_at
        })

    import time as _time
    response = {
        "symbol":       symbol,
        "candles":      candles,
        "signals":      stamped_signals,
        "fvgs":         fvgs[-20:],
        "order_blocks": obs[-20:],
        "bos":          bos[-20:],
        "sweeps":       sweeps[-20:]
    }
    _market_cache[symbol] = {"data": response, "ts": _time.time()}
    return response


@app.get("/prices")
def live_prices():
    """Live prices for all three instruments — used by MT5 Trader tiles."""
    import time as _time
    result = {}
    # BTC
    try:
        df = get_recent_candles_df(symbol="BTCUSDT", interval="1m", limit=1)
        if not df.empty:
            result["BTCUSD"] = {"price": round(float(df["close"].iloc[-1]), 2), "symbol": "BTCUSD"}
    except Exception:
        pass
    # ETH
    try:
        df = get_recent_candles_df(symbol="ETHUSDT", interval="1m", limit=1)
        if not df.empty:
            result["ETHUSD"] = {"price": round(float(df["close"].iloc[-1]), 2), "symbol": "ETHUSD"}
    except Exception:
        pass
    # Gold
    try:
        price = gold_get_price()
        if price:
            result["XAUUSD+"] = {"price": round(float(price), 2), "symbol": "XAUUSD+"}
    except Exception:
        pass
    return result


@app.get("/backtest/strategy")
def run_strategy_backtest(  # sync def → FastAPI runs it in a worker thread,
                            # so long backtests no longer freeze every other request
    strategy: str = "liquidity_sweep",
    symbol:   str = "BTCUSDT",
    days:     int = 60,
    rr:       float = 2.0,
    min_score: int = 6
):
    """
    Run a backtest for a selected strategy and return results.
    Strategies: liquidity_sweep, ict_signals, atr_trailing, choch_scalp, choch_intraday, choch_swing
    """
    import time as _t

    # Alias HTF ICT Intraday strategy IDs → the ict_signals engine
    if strategy in ("htf_ict_intraday_btc", "htf_ict_intraday_eth"):
        strategy = "ict_signals"

    if strategy == "atr_trailing":
        symbol = normalize_symbol(symbol)
        try:
            data = fetch_historical_mtf(symbol, days, intervals=["1h"])
            df_1h = data.get("1h")
            if df_1h is None or df_1h.empty:
                raise HTTPException(status_code=400, detail="No 1H data available")

            from app.strategies.atr_trailing_strategy import (
                run_atr_trailing_backtest, summarise_atr
            )
            trades  = run_atr_trailing_backtest(df_1h, symbol=symbol, atr_mult=rr)
            summary = summarise_atr(trades, symbol, days, rr)

            from collections import defaultdict
            from datetime import datetime, timezone, timedelta
            day_map = defaultdict(lambda: {"wins":0,"losses":0,"trades":0})
            for t in trades:
                d = t.get("date", "")[:10]
                if d:
                    day_map[d]["trades"] += 1
                    if t.get("outcome") == "WIN":  day_map[d]["wins"]   += 1
                    if t.get("outcome") == "LOSS": day_map[d]["losses"] += 1

            return {
                "strategy": "atr_trailing",
                "symbol":   symbol,
                "days":     days,
                "summary":  summary,
                "days_breakdown": [
                    {"date": k, **v,
                     "win_rate": round(v["wins"]/v["trades"]*100,1) if v["trades"] else 0}
                    for k, v in sorted(day_map.items())
                ],
                "trades": trades[-100:],
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    if strategy == "momentum_scalp":
        # Map MT5 symbol names → Binance names for data fetch
        _mt5_to_binance = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "XAUUSD+": "XAUUSD"}
        symbol = _mt5_to_binance.get(symbol.upper(), symbol.upper())
        symbol = normalize_symbol(symbol)
        try:
            # Fetch M1 data for scalper backtest (only the 1m timeframe is needed)
            data   = fetch_historical_mtf(symbol, days, intervals=["1m"])
            df_m1  = data.get("1m")
            if df_m1 is None or df_m1.empty:
                raise HTTPException(status_code=400, detail="No M1 data available for scalper backtest")

            from app.strategies.momentum_scalper import run_scalper_backtest
            trades  = run_scalper_backtest(df_m1, symbol=symbol, atr_mult=1.5, rr=rr)

            wins    = [t for t in trades if t.get("outcome") == "WIN"]
            losses  = [t for t in trades if t.get("outcome") == "LOSS"]
            timeouts = [t for t in trades if t.get("timeout")]
            total   = len(trades)
            wr      = round(len(wins)/total*100, 1) if total else 0
            net_pts = round(sum(float(t.get("points",0) or 0) for t in trades), 5)

            from collections import defaultdict
            day_map = defaultdict(lambda: {"wins":0,"losses":0,"trades":0,"timeouts":0})
            for t in trades:
                d = t.get("date","—")[:10] if t.get("date") else "—"
                day_map[d]["trades"] += 1
                if t.get("outcome") == "WIN":  day_map[d]["wins"]   += 1
                if t.get("outcome") == "LOSS": day_map[d]["losses"] += 1
                if t.get("timeout"):           day_map[d]["timeouts"] += 1

            return {
                "strategy": "momentum_scalp",
                "symbol":   symbol,
                "days":     days,
                "summary": {
                    "total":        total,
                    "wins":         len(wins),
                    "losses":       len(losses),
                    "timeouts":     len(timeouts),
                    "win_rate":     wr,
                    "breakeven_wr": round(100/(1+rr), 1),
                    "net_points":   net_pts,
                    "best_trade":   round(max((float(t.get("points", 0) or 0) for t in trades), default=0), 5),
                    "worst_trade":  round(min((float(t.get("points", 0) or 0) for t in trades), default=0), 5),
                    "note":         "All trades auto-close within 5 minutes (5 M1 bars max)"
                },
                "days_breakdown": [
                    {"date": k, **v,
                     "win_rate": round(v["wins"]/v["trades"]*100, 1) if v["trades"] else 0}
                    for k, v in sorted(day_map.items())
                ],
                "trades": trades[-100:],
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    if strategy == "ict_signals":
        symbol = normalize_symbol(symbol)
        try:
            # Analysis uses 1h; the trade simulation walks 1m candles
            data    = fetch_historical_mtf(symbol, days, intervals=["1m", "1h"])
            from app.strategies.mtf_analysis import analyze_multi_timeframe
            from app.backtests.simple_backtest import run_backtest as _rb
            df      = data.get("1m")
            signals = analyze_multi_timeframe(data, symbol=symbol, scan_all=True, rr_ratio=rr)

            # Signals carry 1H indexes, but the simulation walks 1m candles.
            # Remap each signal to the first 1m bar AFTER its 1H bar closes.
            if df is not None and not df.empty and "timestamp" in df.columns:
                import bisect
                ts_1m = [int(t) for t in df["timestamp"].tolist()]
                for s in signals:
                    sig_ts = s.get("timestamp")
                    if sig_ts:
                        # 1H bar open + 1h = the moment the signal is confirmed
                        s["index"] = bisect.bisect_left(ts_1m, int(sig_ts) + 3_600_000) - 1

            results = _rb(df, signals)

            wins    = [r for r in results if r.get("outcome") == "WIN"]
            losses  = [r for r in results if r.get("outcome") == "LOSS"]
            total   = len(wins) + len(losses)
            wr      = round(len(wins)/total*100, 1) if total else 0

            # Day-wise grouping
            from collections import defaultdict
            day_map = defaultdict(lambda: {"wins":0,"losses":0,"trades":0})
            for r in results:
                ts = r.get("timestamp")
                if ts:
                    from datetime import datetime, timezone, timedelta
                    d = ist_date(int(ts))
                    day_map[d]["trades"] += 1
                    if r.get("outcome") == "WIN":   day_map[d]["wins"]   += 1
                    if r.get("outcome") == "LOSS":  day_map[d]["losses"] += 1

            return {
                "strategy": strategy, "symbol": symbol, "days": days,
                "summary": {
                    "total": len(results), "wins": len(wins), "losses": len(losses),
                    "win_rate": wr, "breakeven_wr": round(100/(1+rr), 1),
                    "net_pips": round(sum(float(r.get("points",0) or 0) for r in results), 2),
                    "best_trade":  round(max((float(r.get("points",0) or 0) for r in results), default=0), 2),
                    "worst_trade": round(min((float(r.get("points",0) or 0) for r in results), default=0), 2),
                    "profit_factor": (lambda gw, gl: round(gw / gl, 2) if gl > 0 else (999.0 if gw > 0 else 0))(
                        sum(float(r.get("points",0) or 0) for r in wins),
                        abs(sum(float(r.get("points",0) or 0) for r in losses))),
                },
                "days_breakdown": [{"date":k,**v,"win_rate":round(v["wins"]/v["trades"]*100,1) if v["trades"] else 0}
                                    for k,v in sorted(day_map.items())],
                "trades": [{"date": ist_str(r.get("timestamp")) if r.get("timestamp") else "—",
                            "signal": r.get("signal"), "entry": r.get("entry"),
                            "sl": r.get("sl"), "tp": r.get("tp"),
                            "outcome": r.get("outcome"), "points": r.get("points"),
                            "quality_score": r.get("quality_score")}
                           for r in results[-100:]]
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif strategy == "liquidity_sweep":
        # Legacy strategy — replaced by gold_frvp_liquidity_trap in Strategy Tester.
        # Kept here only for backward compatibility; the UI no longer calls this.
        raise HTTPException(
            status_code=410,
            detail="Session Liquidity Sweep has been retired. Use the FRVP Liquidity Trap strategy in the Strategy Tester instead."
        )
        import json as _j
        bt_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "gold_backtest_trades.json"))
        try:
            with open(bt_file) as f:
                bt = _j.load(f)
            trades  = bt.get("trades", [])
            wins    = [t for t in trades if t.get("outcome") == "WIN"]
            losses  = [t for t in trades if t.get("outcome") == "LOSS"]
            be      = [t for t in trades if t.get("outcome") == "BE"]
            total   = len(wins) + len(losses)
            wr      = round(len(wins)/total*100, 1) if total else 0
            net_pips= sum(float(t.get("pnl_pips",0) or 0) for t in trades)

            from collections import defaultdict
            day_map = defaultdict(lambda: {"wins":0,"losses":0,"be":0,"trades":0,"pnl_pips":0.0})
            for t in trades:
                d = str(t.get("date",""))
                day_map[d]["trades"]   += 1
                day_map[d]["pnl_pips"] += float(t.get("pnl_pips",0) or 0)
                if t.get("outcome")=="WIN":  day_map[d]["wins"]  += 1
                if t.get("outcome")=="LOSS": day_map[d]["losses"]+= 1
                if t.get("outcome")=="BE":   day_map[d]["be"]    += 1

            return {
                "strategy": strategy, "symbol": "XAUUSD+", "days": bt.get("days", days),
                "ran_at": bt.get("ran_at"),
                "summary": {
                    "total": len(trades), "wins": len(wins), "losses": len(losses),
                    "breakevens": len(be), "win_rate": wr,
                    "breakeven_wr": round(100/(1+rr), 1),
                    "net_pips": round(net_pips, 1),
                    "net_usd": round(net_pips * 0.01, 2),
                },
                "days_breakdown": [{"date":k,**v,
                    "win_rate":round(v["wins"]/v["trades"]*100,1) if v["trades"] else 0}
                    for k,v in sorted(day_map.items())],
                "trades": trades[-100:]
            }
        except FileNotFoundError:
            raise HTTPException(status_code=404,
                detail="No gold backtest data found. Run: python mt5_backtest.py first.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif strategy in ("choch_scalp", "choch_intraday", "choch_swing"):
        style_map = {"choch_scalp": "Scalping", "choch_intraday": "Intraday", "choch_swing": "Swing"}
        style = style_map[strategy]

        symbol = normalize_symbol(symbol)
        try:
            from app.strategies.choch_backtest import run_choch_backtest, summarise

            # Fetch data — only the timeframes this style needs (1m skipped: it
            # alone was ~44 Binance requests and caused frontend timeouts)
            if strategy == "choch_swing":
                data_exec = fetch_historical_mtf(symbol, days + 5, intervals=["1h"])
            else:
                data_exec = fetch_historical_mtf(symbol, days + 5, intervals=["15m", "1h"])

            if strategy == "choch_swing":
                df_exec = data_exec.get("1h")     # H1 execution for swing
                df_htf  = data_exec.get("1h")     # resample to D1 below
                if df_htf is not None and not df_htf.empty:
                    from app.strategies.choch_backtest import _resample_d1
                    df_htf = _resample_d1(df_htf)
            else:
                # Scalping & Intraday both use M15 execution
                _df15 = data_exec.get("15m")
                _df1  = data_exec.get("1m")
                df_exec = _df15 if (_df15 is not None and not _df15.empty) else _df1
                df_htf  = data_exec.get("1h")
                if df_htf is not None and not df_htf.empty:
                    from app.strategies.choch_backtest import _resample_h4
                    df_htf = _resample_h4(df_htf)

            if df_exec is None or df_exec.empty:
                raise HTTPException(status_code=503, detail="Market data unavailable")

            trades = run_choch_backtest(df_exec, df_htf, rr=rr, style=style)

            # Trim to requested days
            from datetime import datetime, timezone, timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            trades = [t for t in trades if t["date"] >= cutoff]

            summary, days_bd = summarise(trades, rr)

            return {
                "strategy": strategy, "symbol": symbol, "days": days,
                "style": style, "rr": rr,
                "summary": summary,
                "days_breakdown": days_bd,
                "trades": trades[-100:]
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif strategy == "ema_9_20":
        symbol = normalize_symbol(symbol)
        try:
            data  = fetch_historical_mtf(symbol, days + 5, intervals=["1h"])
            df_1h = data.get("1h")
            if df_1h is None or df_1h.empty:
                raise HTTPException(status_code=503, detail="No 1H data available")

            from app.strategies.ema_9_20_strategy import generate_ema_9_20_signals

            # Add timestamp column so run_backtest can surface dates
            if "timestamp" not in df_1h.columns:
                df_1h = df_1h.copy()
                df_1h["timestamp"] = df_1h.index.astype(str)

            from datetime import datetime, timezone, timedelta

            signals = generate_ema_9_20_signals(df_1h, rr_ratio=rr, symbol=symbol)

            # Attach human-readable timestamp to each signal
            # df_1h has a numeric RangeIndex; real dates are in the "timestamp" column (ms)
            has_ts_col = "timestamp" in df_1h.columns
            for sig in signals:
                idx = sig.get("index", 0)
                if 0 <= idx < len(df_1h):
                    if has_ts_col:
                        ms = int(df_1h["timestamp"].iloc[idx])
                        sig["timestamp"] = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        sig["timestamp"] = str(df_1h.index[idx])

            results = run_backtest(df_1h, signals)

            # Trim to requested days
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            results = [r for r in results if str(r.get("timestamp", ""))[:10] >= cutoff]

            wins    = [r for r in results if r.get("outcome") == "WIN"]
            losses  = [r for r in results if r.get("outcome") == "LOSS"]
            total   = len(results)
            wr      = round(len(wins) / total * 100, 1) if total else 0
            bp      = [r for r in results if r.get("quadrant") == "BP"]
            bl      = [r for r in results if r.get("quadrant") == "BL"]
            breakeven_wr = round(100 / (1 + rr), 1)

            from collections import defaultdict
            day_map = defaultdict(lambda: {"wins": 0, "losses": 0, "trades": 0})
            for r in results:
                d = str(r.get("timestamp", ""))[:10]
                if d and d >= cutoff:
                    day_map[d]["trades"] += 1
                    if r.get("outcome") == "WIN":  day_map[d]["wins"]   += 1
                    if r.get("outcome") == "LOSS": day_map[d]["losses"] += 1

            return {
                "strategy": "ema_9_20",
                "symbol":   symbol,
                "days":     days,
                "summary": {
                    "total":            total,
                    "wins":             len(wins),
                    "losses":           len(losses),
                    "win_rate":         wr,
                    "breakeven_wr":     breakeven_wr,
                    "avg_rr":           rr,
                    "bp_trades":        len(bp),
                    "bl_trades":        len(bl),
                    "signals_generated": len(signals),
                },
                "days_breakdown": [
                    {"date": k, **v,
                     "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0}
                    for k, v in sorted(day_map.items())
                ],
                "trades": results[-100:],
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif strategy == "golden_setup":
        symbol = normalize_symbol(symbol)
        try:
            data  = fetch_historical_mtf(symbol, days + 5, intervals=["1h"])
            df_1h = data.get("1h")
            if df_1h is None or df_1h.empty:
                raise HTTPException(status_code=503, detail="No 1H data available")

            from datetime import datetime, timezone, timedelta
            from app.strategies.golden_setup_strategy import generate_golden_setup_signals

            signals = generate_golden_setup_signals(
                df_1h,
                symbol=symbol,
                rr_ratio=rr,
                apply_time_filter=True,
            )

            has_ts_col = "timestamp" in df_1h.columns
            for sig in signals:
                idx = sig.get("index", 0)
                if 0 <= idx < len(df_1h):
                    if has_ts_col:
                        ms = int(df_1h["timestamp"].iloc[idx])
                        sig["timestamp"] = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        sig["timestamp"] = str(df_1h.index[idx])

            results = run_backtest(df_1h, signals)

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            results = [r for r in results if str(r.get("timestamp", ""))[:10] >= cutoff]

            wins   = [r for r in results if r.get("outcome") == "WIN"]
            losses = [r for r in results if r.get("outcome") == "LOSS"]
            total  = len(results)
            wr     = round(len(wins) / total * 100, 1) if total else 0
            bp     = [r for r in results if r.get("quadrant") == "BP"]
            bl     = [r for r in results if r.get("quadrant") == "BL"]
            breakeven_wr = round(100 / (1 + rr), 1)

            from collections import defaultdict
            day_map = defaultdict(lambda: {"wins": 0, "losses": 0, "trades": 0})
            for r in results:
                d = str(r.get("timestamp", ""))[:10]
                if d and d >= cutoff:
                    day_map[d]["trades"] += 1
                    if r.get("outcome") == "WIN":  day_map[d]["wins"]   += 1
                    if r.get("outcome") == "LOSS": day_map[d]["losses"] += 1

            return {
                "strategy": "golden_setup",
                "symbol":   symbol,
                "days":     days,
                "summary": {
                    "total":             total,
                    "wins":              len(wins),
                    "losses":            len(losses),
                    "win_rate":          wr,
                    "breakeven_wr":      breakeven_wr,
                    "avg_rr":            rr,
                    "bp_trades":         len(bp),
                    "bl_trades":         len(bl),
                    "signals_generated": len(signals),
                },
                "days_breakdown": [
                    {"date": k, **v,
                     "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0}
                    for k, v in sorted(day_map.items())
                ],
                "trades": results[-100:],
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy}")


@app.get("/signals/history")
def signal_history(days: int = 90, symbol: str = "BTCUSDT"):
    symbol = normalize_symbol(symbol)
    try:
        data = fetch_historical_mtf(symbol, days)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Unable to reach Binance market data.") from error

    df      = data["1m"]
    signals = analyze_multi_timeframe(data)
    results = run_backtest(df, signals)
    history = []

    for result in results:
        signal_index = result.get("index", 0)
        timestamp    = None
        if "timestamp" in df.columns and signal_index < len(df):
            timestamp = int(df["timestamp"].iloc[signal_index])
        history.append({**result, "timestamp": timestamp})

    history.sort(key=lambda s: s.get("timestamp") or 0, reverse=True)
    return {"days": days, "symbol": symbol, "signals": history}


# ══════════════════════════════════════════════════════════════════════════════
# Strategy Tester — registry, backtest runner, deploy, delete
# ══════════════════════════════════════════════════════════════════════════════

_TESTER_REGISTRY_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "strategy_tester_registry.json")
)

# Built-in strategies that are always listed (cannot be deleted)
_BUILTIN_STRATEGIES = [
    # ── HTF ICT Intraday ─────────────────────────────────────────────────────
    {
        "id":          "htf_ict_intraday_btc",
        "name":        "HTF ICT Intraday — BTC",
        "symbol":      "BTCUSDT",
        "description": (
            "The live production strategy for BTC. "
            "1H Fair Value Gaps + 20 EMA regime filter. "
            "Entry at FVG top/bottom with EMA confluence. "
            "SL below the FVG, configurable TP via RR. "
            "Same logic currently generating live BTC signals."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 2.0},
    },
    {
        "id":          "htf_ict_intraday_eth",
        "name":        "HTF ICT Intraday — ETH",
        "symbol":      "ETHUSDT",
        "description": (
            "The live production strategy for ETH. "
            "1H Fair Value Gaps + 20 EMA regime filter. "
            "Entry at FVG top/bottom with EMA confluence. "
            "SL below the FVG, configurable TP via RR. "
            "Same logic currently generating live ETH signals."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 2.0},
    },
    {
        "id":          "gold_frvp_liquidity_trap",
        "name":        "Gold FRVP Liquidity Trap",
        "symbol":      "XAUUSD",
        "description": (
            "Uses Fixed Range Volume Profile (VAH/VAL) computed before 16:00 IST, "
            "then enters on liquidity traps (false breakouts) after 16:00 IST. "
            "Max 2 trades/day, $8 SL, configurable RR."
        ),
        "builtin":     True,
        "params": {
            "days":       30,
            "sl_distance": 8.0,
            "rr_ratio":    2.0,
        },
    },
    {
        "id":          "atr_trailing",
        "name":        "ATR Trailing Stop",
        "symbol":      "XAUUSD",
        "description": "1H trend-following strategy using ATR-based trailing stop exits.",
        "builtin":     True,
        "params": {"days": 60, "rr_ratio": 2.0},
    },
    {
        "id":          "momentum_scalp",
        "name":        "Momentum Scalper",
        "symbol":      "BTCUSDT",
        "description": "1-minute momentum scalp using EMA + volume confirmation.",
        "builtin":     True,
        "params": {"days": 30, "rr_ratio": 1.5},
    },
    # ── 9 & 20 EMA Pullback ────────────────────────────────────────────────────
    {
        "id":          "ema_9_20_btc",
        "name":        "9/20 EMA Pullback — BTC",
        "symbol":      "BTCUSDT",
        "description": (
            "Stock Burner strategy. 9 EMA above/below 20 EMA defines trend. "
            "Price pulls back to either EMA; entry on a rejection candle "
            "(green + close > prior close for longs; red + close near low for shorts). "
            "Stop at 3-bar swing low/high. Minimum 1:3 R:R. "
            "Ranging market gate: skip if EMA spread < 0.10%."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 3.0},
    },
    {
        "id":          "ema_9_20_eth",
        "name":        "9/20 EMA Pullback — ETH",
        "symbol":      "ETHUSDT",
        "description": (
            "Stock Burner strategy applied to ETH. 9/20 EMA trend filter + "
            "pullback rejection candle entry. Stop at 3-bar swing low/high. "
            "Minimum 1:3 R:R. Ranging market skipped automatically."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 3.0},
    },
    {
        "id":          "golden_setup_xau",
        "name":        "Golden Setup — XAU/USD",
        "symbol":      "XAUUSD",
        "description": (
            "Power of Stocks (Subasish) round-number breakout strategy on Gold. "
            "Entry when price closes above/below a 50-point round level aligned with "
            "structural trend. Stop 5 pts beyond the round level. Min 1:5 R:R. "
            "Time windows: 13:00–15:00 UTC and 02:00–04:00 UTC."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 5.0},
    },
    {
        "id":          "golden_setup_btc",
        "name":        "Golden Setup — BTC",
        "symbol":      "BTCUSDT",
        "description": (
            "Power of Stocks round-number breakout on BTC. "
            "Entry at 1000-point level crossovers with structural trend alignment. "
            "Stop 300 pts beyond the round level. Min 1:5 R:R. "
            "Time windows: 13:00–15:00 UTC and 02:00–04:00 UTC."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 5.0},
    },
    # ── Gold Advanced Strategies ──────────────────────────────────────────────
    {
        "id":          "london_breakout",
        "name":        "London Session Breakout",
        "symbol":      "XAUUSD",
        "description": (
            "Marks the Asian session range (00:00–08:00 GMT), then enters on a "
            "decisive candle close outside that range during London open (08:00–11:00 GMT). "
            "Volume filter + ATR-scaled SL. Max 1 trade/day."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 2.0, "timeframe": "15m"},
    },
    {
        "id":          "m5_mean_reversion",
        "name":        "M5 Mean Reversion (RSI + BB)",
        "symbol":      "XAUUSD",
        "description": (
            "Detects RSI 80/20 extremes + Bollinger Band spike during NY session "
            "(13:00–16:00 GMT). Enters counter-trend when RSI crosses back inside "
            "70/30. SL above spike high/low, TP at BB midline then opposite band."
        ),
        "builtin":     True,
        "params":      {"days": 30, "rr_ratio": 2.0},
    },
    {
        "id":          "h4_break_retest",
        "name":        "H4 Break-and-Retest",
        "symbol":      "XAUUSD",
        "description": (
            "Swing strategy: Daily 50/200 EMA trend filter, then waits for an "
            "H4 structural breakout followed by a pullback to the broken level. "
            "Enters only on PA confirmation (pin bar, doji, engulfing). "
            "SL uses Daily ATR for breathing room. Best over 90–180d periods."
        ),
        "builtin":     True,
        "params":      {"days": 180, "rr_ratio": 2.5},
    },
    # ── BTC Momentum ─────────────────────────────────────────────────────────
    {
        "id":          "btc_momentum",
        "name":        "BTC Dual-TF Momentum",
        "symbol":      "BTCUSDT",
        "description": (
            "Same dual-timeframe momentum strategy as ETH, applied to Bitcoin. "
            "Daily 50/200 EMA sets macro regime (bull/bear). "
            "4H MACD(12,26,9) crossover triggers entry when RSI(14) is in the "
            "acceleration zone (45–65 long, 35–55 short). "
            "SL at recent 4H swing extreme; trailing exit on MACD cross-back. "
            "No fixed TP — rides the trend until momentum dies."
        ),
        "builtin":     True,
        "params":      {"days": 180, "rsi_low": 45, "rsi_high": 65, "timeframe": "Daily/4H"},
    },
    # ── ETH Momentum ─────────────────────────────────────────────────────────
    {
        "id":          "eth_momentum",
        "name":        "ETH Dual-TF Momentum",
        "symbol":      "ETHUSD",
        "description": (
            "Dual-timeframe momentum strategy for ETH. Daily 50/200 EMA filters "
            "macro regime (bull/bear). 4H MACD(12,26,9) crossover triggers entry "
            "when RSI(14) is in the acceleration zone (45–65 long, 35–55 short). "
            "SL at recent 4H swing extreme; trailing exit on MACD cross-back. "
            "No fixed TP — rides the trend until momentum dies."
        ),
        "builtin":     True,
        "params":      {"days": 180, "rsi_low": 45, "rsi_high": 65, "timeframe": "Daily/4H"},
    },
    # ── Gold HTF Liquidity Sweep ──────────────────────────────────────────────
    {
        "id":          "htf_liquidity_sweep",
        "name":        "Gold HTF Liquidity Sweep (Judas Swing)",
        "symbol":      "XAUUSD",
        "description": (
            "Daily BSL/SSL Judas Swing strategy. Marks Previous Day High/Low on 1m "
            "bars. Waits for a sweep above PDH (Judas spike) then enters SHORT below "
            "the spike; sweeps below PDL trigger LONG above the swing. "
            "SL beyond the sweep extreme, TP configurable via RR. "
            "Daily breaker halts direction after 2 consecutive SL hits."
        ),
        "builtin":     True,
        "params":      {"days": 30, "rr_ratio": 5.0, "max_risk_points": 4.0},
    },
    # ── SMC / ICT Swing Strategies (BTC & ETH) ───────────────────────────────
    {
        "id":          "smc_swing",
        "name":        "SMC Liquidity Sweep",
        "symbol":      "BTCUSD",
        "description": (
            "ICT/SMC multi-timeframe swing strategy. HTF (4H) identifies liquidity "
            "sweeps (Judas swings). LTF (1H) confirms with Market Structure Shift + "
            "Fair Value Gap. Partial TP: 50% at 1:2 RR, 50% at target RR. "
            "Best for BTC and ETH over 60–90d windows."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 3.0, "timeframe": "4H/1H"},
    },
    # ── Oliver Velez Swing — BTC ─────────────────────────────────────────────
    {
        "id":          "oliver_velez_btc",
        "name":        "Oliver Velez Swing — BTC",
        "symbol":      "BTCUSDT",
        "description": (
            "Oliver Velez swing rules applied to Bitcoin daily bars. "
            "Elephant Bars, Bottoming/Double Tails, and Color Changes at the "
            "rising 20 MA or flat 200 MA. BTC-tuned defaults: lower Elephant "
            "multiplier (1.5×) for crypto-sized candles, 2% MA tolerance for "
            "BTC's wider swings, fast 3-bar partial exit. "
            "Runner trails the 8 MA with Big Bar jam + bar-by-bar trailing."
        ),
        "builtin":     True,
        "params":      {
            "days":        365,
            "sl_distance": 1.5,   # elephant_mult — looser for BTC bodies
            "rr_ratio":    3.0,   # bar_count_target — faster partial exit
            "timeframe":   "Daily",
        },
    },
    # ── Oliver Velez Swing (Stocks) ───────────────────────────────────────────
    {
        "id":          "oliver_velez_swing",
        "name":        "Oliver Velez Swing Strategy",
        "symbol":      "AAPL",
        "description": (
            "Classic swing trading strategy targeting 2–10 day holds on stocks. "
            "Identifies Elephant Bars (institutional footprints), Bottoming Tails, "
            "Double Tails, and Bullish Color Changes at key MA locations "
            "(rising 20 MA or flat 200 MA). Split-position management: "
            "half exits after 3–5 new-high bars; runner trails the 8 MA with "
            "Big Bar jam and bar-by-bar trailing. Works on any Yahoo Finance ticker."
        ),
        "builtin":     True,
        "params":      {
            "days":             365,
            "sl_distance":      2.0,    # repurposed → elephant_mult
            "rr_ratio":         4.0,    # repurposed → bar_count_target
            "timeframe":        "Daily",
        },
    },
    # ── Adaptive Support & Resistance Pro ────────────────────────────────────
    {
        "id":          "adaptive_sr_gbpaud",
        "name":        "Adaptive S/R Pro — GBP/AUD",
        "symbol":      "GBPAUD",
        "description": (
            "Hybrid momentum + RSI + pivot engine that auto-identifies institutional "
            "supply/demand zones. CMO via HMA-12/26 detects momentum shifts; "
            "RSI-9 < 25 gates BUY signals (structural support); RSI-9 > 75 gates "
            "SELL signals (institutional resistance). One signal per fresh level. "
            "Strict RSI thresholds eliminate noise on GBP pairs. "
            "SL: ATR-1.5 × level buffer. TP: 1:2.5 R:R."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 2.5, "sl_distance": 1.5},
    },
    {
        "id":          "adaptive_sr_xauusd",
        "name":        "Adaptive S/R Pro — XAU/USD",
        "symbol":      "XAUUSD",
        "description": (
            "Adaptive S/R engine on Gold 1H. HMA momentum shift from a key pivot "
            "confirms that institutions are defending the level. RSI-9 thresholds "
            "25/75 filter out mid-range noise. ATR-based SL gives gold's volatility "
            "breathing room while maintaining 1:2.5 RR discipline."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 2.5, "sl_distance": 1.5},
    },
    {
        "id":          "adaptive_sr_btcusdt",
        "name":        "Adaptive S/R Pro — BTC",
        "symbol":      "BTCUSDT",
        "description": (
            "Adaptive S/R applied to BTC 1H. CMO momentum via HMA plus strict "
            "RSI-9 < 25 / > 75 gates catch high-conviction structural bounces at "
            "BTC's institutional supply and demand zones. ATR-1.5 SL calibrated "
            "for crypto volatility. Target: 1:2.5 R:R per trade."
        ),
        "builtin":     True,
        "params":      {"days": 60, "rr_ratio": 2.5, "sl_distance": 1.5},
    },
]


def _load_tester_registry() -> dict:
    try:
        with open(_TESTER_REGISTRY_FILE) as f:
            return json.load(f)
    except Exception:
        return {"strategies": [], "backtest_results": {}}


def _save_tester_registry(reg: dict):
    with open(_TESTER_REGISTRY_FILE, "w") as f:
        json.dump(reg, f, indent=2, default=str)


@app.get("/strategy-tester/list")
def strategy_tester_list():
    """Return all strategies (built-in + user-added) with latest backtest results."""
    reg = _load_tester_registry()
    user_strategies = reg.get("strategies", [])
    results_cache   = reg.get("backtest_results", {})

    combined = []
    for s in _BUILTIN_STRATEGIES:
        entry = dict(s)
        entry["last_result"] = results_cache.get(s["id"])
        combined.append(entry)

    for s in user_strategies:
        entry = dict(s)
        entry["last_result"] = results_cache.get(s["id"])
        combined.append(entry)

    # ── Custom strategies from the Strategy Builder ──────────────────────────
    try:
        from app.strategies.strategy_builder_engine import load_registry as _load_builder_reg
        _sym_map = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "Gold": "XAUUSD"}
        breg = _load_builder_reg()
        for s in breg.get("strategies", []):
            combined.append({
                "id":          s["id"],
                "name":        f"🛠 {s.get('name', s['id'])}",
                "symbol":      _sym_map.get(s.get("asset", "Gold"), "XAUUSD"),
                "description": (
                    f"Custom Builder strategy — {s.get('asset', '?')} · "
                    f"{s.get('timeframe', '?')} · "
                    f"{len(s.get('conditions', []))} entry condition(s), "
                    f"{len(s.get('exit_conditions', []) or [])} exit rule(s). "
                    f"Edit it on the Builder page."
                ),
                "builtin":     False,
                "custom":      True,
                "params":      {"days": 30,
                                "rr_ratio": (s.get("risk") or {}).get("rr_ratio", 2.0)},
                "last_result": results_cache.get(s["id"])
                               or breg.get("results", {}).get(s["id"]),
            })
    except Exception as exc:
        print(f"[Tester] Builder strategies unavailable: {exc}")

    return {"strategies": combined}


@app.post("/strategy-tester/run")
def strategy_tester_run(  # sync def → runs in a worker thread (see note above)
    strategy_id:  str   = "gold_frvp_liquidity_trap",
    days:         int   = 30,
    sl_distance:  float = 8.0,
    rr_ratio:     float = 2.0,
    symbol:       str   = "XAUUSD",
):
    """
    Run a backtest for the given strategy and save the result to registry.
    """
    import time as _t
    reg = _load_tester_registry()

    # ── Custom strategies from the Strategy Builder ──────────────────────────
    if strategy_id.startswith("custom_"):
        from app.strategies.strategy_builder_engine import (
            load_custom_strategy, run_custom_backtest,
        )
        definition = load_custom_strategy(strategy_id)
        if not definition:
            raise HTTPException(status_code=404,
                                detail=f"Custom strategy '{strategy_id}' not found.")
        try:
            result = run_custom_backtest(definition, days=days)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        result.setdefault("day_breakdown", [])
        reg.setdefault("backtest_results", {})[strategy_id] = {
            "summary":       result["summary"],
            "day_breakdown": [],
            "trades":        result["trades"],
            "ran_at":        result["ran_at"],
            "params":        result["params"],
        }
        _save_tester_registry(reg)
        return result

    # ── Gold FRVP strategy ───────────────────────────────────────────────────
    if strategy_id == "gold_frvp_liquidity_trap":
        try:
            from app.strategies.gold_frvp_strategy import run_backtest as frvp_backtest
            result = frvp_backtest(
                days=days,
                sl_distance=sl_distance,
                rr_ratio=rr_ratio,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result["day_breakdown"],
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        {"days": days, "sl_distance": sl_distance, "rr_ratio": rr_ratio},
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── HTF ICT Intraday (BTC / ETH) — maps to ict_signals backtest ─────────
    if strategy_id in ("htf_ict_intraday_btc", "htf_ict_intraday_eth"):
        sym = normalize_symbol(symbol)
        try:
            result = run_strategy_backtest(
                strategy="ict_signals", symbol=sym, days=days, rr=rr_ratio
            )
            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":   result.get("summary", {}),
                "trades":    result.get("trades", [])[-100:],
                "ran_at":    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                "params":    {"days": days, "rr_ratio": rr_ratio, "symbol": sym},
            }
            _save_tester_registry(reg)
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Delegate to existing backtest endpoint for other strategies ──────────
    if strategy_id in ("atr_trailing", "momentum_scalp", "ict_signals",
                        "choch_scalp", "choch_intraday", "choch_swing",
                        "liquidity_sweep"):
        sym = normalize_symbol(symbol)
        try:
            result = run_strategy_backtest(
                strategy=strategy_id, symbol=sym, days=days, rr=rr_ratio
            )
            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":   result.get("summary", {}),
                "trades":    result.get("trades", [])[-100:],
                "ran_at":    datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                "params":    {"days": days, "rr_ratio": rr_ratio, "symbol": sym},
            }
            _save_tester_registry(reg)
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Gold Advanced Strategies ─────────────────────────────────────────────
    if strategy_id in ("london_breakout", "m5_mean_reversion", "h4_break_retest"):
        try:
            from app.strategies.gold_advanced_strategies import (
                run_london_breakout_backtest,
                run_m5_mean_reversion_backtest,
                run_h4_break_retest_backtest,
            )
            _adv_map = {
                "london_breakout":   run_london_breakout_backtest,
                "m5_mean_reversion": run_m5_mean_reversion_backtest,
                "h4_break_retest":   run_h4_break_retest_backtest,
            }
            fn     = _adv_map[strategy_id]
            # H4 break-retest needs more history; default to 180d
            _days  = days if days > 0 else (180 if strategy_id == "h4_break_retest" else days)
            result = fn(days=_days, rr_ratio=rr_ratio)

            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── SMC Liquidity Sweep (BTC / ETH) ─────────────────────────────────────
    if strategy_id == "smc_swing":
        try:
            from app.strategies.smc_swing_strategy import run_smc_swing_backtest
            result = run_smc_swing_backtest(
                symbol=symbol or "BTCUSD",
                days=days,
                rr_ratio=rr_ratio,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── BTC Dual-TF Momentum ─────────────────────────────────────────────────
    if strategy_id == "btc_momentum":
        try:
            from app.strategies.eth_momentum_strategy import run_eth_momentum_backtest
            _rsi_low  = float(sl_distance) if 10 <= sl_distance <= 49 else 45.0
            _rsi_high = float(rr_ratio)    if 50 <= rr_ratio    <= 90 else 65.0
            result = run_eth_momentum_backtest(
                symbol   = symbol or "BTCUSDT",
                days     = days,
                rsi_low  = _rsi_low,
                rsi_high = _rsi_high,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── ETH Dual-TF Momentum ─────────────────────────────────────────────────
    if strategy_id == "eth_momentum":
        try:
            from app.strategies.eth_momentum_strategy import run_eth_momentum_backtest
            # rsi_low / rsi_high come in via sl_distance / rr_ratio repurposed,
            # or fall back to defaults if the caller doesn't supply them.
            _rsi_low  = float(sl_distance) if 10 <= sl_distance <= 49 else 45.0
            _rsi_high = float(rr_ratio)    if 50 <= rr_ratio    <= 90 else 65.0
            result = run_eth_momentum_backtest(
                symbol=symbol or "ETHUSD",
                days=days,
                rsi_low=_rsi_low,
                rsi_high=_rsi_high,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Gold HTF Liquidity Sweep ─────────────────────────────────────────────
    if strategy_id == "htf_liquidity_sweep":
        try:
            from app.strategies.gold_htf_sweep_strategy import run_htf_sweep_backtest
            result = run_htf_sweep_backtest(
                days=days,
                rr_ratio=rr_ratio if rr_ratio and rr_ratio > 0 else 5.0,
                max_risk_points=sl_distance if sl_distance and sl_distance > 0 else 4.0,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Oliver Velez Swing — BTC ─────────────────────────────────────────────
    if strategy_id == "oliver_velez_btc":
        try:
            from app.strategies.oliver_velez_strategy import run_oliver_velez_backtest
            # BTC defaults: elephant_mult=1.5, bar_count=3, location_tol=2%
            _elephant  = float(sl_distance) if 1.0 <= sl_distance <= 5.0 else 1.5
            _bar_count = int(rr_ratio)      if 2   <= rr_ratio    <= 6   else 3
            result = run_oliver_velez_backtest(
                symbol           = symbol or "BTCUSDT",
                days             = days,
                elephant_mult    = _elephant,
                bar_count_target = _bar_count,
                location_tol     = 0.02,   # 2% — BTC swings wider around MAs
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Oliver Velez Swing (Stocks) ──────────────────────────────────────────
    if strategy_id == "oliver_velez_swing":
        try:
            from app.strategies.oliver_velez_strategy import run_oliver_velez_backtest
            # sl_distance is repurposed as elephant_mult (default 2.0, range 1.5–3.0)
            # rr_ratio    is repurposed as bar_count_target (default 4, valid 3–5)
            _elephant = float(sl_distance) if 1.0 <= sl_distance <= 5.0 else 2.0
            _bar_count = int(rr_ratio)     if 2   <= rr_ratio    <= 6   else 4
            result = run_oliver_velez_backtest(
                symbol          = symbol or "AAPL",
                days            = days,
                elephant_mult   = _elephant,
                bar_count_target= _bar_count,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Adaptive S/R Pro ─────────────────────────────────────────────────────
    if strategy_id in ("adaptive_sr_gbpaud", "adaptive_sr_xauusd", "adaptive_sr_btcusdt"):
        try:
            from app.strategies.adaptive_sr_strategy import run_adaptive_sr_backtest

            _sym_map = {
                "adaptive_sr_gbpaud":   "GBPAUD",
                "adaptive_sr_xauusd":   "XAUUSD",
                "adaptive_sr_btcusdt":  "BTCUSDT",
            }
            _sym = symbol or _sym_map[strategy_id]
            # sl_distance is repurposed as sl_atr_mult (default 1.5, valid 0.5–3.0)
            _sl_atr = float(sl_distance) if 0.5 <= sl_distance <= 3.0 else 1.5

            result = run_adaptive_sr_backtest(
                symbol      = _sym,
                days        = days,
                rr_ratio    = rr_ratio if rr_ratio and rr_ratio > 0 else 2.5,
                sl_atr_mult = _sl_atr,
            )
            if "error" in result:
                raise HTTPException(status_code=503, detail=result["error"])

            reg.setdefault("backtest_results", {})[strategy_id] = {
                "summary":       result["summary"],
                "day_breakdown": result.get("day_breakdown", []),
                "trades":        result["trades"],
                "ran_at":        result["ran_at"],
                "params":        result["params"],
            }
            _save_tester_registry(reg)
            return result

        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    raise HTTPException(status_code=400, detail=f"Unknown strategy_id: {strategy_id}")


@app.delete("/strategy-tester/{strategy_id}")
def strategy_tester_delete(strategy_id: str):
    """
    Delete a user-added strategy from the tester registry.
    Built-in strategies cannot be deleted.
    """
    builtin_ids = {s["id"] for s in _BUILTIN_STRATEGIES}
    if strategy_id in builtin_ids:
        raise HTTPException(status_code=400, detail="Built-in strategies cannot be deleted.")

    reg = _load_tester_registry()
    before = len(reg.get("strategies", []))
    reg["strategies"] = [s for s in reg.get("strategies", []) if s["id"] != strategy_id]
    reg.get("backtest_results", {}).pop(strategy_id, None)

    if len(reg["strategies"]) == before:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found.")

    _save_tester_registry(reg)
    return {"success": True, "deleted": strategy_id}


@app.post("/strategy-tester/deploy")
def strategy_tester_deploy(strategy_id: str, mode: str = "demo"):
    """
    Deploy a backtested strategy to demo or live trading by activating it
    in the strategy config. Mode: 'demo' | 'live' | 'paper'.
    """
    if mode not in ("demo", "live", "paper"):
        raise HTTPException(status_code=400, detail="mode must be demo, live, or paper")

    cfg = _load_strategy_config()
    key = f"active_strategies_{mode}"
    current = cfg.get(key, cfg.get("active_strategies", []))
    if strategy_id not in current:
        current.append(strategy_id)
    cfg[key] = current
    _save_strategy_config(cfg)

    return {
        "success":     True,
        "strategy_id": strategy_id,
        "deployed_to": mode,
        "active":      current,
    }


# ══════════════════════════════════════════════════════════════════════════════
# License — activation codes (6 / 12 months)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/license/status")
def license_status():
    from app.services.license_service import get_status
    return get_status()


@app.post("/license/activate")
def license_activate(payload: dict = Body(...)):
    from app.services.license_service import activate
    code = (payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Enter an activation code.")
    try:
        return activate(code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# App Settings — Telegram credentials + MT5 terminal accounts
# ══════════════════════════════════════════════════════════════════════════════

def _mask(value, keep=4):
    """Show only the last few characters of a secret."""
    s = str(value or "")
    return ("•" * max(len(s) - keep, 0)) + s[-keep:] if s else ""


@app.get("/settings")
def get_app_settings():
    """Current Telegram + MT5 settings. Secrets are masked — the UI sends new
    values only for fields the user actually changes."""
    from app.services.telegram_service import get_telegram_config
    token, chat = get_telegram_config()

    mt5 = {}
    if _MT4_ENABLED:
        try:
            cfg = load_config()
            mt5 = {
                "mode":          cfg.get("mode", "paper"),
                "login":         cfg.get("login"),
                "password":      _mask(cfg.get("password")),
                "server":        cfg.get("server"),
                "live_login":    cfg.get("live_login"),
                "live_password": _mask(cfg.get("live_password")),
                "live_server":   cfg.get("live_server"),
            }
        except Exception:
            pass

    return {
        "telegram": {"bot_token": _mask(token, 6), "chat_id": chat},
        "mt5":      mt5,
        "mt5_available": _MT4_ENABLED,
    }


@app.post("/settings")
def save_app_settings(payload: dict = Body(...)):
    """
    Update settings. Only provided, non-empty fields are changed.
    Body: { "telegram": {bot_token, chat_id},
            "mt5": {login, password, server, live_login, live_password, live_server} }
    """
    import json as _j
    from app.services.telegram_service import SETTINGS_FILE

    changed = []

    tg = payload.get("telegram") or {}
    if tg.get("bot_token") or tg.get("chat_id"):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = _j.load(f)
        except Exception:
            s = {}
        if tg.get("bot_token") and "•" not in tg["bot_token"]:
            s["telegram_bot_token"] = tg["bot_token"].strip()
            changed.append("telegram_bot_token")
        if tg.get("chat_id"):
            s["telegram_chat_id"] = str(tg["chat_id"]).strip()
            changed.append("telegram_chat_id")
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            _j.dump(s, f, indent=2)

    mt5 = payload.get("mt5") or {}
    if mt5 and _MT4_ENABLED:
        cfg = load_config()
        for key in ("login", "server", "live_login", "live_server"):
            if mt5.get(key) not in (None, ""):
                cfg[key] = int(mt5[key]) if "login" in key else str(mt5[key]).strip()
                changed.append(key)
        for key in ("password", "live_password"):
            val = mt5.get(key)
            if val and "•" not in val:               # ignore masked placeholders
                cfg[key] = val
                changed.append(key)
        save_config(cfg)

    return {"success": True, "changed": changed}


# ══════════════════════════════════════════════════════════════════════════════
# Money Management — live market info for position sizing
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/money/market-info")
def money_market_info(asset: str = "Gold", timeframe: str = "1h"):
    """
    Live price + ATR + contract specs for the Money Management calculator.
    contract_size: USD P&L per 1.0 lot per 1 point of price movement.
    """
    if asset not in ("BTC", "ETH", "Gold"):
        raise HTTPException(status_code=400, detail="asset must be 'BTC', 'ETH', or 'Gold'")
    if timeframe not in ("15m", "1h"):
        raise HTTPException(status_code=400, detail="timeframe must be '15m' or '1h'")

    contract = {"BTC": 1.0, "ETH": 1.0, "Gold": 100.0}[asset]
    mt5_sym  = {"BTC": "BTCUSD", "ETH": "ETHUSD", "Gold": "XAUUSD+"}[asset]

    try:
        if asset == "Gold":
            from app.services.gold_service import get_multi_timeframe_data as _gold_mtf
            df = (_gold_mtf("XAUUSD") or {}).get(timeframe)
        else:
            df = get_recent_candles_df("BTCUSDT" if asset == "BTC" else "ETHUSDT",
                                       timeframe, 120)
        if df is None or df.empty or len(df) < 20:
            raise RuntimeError("no candle data")
        from app.strategies.strategy_builder_engine import _atr
        atr   = float(_atr(df, 14).iloc[-1])
        price = float(df["close"].iloc[-1])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {exc}")

    current_lot = None
    if _MT4_ENABLED:
        try:
            current_lot = (load_config().get("lot_sizes") or {}).get(mt5_sym)
        except Exception:
            pass

    return {
        "asset":         asset,
        "mt5_symbol":    mt5_sym,
        "timeframe":     timeframe,
        "price":         round(price, 2),
        "atr":           round(atr, 2),
        "contract_size": contract,
        "min_lot":       0.01,
        "lot_step":      0.01,
        "current_lot":   current_lot,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Strategy Builder — user-composed strategies (backtest → save → deploy)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/strategy-builder/options")
def strategy_builder_options():
    """Condition catalogues for the builder dropdowns (entries + exits)."""
    from app.strategies.strategy_builder_engine import CONDITION_CATALOGUE, EXIT_CATALOGUE
    return {"conditions": CONDITION_CATALOGUE, "exits": EXIT_CATALOGUE}


@app.post("/strategy-builder/backtest")
def strategy_builder_backtest(payload: dict = Body(...)):
    """
    Backtest a strategy definition (does not save it).
    Body: { "definition": {...}, "days": 30 }
    """
    from app.strategies.strategy_builder_engine import run_custom_backtest
    definition = payload.get("definition")
    days       = int(payload.get("days", 30))
    if not definition or not definition.get("conditions"):
        raise HTTPException(status_code=400, detail="Add at least one condition.")
    try:
        return run_custom_backtest(definition, days=days)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/strategy-builder/save")
def strategy_builder_save(payload: dict = Body(...)):
    """
    Save (or update) a strategy definition. Body: { "definition": {...}, "result": {...} }
    Assigns an id like custom_<epoch> when missing.
    """
    import uuid as _uuid
    from app.strategies.strategy_builder_engine import load_registry, save_registry
    definition = payload.get("definition")
    if not definition or not definition.get("name"):
        raise HTTPException(status_code=400, detail="Strategy needs a name.")
    if not definition.get("id"):
        definition["id"] = f"custom_{_uuid.uuid4().hex[:10]}"
    if not definition["id"].startswith("custom_"):
        definition["id"] = f"custom_{definition['id']}"

    reg = load_registry()
    reg["strategies"] = [s for s in reg.get("strategies", []) if s.get("id") != definition["id"]]
    reg["strategies"].append(definition)
    if payload.get("result"):
        reg.setdefault("results", {})[definition["id"]] = {
            "summary": payload["result"].get("summary", {}),
            "ran_at":  payload["result"].get("ran_at", ""),
        }
    save_registry(reg)
    return {"success": True, "id": definition["id"]}


@app.get("/strategy-builder/list")
def strategy_builder_list():
    """All saved custom strategies with their last backtest summary + deployment."""
    from app.strategies.strategy_builder_engine import load_registry
    from app.services.live_signal_service import get_asset_strategy_config
    reg = load_registry()
    cfg = get_asset_strategy_config()
    out = []
    for s in reg.get("strategies", []):
        deployed_to = [a for a, ids in cfg.items() if s["id"] in ids]
        out.append({
            "definition":  s,
            "last_result": reg.get("results", {}).get(s["id"]),
            "deployed_to": deployed_to,
        })
    return {"strategies": out}


@app.delete("/strategy-builder/{strategy_id}")
def strategy_builder_delete(strategy_id: str):
    """Delete a custom strategy and detach it from any asset."""
    from app.strategies.strategy_builder_engine import load_registry, save_registry
    from app.services.live_signal_service import (
        _load_asset_strategy_config, _save_asset_strategy_config,
    )
    reg    = load_registry()
    before = len(reg.get("strategies", []))
    reg["strategies"] = [s for s in reg.get("strategies", []) if s.get("id") != strategy_id]
    reg.get("results", {}).pop(strategy_id, None)
    if len(reg["strategies"]) == before:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found.")
    save_registry(reg)

    cfg = _load_asset_strategy_config()
    changed = False
    for asset, ids in cfg.items():
        if strategy_id in ids:
            ids.remove(strategy_id)
            changed = True
    if changed:
        _save_asset_strategy_config(cfg)
    return {"success": True, "deleted": strategy_id}


@app.post("/strategy-builder/deploy")
def strategy_builder_deploy(payload: dict = Body(...)):
    """
    Attach/detach a saved custom strategy to an asset's live signal engine.
    Body: { "strategy_id": "custom_...", "asset": "Gold", "enable": true }
    """
    from app.strategies.strategy_builder_engine import load_custom_strategy
    from app.services.live_signal_service import (
        _load_asset_strategy_config, _save_asset_strategy_config,
    )
    strategy_id = payload.get("strategy_id", "")
    asset       = payload.get("asset", "")
    enable      = bool(payload.get("enable", True))

    if asset not in ("BTC", "ETH", "Gold"):
        raise HTTPException(status_code=400, detail="asset must be 'BTC', 'ETH', or 'Gold'")
    if not load_custom_strategy(strategy_id):
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found — save it first.")

    cfg = _load_asset_strategy_config()
    current = cfg.get(asset, [])
    if enable and strategy_id not in current:
        current.append(strategy_id)
    if not enable and strategy_id in current:
        current.remove(strategy_id)
    cfg[asset] = current
    _save_asset_strategy_config(cfg)
    return {"success": True, "asset": asset, "strategies": current}


# ══════════════════════════════════════════════════════════════════════════════
# Gold Real-Time Feed — MT5 tick feed status
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/gold/feed-status")
def gold_feed_status():
    """
    Returns the current state of the MT5 Gold tick feed.
    status: 'stopped' | 'connecting' | 'live' | 'error'
    Source is always 'mt5' — data comes from your Vantage Markets terminal.
    """
    try:
        from app.services.gold_realtime_service import get_latest_tick
        tick = get_latest_tick()
        return {
            "status":     tick.get("status", "stopped"),
            "connected":  tick.get("connected", False),
            "last_price": tick.get("last"),
            "bid":        tick.get("bid"),
            "ask":        tick.get("ask"),
            "spread":     tick.get("spread"),
            "source":     tick.get("source", "none"),
            "updated_at": tick.get("updated_at"),
            "error":      tick.get("error"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/signals/strategy-catalogue")
def get_strategy_catalogue():
    """All known strategies with metadata (label, desc, assets, live status)."""
    try:
        from app.services.live_signal_service import get_strategy_catalogue
        return get_strategy_catalogue()
    except Exception as e:
        return {}


@app.get("/signals/asset-strategies")
def get_asset_strategies():
    """Which strategy is assigned to each asset. {"BTC": "HTF_ICT_Intraday", ...}"""
    try:
        from app.services.live_signal_service import get_asset_strategy_config
        return get_asset_strategy_config()
    except Exception as e:
        return {"BTC": "HTF_ICT_Intraday", "ETH": "HTF_ICT_Intraday", "Gold": "HTF_ICT_Intraday"}


@app.post("/signals/asset-strategies/toggle")
def toggle_asset_strategy_endpoint(asset: str, strategy_id: str):
    """
    Toggle a strategy on/off for an asset.
    Adds it if not present, removes it if already assigned.
    Will not remove the last remaining strategy (must keep at least one).
    asset: 'BTC' | 'ETH' | 'Gold'
    strategy_id: key from /signals/strategy-catalogue
    """
    if asset not in ("BTC", "ETH", "Gold"):
        raise HTTPException(status_code=400, detail="asset must be 'BTC', 'ETH', or 'Gold'")
    try:
        from app.services.live_signal_service import toggle_asset_strategy, get_strategy_catalogue
        catalogue = get_strategy_catalogue()   # built-in + custom strategies
        if strategy_id not in catalogue:
            raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy_id}")
        cfg   = toggle_asset_strategy(asset, strategy_id)
        strat = catalogue[strategy_id]
        now_on = strategy_id in cfg.get(asset, [])
        print(f"[Assets] {asset} · {strat['label']} → {'ON' if now_on else 'OFF'}")
        return cfg
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/signals/assets")
def get_signal_assets():
    """
    Returns which assets are currently enabled for live signal generation.
    Response: {"BTC": true, "ETH": true, "Gold": true}
    """
    try:
        from app.services.live_signal_service import get_asset_config
        return get_asset_config()
    except Exception as e:
        return {"BTC": True, "ETH": True, "Gold": True}


@app.post("/signals/assets/toggle")
def toggle_signal_asset(asset: str):
    """
    Toggle signal generation on/off for a given asset.
    asset: 'BTC' | 'ETH' | 'Gold'
    Returns the updated config.
    """
    if asset not in ("BTC", "ETH", "Gold"):
        raise HTTPException(status_code=400, detail="asset must be 'BTC', 'ETH', or 'Gold'")
    try:
        from app.services.live_signal_service import toggle_asset
        cfg = toggle_asset(asset)
        state = "enabled" if cfg[asset] else "disabled"
        print(f"[Assets] {asset} signals {state}")
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/gold/cache-info")
def gold_cache_info():
    """
    Returns info about the MT5 history cache on disk.
    Response: { files: [...], total_size_kb: float, cache_dir: str }
    """
    try:
        from app.services.gold_mt5_history import get_cache_info
        return get_cache_info()
    except Exception as e:
        return {"files": [], "total_size_kb": 0.0, "error": str(e)}


@app.delete("/gold/cache")
def gold_clear_cache():
    """
    Deletes all cached MT5 history files from backend/mt5_cache/.
    Frees disk space; next backtest re-fetches from MT5.
    Response: { deleted_files: int, freed_kb: float }
    """
    try:
        from app.services.gold_mt5_history import clear_cache
        result = clear_cache()
        print(f"[Cache] Cleared MT5 history cache: {result['deleted_files']} files, {result['freed_kb']:.1f} KB freed")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cache/clear-all")
def clear_all_caches():
    """
    Clears ALL SMT caches in one shot:
      1. In-memory Gold market data cache (_gold_cache)
      2. In-memory per-symbol market data cache (_market_cache)
      3. MT5 OHLCV history files  (backend/mt5_cache/*.json)
      4. Strategy Tester backtest results  (strategy_tester_registry.json)

    Returns a summary of what was cleared.
    """
    global _gold_cache, _market_cache

    report = {}

    # 1. In-memory caches
    _gold_cache   = {"data": None, "ts": 0}
    _market_cache = {}
    report["memory_caches"] = "cleared"

    # 2. MT5 file cache
    try:
        from app.services.gold_mt5_history import clear_cache as _clear_mt5
        mt5_result = _clear_mt5()
        report["mt5_cache"] = mt5_result
    except Exception as e:
        report["mt5_cache"] = {"error": str(e)}

    # 3. Tester registry backtest results (keep strategy list, wipe stored results)
    try:
        reg = _load_tester_registry()
        cleared_results = len(reg.get("backtest_results", {}))
        reg["backtest_results"] = {}
        _save_tester_registry(reg)
        report["tester_results"] = {"cleared": cleared_results}
    except Exception as e:
        report["tester_results"] = {"error": str(e)}

    print(f"[Cache] clear-all: {report}")
    return {"status": "ok", "cleared": report}


# ══════════════════════════════════════════════════════════════════════════════
# Static frontend — serve the built dashboard (frontend/dist) from the backend
# so the packaged app needs no Node.js. Must stay LAST so API routes win.
# ══════════════════════════════════════════════════════════════════════════════
try:
    from fastapi.staticfiles import StaticFiles
    _DIST_DIR = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    )
    if os.path.isdir(_DIST_DIR):
        app.mount("/app", StaticFiles(directory=_DIST_DIR, html=True), name="frontend")
        print(f"[Frontend] Serving built dashboard at /app from {_DIST_DIR}")
except Exception as _exc:
    print(f"[Frontend] Static serving disabled: {_exc}")
