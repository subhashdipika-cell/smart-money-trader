"""
trend_benchmark.py — can a SLOW trend rule catch the moves the live
strategies keep missing?

Context. Walk-forward said every live strategy is flat-to-losing after costs,
which invites the wrong conclusion ("nothing works"). The cost table says
something more specific:

                buy & hold 2y     cost drag/yr at live trade frequency
    BTCUSDT           +1.0%                28.5%
    ETHUSDT          -44.9%                67.8%
    XAUUSD           +66.6%                 3.6%

The live strategies take 300-640 trades per instrument per 2 years. At Vantage
spreads that is 28-68% of notional PER YEAR paid to trade. No edge of ~0.2%
per trade survives that. The strategies are not wrong so much as far too busy
for the instruments they run on — and ETH, the most expensive to trade, is the
one they trade most.

So this tests the opposite end: rules that trade a handful of times a year and
hold for weeks, where cost is a rounding error and a sustained move is
actually captured.

  ema_cross     long while fast EMA > slow EMA (classic trend following)
  donchian      long on N-bar high breakout, exit on M-bar low
  above_ma      long while price is above a long moving average

Long-only and long/short are both reported: shorting pays swap on crypto and
earns it on gold, and the two years covered are not symmetric.

This is a BENCHMARK, not a proposal. It is deliberately unoptimised — one
obvious parameter set per rule, no grid, no fitting — because its job is to
answer "is there a slow way to capture these moves at all?" Anything that
looks promising here must then survive tools/walk_forward.py before it means
anything.

Run:  backend/.venv/Scripts/python.exe -m tools.trend_benchmark
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.session_backtest import load_1h                    # noqa: E402
from tools.walk_forward import COSTS                          # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSD"]


def ema(vals, span):
    k, out, cur = 2 / (span + 1), [], None
    for v in vals:
        cur = v if cur is None else v * k + cur * (1 - k)
        out.append(cur)
    return out


def _run(closes, highs, lows, ts, signal_fn, sym, allow_short):
    """Walk the series holding a position dictated by signal_fn(i) in {1,0,-1}.
    Costs are charged on every position CHANGE, plus swap per night held."""
    c = COSTS[sym]
    pos, entry_px, entry_ts = 0, None, None
    rets, trades = [], 0
    for i in range(len(closes)):
        want = signal_fn(i)
        if not allow_short and want < 0:
            want = 0
        if want == pos:
            continue
        if pos != 0:                                  # close current
            gross = ((closes[i] - entry_px) / entry_px * 100.0) * pos
            nights = (ts[i] - entry_ts) / 86_400_000
            swap = (c["swap_long_night"] if pos > 0 else c["swap_short_night"]) * nights
            rets.append(gross - c["spread_pct"] + swap)
            trades += 1
        pos, entry_px, entry_ts = want, closes[i], ts[i]
    return rets, trades


def report(name, rows_by_sym):
    print(f"── {name} ──")
    print(f"{'symbol':>9} {'trades':>7} {'total':>10} {'ann':>9} "
          f"{'per trade':>10} {'win%':>6}")
    for sym, (rets, trades, years) in rows_by_sym.items():
        if not rets:
            print(f"{sym:>9} {'—':>7}")
            continue
        tot = sum(rets)
        win = 100 * sum(1 for r in rets if r > 0) / len(rets)
        print(f"{sym:>9} {trades:>7} {tot:>+9.1f}% {tot/years:>+8.1f}% "
              f"{tot/len(rets):>+9.2f}% {win:>5.0f}%")
    print()


def main():
    now = int(time.time() * 1000)
    start = now - int(2 * 365 * 86_400_000)
    data = {}
    for sym in SYMBOLS:
        rows = load_1h(sym, start, now)
        if rows:
            data[sym] = rows

    print("2 years of 1H bars. Costs charged per position change "
          "(spread + swap).\n")

    # Buy & hold reference, cost charged once.
    bh = {}
    for sym, rows in data.items():
        closes = [r[4] for r in rows]
        years = (rows[-1][0] - rows[0][0]) / (365 * 86_400_000)
        nights = (rows[-1][0] - rows[0][0]) / 86_400_000
        c = COSTS[sym]
        gross = (closes[-1] / closes[0] - 1) * 100
        bh[sym] = ([gross - c["spread_pct"] + c["swap_long_night"] * nights],
                   1, years)
    report("buy & hold (reference, incl. 2y of swap)", bh)

    for allow_short in (False, True):
        tag = "long/short" if allow_short else "long only"

        for fast, slow in ((50, 200),):
            out = {}
            for sym, rows in data.items():
                closes = [r[4] for r in rows]
                f, s = ema(closes, fast), ema(closes, slow)
                years = (rows[-1][0] - rows[0][0]) / (365 * 86_400_000)
                out[sym] = _run(closes, None, None, [r[0] for r in rows],
                                lambda i: 1 if f[i] > s[i] else -1,
                                sym, allow_short) + (years,)
            report(f"EMA {fast}/{slow} cross on 1H — {tag}", out)

        for n in (200,):
            out = {}
            for sym, rows in data.items():
                closes = [r[4] for r in rows]
                m = ema(closes, n)
                years = (rows[-1][0] - rows[0][0]) / (365 * 86_400_000)
                out[sym] = _run(closes, None, None, [r[0] for r in rows],
                                lambda i: 1 if closes[i] > m[i] else -1,
                                sym, allow_short) + (years,)
            report(f"price vs EMA{n} on 1H — {tag}", out)

        for lb, ex in ((300, 150),):
            out = {}
            for sym, rows in data.items():
                closes = [r[4] for r in rows]
                highs = [r[2] for r in rows]
                lows = [r[3] for r in rows]
                years = (rows[-1][0] - rows[0][0]) / (365 * 86_400_000)
                state = {"p": 0}

                def sig(i, highs=highs, lows=lows, closes=closes, state=state):
                    if i < lb:
                        return 0
                    hi = max(highs[i - lb:i])
                    lo = min(lows[i - ex:i])
                    if closes[i] > hi:
                        state["p"] = 1
                    elif closes[i] < lo:
                        state["p"] = -1
                    return state["p"]

                out[sym] = _run(closes, highs, lows, [r[0] for r in rows],
                                sig, sym, allow_short) + (years,)
            report(f"Donchian {lb}/{ex} breakout on 1H — {tag}", out)

    print("Read this as a feasibility check, not a strategy. Any rule that")
    print("looks good here still has to clear tools/walk_forward.py — these")
    print("are single unoptimised parameter sets over one 2-year window, and")
    print("that window was a strong gold uptrend and an ETH bear market.")


if __name__ == "__main__":
    main()
