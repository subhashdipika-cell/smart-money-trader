"""
trading_journal.py
------------------
Rich per-signal trading journal implementing the 4 Quadrant rules:

  SL = Small Loss  (stop hit — normal, expected)
  SP = Small Profit (partial exit or low RR win)
  BL = Big Loss    (SHOULD NEVER HAPPEN — alerts fired)
  BP = Big Profit  (high RR win — account growth engine)

Rule 1: Track all 4 outcomes
Rule 2: Alert + block if Big Loss risk detected
Rule 3: Reward high RR wins, analyse missed BP opportunities  
Rule 4: Auto-generate journal review on month end
"""

import json
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

_BASE          = os.path.join(os.path.dirname(__file__), "..", "..")
JOURNAL_FILE   = os.path.abspath(os.path.join(_BASE, "trader_journal.json"))
LOG_FILE       = os.path.abspath(os.path.join(_BASE, "signals_log.json"))
ACCOUNT_FILE   = os.path.abspath(os.path.join(_BASE, "account_settings.json"))

# ── Quadrant thresholds ───────────────────────────────────────────────────────
# Based on RR achieved vs planned RR
SP_MAX_RR  = 1.0   # Small Profit: won but less than 1R
BP_MIN_RR  = 2.5   # Big Profit: won with 2.5R or more
BL_SL_MULT = 2.0   # Big Loss: lost more than 2x normal SL (should never happen)

# Daily loss cap — stop trading if daily loss exceeds this % of capital
DAILY_LOSS_CAP_PCT = 0.05   # 5% of capital


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _load_journal():
    try:
        with open(JOURNAL_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_journal(entries):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def _load_account():
    try:
        with open(ACCOUNT_FILE) as f:
            return json.load(f)
    except Exception:
        return {"capital": 1000, "risk_pct": 2, "currency": "USD"}


def classify_quadrant(outcome, points, entry, sl, tp):
    """
    Classify trade into one of 4 quadrants per the trading rules.
    Returns (quadrant, description)
    """
    if outcome not in ("WIN", "LOSS"):
        return "OPEN", "Trade still open"

    try:
        sl_dist = abs(float(entry) - float(sl))
        tp_dist = abs(float(tp) - float(entry))
        pts     = abs(float(points)) if points else 0
    except Exception:
        return outcome, ""

    if outcome == "LOSS":
        if sl_dist > 0 and pts > sl_dist * BL_SL_MULT:
            return "BL", f"⛔ BIG LOSS — lost {pts:.1f}pts vs planned SL {sl_dist:.1f}pts"
        return "SL", f"Small Loss — {pts:.1f}pts (planned SL)"

    if outcome == "WIN":
        rr_achieved = pts / sl_dist if sl_dist > 0 else 0
        if rr_achieved >= BP_MIN_RR:
            return "BP", f"🏆 BIG PROFIT — {pts:.1f}pts ({rr_achieved:.1f}R)"
        return "SP", f"Small Profit — {pts:.1f}pts ({rr_achieved:.1f}R)"


def add_journal_entry(signal, outcome, points, market_context=None):
    """
    Create a rich journal entry for a resolved signal.
    Called automatically when resolver determines outcome.
    """
    entries = _load_journal()
    entry   = float(signal.get("entry", 0))
    sl      = float(signal.get("sl", 0))
    tp      = float(signal.get("tp", 0))
    planned_rr = signal.get("rr", 0)

    quadrant, quad_desc = classify_quadrant(outcome, points, entry, sl, tp)

    # Calculate actual RR achieved
    sl_dist = abs(entry - sl)
    pts_abs = abs(points) if points else 0
    actual_rr = round(pts_abs / sl_dist, 2) if sl_dist > 0 else 0

    # Lesson learned based on outcome
    lesson = _generate_lesson(signal, outcome, quadrant, actual_rr, planned_rr)

    journal_entry = {
        "id":            len(entries) + 1,
        "timestamp":     signal.get("timestamp"),
        "date_ist":      _ist_now().strftime("%Y-%m-%d %H:%M IST"),
        "symbol":        signal.get("symbol"),
        "signal":        signal.get("signal"),
        "timeframe":     signal.get("timeframe"),
        "strategy":      _detect_strategy(signal),
        "session":       signal.get("session", "Unknown"),
        "entry":         entry,
        "sl":            sl,
        "tp":            tp,
        "planned_rr":    planned_rr,
        "actual_rr":     actual_rr if outcome == "WIN" else -1.0,
        "outcome":       outcome,
        "points":        points,
        "quadrant":      quadrant,
        "quad_desc":     quad_desc,
        "quality_score": signal.get("quality_score"),
        "confluences":   signal.get("confluences", []),
        "sentiment":     signal.get("sentiment"),
        "geo_risk":      signal.get("geo_risk"),
        "lesson":        lesson,
        "market_context": market_context or {}
    }

    # Alert on Big Loss
    if quadrant == "BL":
        print(f"[Journal] ⛔ BIG LOSS DETECTED on {signal.get('symbol')} — {quad_desc}")
        print(f"[Journal] ⛔ RULE 2 VIOLATION: Big losses must be eliminated!")

    entries.insert(0, journal_entry)
    _save_journal(entries)
    print(f"[Journal] {quadrant} — {signal.get('symbol')} {signal.get('signal')} @ {entry} | {quad_desc}")
    return journal_entry


def _detect_strategy(signal):
    """Detect which strategy generated this signal."""
    confluences = signal.get("confluences", [])
    conf_str    = " ".join(confluences).lower()
    timeframe   = signal.get("timeframe", "Scalping")

    if "ema50" in conf_str or "ema13" in conf_str:
        return f"EMA_{timeframe}"
    if "liquidity sweep" in conf_str and "fair value gap" in conf_str:
        return f"ICT_{timeframe}"
    return f"ICT_{timeframe}"


def _generate_lesson(signal, outcome, quadrant, actual_rr, planned_rr):
    """Generate a specific lesson from this trade."""
    symbol    = signal.get("symbol", "")
    session   = signal.get("session", "")
    direction = signal.get("signal", "")
    sentiment = signal.get("sentiment", "")

    if quadrant == "BP":
        return f"✅ {symbol} {direction} in {session} session delivered {actual_rr:.1f}R. Setup quality validated."

    if quadrant == "SP":
        if actual_rr < 0.5:
            return f"⚠️ Won but barely — {actual_rr:.1f}R achieved vs {planned_rr}R planned. Consider wider TP or trailing stop."
        return f"✅ Captured {actual_rr:.1f}R. Could have held for {planned_rr}R target."

    if quadrant == "SL":
        if sentiment and "bullish" in sentiment.lower() and direction == "SELL":
            return f"📚 Lost on counter-trend trade ({direction} during {sentiment} sentiment). Avoid trading against momentum."
        if session == "Asia":
            return f"📚 Asia session {symbol} {direction} failed. Lower liquidity increases false signals."
        return f"📚 Setup invalidated. Review entry timing and confluence quality."

    if quadrant == "BL":
        return f"🚨 CRITICAL: Big Loss on {symbol}. Review SL placement immediately. This should never happen with proper risk management."

    return "Trade resolved."


def check_daily_loss_cap(risk_usd):
    """
    Check if daily loss cap has been reached.
    Returns (is_blocked, message)
    """
    try:
        with open(LOG_FILE) as f:
            log = json.load(f)
    except Exception:
        return False, None

    # Get today's losses in IST
    now_ist   = _ist_now()
    today_str = now_ist.strftime("%Y-%m-%d")

    daily_loss_pts = 0
    for s in log:
        if s.get("outcome") != "LOSS":
            continue
        ts = s.get("timestamp", 0)
        try:
            ts_ist = datetime.fromtimestamp(int(ts)/1000, tz=timezone.utc) + timedelta(hours=5,minutes=30)
            if ts_ist.strftime("%Y-%m-%d") != today_str:
                continue
        except Exception:
            continue
        daily_loss_pts += abs(float(s.get("points", 0) or 0))

    # Convert to USD using risk amount
    if risk_usd and risk_usd > 0:
        # Rough estimate: each SL = 1R = risk_usd
        account   = _load_account()
        capital   = account.get("capital", 1000)
        cap_usd   = capital if account.get("currency","USD") == "USD" else capital / account.get("usdtInr", 84)
        daily_loss_cap = cap_usd * DAILY_LOSS_CAP_PCT

        # Count number of SL losses today as proxy for USD loss
        today_losses = sum(1 for s in log if s.get("outcome") == "LOSS" and
                          _is_today(s.get("timestamp", 0)))
        daily_loss_usd = today_losses * risk_usd

        if daily_loss_usd >= daily_loss_cap:
            return True, f"Daily loss cap reached (${daily_loss_usd:.0f} / ${daily_loss_cap:.0f}). No new signals today."

    return False, None


def _is_today(ts):
    try:
        ist = datetime.fromtimestamp(int(ts)/1000, tz=timezone.utc) + timedelta(hours=5,minutes=30)
        return ist.strftime("%Y-%m-%d") == _ist_now().strftime("%Y-%m-%d")
    except Exception:
        return False


def get_quadrant_stats():
    """Get quadrant distribution for dashboard display."""
    try:
        with open(LOG_FILE) as f:
            log = json.load(f)
    except Exception:
        return {}

    resolved = [s for s in log if s.get("outcome") in ("WIN","LOSS")]
    stats    = {"BP": 0, "SP": 0, "SL": 0, "BL": 0}

    for s in resolved:
        q, _ = classify_quadrant(s.get("outcome"), s.get("points"), s.get("entry"), s.get("sl"), s.get("tp"))
        if q in stats:
            stats[q] += 1

    total = sum(stats.values())
    if total:
        stats["total"]    = total
        stats["bp_rate"]  = round(stats["BP"] / total, 3)
        stats["bl_rate"]  = round(stats["BL"] / total, 3)

    return stats


def get_monthly_journal_summary(year=None, month=None):
    """Generate end-of-month journal summary."""
    now = _ist_now()
    if not year:  year  = now.year
    if not month: month = now.month

    try:
        with open(LOG_FILE) as f:
            log = json.load(f)
    except Exception:
        return {}

    month_signals = []
    for s in log:
        try:
            ts_ist = datetime.fromtimestamp(int(s["timestamp"])/1000, tz=timezone.utc) + timedelta(hours=5,minutes=30)
            if ts_ist.year == year and ts_ist.month == month and s.get("outcome") in ("WIN","LOSS"):
                month_signals.append(s)
        except Exception:
            continue

    if not month_signals:
        return {"message": "No resolved signals this month"}

    wins   = [s for s in month_signals if s.get("outcome") == "WIN"]
    losses = [s for s in month_signals if s.get("outcome") == "LOSS"]
    wr     = round(len(wins) / len(month_signals), 3) if month_signals else 0

    # Quadrant breakdown
    quads = defaultdict(int)
    for s in month_signals:
        q, _ = classify_quadrant(s.get("outcome"), s.get("points"), s.get("entry"), s.get("sl"), s.get("tp"))
        quads[q] += 1

    # Strategy breakdown
    strategy_stats = defaultdict(lambda: {"wins": 0, "losses": 0})
    for s in month_signals:
        strat = _detect_strategy(s)
        if s.get("outcome") == "WIN":
            strategy_stats[strat]["wins"] += 1
        else:
            strategy_stats[strat]["losses"] += 1

    best_strategy = None
    best_wr       = 0
    for strat, data in strategy_stats.items():
        total = data["wins"] + data["losses"]
        if total >= 3:
            strat_wr = data["wins"] / total
            if strat_wr > best_wr:
                best_wr       = strat_wr
                best_strategy = strat

    return {
        "month":           f"{year}-{month:02d}",
        "total_trades":    len(month_signals),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate":        wr,
        "quadrants":       dict(quads),
        "bp_count":        quads.get("BP", 0),
        "bl_count":        quads.get("BL", 0),
        "strategy_stats":  dict(strategy_stats),
        "best_strategy":   best_strategy,
        "best_strategy_wr": best_wr,
        "total_points":    round(sum(float(s.get("points",0) or 0) for s in month_signals), 2),
        "rule2_violations": quads.get("BL", 0),
        "generated_at":    now.strftime("%Y-%m-%d %H:%M IST")
    }


# ── Obsidian monthly export ───────────────────────────────────────────────────
# Fixed shared destination across the four trading apps; each writes to its own
# subfolder. Reads resolved (WIN/LOSS) signals from the signals log — the same
# source get_monthly_journal_summary() uses — which carry points + realized USD.
# SMT trades crypto/gold (24h), so session is derived from the timestamp.
OBSIDIAN_TRADES_DIR = r"E:\Obsidian\Trading_Mind\raw\trades"


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _usd(v):
    v = _num(v)
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def _fmt_pts(v):
    v = _num(v)
    return f"{'+' if v >= 0 else '-'}{abs(v):.1f}"


def _cell(v):
    return str("—" if v in (None, "", []) else v).replace("|", "/").replace("\n", " ")


def _ist(ms):
    """(datetime IST, 'YYYY-MM' key) for an epoch-ms timestamp."""
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc) + timedelta(hours=5, minutes=30)
        return dt, f"{dt.year}-{dt.month:02d}"
    except (TypeError, ValueError, OverflowError):
        return None, ""


def _session_from_ts(ms):
    """Global trading session from the UTC hour (24h crypto/gold markets)."""
    try:
        h = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).hour
    except (TypeError, ValueError, OverflowError):
        return "Unknown"
    if h < 7:
        return "Asian"
    if h < 12:
        return "London"
    if h < 16:
        return "London/NY overlap"
    if h < 21:
        return "New York"
    return "Late US"


def _enrich(s):
    """Normalise one resolved signal into the fields the rollup needs."""
    dt, _ = _ist(s.get("timestamp"))
    quad, _desc = classify_quadrant(s.get("outcome"), s.get("points"), s.get("entry"), s.get("sl"), s.get("tp"))
    return {
        "when": dt.strftime("%Y-%m-%d %H:%M") if dt else "—",
        "session": _session_from_ts(s.get("timestamp")),
        "symbol": s.get("symbol"),
        "dir": s.get("signal") or s.get("direction"),
        "strategy": s.get("strategy_tag") or _detect_strategy(s),
        "outcome": s.get("outcome"),
        "quadrant": quad,
        "points": _num(s.get("points")),
        "usd": _num(s.get("realized_usd")),
        "rr": s.get("rr"),
    }


def _group_entries(entries, key_fn):
    g = {}
    for e in entries:
        k = key_fn(e) or "—"
        x = g.setdefault(k, {"n": 0, "w": 0, "usd": 0.0, "pts": 0.0})
        x["n"] += 1
        if e["outcome"] == "WIN":
            x["w"] += 1
        x["usd"] += e["usd"]
        x["pts"] += e["points"]
    return sorted(
        ({"label": k, "n": v["n"], "win": (v["w"] / v["n"] * 100) if v["n"] else 0.0,
          "usd": v["usd"], "pts": v["pts"]} for k, v in g.items()),
        key=lambda d: d["usd"], reverse=True,
    )


def _monthly_markdown(key, entries):
    wins = sum(1 for e in entries if e["outcome"] == "WIN")
    losses = sum(1 for e in entries if e["outcome"] == "LOSS")
    total = len(entries)
    net_usd = sum(e["usd"] for e in entries)
    net_pts = sum(e["points"] for e in entries)
    wr = (wins / total * 100) if total else 0.0
    bp = sum(1 for e in entries if e["quadrant"] == "BP")
    bl = sum(1 for e in entries if e["quadrant"] == "BL")
    yr, mo = key.split("-")
    month_name = datetime(int(yr), int(mo), 1).strftime("%B")

    def table(title, rows):
        out = f"\n## By {title}\n\n| {title[:1].upper() + title[1:]} | Trades | Win% | Net USD | Net pts |\n|---|---|---|---|---|\n"
        for r in rows:
            out += f"| {_cell(r['label'])} | {r['n']} | {r['win']:.0f}% | {_usd(r['usd'])} | {_fmt_pts(r['pts'])} |\n"
        return out

    tags = "".join(f"\n  - {t}" for t in ("smart-money-trader", "monthly", "trades", key))
    md = (
        f"---\ntype: monthly-trade-summary\napp: smart-money-trader\nmonth: {key}\n"
        f"generated: {_ist_now().strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"trades: {total}\nwins: {wins}\nlosses: {losses}\nwin_rate: {wr:.1f}\n"
        f"net_usd: {net_usd:.2f}\nnet_points: {net_pts:.1f}\nbig_profits: {bp}\nbig_losses: {bl}\n"
        f"tags:{tags}\n---\n\n"
    )
    md += f"# Smart-Money-Trader — {month_name} {yr} Trade Summary\n\n"
    md += (f"**{total} trades** · {wins}W / {losses}L · **{wr:.1f}% win** · "
           f"net **{_usd(net_usd)}** ({_fmt_pts(net_pts)} pts) · BP {bp} / BL {bl}\n")
    md += "\n> Realized USD is net of spread on resolution. BP = Big Profit, BL = Big Loss (Rule-2 quadrant).\n"
    md += table("session", _group_entries(entries, lambda e: e["session"]))
    md += table("strategy", _group_entries(entries, lambda e: e["strategy"]))
    md += table("symbol", _group_entries(entries, lambda e: e["symbol"]))
    md += ("\n## Trades\n\n| Date (IST) | Session | Symbol | Dir | Strategy | Outcome | Quad | USD | Pts | RR |\n"
           "|---|---|---|---|---|---|---|---|---|---|\n")
    for e in sorted(entries, key=lambda x: x["when"]):
        md += (f"| {_cell(e['when'])} | {_cell(e['session'])} | {_cell(e['symbol'])} | {_cell(e['dir'])} | "
               f"{_cell(e['strategy'])} | {_cell(e['outcome'])} | {_cell(e['quadrant'])} | "
               f"{_usd(e['usd'])} | {_fmt_pts(e['points'])} | {_cell(e['rr'])} |\n")
    md += "\n_Generated by Smart-Money-Trader for Obsidian ingestion._\n"
    return md


def export_monthly_to_obsidian(year=None, month=None):
    """Write the month's resolved-trade rollup (per-trade session column +
    session/strategy/symbol breakdowns) to
    E:\\Obsidian\\Trading_Mind\\raw\\trades\\smart-money-trader\\<YYYY-MM>.md."""
    now = _ist_now()
    key = f"{int(year or now.year)}-{int(month or now.month):02d}"

    try:
        with open(LOG_FILE) as f:
            log = json.load(f)
    except (OSError, json.JSONDecodeError):
        log = []
    entries = [_enrich(s) for s in log
               if s.get("outcome") in ("WIN", "LOSS") and _ist(s.get("timestamp"))[1] == key]
    if not entries:
        return {"status": "EMPTY", "month": key, "message": "No resolved trades this month."}

    try:
        folder = os.path.join(OBSIDIAN_TRADES_DIR, "smart-money-trader")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{key}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_monthly_markdown(key, entries))
        return {"status": "SUCCESS", "month": key, "trades": len(entries), "path": path}
    except OSError as exc:
        return {"status": "ERROR", "message": str(exc)}
