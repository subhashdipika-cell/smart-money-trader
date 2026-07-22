"""
walk_forward.py — does a tuned parameter survive data it has never seen?

strategy_tuner picks the best parameter value by scoring every value on the
SAME history it then reports. That is in-sample fitting: with a 4-value grid
something always wins, and the winner's score is a high-water mark of noise,
not an estimate of future performance. Nothing in SMT currently distinguishes
"this parameter is good" from "this parameter was lucky on this stretch".

This harness answers that. It splits history into rolling folds, chooses the
parameter on the TRAIN window only, and scores it on the TEST window that
follows — data the choice never saw. Stitching the test windows together gives
an out-of-sample track record.

It reports three things that matter more than the headline number:

  IS vs OOS gap   How much of the in-sample edge evaporates out of sample.
                  A large gap is the overfitting tax, measured rather than
                  assumed.
  vs fixed param  Whether adaptive tuning beats simply pinning one value for
                  the whole period. If it does not, tuning is adding noise and
                  the honest move is to stop tuning.
  param stability Which value each fold picked. If the winner jumps around,
                  the grid is fitting noise regardless of what OOS says.

Method note: each parameter is backtested ONCE over the full frame and the
resulting trades are then split by entry_ts. The engines are causal — every
indicator is computed from prior bars only — so a trade landing in the test
window used no test-window information, and splitting after the fact is
equivalent to re-running per window while being far cheaper. Warmup bars carry
across the boundary, which is realistic: a live system does not forget history
at a fold edge.

Run:  backend/.venv/Scripts/python.exe -m tools.walk_forward
      ... --strategy atr_trailing --train 180 --test 60
"""
import argparse
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.clock import ist_str                                  # noqa: E402
from tools.session_backtest import load_1h, to_frame, INSTRUMENTS       # noqa: E402

DAY_MS = 86_400_000
MIN_TRAIN_TRADES = 15   # below this a fold's parameter choice is noise
MIN_TEST_TRADES  = 5

# ── Execution costs ──────────────────────────────────────────────────────
# None of SMT's engines model costs, so every backtest in this repo is a
# gross-return figure. That is fine for ranking but not for deciding whether
# something is worth trading: ATR_Trailing's edges are of the same order as
# the spread.
#
# Measured from the live Vantage terminal on 2026-07-19 (symbol_info_tick /
# symbol_info). NOTE the backtests run on Binance data but execution is on
# Vantage CFDs, so these are the costs that actually apply:
#
#   spread_pct  (ask-bid)/mid at the time of measurement, charged once per
#               round trip — you enter at one side and exit at the other.
#   swap_*      per NIGHT held, as a % of notional. Crypto quotes swap as an
#               annual interest rate (MT5 swap_mode=5): -25 means -25%/yr, so
#               per night = 25/365. Gold quotes it in points (swap_mode=1):
#               -74.84 points x 0.01 = $0.7484/oz/night on a ~$4,119 price.
#               Gold SHORTS earn swap, hence the positive number.
#
# Spreads widen in thin liquidity and swaps are revised, so treat these as a
# reasonable central estimate, not a constant.
COSTS = {
    "BTCUSDT": {"exec": "BTCUSD",  "spread_pct": 0.0258,
                "swap_long_night": -25.0 / 365, "swap_short_night": 0.0},
    "ETHUSDT": {"exec": "ETHUSD",  "spread_pct": 0.1286,
                "swap_long_night": -30.0 / 365, "swap_short_night": -3.0 / 365},
    "XAUUSD":  {"exec": "XAUUSD+", "spread_pct": 0.0032,
                "swap_long_night": -0.0182,     "swap_short_night": +0.0065},
}


def _cost_pct(sym, trade):
    """Round-trip cost for one trade, as a % of notional.

    Nights are taken as a fraction of 24h rather than counting broker
    rollovers: it slightly understates a short hold that crosses one rollover
    and overstates a long intraday one. Mean hold here is ~1 night, so the
    approximation is close enough to decide with and is stated rather than
    hidden."""
    c = COSTS.get(sym)
    if not c:
        return 0.0
    entry_ts, exit_ts = trade.get("entry_ts"), trade.get("exit_ts")
    nights = 0.0
    if entry_ts and exit_ts and exit_ts > entry_ts:
        nights = (exit_ts - entry_ts) / DAY_MS
    is_long = str(trade.get("signal", "")).upper() == "BUY"
    swap = (c["swap_long_night"] if is_long else c["swap_short_night"]) * nights
    return c["spread_pct"] - swap   # swap is signed; negative swap = a cost


# ── strategy adapters ────────────────────────────────────────────────────
# (strategy_id, symbol, param value, 1H frame) -> list of trades w/ entry_ts.
# Only df-driven engines can be walk-forwarded here; the ones that fetch their
# own yfinance history on a different calendar are excluded rather than
# silently compared against a different market.

def _atr(symbol, value, df):
    from app.strategies.atr_trailing_strategy import run_atr_trailing_backtest
    return run_atr_trailing_backtest(df, symbol=symbol, atr_mult=value,
                                     lookback=len(df))


ADAPTERS = {
    "atr_trailing": {
        "fn":      _atr,
        "param":   "atr_mult",
        "values":  [2.0, 2.5, 3.0, 3.5],     # mirrors strategy_tuner.TUNABLE
        "symbols": ["BTCUSDT", "ETHUSDT", "XAUUSD"],
    },
}


# ── scoring ──────────────────────────────────────────────────────────────
def _returns(trades, lo, hi, sym=None, net=True):
    """Per-trade % returns for trades entered in [lo, hi).

    Net of execution costs by default — a gross figure would rank ATR_Trailing
    as marginally profitable on BTC when the spread alone exceeds its edge.
    """
    out = []
    for t in trades:
        ts = t.get("entry_ts")
        entry = float(t.get("entry") or 0)
        if not ts or entry <= 0 or not (lo <= ts < hi):
            continue
        r = float(t.get("points") or 0) / entry * 100.0
        if net and sym:
            r -= _cost_pct(sym, t)
        out.append(r)
    return out


def _score(rets):
    """Mean % return per trade. Deliberately simple: a compound objective
    (PF x winrate x ...) has more knobs to overfit and is harder to read."""
    return sum(rets) / len(rets) if rets else 0.0


def _pf(rets):
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)


# ──────────────────────────────── main ───────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="atr_trailing")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--train", type=int, default=180, help="train window, days")
    ap.add_argument("--test",  type=int, default=60,  help="test window, days")
    args = ap.parse_args()

    spec = ADAPTERS.get(args.strategy)
    if not spec:
        print(f"no walk-forward adapter for '{args.strategy}'. "
              f"available: {', '.join(ADAPTERS)}")
        return

    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - int(args.years * 365 * DAY_MS)
    print(f"strategy : {args.strategy} ({spec['param']} in {spec['values']})")
    print(f"window   : {ist_str(start_ms)} -> {ist_str(now_ms)} IST")
    print(f"folds    : train {args.train}d / test {args.test}d, "
          f"rolling by {args.test}d\n")

    grand = defaultdict(list)   # symbol -> OOS returns (tuned)
    fixed = defaultdict(lambda: defaultdict(list))   # symbol -> value -> OOS rets
    picks = defaultdict(list)
    fold_scores = defaultdict(list)   # per-fold OOS mean, for sign consistency
    gross = defaultdict(list)         # same trades before costs

    for sym in spec["symbols"]:
        if sym not in INSTRUMENTS:
            continue
        rows = load_1h(sym, start_ms, now_ms)
        if not rows:
            print(f"{sym}: no data, skipped")
            continue
        df = to_frame(rows)

        # One backtest per parameter value over the whole frame; split later.
        runs = {v: spec["fn"](sym, v, df) for v in spec["values"]}
        t0, t1 = rows[0][0], rows[-1][0]

        print(f"── {sym} ──")
        print(f"{'fold':>4} {'train end':>12} {'pick':>6} {'train':>9} "
              f"{'test':>9} {'n_tr':>5} {'n_te':>5}")

        fold, cur = 0, t0 + args.train * DAY_MS
        while cur + args.test * DAY_MS <= t1:
            tr_lo, tr_hi = t0, cur                      # expanding-anchored train
            te_lo, te_hi = cur, cur + args.test * DAY_MS

            scored = []
            for v in spec["values"]:
                r = _returns(runs[v], tr_lo, tr_hi, sym)
                if len(r) >= MIN_TRAIN_TRADES:
                    scored.append((_score(r), v, len(r)))
            if not scored:
                cur += args.test * DAY_MS
                continue
            scored.sort(reverse=True)
            best_s, best_v, n_tr = scored[0]

            te = _returns(runs[best_v], te_lo, te_hi, sym)
            if len(te) >= MIN_TEST_TRADES:
                fold += 1
                grand[sym] += te
                picks[sym].append(best_v)
                fold_scores[sym].append(_score(te))
                gross[sym] += _returns(runs[best_v], te_lo, te_hi, sym, net=False)
                for v in spec["values"]:
                    fixed[sym][v] += _returns(runs[v], te_lo, te_hi, sym)
                print(f"{fold:>4} {ist_str(cur)[:10]:>12} {best_v:>6.1f} "
                      f"{best_s:>+8.3f}% {_score(te):>+8.3f}% {n_tr:>5} {len(te):>5}")
            cur += args.test * DAY_MS
        print()

    # ── verdict ──────────────────────────────────────────────────────────
    print("=" * 72)
    print("COST IMPACT — gross vs net of measured Vantage spread + swap")
    print("=" * 72)
    print(f"{'symbol':>9} {'n':>5} {'gross':>9} {'cost':>9} {'net':>9} {'verdict':>22}")
    for sym in grand:
        g, nt = gross[sym], grand[sym]
        if not g or not nt:
            continue
        gs, ns = _score(g), _score(nt)
        flip = ("survives costs" if ns > 0 and gs > 0 else
                "KILLED by costs" if gs > 0 >= ns else
                "negative either way")
        print(f"{sym:>9} {len(nt):>5} {gs:>+8.3f}% {gs-ns:>+8.3f}% {ns:>+8.3f}% {flip:>22}")
    print()

    print("=" * 72)
    print("OUT-OF-SAMPLE RESULT — tuned vs fixed parameters")
    print("=" * 72)
    print(f"{'symbol':>9} {'n':>5} {'OOS mean':>10} {'PF':>6}   "
          f"{'best fixed':>12} {'fixed mean':>11} {'tuning adds':>12}")
    for sym in grand:
        oos = grand[sym]
        if not oos:
            continue
        best_fixed_v, best_fixed_r = None, None
        for v, r in fixed[sym].items():
            if r and (best_fixed_r is None or _score(r) > _score(best_fixed_r)):
                best_fixed_v, best_fixed_r = v, r
        delta = _score(oos) - _score(best_fixed_r or [])
        print(f"{sym:>9} {len(oos):>5} {_score(oos):>+9.3f}% {_pf(oos):>6.2f}   "
              f"{best_fixed_v:>12} {_score(best_fixed_r or []):>+10.3f}% "
              f"{delta:>+11.3f}%")
    print()

    print("is the OOS result distinguishable from zero?")
    print(f"{'symbol':>9} {'n':>5} {'mean':>9} {'95% CI':>20} {'folds +':>8} "
          f"{'verdict':>12}")
    for sym in grand:
        oos = grand[sym]
        if len(oos) < 2:
            continue
        m   = _score(oos)
        var = sum((x - m) ** 2 for x in oos) / (len(oos) - 1)
        h   = 1.96 * (var ** 0.5) / (len(oos) ** 0.5)
        # Fold-level sign consistency: a mean dragged up by one lucky window is
        # not the same as an edge that shows up repeatedly.
        pos = sum(1 for f in fold_scores[sym] if f > 0)
        sig = "SIGNAL" if abs(m) > h else "noise"
        print(f"{sym:>9} {len(oos):>5} {m:>+8.3f}% "
              f"[{m-h:>+6.3f}, {m+h:>+6.3f}] "
              f"{pos:>3}/{len(fold_scores[sym]):<4} {sig:>12}")
    print()

    # Per-trade returns are fat-tailed, so a t-interval on them is weak. Whether
    # each independent test WINDOW came out positive is the better question:
    # a consistent small edge and one lucky window look identical in the mean
    # but completely different here. One-sided sign test against a coin flip.
    import math
    print("fold sign test — did the edge repeat across independent windows?")
    print(f"{'symbol':>9} {'folds +':>9} {'p(>=k | coin)':>15} {'verdict':>14}")
    n_tested = len([s for s in grand if fold_scores[s]])
    for sym in grand:
        f = fold_scores[sym]
        if not f:
            continue
        k, n = sum(1 for x in f if x > 0), len(f)
        p = sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)
        print(f"{sym:>9} {k:>4}/{n:<4} {p:>15.4f} "
              f"{('consistent' if p < 0.05 else 'not consistent'):>14}")
    # Honesty about multiple comparisons: scanning several instruments and
    # reporting the winner is how spurious results get published.
    bonf = 0.05 / max(n_tested, 1)
    print(f"\n  {n_tested} instruments were tested, so the multiplicity-adjusted")
    print(f"  threshold is {bonf:.4f}, not 0.05. A result between the two is")
    print("  suggestive at best — it is roughly what scanning this many series")
    print("  turns up by chance.")
    print()

    # A positive mean built from a handful of outsized winners is a different
    # animal from a positive mean built from many small ones, even when every
    # significance test agrees. It cannot be sized the same way, and it dies if
    # you miss a few trades to downtime, slippage or a filter tweak.
    print("concentration — is the result carried by a few trades?")
    print(f"{'symbol':>9} {'n':>5} {'median':>9} {'top5 share':>11} "
          f"{'mean ex-top5':>13} {'shape':>22}")
    for sym in grand:
        oos = sorted(grand[sym], reverse=True)
        if len(oos) < 12:
            continue
        tot = sum(oos)
        med = sorted(oos)[len(oos) // 2]
        ex5 = sum(oos[5:]) / len(oos[5:])
        # "Share of total" is only meaningful when the total is positive. On a
        # losing series the ratio inverts and a naive threshold reports the
        # concentration as broad-based, which is backwards.
        if tot > 0:
            share = sum(oos[:5]) / tot * 100
            shape = ("outlier-driven" if share > 50 else
                     "partly outlier-driven" if share > 30 else "broad-based")
            share_s = f"{share:>10.0f}%"
        else:
            shape, share_s = "n/a — series loses", f"{'—':>11}"
        print(f"{sym:>9} {len(oos):>5} {med:>+8.3f}% {share_s} "
              f"{ex5:>+12.3f}% {shape:>22}")
    print()

    print("parameter stability — what each fold picked:")
    for sym, p in picks.items():
        uniq = sorted(set(p))
        churn = sum(1 for a, b in zip(p, p[1:]) if a != b)
        verdict = ("STABLE" if len(uniq) == 1 else
                   "unstable — grid is fitting noise" if churn >= len(p) / 2 else
                   "drifts")
        print(f"  {sym:>9}: {p}  -> {verdict}")
    print()

    print("how to read this:")
    print("  * 'tuning adds' <= 0 means walk-forward tuning did NOT beat pinning")
    print("    one value. That is the common outcome, and it means the honest")
    print("    move is to stop tuning that knob rather than tune it better.")
    print("  * An OOS mean near zero with PF near 1.0 is a strategy with no")
    print("    demonstrated edge on this data — not a strategy to size up.")
    print("  * A parameter that changes most folds is noise-fitting even if the")
    print("    OOS number happens to look acceptable.")


if __name__ == "__main__":
    main()
