"""
br_filters.py — can selectivity rescue Break & Retest?

Widening the target was measured to be a dead end: the hit rate falls in
lockstep with the rising breakeven, so no rr is positive. The remaining lever
is taking FEWER trades — lifting the hit rate at a fixed 1:2 by refusing the
breaks least likely to hold.

The danger is obvious. With ~1500 setups per asset, filtering will always find
a subset that looks profitable, and the more filters tried the more certain
that becomes. Three guards:

  * Filters are chosen from MECHANISM, before seeing results, not searched.
    Each has a reason to work that could be stated in advance.
  * Every filter is scored on a held-out SECOND HALF of history. The first
    half is shown alongside so a filter that only works in one era is visible
    as such.
  * The multiplicity is stated. 5 filters x 3 assets = 15 tests, so the 5%
    threshold is really ~0.3% and a single "significant" result is roughly
    what chance produces.

A filter is only interesting if it improves BOTH halves, on more than one
asset, by more than the cost of the trades it removes.

Run:  backend/.venv/Scripts/python.exe -m tools.br_filters
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.strategies.break_retest_strategy import generate_break_retest_signals  # noqa: E402
from tools.session_backtest import load_1h, to_frame                            # noqa: E402
from tools.walk_forward import _cost_pct                                        # noqa: E402

RR = 2.0
SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSD"]

# Each filter: a reason it should work, stated before measuring.
FILTERS = {
    "all (baseline)":   lambda s: True,
    # A break against the prevailing trend is more likely to be a liquidity
    # grab than a continuation.
    "trend-aligned":    lambda s: s.get("br_aligned") == 1,
    # A bar that closes barely beyond the level has not really broken it.
    "decisive break":   lambda s: (s.get("br_break_atr") or 0) >= 0.5,
    # A level touched repeatedly is one the market is actually watching.
    "tested level >=2": lambda s: (s.get("br_touches") or 0) >= 2,
    # A fast return shows the break was accepted rather than rejected slowly.
    "fast retest <=3":  lambda s: (s.get("br_retest_bars") or 99) <= 3,
    # Breaks in dead volatility are chop; require ATR at or above its norm.
    "vol >= norm":      lambda s: (s.get("br_atr_rel") or 0) >= 1.0,
}


def resolve(df, sigs, sym):
    """Turn setups into completed trades, net of costs, keeping diagnostics."""
    ts = df["timestamp"].astype("int64").values
    hi = df["high"].astype(float).values
    lo = df["low"].astype(float).values
    out = []
    for s in sigs:
        i = int(s["index"])
        entry, sl, tp = float(s["entry"]), float(s["sl"]), float(s["tp"])
        buy = s["signal"] == "BUY"
        risk = abs(entry - sl)
        if risk <= 0 or i + 1 >= len(hi):
            continue
        for j in range(i + 1, len(hi)):
            hit_sl = (lo[j] <= sl) if buy else (hi[j] >= sl)
            hit_tp = (hi[j] >= tp) if buy else (lo[j] <= tp)
            if not (hit_sl or hit_tp):
                continue
            pts = -risk if hit_sl else abs(tp - entry)
            trade = {"signal": s["signal"], "entry": entry, "points": pts,
                     "entry_ts": int(ts[i]), "exit_ts": int(ts[j]),
                     "win": not hit_sl}
            trade["ret"] = pts / entry * 100.0 - _cost_pct(sym, trade)
            trade.update({k: v for k, v in s.items() if k.startswith("br_")})
            out.append(trade)
            break
    return out


def stats(trades):
    if not trades:
        return None
    n = len(trades)
    rets = [t["ret"] for t in trades]
    m = sum(rets) / n
    wins = sum(1 for t in trades if t["win"])
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    return {"n": n, "win": 100 * wins / n, "mean": m,
            "pf": (gw / gl) if gl else float("inf"), "total": sum(rets)}


def main():
    now = int(time.time() * 1000)
    start = now - int(8.8 * 365 * 86_400_000)
    print("Break & Retest @ 1:2 — selectivity filters")
    print("breakeven win rate at 1:2 is 33.3%\n")

    for sym in SYMBOLS:
        rows = load_1h(sym, start, now)
        if not rows:
            continue
        df = to_frame(rows)
        sigs = generate_break_retest_signals(df, rr_ratio=RR, scan_bars=len(df),
                                             symbol=sym)
        trades = resolve(df, sigs, sym)
        if not trades:
            continue
        # Split by TIME, not by trade count, so both halves are real eras.
        mid = (trades[0]["entry_ts"] + trades[-1]["entry_ts"]) // 2
        first = [t for t in trades if t["entry_ts"] < mid]
        second = [t for t in trades if t["entry_ts"] >= mid]

        print(f"── {sym} ── {len(trades)} trades, split at the midpoint of history")
        print(f"{'filter':>18} | {'FIRST half (in-era)':^28} | {'SECOND half (held out)':^28}")
        print(f"{'':>18} | {'n':>5} {'win%':>6} {'mean':>8} {'PF':>5} | "
              f"{'n':>5} {'win%':>6} {'mean':>8} {'PF':>5}")
        base2 = stats([t for t in second])
        for name, fn in FILTERS.items():
            a, b = stats([t for t in first if fn(t)]), stats([t for t in second if fn(t)])
            if not a or not b:
                print(f"{name:>18} |  (too few trades)")
                continue
            keep = 100 * b["n"] / base2["n"]
            better = "  <-- beats baseline in BOTH" if (
                name != "all (baseline)"
                and a["mean"] > stats([t for t in first])["mean"]
                and b["mean"] > base2["mean"]) else ""
            print(f"{name:>18} | {a['n']:>5} {a['win']:>5.1f}% {a['mean']:>+7.3f}% "
                  f"{a['pf']:>5.2f} | {b['n']:>5} {b['win']:>5.1f}% {b['mean']:>+7.3f}% "
                  f"{b['pf']:>5.2f}  (keeps {keep:.0f}%){better}")
        print()

    print("5 filters x 3 assets = 15 tests. At that multiplicity the 5% bar is")
    print("really ~0.3%, so one filter looking good in one place is noise. Only a")
    print("filter that improves BOTH halves on MORE THAN ONE asset is worth")
    print("carrying into tools/walk_forward.py.")


if __name__ == "__main__":
    main()
