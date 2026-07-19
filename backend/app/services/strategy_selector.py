"""
strategy_selector.py
--------------------
Monthly strategy selector — runs on last day of each month.

Compares all strategies by win rate + RR quality:
  - ICT_Scalping
  - ICT_Intraday
  - ICT_Swing
  - EMA_Scalping
  - EMA_Intraday

Selects the best and updates weights + sends Telegram report.
Also runs historical backtest on 60 days to fast-train new strategies.
"""

import json
import os
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from app.services.clean_data import load_clean_log

_BASE           = os.path.join(os.path.dirname(__file__), "..", "..")
LOG_FILE        = os.path.abspath(os.path.join(_BASE, "signals_log.json"))
WEIGHTS_FILE    = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))
SELECTOR_FILE   = os.path.abspath(os.path.join(_BASE, "strategy_selection.json"))
BACKTEST_FILE   = os.path.abspath(os.path.join(_BASE, "backtest_results.json"))

MIN_TRADES_FOR_SELECTION = 5


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _detect_strategy(signal):
    conf_str  = " ".join(signal.get("confluences", [])).lower()
    timeframe = signal.get("timeframe", "Scalping")
    if "ema50" in conf_str or "ema13" in conf_str:
        return f"EMA_{timeframe}"
    return f"ICT_{timeframe}"


def _load_log():
    # Epoch-filtered: this module ranks strategies and writes `best_strategy`
    # into learned_weights.json, so reading the pre-2026-07-05 outcomes would
    # smuggle the corrupted history past the learner's guard. See clean_data.py.
    return load_clean_log(LOG_FILE)


def _load_weights():
    try:
        with open(WEIGHTS_FILE) as f: return json.load(f)
    except Exception: return {}


def _save_weights(w):
    with open(WEIGHTS_FILE, "w") as f: json.dump(w, f, indent=2)


def analyse_strategies():
    """Analyse all strategies and return ranked performance."""
    log      = _load_log()
    resolved = [s for s in log if s.get("outcome") in ("WIN", "LOSS")]

    strategy_data = defaultdict(lambda: {
        "wins": 0, "losses": 0, "total_pts": 0,
        "bp": 0, "sp": 0, "sl": 0, "bl": 0,
        "rr_sum": 0, "rr_count": 0
    })

    for s in resolved:
        strat  = _detect_strategy(s)
        pts    = float(s.get("points", 0) or 0)
        entry  = float(s.get("entry", 0))
        sl     = float(s.get("sl", 0))
        tp     = float(s.get("tp", 0))
        sl_dist = abs(entry - sl)

        if s["outcome"] == "WIN":
            strategy_data[strat]["wins"]      += 1
            strategy_data[strat]["total_pts"] += pts
            if sl_dist > 0:
                rr = pts / sl_dist
                strategy_data[strat]["rr_sum"]   += rr
                strategy_data[strat]["rr_count"] += 1
                if rr >= 2.5:   strategy_data[strat]["bp"] += 1
                else:           strategy_data[strat]["sp"] += 1
        else:
            strategy_data[strat]["losses"]    += 1
            strategy_data[strat]["total_pts"] += pts
            tp_dist = abs(tp - entry)
            if sl_dist > 0 and abs(pts) > sl_dist * 2:
                strategy_data[strat]["bl"] += 1
            else:
                strategy_data[strat]["sl"] += 1

    results = []
    for strat, data in strategy_data.items():
        total = data["wins"] + data["losses"]
        if total < 1:
            continue
        wr      = round(data["wins"] / total, 3)
        avg_rr  = round(data["rr_sum"] / data["rr_count"], 2) if data["rr_count"] > 0 else 0
        # Score = win rate × avg RR (rewards both accuracy AND big wins)
        score   = round(wr * max(avg_rr, 1.0), 3)
        results.append({
            "strategy":  strat,
            "total":     total,
            "wins":      data["wins"],
            "losses":    data["losses"],
            "win_rate":  wr,
            "avg_rr":    avg_rr,
            "score":     score,
            "total_pts": round(data["total_pts"], 2),
            "BP":        data["bp"],
            "SP":        data["sp"],
            "SL":        data["sl"],
            "BL":        data["bl"],
        })

    results.sort(key=lambda x: -x["score"])
    return results


def run_monthly_selection():
    """
    Run on last day of month.
    Select best strategy, update weights, send Telegram report.
    """
    now      = _ist_now()
    results  = analyse_strategies()
    weights  = _load_weights()

    print(f"\n[Selector] ══ Monthly Strategy Review — {now.strftime('%B %Y')} ══")

    if not results:
        print("[Selector] Not enough data for strategy selection")
        return None

    print(f"\n{'Strategy':<20} {'Trades':>6} {'WR':>6} {'Avg RR':>7} {'Score':>7} {'BP':>4} {'BL':>4}")
    print("-" * 60)
    for r in results:
        print(f"{r['strategy']:<20} {r['total']:>6} {r['win_rate']:>5.0%} {r['avg_rr']:>7.2f} {r['score']:>7.3f} {r['BP']:>4} {r['BL']:>4}")

    # Best strategy
    qualified = [r for r in results if r["total"] >= MIN_TRADES_FOR_SELECTION]
    if not qualified:
        print(f"[Selector] No strategy has {MIN_TRADES_FOR_SELECTION}+ trades yet")
        best = results[0] if results else None
    else:
        best = qualified[0]

    if best:
        print(f"\n[Selector] ✅ Best strategy: {best['strategy']} (score={best['score']:.3f}, WR={best['win_rate']:.0%})")

        # Update weights to favour best strategy
        weights["best_strategy"]       = best["strategy"]
        weights["best_strategy_wr"]    = best["win_rate"]
        weights["best_strategy_score"] = best["score"]
        weights["strategy_selected_at"] = now.strftime("%Y-%m-%d")
        _save_weights(weights)

    # Rule 2 check — any Big Losses this month?
    total_bl = sum(r["BL"] for r in results)
    if total_bl > 0:
        print(f"\n[Selector] ⚠️  RULE 2 VIOLATION: {total_bl} Big Losses detected this month!")
        print("[Selector] ⚠️  Review SL settings immediately")

    # Save selection
    selection = {
        "month":        now.strftime("%Y-%m"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M IST"),
        "strategies":   results,
        "best_strategy": best,
        "total_bl":      total_bl,
        "recommendation": _generate_recommendation(results, best)
    }

    with open(SELECTOR_FILE, "w") as f:
        json.dump(selection, f, indent=2)

    # Send Telegram summary
    try:
        from app.services.telegram_service import send_alert
        _send_monthly_report(selection, send_alert)
    except Exception as e:
        print(f"[Selector] Telegram report failed: {e}")

    return selection


def _generate_recommendation(results, best):
    if not best:
        return "Insufficient data. Keep collecting signals."
    recs = []
    if best["win_rate"] >= 0.6:
        recs.append(f"✅ {best['strategy']} is performing well — continue with current settings")
    elif best["win_rate"] >= 0.4:
        recs.append(f"⚠️ {best['strategy']} at 40-60% WR — raise quality threshold by +1")
    else:
        recs.append(f"❌ All strategies below 40% WR — review market conditions and parameters")

    if best["BL"] > 0:
        recs.append(f"🚨 {best['BL']} Big Losses detected — check SL settings immediately (Rule 2)")

    if best["avg_rr"] < 2.0:
        recs.append("📈 Average RR below 2.0 — increase TP targets to let winners run (Rule 3)")

    return " | ".join(recs)


def _send_monthly_report(selection, send_alert):
    best = selection.get("best_strategy", {})
    if not best:
        return

    strats_text = ""
    for r in selection.get("strategies", [])[:5]:
        strats_text += f"\n  {r['strategy']:<18} WR:{r['win_rate']:.0%} RR:{r['avg_rr']:.1f} BP:{r['BP']} BL:{r['BL']}"

    msg = f"""
📊 MONTHLY STRATEGY REVIEW — {selection['month']}
Smart Money Trader

🏆 Best Strategy: {best.get('strategy','—')}
   Win Rate: {best.get('win_rate',0):.0%} | Avg RR: {best.get('avg_rr',0):.2f}
   Score: {best.get('score',0):.3f}

📈 Strategy Rankings:{strats_text}

{'⚠️ RULE 2 ALERT: ' + str(selection['total_bl']) + ' Big Losses this month!' if selection['total_bl'] > 0 else '✅ Rule 2: No Big Losses this month'}

💡 {selection.get('recommendation','')}
"""
    send_alert(msg)
    print("[Selector] Monthly report sent to Telegram")


def is_last_day_of_month():
    """Check if today is the last day of the current month."""
    now      = _ist_now()
    tomorrow = now + timedelta(days=1)
    return tomorrow.month != now.month


def run_historical_backtest_training(symbol="BTCUSDT", days=60):
    """
    Fast-train the system by running signal engine on historical data.
    Populates signals_log.json with simulated outcomes so the learning
    engine has real data to work with from day 1.
    """
    print(f"\n[Trainer] Running {days}-day historical backtest on {symbol}...")

    try:
        if symbol in ("BTCUSDT", "ETHUSDT"):
            from app.services.binance_service import get_historical_multi_timeframe_data
            data = get_historical_multi_timeframe_data(symbol, days)
        else:
            print(f"[Trainer] {symbol} historical training not supported")
            return []

        from app.strategies.mtf_analysis import analyze_multi_timeframe
        from app.backtests.simple_backtest import run_backtest

        signals = analyze_multi_timeframe(data)
        df      = data["1m"]
        results = run_backtest(df, signals)

        resolved = [r for r in results if r["outcome"] in ("WIN", "LOSS")]
        wins     = sum(1 for r in resolved if r["outcome"] == "WIN")

        print(f"[Trainer] {symbol}: {len(results)} signals, {len(resolved)} resolved, {wins} wins")

        # Add to log as historical entries (marked as backtest)
        log = []
        try:
            with open(LOG_FILE) as f: log = json.load(f)
        except Exception: pass

        existing_ts = {s.get("timestamp") for s in log}
        added = 0

        for r in resolved:
            ts = r.get("timestamp") or int(datetime.now(timezone.utc).timestamp() * 1000) - (days * 86400000)
            if ts in existing_ts:
                continue
            entry_val = float(r.get("entry", 0))
            sl_val    = float(r.get("sl", 0))
            tp_val    = float(r.get("tp", 0))
            pts       = abs(tp_val - entry_val) if r["outcome"] == "WIN" else -abs(sl_val - entry_val)

            log.append({
                **r,
                "timestamp":    ts,
                "sent_at":      "backtest",
                "symbol":       symbol,
                "points":       round(pts, 4),
                "sentiment":    "Neutral",
                "geo_risk":     "LOW",
                "is_backtest":  True
            })
            added += 1

        with open(LOG_FILE, "w") as f:
            json.dump(log, f, indent=2)

        print(f"[Trainer] Added {added} historical signals to log")
        return results

    except Exception as e:
        print(f"[Trainer] Failed: {e}")
        return []
