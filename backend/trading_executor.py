"""
trading_executor.py — Smart Money Trader
MT5 bridge: paper / demo / live modes

Order-expiry timeouts by trade type (auto-cancel pending orders):
  scalp     → 2 hours
  intraday  → 8 hours   (default)
  swing     → 48 hours

Key rules:
  - NEVER call mt5.login() — it disconnects the terminal
  - NEVER call mt5.shutdown() — it drops the broker connection
  - Use the account already logged-in in MT5 terminal
  - Cache account info (60s) to avoid polling MT5 every 15s
"""

import os, json, time
from datetime import datetime, timezone, timedelta

# Magic numbers stamped on THIS app's orders. The MT5 account is shared with
# other bots (AlphaEdge, manual trades), so History/P&L must filter on these so
# only Smart Money Trader's own trades are counted.
#   20260101 → main executor (EMA etc.)   202609 → momentum_scalper strategy
SMT_MAGICS = {20260101, 202609}


def _notify_trade(event, info):
    """Telegram alert restricted to ACTUAL trade events (opened / closed) — no
    signal-generation broadcasts, per user preference."""
    try:
        from app.services.telegram_service import send_alert
    except Exception:
        return
    try:
        sym = info.get("symbol", "?")
        d   = str(info.get("direction", "")).upper()
        arrow = "▲ BUY" if d.startswith("B") else "▼ SELL"
        if event == "opened":
            msg = (f"🟢 <b>TRADE OPENED</b>\n\n"
                   f"{arrow} {sym}  ({info.get('mode','')})\n"
                   f"Entry: {info.get('entry')}\n"
                   f"SL: {info.get('sl')}   TP: {info.get('tp')}\n"
                   f"Lot: {info.get('lot')}"
                   + (f"\n#{info.get('ticket')}" if info.get("ticket") else ""))
        else:
            pnl = info.get("realized_usd")
            # Spell out PROFIT / LOSS with a signed $ amount — no ambiguity.
            if isinstance(pnl, (int, float)):
                word  = "✅ PROFIT" if pnl >= 0 else "🔴 LOSS"
                pnl_s = f"{word}  {'+' if pnl >= 0 else '-'}${abs(pnl):.2f}"
            else:
                pnl_s = "—"
            msg = (f"📉 <b>TRADE CLOSED</b>\n\n"
                   f"{sym} {d}\nResult: {pnl_s}"
                   + (f"\n#{info.get('ticket')}" if info.get("ticket") else ""))
        send_alert(msg)
    except Exception:
        pass


def _notified_file():
    return os.path.join(os.path.dirname(__file__), "notified_closed.json")

_baseline_inited = False
def _baseline_notified_once():
    """First run: silently record already-closed tickets so historical closes
    don't all alert on startup."""
    global _baseline_inited
    if _baseline_inited:
        return
    _baseline_inited = True
    fp = _notified_file()
    if os.path.exists(fp):
        return
    seen = set()
    try:
        for t in load_all_trades():
            if t.get("status") == "closed" and t.get("ticket"):
                seen.add(str(t.get("ticket")))
    except Exception:
        pass
    try:
        with open(fp, "w") as f:
            json.dump(list(seen), f)
    except Exception:
        pass

def _maybe_notify_close(ticket, symbol, direction, pnl):
    """Send a 'trade closed' alert exactly once per ticket (file-based dedup)."""
    if not ticket:
        return
    fp = _notified_file()
    try:
        with open(fp) as f:
            s = set(map(str, json.load(f)))
    except Exception:
        s = set()
    tk = str(ticket)
    if tk in s:
        return
    _notify_trade("closed", {"symbol": symbol, "direction": direction,
                             "realized_usd": pnl, "ticket": ticket})
    s.add(tk)
    try:
        with open(fp, "w") as f:
            json.dump(list(s)[-3000:], f)
    except Exception:
        pass


# ── Native MT5 expiry timestamps ──────────────────────────────────────────────

def _expiry_timestamp(setup: str) -> int:
    """
    Return a UNIX timestamp for MT5's ORDER_TIME_SPECIFIED expiry.
    MT5 will auto-delete the pending order at this time — even if Python is offline.

    Timeouts by strategy type (detected from setup tag):
      scalp     →  2 hours
      intraday  →  8 hours  (default)
      swing     → 48 hours
    """
    s = (setup or "").lower()
    if any(k in s for k in ("scalp", "5m", "m5", "1m", "m1")):
        delta = timedelta(hours=2)
    elif any(k in s for k in ("swing", "daily", "d1", "weekly", "w1", "4h", "h4")):
        delta = timedelta(hours=48)
    else:
        delta = timedelta(hours=8)   # intraday default
    return int((datetime.now(timezone.utc) + delta).timestamp())

_BASE       = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_BASE, "mt4_config.json")

# ── Per-mode trade files ──────────────────────────────────────────────────────
# Each mode stores trades in its own file so demo and live P&L never mix.
_TRADES_FILES = {
    "paper": os.path.join(_BASE, "mt5_trades_paper.json"),
    "demo":  os.path.join(_BASE, "mt5_trades_demo.json"),
    "live":  os.path.join(_BASE, "mt5_trades_live.json"),
}
_LEGACY_TRADES_FILE = os.path.join(_BASE, "mt5_trades.json")

def _trades_file(mode: str = None) -> str:
    """Return the trades file path for the given (or current) mode."""
    m = mode or get_mode()
    return _TRADES_FILES.get(m, _TRADES_FILES["demo"])

def _migrate_legacy_trades():
    """
    One-time migration: if the old mt5_trades.json exists and
    mt5_trades_demo.json does not yet, copy it across so no history is lost.
    """
    demo_file = _TRADES_FILES["demo"]
    if os.path.exists(_LEGACY_TRADES_FILE) and not os.path.exists(demo_file):
        import shutil
        shutil.copy2(_LEGACY_TRADES_FILE, demo_file)
        print("[Executor] Migrated mt5_trades.json → mt5_trades_demo.json")

_migrate_legacy_trades()  # runs once at import time

SYMBOL_MAP = {
    "BTCUSDT": "BTCUSD",
    "ETHUSDT": "ETHUSD",
    "XAUUSD":  "XAUUSD+"
}

DEFAULT_LOT_SIZES = {
    "BTCUSD":  0.01,
    "ETHUSD":  0.01,
    "XAUUSD+": 0.01
}

# ── FRIDAY GOLD CUTOFF ───────────────────────────────────────────────────────
# Vantage XAUUSD+ hours, verified from actual M1 bar data (server = UTC+3):
# daily open 03:30 IST → close 02:27 IST next day. Normal Fridays run the full
# session (close ~02:26 IST Sat), but US-HOLIDAY Fridays (Juneteenth 19-Jun-26,
# July-4th-observed 3-Jul-26) closed at 22:29 IST with no warning — a position
# caught there holds through a 2.5-day weekend gap.
# Rule: gold is flat before 22:30 IST EVERY Friday.
GOLD_FRI_BLOCK_MIN   = 21 * 60 + 45   # 21:45 IST Fri — reject new gold entries
GOLD_FRI_FLATTEN_MIN = 22 * 60 + 15   # 22:15 IST Fri — close open gold positions
SMT_MAGICS = {20260101, 202609}       # only OUR trades — the terminal is shared


def _gold_friday_state():
    """(block_new, flatten) for the Friday gold cutoff, in IST."""
    from datetime import datetime, timezone, timedelta
    ist = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
    if ist.weekday() != 4:   # Friday only
        return False, False
    mins = ist.hour * 60 + ist.minute
    return mins >= GOLD_FRI_BLOCK_MIN, mins >= GOLD_FRI_FLATTEN_MIN


def flatten_gold_friday():
    """Close SMT's open XAU positions and cancel SMT's pending XAU orders before
    the (possibly early) Vantage Friday close. Magic-filtered — never touches
    AlphaEdge's or other apps' positions on the shared terminal. Idempotent."""
    _, flatten = _gold_friday_state()
    if not flatten:
        return {"closed": [], "cancelled": []}
    mt5c, _ = _connect()
    if mt5c is None:
        return {"closed": [], "cancelled": [], "error": "MT5 not connected"}
    import MetaTrader5 as mt5_lib
    closed, cancelled = [], []
    for pos in (mt5_lib.positions_get() or []):
        if pos.magic not in SMT_MAGICS or "XAU" not in pos.symbol.upper():
            continue
        close_type = mt5_lib.ORDER_TYPE_SELL if pos.type == 0 else mt5_lib.ORDER_TYPE_BUY
        tick = mt5_lib.symbol_info_tick(pos.symbol)
        if tick is None:
            continue
        price = tick.bid if close_type == mt5_lib.ORDER_TYPE_SELL else tick.ask
        r = mt5_lib.order_send({
            "action": mt5_lib.TRADE_ACTION_DEAL, "symbol": pos.symbol,
            "volume": pos.volume, "type": close_type, "position": pos.ticket,
            "price": price, "deviation": 30, "magic": pos.magic,
            "comment": "SMT_FRI_GOLD_CLOSE", "type_filling": mt5_lib.ORDER_FILLING_IOC,
        })
        ok = r is not None and r.retcode == mt5_lib.TRADE_RETCODE_DONE
        print(f"[MT5] {'OK' if ok else 'FAIL'} Fri gold cutoff: close #{pos.ticket} {pos.symbol}")
        if ok:
            closed.append(pos.ticket)
    for order in (mt5_lib.orders_get() or []):
        if order.magic not in SMT_MAGICS or "XAU" not in order.symbol.upper():
            continue
        r = mt5_lib.order_send({"action": mt5_lib.TRADE_ACTION_REMOVE, "order": order.ticket})
        ok = r is not None and r.retcode == mt5_lib.TRADE_RETCODE_DONE
        print(f"[MT5] {'OK' if ok else 'FAIL'} Fri gold cutoff: cancel pending #{order.ticket}")
        if ok:
            cancelled.append(order.ticket)
    return {"closed": closed, "cancelled": cancelled}

# ── Config ───────────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {"login": None, "password": None, "server": None,
                "mode": "paper", "lot_sizes": DEFAULT_LOT_SIZES}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_mode():
    return load_config().get("mode", "paper")

def set_mode(mode):
    cfg = load_config()
    cfg["mode"] = mode
    save_config(cfg)
    print(f"[Executor] Mode → {mode.upper()}")

def get_lot_size(mt5_symbol):
    cfg  = load_config()
    lots = cfg.get("lot_sizes", DEFAULT_LOT_SIZES)
    return float(lots.get(mt5_symbol) or
                 lots.get(mt5_symbol.rstrip("+").rstrip(".v"), 0.01))

# ── Trades log ───────────────────────────────────────────────────────────────

def _load_trades(mode: str = None):
    """Load trades for the given mode (defaults to current mode)."""
    try:
        with open(_trades_file(mode)) as f:
            return json.load(f)
    except Exception:
        return []

def _save_trades(t, mode: str = None):
    """Save trades to the file for the given mode (defaults to current mode)."""
    with open(_trades_file(mode), "w") as f:
        json.dump(t, f, indent=2)

def load_all_trades():
    """Load trades from ALL modes combined — used by History page."""
    combined = []
    for m, path in _TRADES_FILES.items():
        try:
            with open(path) as f:
                trades = json.load(f)
                for t in trades:
                    t.setdefault("mode", m)   # ensure mode tag is present
                combined.extend(trades)
        except Exception:
            pass
    return combined

def _ist():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            ).strftime("%Y-%m-%d %H:%M IST")

# ── MT5 connection (NO login, NO shutdown) ───────────────────────────────────

_mt5_initialized = False

def _connect():
    """
    Use the account ALREADY logged-in in MT5 terminal.
    Never calls mt5.login() or mt5.shutdown() — both break the terminal.
    User must login manually in MT5 to the correct account before using demo/live.
    """
    global _mt5_initialized
    try:
        import MetaTrader5 as mt5

        if not _mt5_initialized:
            # Pin to this app's Vantage terminal so it never grabs IntelliTrade's terminal.
            if not mt5.initialize(path=r"C:\Program Files\Vantage Markets MT5 Terminal\terminal64.exe"):
                err = mt5.last_error()
                print(f"[MT5] initialize() failed: {err}")
                if err[0] == -10005:
                    print("[MT5] → Open MT5 terminal and login first")
                return None, None
            _mt5_initialized = True

        info = mt5.account_info()
        if info is None:
            print("[MT5] No active account — login in MT5 terminal first")
            _mt5_initialized = False
            return None, None

        return mt5, info

    except ImportError:
        print("[MT5] MetaTrader5 not installed — run: pip install MetaTrader5")
        return None, None
    except Exception as e:
        print(f"[MT5] Error: {e}")
        _mt5_initialized = False
        return None, None

# ── Account info (60s cache) ─────────────────────────────────────────────────

_mt5_cache      = {}
_mt5_cache_time = 0

def get_account_info(force_refresh=False):
    global _mt5_cache, _mt5_cache_time
    cfg  = load_config()
    mode = cfg.get("mode", "paper")

    if mode == "paper":
        trades   = _load_trades()
        open_pos = [t for t in trades if t.get("status") == "open"]
        return {"mode": "paper", "balance": "Paper",
                "open_trades": len(open_pos), "trades": open_pos[:10]}

    # Return cache if fresh
    if not force_refresh and _mt5_cache and (time.time() - _mt5_cache_time) < 60:
        return _mt5_cache

    mt5, info = _connect()
    if mt5 is None:
        if _mt5_cache:
            result = dict(_mt5_cache)
            result["warning"] = "MT5 disconnected — showing cached data"
            return result
        # Expected accounts per mode
        expected = cfg.get("login") if mode == "demo" else cfg.get("live_login")
        server   = cfg.get("server") if mode == "demo" else cfg.get("live_server")
        return {
            "mode":    mode,
            "error":   f"MT5 not connected — open MT5 and login to {expected} on {server}",
            "balance": "—", "equity": "—", "open_trades": 0
        }

    try:
        import MetaTrader5 as mt5_lib
        positions = mt5_lib.positions_get() or []

        # Warn if wrong account is active (safe — handles null credentials)
        try:
            raw = cfg.get("login") if mode == "demo" else cfg.get("live_login")
            expected_login = int(raw) if raw else 0
        except (TypeError, ValueError):
            expected_login = 0
        if expected_login and info.login != expected_login:
            print(f"[MT5] ⚠️  MT5 shows {info.login} but {mode} needs {expected_login}")
            print(f"[MT5]    Switch accounts in MT5 terminal")

        result = {
            "mode":        mode,
            "login":       info.login,
            "name":        info.name,
            "server":      info.server,
            "balance":     round(info.balance, 2),
            "equity":      round(info.equity, 2),
            "profit":      round(info.profit, 2),
            "free_margin": round(info.margin_free, 2),
            "currency":    info.currency,
            "open_trades": len(positions)
        }
        _mt5_cache      = result
        _mt5_cache_time = time.time()
        return result
    except Exception as e:
        return {"mode": mode, "error": str(e)}

# ── Duplicate order guard ────────────────────────────────────────────────────

# Price tolerance for "same entry" check.
# If an existing pending order's entry is within this % of the new signal's
# entry, the new order is blocked as a duplicate.
_DUPLICATE_PRICE_TOLERANCE_PCT = 0.003   # 0.3%  (e.g. $5 on a $1600 ETH order)

def _is_duplicate_mt5(sym_mt5: str, direction: str, entry: float) -> bool:
    """
    Check MT5 terminal for an existing pending order OR active position on
    the same symbol, same direction, and a similar entry price (within tolerance).

    Bug fixes vs original:
      1. Fail-CLOSED: if MT5 connection fails, block the order (don't allow it).
         This prevents duplicates from slipping through during connectivity gaps.
      2. Also checks positions_get() (active trades), not just orders_get() (pending).
         Without this, once a pending order fills and becomes active, a new
         pending at the same price could be placed alongside it.
    """
    try:
        import MetaTrader5 as mt5_lib

        # ── Check pending orders ──────────────────────────────────────────────
        pending = mt5_lib.orders_get(symbol=sym_mt5)
        if pending is None:
            # orders_get returns None on error — fail CLOSED (block the order)
            err = mt5_lib.last_error()
            print(f"[DupeGuard] ⚠️ orders_get failed for {sym_mt5}: {err} — blocking order to be safe")
            return True

        buy_types  = {mt5_lib.ORDER_TYPE_BUY_LIMIT,  mt5_lib.ORDER_TYPE_BUY_STOP}
        sell_types = {mt5_lib.ORDER_TYPE_SELL_LIMIT, mt5_lib.ORDER_TYPE_SELL_STOP}
        expected_types = buy_types if direction == "BUY" else sell_types

        for o in pending:
            if o.type not in expected_types:
                continue
            price_diff_pct = abs(o.price_open - entry) / entry
            if price_diff_pct <= _DUPLICATE_PRICE_TOLERANCE_PCT:
                print(f"[DupeGuard] 🚫 Blocked duplicate pending | {sym_mt5} {direction} "
                      f"@ {entry:.2f} — existing #{o.ticket} @ {o.price_open:.2f} "
                      f"(diff: {price_diff_pct*100:.3f}%)")
                return True

        # ── Check active positions (filled orders) ────────────────────────────
        positions = mt5_lib.positions_get(symbol=sym_mt5)
        if positions is None:
            positions = []

        pos_buy_type  = 0  # mt5.POSITION_TYPE_BUY
        pos_sell_type = 1  # mt5.POSITION_TYPE_SELL
        expected_pos_type = pos_buy_type if direction == "BUY" else pos_sell_type

        for p in positions:
            if p.type != expected_pos_type:
                continue
            price_diff_pct = abs(p.price_open - entry) / entry
            if price_diff_pct <= _DUPLICATE_PRICE_TOLERANCE_PCT:
                print(f"[DupeGuard] 🚫 Blocked duplicate active | {sym_mt5} {direction} "
                      f"@ {entry:.2f} — active position #{p.ticket} @ {p.price_open:.2f} "
                      f"(diff: {price_diff_pct*100:.3f}%)")
                return True

        return False

    except Exception as e:
        print(f"[DupeGuard] ⚠️ Check exception: {e} — blocking order to be safe")
        return True   # fail CLOSED on any unexpected error


def _is_duplicate_paper(sym_mt5: str, direction: str, entry: float) -> bool:
    """
    Check local paper trade log for an existing open order at similar price.
    """
    trades = _load_trades()
    for t in trades:
        if (t.get("status") == "open"
                and t.get("symbol") == sym_mt5
                and t.get("direction") == direction):
            existing_entry = float(t.get("entry", 0))
            if existing_entry == 0:
                continue
            price_diff_pct = abs(existing_entry - entry) / entry
            if price_diff_pct <= _DUPLICATE_PRICE_TOLERANCE_PCT:
                print(f"[DupeGuard] 🚫 Blocked duplicate paper trade | "
                      f"{sym_mt5} {direction} @ {entry:.2f} — "
                      f"existing {t.get('ticket')} @ {existing_entry:.2f} "
                      f"(diff: {price_diff_pct*100:.3f}%)")
                return True
    return False


# ── Execute signal ───────────────────────────────────────────────────────────

def execute_signal(signal):
    cfg       = load_config()
    mode      = cfg.get("mode", "paper")
    sym_smt   = signal.get("symbol", "BTCUSDT")
    sym_mt5   = SYMBOL_MAP.get(sym_smt, sym_smt)
    lot       = get_lot_size(sym_mt5)
    direction = signal.get("signal", "BUY")
    entry     = float(signal.get("entry", 0))
    sl        = float(signal.get("sl", 0))
    tp        = float(signal.get("tp", 0))

    print(f"\n[MT5] {'📋 PAPER' if mode=='paper' else ('🟡 DEMO' if mode=='demo' else '🔴 LIVE')} | "
          f"{direction} {sym_mt5} @ {entry:.2f} | SL:{sl:.2f} TP:{tp:.2f} | {lot} lots")

    # ── Friday gold cutoff: no new gold entries after 21:45 IST ──────────────
    if "XAU" in sym_mt5.upper():
        block_gold, _ = _gold_friday_state()
        if block_gold:
            return {"success": False, "blocked": True,
                    "error": "Friday gold cutoff: no new XAUUSD entries after 21:45 IST "
                             "(Vantage closes gold at 22:30 IST on US-holiday Fridays; "
                             "open gold is flattened at 22:15 IST)"}

    # ── Duplicate guard: block if a similar order already exists ─────────────
    if mode == "paper":
        if _is_duplicate_paper(sym_mt5, direction, entry):
            return {"success": False, "duplicate": True,
                    "error": f"Duplicate blocked: {direction} {sym_mt5} @ {entry:.2f} already open"}
    elif mode in ("demo", "live"):
        if _is_duplicate_mt5(sym_mt5, direction, entry):
            return {"success": False, "duplicate": True,
                    "error": f"Duplicate blocked: {direction} {sym_mt5} @ {entry:.2f} already pending in MT5"}

    if mode == "paper":
        res = _paper(signal, sym_mt5, direction, entry, sl, tp, lot)
    elif mode in ("demo", "live"):
        res = _live(signal, sym_mt5, direction, entry, sl, tp, lot, mode)
    else:
        return {"success": False, "error": f"Unknown mode: {mode}"}
    # Telegram alert ONLY on an actual trade open (not on signal generation),
    # and only for real MT5 trades (demo/live) — paper is simulated.
    if res.get("success") and mode in ("demo", "live"):
        _notify_trade("opened", {"symbol": sym_mt5, "direction": direction, "entry": entry,
                                 "sl": sl, "tp": tp, "lot": lot, "mode": mode, "ticket": res.get("ticket")})
    return res

def _paper(signal, sym_mt5, direction, entry, sl, tp, lot):
    trade = {
        "id":         len(_load_trades()) + 1,
        "time_ist":   _ist(),
        "timestamp":  int(time.time() * 1000),   # ms epoch — History sorting/grouping
        "symbol":     sym_mt5,
        "direction":  direction,
        "entry":      entry,
        "sl":         sl,
        "tp":         tp,
        "lot":        lot,
        "mode":       "paper",
        "status":       "open",
        "order_type":   "limit",
        "ticket":       f"PAPER-{int(time.time())}",
        "profit":       None,
        "setup":        signal.get("setup", "1H FVG+EMA"),
        "strategy_tag": signal.get("strategy_tag", "")
    }
    trades = _load_trades()
    trades.insert(0, trade)
    _save_trades(trades)
    print(f"[MT5] 📋 Paper trade saved: {trade['ticket']}")
    return {"success": True, "ticket": trade["ticket"], "mode": "paper"}

def _live(signal, sym_mt5, direction, entry, sl, tp, lot, mode):
    mt5, info = _connect()
    if mt5 is None:
        return {"success": False, "error": "MT5 not connected"}

    try:
        import MetaTrader5 as mt5_lib

        sym      = sym_mt5
        sym_info = mt5_lib.symbol_info(sym)
        if sym_info is None:
            for sfx in ["+", ".v", "m", "c"]:
                sym_info = mt5_lib.symbol_info(sym_mt5 + sfx)
                if sym_info:
                    sym = sym_mt5 + sfx
                    break
        if sym_info is None:
            return {"success": False, "error": f"Symbol {sym_mt5} not found"}

        if not sym_info.visible:
            mt5_lib.symbol_select(sym, True)
            time.sleep(0.1)

        order_type  = (mt5_lib.ORDER_TYPE_BUY_LIMIT if direction == "BUY"
                       else mt5_lib.ORDER_TYPE_SELL_LIMIT)
        setup       = signal.get("setup", "1H FVG+EMA")
        expiry_ts   = _expiry_timestamp(setup)
        expiry_str  = datetime.fromtimestamp(expiry_ts, tz=timezone.utc).strftime("%H:%M UTC")

        print(f"[MT5] ⏱  Order expiry set to {expiry_str} (setup: '{setup}')")

        request = {
            "action":       mt5_lib.TRADE_ACTION_PENDING,
            "symbol":       sym,
            "volume":       lot,
            "type":         order_type,
            "price":        round(entry, 2),
            "sl":           round(sl, 2),
            "tp":           round(tp, 2),
            "deviation":    30,
            "magic":        20260101,
            # Stamp the ACTUAL setup into the comment (was hardcoded
            # "SMT_1H_FVG" for every strategy, which corrupted MT5-side
            # attribution — audits grouped all live trades under 1H FVG).
            "comment":      ("SMT " + setup)[:31],
            "type_time":    mt5_lib.ORDER_TIME_SPECIFIED,  # native MT5 expiry
            "expiration":   expiry_ts,                      # UNIX timestamp — MT5 deletes automatically
            "type_filling": mt5_lib.ORDER_FILLING_IOC,
        }

        result = mt5_lib.order_send(request)
        if result.retcode == mt5_lib.TRADE_RETCODE_DONE:
            print(f"[MT5] ✅ Order placed! Ticket: #{result.order}")
            trade = {
                "id":           len(_load_trades()) + 1,
                "time_ist":     _ist(),
                "timestamp":    int(time.time() * 1000),   # ms epoch — History sorting/grouping
                "symbol":       sym,
                "direction":    direction,
                "entry":        entry,
                "sl":           sl,
                "tp":           tp,
                "lot":          lot,
                "mode":         mode,
                "status":       "open",
                "order_type":   "limit",
                "ticket":       result.order,
                "profit":       None,
                "setup":        setup,
                "strategy_tag": signal.get("strategy_tag", ""),
                "expiry_ts":    expiry_ts,
                "expiry_utc":   expiry_str,
            }
            trades = _load_trades()
            trades.insert(0, trade)
            _save_trades(trades)
            return {"success": True, "ticket": result.order, "mode": mode}
        else:
            err = f"retcode={result.retcode} | {result.comment}"
            print(f"[MT5] ❌ Order failed: {err}")
            return {"success": False, "error": err}

    except Exception as e:
        print(f"[MT5] Exception: {e}")
        return {"success": False, "error": str(e)}

# ── Trades log with unrealized P&L ──────────────────────────────────────────

def get_trades_log():
    _baseline_notified_once()   # silence historical closes on first run
    trades      = _load_trades()
    open_trades = [t for t in trades if t.get("status") == "open"]
    # NOTE: even with no open trades we still continue — mt5_state and
    # realized P&L from MT5 history must be refreshed for closed rows too.

    live_prices   = {}
    # Vantage CFD contract sizes: 1 lot = 1 BTC / 1 ETH / 100 oz Gold
    # P&L = price_diff × lot_size × contract_size
    # ETHUSD: 1 lot = 1 ETH → cs=1  (was wrongly 10, causing 10× inflated USD P&L)
    # XAUUSD+: 1 lot = 100 oz, 0.01 lot = 1 oz → cs=100 gives correct per-oz value
    CONTRACT_SIZE = {"BTCUSD": 1, "ETHUSD": 1, "XAUUSD+": 100}
    PRICE_MAP     = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}

    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(_BASE, "app"))
        from services.binance_service import get_recent_candles_df as _gcdf
        for sym in {t["symbol"] for t in open_trades}:
            bsym = PRICE_MAP.get(sym)
            if bsym:
                try:
                    df = _gcdf(symbol=bsym, interval="1m", limit=1)
                    if not df.empty:
                        live_prices[sym] = float(df["close"].iloc[-1])
                except Exception:
                    pass
    except Exception:
        pass

    # ── Query MT5 directly for pending orders and active positions ───────────
    # A limit order ticket appears in mt5.orders_get() while pending.
    # Once triggered it moves to mt5.positions_get().
    pending_tickets  = set()
    active_tickets   = set()
    mt5_active_map   = {}   # ticket → position object (for positions not in local log)
    try:
        import MetaTrader5 as mt5_lib
        pending = mt5_lib.orders_get()
        if pending:
            pending_tickets = {o.ticket for o in pending}
        active = mt5_lib.positions_get()
        if active:
            active_tickets = {p.ticket for p in active}
            mt5_active_map = {p.ticket: p for p in active}
    except Exception:
        pass

    # ── Inject any MT5 positions not in our local log ─────────────────────
    # This catches trades placed directly in MT5 terminal or by other bots.
    local_tickets = {
        int(t["ticket"]) for t in trades
        if t.get("ticket") and str(t["ticket"]).isdigit()
    }
    _REVERSE_SYMBOL_MAP = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "XAUUSD+": "XAUUSD"}
    for ticket, pos in mt5_active_map.items():
        if ticket in local_tickets:
            continue   # already in our log
        sym_mt5 = pos.symbol
        direction = "BUY" if pos.type == 0 else "SELL"
        _dt_ist = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                   .replace(tzinfo=None) + timedelta(seconds=pos.time + 5*3600 + 30*60)
                   ).strftime("%Y-%m-%d %H:%M IST")
        trades.append({
            "ticket":    ticket,
            "symbol":    sym_mt5,
            "direction": direction,
            "entry":     pos.price_open,
            "sl":        pos.sl,
            "tp":        pos.tp,
            "lot":       pos.volume,
            "status":    "open",
            "mode":      "demo",
            "mt5_state": "active",
            "source":    "mt5_direct",
            "timestamp": pos.time * 1000,
            "time_ist":  _dt_ist,
        })

    for t in trades:
        sym    = t.get("symbol", "")
        price  = live_prices.get(sym)
        ticket = t.get("ticket")

        # Determine real-time order state from MT5
        is_numeric_ticket = isinstance(ticket, int) or (isinstance(ticket, str) and ticket.isdigit())
        if is_numeric_ticket:
            int_ticket = int(ticket)
            if int_ticket in pending_tickets:
                t["mt5_state"] = "pending"    # limit order waiting to trigger
            elif int_ticket in active_tickets:
                t["mt5_state"] = "active"     # position is live and running
            else:
                t["mt5_state"] = "closed"     # no longer in MT5 (filled or cancelled)
        else:
            # Paper trade — treat as pending (same limit logic)
            t["mt5_state"] = "pending"

        # Only calculate unrealized P&L for ACTIVE positions, not pending orders
        if t.get("status") != "open" or price is None or t.get("mt5_state") != "active":
            t["unreal_pts"] = None
            t["unreal_usd"] = None
            continue

        entry = float(t.get("entry", 0))
        lot   = float(t.get("lot", 0.01))
        cs    = CONTRACT_SIZE.get(sym, 1)
        pts   = round(price - entry, 4) if t.get("direction") == "BUY" else round(entry - price, 4)
        t["unreal_pts"]    = pts
        t["unreal_usd"]    = round(pts * lot * cs, 2)
        t["current_price"] = price

    # ── Inject closed deals from MT5 history ─────────────────────────────────
    # Fetch the last 30 days of deal history so closed trades appear in SMT
    # even if they were placed/closed outside SMT's local log.
    try:
        import MetaTrader5 as _mt5h
        import datetime as _dt
        # MT5 deal.time is BROKER-SERVER time (can run hours ahead of UTC/local),
        # so a tight upper bound clips just-closed trades out of the trades log /
        # History. Over-shoot it — there are no real deals in the future.
        _from = _dt.datetime.utcnow() - _dt.timedelta(days=31)
        _to   = _dt.datetime.utcnow() + _dt.timedelta(days=1)
        deals = _mt5h.history_deals_get(_from, _to)
        if deals:
            # Build per-position maps:
            #   _pos_open[pos_id]   = the opening deal (DEAL_ENTRY_IN=0)
            #   _pos_profits[pos_id] = net realized P&L (sum of ALL deal profits for that position)
            # Summing all deals gives the true MT5-terminal P&L incl. commission/swap.
            DEAL_ENTRY_IN = 0
            _pos_open    = {}   # position_id → open deal
            _pos_profits = {}   # position_id → net profit
            for d in deals:
                # Only THIS app's trades (shared account with other bots).
                if getattr(d, "magic", 0) not in SMT_MAGICS:
                    continue
                if d.entry == DEAL_ENTRY_IN and d.position_id not in _pos_open:
                    _pos_open[d.position_id] = d
                _pos_profits[d.position_id] = round(
                    _pos_profits.get(d.position_id, 0.0) + d.profit, 2
                )

            # Collect all tickets already in local log so we don't duplicate
            _known_tickets = set()
            for t in trades:
                tk = t.get("ticket")
                if tk and str(tk).isdigit():
                    _known_tickets.add(int(tk))

            def _ts_to_ist(unix_ts):
                try:
                    dt = _dt.datetime(1970, 1, 1) + _dt.timedelta(seconds=unix_ts + 5*3600 + 30*60)
                    return dt.strftime("%Y-%m-%d %H:%M IST")
                except Exception:
                    return ""

            for pos_id, open_deal in _pos_open.items():
                if pos_id in mt5_active_map:
                    continue
                if open_deal.order in _known_tickets:
                    # Ticket is in our local log — attach the realized P&L from
                    # MT5 history to that row so Status/Result update properly.
                    for t in trades:
                        tk = t.get("ticket")
                        if tk and str(tk).isdigit() and int(tk) == open_deal.order:
                            if t.get("realized_usd") is None:
                                t["realized_usd"] = _pos_profits.get(pos_id)
                            if t.get("mt5_state") not in ("active", "pending"):
                                t["mt5_state"] = "closed"
                            # Telegram alert on trade close (once per ticket).
                            _maybe_notify_close(tk, t.get("symbol"), t.get("direction"),
                                                _pos_profits.get(pos_id))
                            break
                    continue

                # Net realized P&L = sum of all deal profits for this position
                realized  = _pos_profits.get(pos_id)  # None if position still open somehow
                direction = "BUY" if open_deal.type == 0 else "SELL"
                trades.append({
                    "ticket":       open_deal.order,
                    "symbol":       open_deal.symbol,
                    "direction":    direction,
                    "entry":        open_deal.price,
                    "sl":           None,
                    "tp":           None,
                    "lot":          open_deal.volume,
                    "status":       "closed",
                    "mode":         "demo",
                    "mt5_state":    "closed",
                    "source":       "mt5_history",
                    "timestamp":    open_deal.time * 1000,
                    "time_ist":     _ts_to_ist(open_deal.time),
                    "close_time":   None,
                    "realized_usd": realized,
                    "unreal_pts":   None,
                    "unreal_usd":   None,
                })
    except Exception as _e:
        print(f"[MT5 History] Could not fetch deal history: {_e}")

    return trades

# ── Cancel duplicate pending orders ──────────────────────────────────────────

def cancel_duplicate_pending_orders():
    """
    Scan all pending orders in MT5. For each group of orders that share the same
    symbol + direction + similar entry price (within 0.3%), keep the OLDEST
    (lowest ticket number = placed first) and cancel all the newer duplicates.

    Also cleans up the local paper trade log the same way.
    Returns a summary of what was cancelled.
    """
    cancelled = []
    errors    = []

    # ── Real MT5 pending orders ───────────────────────────────────────────────
    mt5_conn, _ = _connect()
    if mt5_conn:
        try:
            import MetaTrader5 as mt5_lib
            pending = mt5_lib.orders_get() or []

            buy_types  = {mt5_lib.ORDER_TYPE_BUY_LIMIT,  mt5_lib.ORDER_TYPE_BUY_STOP}
            sell_types = {mt5_lib.ORDER_TYPE_SELL_LIMIT, mt5_lib.ORDER_TYPE_SELL_STOP}

            def direction_of(order):
                return "BUY" if order.type in buy_types else "SELL"

            # Sort oldest first so we always keep the first-placed order
            pending_sorted = sorted(pending, key=lambda o: o.ticket)

            seen = []   # list of (symbol, direction, price) already kept
            for order in pending_sorted:
                sym  = order.symbol
                dirn = direction_of(order)
                p    = order.price_open

                # Check if this order is a duplicate of one we already kept
                is_dup = False
                for (s_sym, s_dir, s_price) in seen:
                    if (s_sym == sym and s_dir == dirn
                            and abs(s_price - p) / p <= _DUPLICATE_PRICE_TOLERANCE_PCT):
                        is_dup = True
                        break

                if is_dup:
                    # Cancel this duplicate
                    result = mt5_lib.order_send({
                        "action": mt5_lib.TRADE_ACTION_REMOVE,
                        "order":  order.ticket,
                    })
                    ok = result and result.retcode == mt5_lib.TRADE_RETCODE_DONE
                    entry = {
                        "ticket":    order.ticket,
                        "symbol":    sym,
                        "direction": dirn,
                        "price":     p,
                        "status":    "cancelled" if ok else "failed",
                        "error":     result.comment if not ok and result else None,
                    }
                    (cancelled if ok else errors).append(entry)
                    print(f"[DupeClean] {'✅' if ok else '❌'} Cancelled duplicate "
                          f"#{order.ticket} {sym} {dirn} @ {p:.2f}")
                else:
                    # Keep this order — record it as the reference price
                    seen.append((sym, dirn, p))
                    print(f"[DupeClean] ✔ Keeping #{order.ticket} {sym} {dirn} @ {p:.2f}")

        except Exception as e:
            errors.append({"error": str(e)})

    # ── Paper trade log cleanup ───────────────────────────────────────────────
    trades  = _load_trades()
    changed = False
    seen_paper = []
    for t in sorted(trades, key=lambda x: x.get("id", 0)):
        if t.get("status") != "open":
            continue
        sym  = t.get("symbol", "")
        dirn = t.get("direction", "")
        p    = float(t.get("entry", 0))
        if p == 0:
            continue

        is_dup = False
        for (s_sym, s_dir, s_price) in seen_paper:
            if (s_sym == sym and s_dir == dirn
                    and abs(s_price - p) / p <= _DUPLICATE_PRICE_TOLERANCE_PCT):
                is_dup = True
                break

        if is_dup:
            t["status"]    = "cancelled"
            t["mt5_state"] = "closed"
            changed = True
            cancelled.append({
                "ticket":    t.get("ticket"),
                "symbol":    sym,
                "direction": dirn,
                "price":     p,
                "status":    "cancelled (paper)",
            })
            print(f"[DupeClean] ✅ Cancelled duplicate paper {t.get('ticket')} {sym} @ {p:.2f}")
        else:
            seen_paper.append((sym, dirn, p))

    if changed:
        _save_trades(trades)

    return {
        "success":   True,
        "cancelled": cancelled,
        "kept":      len(seen) + len(seen_paper),
        "errors":    errors,
    }


# ── Cancel pending orders ────────────────────────────────────────────────────

def cancel_pending_order(ticket):
    """Cancel a single pending limit/stop order by ticket number."""
    mt5_conn, _ = _connect()
    if mt5_conn is None:
        return {"success": False, "error": "MT5 not connected"}
    try:
        import MetaTrader5 as mt5_lib
        ticket = int(ticket)
        result = mt5_lib.order_send({
            "action": mt5_lib.TRADE_ACTION_REMOVE,
            "order":  ticket,
        })
        if result and result.retcode == mt5_lib.TRADE_RETCODE_DONE:
            # Mark the trade as cancelled in our local log
            trades = _load_trades()
            for t in trades:
                if str(t.get("ticket")) == str(ticket):
                    t["status"]    = "cancelled"
                    t["mt5_state"] = "closed"
                    break
            _save_trades(trades)
            print(f"[MT5] ✅ Cancelled pending order #{ticket}")
            return {"success": True, "ticket": ticket}
        err = result.comment if result else "Unknown error"
        print(f"[MT5] ❌ Cancel failed #{ticket}: {err}")
        return {"success": False, "error": err}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _timeout_for_setup(setup: str) -> float:
    """
    Return the max pending-order age (hours) based on the trade's setup tag.
    Scalp setups expire quickly; swing setups get more time.
    """
    s = (setup or "").lower()
    if any(k in s for k in ("scalp", "5m", "m5", "1m", "m1")):
        return 2.0
    if any(k in s for k in ("swing", "daily", "d1", "weekly", "w1", "4h", "h4")):
        return 48.0
    # Default: intraday (1H FVG, EMA, etc.)
    return 8.0


def cancel_expired_orders(max_age_hours=None):
    """
    Cancel all pending orders older than their type-specific timeout.

    If max_age_hours is provided it overrides the per-trade smart timeout
    (useful for manual API calls).  When None the system uses the setup tag
    on each order to decide:  scalp=2h  intraday=8h  swing=48h.

    Also cancels paper trades from our local log that exceed the limit.
    Returns list of cancelled orders with details.
    """
    import MetaTrader5 as mt5_lib
    now       = datetime.now(timezone.utc)
    cancelled = []
    errors    = []

    # ── Cancel real MT5 pending orders ────────────────────────────────────
    mt5_conn, _ = _connect()
    if mt5_conn:
        try:
            orders = mt5_lib.orders_get() or []

            # Build a lookup of setup tags from our local trade log
            local_trades = _load_trades()
            ticket_setup = {str(t.get("ticket")): t.get("setup", "") for t in local_trades}

            for order in orders:
                order_time = datetime.fromtimestamp(order.time_setup, tz=timezone.utc)
                age_hours  = (now - order_time).total_seconds() / 3600

                # Determine timeout: explicit override or smart per-trade
                setup   = ticket_setup.get(str(order.ticket), "")
                timeout = max_age_hours if max_age_hours is not None else _timeout_for_setup(setup)

                if age_hours >= timeout:
                    result = mt5_lib.order_send({
                        "action": mt5_lib.TRADE_ACTION_REMOVE,
                        "order":  order.ticket,
                    })
                    ok = result and result.retcode == mt5_lib.TRADE_RETCODE_DONE
                    entry = {
                        "ticket":     order.ticket,
                        "symbol":     order.symbol,
                        "direction":  "BUY" if order.type in (0, 2, 4) else "SELL",
                        "entry":      order.price_open,
                        "age_hours":  round(age_hours, 1),
                        "status":     "cancelled" if ok else "failed",
                        "error":      result.comment if not ok and result else None,
                    }
                    cancelled.append(entry)
                    if ok:
                        print(f"[MT5] ✅ Auto-cancelled #{order.ticket} {order.symbol} "
                              f"(age: {age_hours:.1f}h > {max_age_hours}h)")
                    else:
                        errors.append(entry)
        except Exception as e:
            errors.append({"error": str(e)})

    # ── Also expire paper/local trades that exceed the age limit ──────────
    trades  = _load_trades()
    changed = False
    for t in trades:
        if t.get("status") != "open":
            continue
        try:
            ticket_str = str(t.get("ticket", ""))
            if ticket_str.startswith("PAPER-"):
                ts     = int(ticket_str.replace("PAPER-", ""))
                placed = datetime.fromtimestamp(ts, tz=timezone.utc)
                age_h  = (now - placed).total_seconds() / 3600

                # Smart timeout per trade type
                setup   = t.get("setup", "")
                timeout = max_age_hours if max_age_hours is not None else _timeout_for_setup(setup)

                if age_h >= timeout:
                    t["status"]    = "cancelled"
                    t["mt5_state"] = "closed"
                    changed = True
                    cancelled.append({
                        "ticket":    ticket_str,
                        "symbol":    t.get("symbol"),
                        "direction": t.get("direction"),
                        "entry":     t.get("entry"),
                        "age_hours": round(age_h, 1),
                        "status":    "cancelled",
                    })
                    print(f"[Paper] ✅ Expired {ticket_str} {t.get('symbol')} "
                          f"(age: {age_h:.1f}h >= {timeout}h limit, setup: '{setup}')")
        except Exception:
            pass

    if changed:
        _save_trades(trades)

    return {
        "success":       True,
        "cancelled":     cancelled,
        "total_checked": len(cancelled) + len(errors),
        "errors":        errors,
    }


# ── Close / cancel a single trade by ticket ──────────────────────────────────

def close_or_cancel_order(ticket):
    """
    Unified close/cancel for any trade type by ticket number:
      - Pending limit/stop order → TRADE_ACTION_REMOVE (cancel)
      - Active position          → TRADE_ACTION_DEAL   (close at market)
      - Paper trade              → mark cancelled in local log only
    """
    import MetaTrader5 as mt5_lib
    ticket = int(ticket) if str(ticket).isdigit() else ticket

    # ── Paper trade ────────────────────────────────────────────────────────
    if isinstance(ticket, str) and ticket.startswith("PAPER-"):
        trades  = _load_trades()
        changed = False
        for t in trades:
            if str(t.get("ticket")) == ticket and t.get("status") == "open":
                t["status"]    = "cancelled"
                t["mt5_state"] = "closed"
                changed = True
                break
        if changed:
            _save_trades(trades)
            return {"success": True, "ticket": ticket, "action": "paper_cancelled"}
        return {"success": False, "error": "Paper trade not found or already closed"}

    # ── Real MT5 order ─────────────────────────────────────────────────────
    mt5_conn, _ = _connect()
    if mt5_conn is None:
        return {"success": False, "error": "MT5 not connected"}

    try:
        # Check if it's a pending order first
        pending = mt5_lib.orders_get() or []
        pending_tickets = {o.ticket for o in pending}

        if ticket in pending_tickets:
            # Cancel pending limit/stop order
            result = mt5_lib.order_send({
                "action": mt5_lib.TRADE_ACTION_REMOVE,
                "order":  ticket,
            })
            action = "cancelled"
        else:
            # Try to close as an active position
            positions = mt5_lib.positions_get() or []
            pos = next((p for p in positions if p.ticket == ticket), None)

            if pos is None:
                return {"success": False, "error": f"Ticket #{ticket} not found in pending orders or active positions"}

            close_type = (mt5_lib.ORDER_TYPE_SELL if pos.type == 0
                          else mt5_lib.ORDER_TYPE_BUY)
            tick  = mt5_lib.symbol_info_tick(pos.symbol)
            price = tick.bid if close_type == mt5_lib.ORDER_TYPE_SELL else tick.ask

            result = mt5_lib.order_send({
                "action":       mt5_lib.TRADE_ACTION_DEAL,
                "symbol":       pos.symbol,
                "volume":       pos.volume,
                "type":         close_type,
                "position":     pos.ticket,
                "price":        price,
                "deviation":    30,
                "magic":        20260101,
                "comment":      "SMT_CLOSE",
                "type_filling": mt5_lib.ORDER_FILLING_IOC,
            })
            action = "closed"

        ok = result and result.retcode == mt5_lib.TRADE_RETCODE_DONE
        if ok:
            # Update local log
            trades = _load_trades()
            for t in trades:
                if str(t.get("ticket")) == str(ticket):
                    t["status"]    = action
                    t["mt5_state"] = "closed"
                    break
            _save_trades(trades)
            print(f"[MT5] ✅ #{ticket} {action}")
            return {"success": True, "ticket": ticket, "action": action}

        err = result.comment if result else "Unknown error"
        print(f"[MT5] ❌ #{ticket} {action} failed: {err}")
        return {"success": False, "error": err}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── EMA-cross TP monitor ─────────────────────────────────────────────────────
#
# Strategy: Instead of a fixed TP, monitor active positions and close when
# price CLOSES through the 20 EMA on the last COMPLETED candle.
#
# Key design decisions (from best practices):
#   1. Use mt5.copy_rates_from_pos() directly — no Binance/Gold dependency,
#      works offline, uses real broker candles not exchange candles.
#   2. Use iloc[-2] (last COMPLETED candle) not iloc[-1] — avoids false exits
#      from transient wicks on the still-forming current candle.
#   3. Magic number filter (20260101) — only manages orders placed by this bot.
#   4. Only acts on EMA-tagged trades — leaves fixed-TP trades alone.

def run_ema_exit_monitor(timeframe=None):
    """
    Check all active EMA-strategy positions via MT5 directly.
    Closes any position where price has closed through the 20 EMA.

    Uses the last COMPLETED candle ([-2]) to avoid wick-based false exits.
    Only manages positions with magic number 20260101 and setup containing 'EMA'.

    timeframe: MT5 timeframe constant (default: TIMEFRAME_H1).
               Pass mt5.TIMEFRAME_M15 for scalp strategies.
    """
    import pandas as pd

    mt5_conn, _ = _connect()
    if mt5_conn is None:
        return {"checked": 0, "closed": [], "error": "MT5 not connected"}

    try:
        import MetaTrader5 as mt5_lib
    except ImportError:
        return {"checked": 0, "closed": [], "error": "MetaTrader5 not installed"}

    if timeframe is None:
        timeframe = mt5_lib.TIMEFRAME_H1

    # Get all open positions placed by this bot
    all_positions = mt5_lib.positions_get() or []
    bot_positions = [p for p in all_positions if p.magic == 20260101]

    if not bot_positions:
        return {"checked": 0, "closed": []}

    closed_list = []
    checked     = 0

    # Group by symbol to fetch rates once per symbol
    by_symbol = {}
    for p in bot_positions:
        by_symbol.setdefault(p.symbol, []).append(p)

    for symbol, positions in by_symbol.items():

        # Fetch last 50 completed bars directly from MT5 (real broker candles)
        rates = mt5_lib.copy_rates_from_pos(symbol, timeframe, 0, 50)
        if rates is None or len(rates) < 22:
            print(f"[EMAMonitor] Not enough bars for {symbol} — skipping")
            continue

        df = pd.DataFrame(rates)
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

        # Use [-2]: last COMPLETED candle — avoids false exits from live wicks
        last_close = float(df["close"].iloc[-2])
        last_ema   = float(df["ema20"].iloc[-2])
        prev_close = float(df["close"].iloc[-3])
        prev_ema   = float(df["ema20"].iloc[-3])

        for p in positions:
            checked += 1

            # Check if this position's trade uses EMA exit logic
            trades    = _load_trades()
            local     = next((t for t in trades if str(t.get("ticket")) == str(p.ticket)), {})
            setup_tag = (local.get("setup") or "").upper()
            if "EMA" not in setup_tag:
                continue  # Fixed-TP trade — MT5 handles it, leave alone

            should_close = False

            # BUY: close if last completed candle closed BELOW 20 EMA
            if p.type == 0 and last_close < last_ema:  # POSITION_TYPE_BUY = 0
                should_close = True
                close_type   = mt5_lib.ORDER_TYPE_SELL
                price        = mt5_lib.symbol_info_tick(symbol).bid
                reason       = f"📉 Price closed under 20 EMA ({last_close:.4f} < {last_ema:.4f})"

            # SELL: close if last completed candle closed ABOVE 20 EMA
            elif p.type == 1 and last_close > last_ema:  # POSITION_TYPE_SELL = 1
                should_close = True
                close_type   = mt5_lib.ORDER_TYPE_BUY
                price        = mt5_lib.symbol_info_tick(symbol).ask
                reason       = f"📈 Price closed above 20 EMA ({last_close:.4f} > {last_ema:.4f})"

            if not should_close:
                continue

            print(f"[EMAMonitor] 🔁 EMA exit | #{p.ticket} {symbol} "
                  f"{'BUY' if p.type == 0 else 'SELL'} | {reason}")

            close_req = {
                "action":       mt5_lib.TRADE_ACTION_DEAL,
                "symbol":       symbol,
                "volume":       p.volume,
                "type":         close_type,
                "position":     p.ticket,
                "price":        price,
                "deviation":    20,
                "magic":        20260101,
                "comment":      "SMT_EMA20_Exit",
                "type_time":    mt5_lib.ORDER_TIME_GTC,
                "type_filling": mt5_lib.ORDER_FILLING_IOC,
            }
            res = mt5_lib.order_send(close_req)

            if res and res.retcode == mt5_lib.TRADE_RETCODE_DONE:
                print(f"[EMAMonitor] ✅ #{p.ticket} closed at {price:.4f}")
                # Update local trade log
                trades = _load_trades()
                for t in trades:
                    if str(t.get("ticket")) == str(p.ticket):
                        t["status"]    = "closed"
                        t["mt5_state"] = "closed"
                        t["exit_price"] = price
                        t["exit_reason"] = "EMA20 cross exit"
                        break
                _save_trades(trades)
                closed_list.append({
                    "ticket":     p.ticket,
                    "symbol":     symbol,
                    "direction":  "BUY" if p.type == 0 else "SELL",
                    "exit_price": price,
                    "ema20":      round(last_ema, 4),
                    "reason":     reason,
                })
            else:
                err = res.comment if res else "No response"
                print(f"[EMAMonitor] ❌ Close failed #{p.ticket}: {err}")

    return {"checked": checked, "closed": closed_list}


# ── Close all positions ──────────────────────────────────────────────────────

def close_all_positions(symbol=None):
    mt5, _ = _connect()
    if mt5 is None:
        return False
    try:
        import MetaTrader5 as mt5_lib
        mt5_sym   = SYMBOL_MAP.get(symbol, symbol) if symbol else None
        positions = (mt5_lib.positions_get(symbol=mt5_sym) if mt5_sym
                     else mt5_lib.positions_get())
        if not positions:
            print("[MT5] No open positions")
            return True
        for pos in positions:
            close_type = (mt5_lib.ORDER_TYPE_SELL if pos.type == 0
                          else mt5_lib.ORDER_TYPE_BUY)
            tick  = mt5_lib.symbol_info_tick(pos.symbol)
            price = tick.bid if close_type == mt5_lib.ORDER_TYPE_SELL else tick.ask
            req   = {
                "action":       mt5_lib.TRADE_ACTION_DEAL,
                "symbol":       pos.symbol,
                "volume":       pos.volume,
                "type":         close_type,
                "position":     pos.ticket,
                "price":        price,
                "deviation":    30,
                "magic":        20260101,
                "comment":      "SMT_CLOSE",
                "type_filling": mt5_lib.ORDER_FILLING_IOC,
            }
            r      = mt5_lib.order_send(req)
            status = "✅" if r.retcode == mt5_lib.TRADE_RETCODE_DONE else "❌"
            print(f"[MT5] {status} Close #{pos.ticket} {pos.symbol}")
        return True
    except Exception as e:
        print(f"[MT5] Close error: {e}")
        return False

# ── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("MT5 Connection Test")
    print("=" * 50)
    cfg = load_config()
    print(f"Mode:   {cfg.get('mode','paper').upper()}")
    print(f"Demo:   {cfg.get('login')} @ {cfg.get('server')}")
    print(f"Live:   {cfg.get('live_login')} @ {cfg.get('live_server')}")
    print()
    info = get_account_info(force_refresh=True)
    if "error" in info:
        print(f"❌ {info['error']}")
    else:
        print(f"✅ {info.get('name')} | ${info.get('balance')} | {info.get('server')}")