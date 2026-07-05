"""
signal_optimizer.py
-------------------
Self-improving signal loop:

1. Runs the full signal engine on historical candles (last 7 days)
2. Simulates each signal forward to find WIN/LOSS/OPEN
3. Measures win rate, avg RR, best confluence combos
4. Adjusts quality thresholds and confluence weights
5. Writes optimized parameters to learned_weights.json
6. Repeats every N cycles until quality stabilises

Think of it as the system studying its own past performance
and continuously refining what it looks for.
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

_BASE          = os.path.join(os.path.dirname(__file__), "..", "..")
WEIGHTS_FILE   = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))
KNOWLEDGE_FILE = os.path.abspath(os.path.join(_BASE, "strategy_knowledge.json"))
BACKTEST_FILE  = os.path.abspath(os.path.join(_BASE, "backtest_results.json"))

MIN_BACKTEST_SIGNALS = 10   # minimum signals needed to draw conclusions
MAX_ITERATIONS       = 3    # optimization iterations per run


def _load_weights():
    try:
        with open(WEIGHTS_FILE) as f: return json.load(f)
    except Exception: return {}

def _save_weights(w):
    with open(WEIGHTS_FILE, "w") as f: json.dump(w, f, indent=2)

def _simulate_signal(signal, df):
    """
    Simulate a signal forward on candle data.
    Returns "WIN", "LOSS", or "OPEN"
    """
    entry     = float(signal["entry"])
    sl        = float(signal["sl"])
    tp        = float(signal["tp"])
    sig_type  = signal["signal"]
    sig_idx   = signal.get("index", 0)

    entry_hit = False
    for i in range(sig_idx + 1, len(df)):
        high  = float(df["high"].iloc[i])
        low   = float(df["low"].iloc[i])
        close = float(df["close"].iloc[i])

        if sig_type == "BUY":
            if not entry_hit:
                if low <= entry:
                    if close <= sl: continue
                    entry_hit = True
                else: continue
            if close <= sl: return "LOSS"
            if high  >= tp: return "WIN"
        else:
            if not entry_hit:
                if high >= entry:
                    if close >= sl: continue
                    entry_hit = True
                else: continue
            if close >= sl: return "LOSS"
            if low   <= tp: return "WIN"

    return "OPEN"


def _win_rate(results):
    resolved = [r for r in results if r["outcome"] in ("WIN","LOSS")]
    if not resolved: return None, 0
    wins = sum(1 for r in resolved if r["outcome"] == "WIN")
    return round(wins / len(resolved), 3), len(resolved)


def run_backtest(symbol="ETHUSDT", days=7):
    """
    Run the full signal engine on historical data and measure performance.
    Returns dict with results and suggested parameter adjustments.
    """
    print(f"\n[Optimizer] Running backtest for {symbol} ({days} days)...")

    try:
        if symbol in ("XAUUSD",):
            from app.services.gold_service import get_multi_timeframe_data
        else:
            from app.services.binance_service import get_multi_timeframe_data
        data = get_multi_timeframe_data(symbol)
    except Exception as e:
        print(f"[Optimizer] Data fetch failed: {e}")
        return None

    try:
        from app.strategies.mtf_analysis import analyze_multi_timeframe
        raw_signals = analyze_multi_timeframe(data)
    except Exception as e:
        print(f"[Optimizer] Signal generation failed: {e}")
        return None

    ltf_df = data.get("1m")
    if ltf_df is None or ltf_df.empty:
        return None

    # Simulate each signal
    results = []
    for sig in raw_signals:
        outcome = _simulate_signal(sig, ltf_df)
        pts = round(abs(float(sig["tp"]) - float(sig["entry"])), 4) if outcome == "WIN" \
              else round(-abs(float(sig["sl"]) - float(sig["entry"])), 4) if outcome == "LOSS" \
              else 0
        results.append({
            **sig,
            "outcome":     outcome,
            "points":      pts,
            "symbol":      symbol
        })

    wr, count = _win_rate(results)
    wins      = sum(1 for r in results if r["outcome"] == "WIN")
    losses    = sum(1 for r in results if r["outcome"] == "LOSS")

    print(f"[Optimizer] {symbol}: {count} simulated | W:{wins} L:{losses} | WR:{wr:.0%}" if wr else f"[Optimizer] {symbol}: {count} signals, not enough resolved")

    # Confluence analysis
    conf_wins   = defaultdict(int)
    conf_totals = defaultdict(int)
    for r in results:
        is_win = r["outcome"] == "WIN"
        for tag in r.get("confluences", []):
            conf_totals[tag] += 1
            if is_win: conf_wins[tag] += 1

    conf_rates = {}
    for tag, total in conf_totals.items():
        if total >= 2:
            conf_rates[tag] = round(conf_wins[tag] / total, 3)

    # Find optimal score threshold
    best_threshold = 6
    best_wr        = 0
    for threshold in range(4, 9):
        filtered = [r for r in results if r.get("quality_score", 0) >= threshold
                    and r["outcome"] in ("WIN","LOSS")]
        if len(filtered) >= 3:
            t_wr = sum(1 for r in filtered if r["outcome"] == "WIN") / len(filtered)
            if t_wr > best_wr:
                best_wr        = t_wr
                best_threshold = threshold

    print(f"[Optimizer] Best threshold for {symbol}: {best_threshold} (WR={best_wr:.0%})")

    return {
        "symbol":         symbol,
        "total_signals":  len(results),
        "win_rate":       wr,
        "wins":           wins,
        "losses":         losses,
        "conf_rates":     conf_rates,
        "best_threshold": best_threshold,
        "best_wr":        best_wr,
        "results":        results[:20]  # save sample
    }


def optimize_parameters():
    """
    Run backtests, analyse results, update weights.
    This is the self-improvement loop.
    """
    print("\n[Optimizer] ════ Starting self-improvement cycle ════")
    weights = _load_weights()
    all_results = {}

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        result = run_backtest(symbol)
        if result:
            all_results[symbol] = result

            # Update per-asset threshold based on backtest
            bt_threshold = result["best_threshold"]
            current      = weights.get(f"min_quality_score_{symbol}", 6)

            # Blend: don't jump more than 1 point at a time
            new_threshold = current
            if bt_threshold > current:
                new_threshold = current + 1
            elif bt_threshold < current:
                new_threshold = max(4, current - 1)

            weights[f"min_quality_score_{symbol}"] = new_threshold
            print(f"[Optimizer] {symbol} threshold: {current} → {new_threshold}")

            # Update confluence bonuses
            for tag, rate in result["conf_rates"].items():
                if rate >= 0.7:
                    weights.setdefault("confluence_bonuses", {})[tag] = 2
                    print(f"[Optimizer] Boosting confluence: '{tag}' ({rate:.0%})")
                elif rate <= 0.3:
                    weights.setdefault("confluence_bonuses", {})[tag] = -1
                    print(f"[Optimizer] Penalising confluence: '{tag}' ({rate:.0%})")

    # Save results
    with open(BACKTEST_FILE, "w") as f:
        json.dump({
            "results":    {k: {kk: vv for kk, vv in v.items() if kk != "results"}
                          for k, v in all_results.items()},
            "ran_at":     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "weights_updated": True
        }, f, indent=2)

    _save_weights(weights)
    print("[Optimizer] ════ Self-improvement cycle complete ════\n")
    return all_results
