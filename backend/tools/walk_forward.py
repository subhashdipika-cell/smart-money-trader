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
    # smc_swing / momentum use yfinance symbol names for the same instruments.
    "BTCUSD":  {"exec": "BTCUSD",  "spread_pct": 0.0258,
                "swap_long_night": -25.0 / 365, "swap_short_night": 0.0},
    "ETHUSD":  {"exec": "ETHUSD",  "spread_pct": 0.1286,
                "swap_long_night": -30.0 / 365, "swap_short_night": -3.0 / 365},
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


# ── EMA20_Pullback ───────────────────────────────────────────────────────
# This one has no backtest engine — it is a live SIGNAL GENERATOR, so the
# harness has to reproduce how live actually calls it or it would be scoring a
# different strategy. Two traps:
#
#   1. generate_ema_signals gates on market regime using df.tail(100). Handing
#      it the whole 2-year frame would judge every historical setup by TODAY's
#      regime — and return nothing at all if today is SIDEWAYS.
#   2. Live passes lookback=LIVE_TRIGGER_BARS+1 (3 bars) and then applies
#      _filter_stale_signals, so only setups on the last ~2 bars survive.
#      Scanning the default 200 bars would invent setups live would never send.
#
# So: walk bar by bar with a trailing window, exactly as live sees it.
_EMA_WINDOW = 250     # EMA50 converges well inside this; covers tail(100)+ATR14
_EMA_SIG_CACHE = {}


def _ema_signals(symbol, df):
    """Every signal live would have emitted, replayed bar by bar. Cached: the
    trigger does not depend on rr_ratio, only the TP does."""
    key = (symbol, len(df), int(df["timestamp"].iloc[0]))
    if key in _EMA_SIG_CACHE:
        return _EMA_SIG_CACHE[key]
    from app.strategies.ema_strategy import generate_ema_signals
    from app.services.live_signal_service import (
        _filter_stale_signals, LIVE_TRIGGER_BARS)
    out, seen = [], set()
    for i in range(_EMA_WINDOW, len(df)):
        w = df.iloc[i - _EMA_WINDOW + 1:i + 1]
        try:
            sigs = generate_ema_signals(w, symbol=symbol,
                                        lookback=LIVE_TRIGGER_BARS + 1)
            sigs = _filter_stale_signals(sigs, w, symbol)
        except Exception:
            continue
        for s in sigs:
            # Map the in-window index back to an absolute bar, then dedupe:
            # a 3-bar lookback re-emits the same setup on consecutive calls,
            # while live blocks duplicates via signal_state.
            idx = s.get("index")
            if idx is None:
                continue
            abs_i = i - _EMA_WINDOW + 1 + int(idx)
            k = (abs_i, s.get("signal"), round(float(s.get("entry", 0)), 2))
            if k in seen:
                continue
            seen.add(k)
            out.append((abs_i, s))
    _EMA_SIG_CACHE[key] = out
    return out


def _resolve(df, bar_i, sig, rr, market=False):
    """Walk a signal forward under the live resolver's rules: touch fill, 24h
    to fill, 48h cap once filled, SL checked BEFORE TP inside a bar.

    Resolution is on 1H bars, same as the ATR engine, so the two are
    comparable. Intrabar order is unknowable at this granularity, hence the
    conservative SL-first assumption.
    """
    ts  = df["timestamp"].astype("int64").values
    hi  = df["high"].astype(float).values
    lo  = df["low"].astype(float).values
    cl  = df["close"].astype(float).values
    entry0 = float(sig["entry"])
    sl     = float(sig["sl"])
    buy    = str(sig.get("signal", "")).upper() == "BUY"
    risk   = abs(entry0 - sl)
    if risk <= 0:
        return None

    start = bar_i + 1
    if start >= len(df):
        return None
    if market:
        entry = cl[bar_i]                    # fill now, keep the risk distance
        sl    = entry - risk if buy else entry + risk
        fill_i = start
    else:
        entry, fill_i = entry0, None
    tp = entry + rr * risk if buy else entry - rr * risk

    t0 = ts[bar_i]
    for j in range(start, len(df)):
        if fill_i is None:
            if ts[j] - t0 > 24 * 3600_000:
                return None                  # expired unfilled — not a trade
            if (lo[j] <= entry) if buy else (hi[j] >= entry):
                fill_i = j
            else:
                continue
        elif ts[j] - ts[fill_i] > 48 * 3600_000:
            px = cl[j]
            pts = (px - entry) if buy else (entry - px)
            return {"signal": sig.get("signal"), "entry": entry,
                    "points": pts, "outcome": "WIN" if pts > 0 else "LOSS",
                    "entry_ts": int(ts[fill_i]), "exit_ts": int(ts[j])}
        hit_sl = (lo[j] <= sl) if buy else (hi[j] >= sl)
        hit_tp = (hi[j] >= tp) if buy else (lo[j] <= tp)
        if hit_sl:
            return {"signal": sig.get("signal"), "entry": entry,
                    "points": -risk, "outcome": "LOSS",
                    "entry_ts": int(ts[fill_i]), "exit_ts": int(ts[j])}
        if hit_tp:
            return {"signal": sig.get("signal"), "entry": entry,
                    "points": rr * risk, "outcome": "WIN",
                    "entry_ts": int(ts[fill_i]), "exit_ts": int(ts[j])}
    return None


def _ema20(symbol, value, df, market=False):
    trades = []
    for bar_i, sig in _ema_signals(symbol, df):
        t = _resolve(df, bar_i, sig, value, market=market)
        if t:
            trades.append(t)
    return trades


def _ema20_market(symbol, value, df):
    return _ema20(symbol, value, df, market=True)


# ── smc_swing / eth_momentum / btc_momentum ──────────────────────────────
# These fetch their own yfinance history and return a result dict rather than
# taking a frame, so they are run once over the full window and their trades
# split by entry_ts. Their data source differs from the Binance/MT5 frames used
# above, so compare them WITHIN strategy across folds, not against ATR.
#
# CRITICAL: unclosed trades are dropped here. smc_swing returns trades with
# outcome "OPEN" and counts their mark-to-market P&L in `net_points` — on
# ETHUSD over 700d that is 17 positions worth +2510 points against -1201 on
# closed trades, so the reported net (+1309) is an artifact and the strategy
# actually loses. The opens are not recent stragglers either: they are spread
# across the whole window, one entered 542 days before the data ends, and 16
# of 17 are profitable. Losers hit their stop and close; winners stay "open"
# and get marked favourably. Counting them is survivorship inflation.
_SELF_CACHE = {}


def _self_data_runner(mod_fn, key_prefix):
    def run(symbol, value, _df, param):
        key = (key_prefix, symbol, value)
        if key not in _SELF_CACHE:
            r = mod_fn(symbol, value, param)
            trades = (r or {}).get("trades") or []
            _SELF_CACHE[key] = [
                t for t in trades
                if t.get("outcome") in ("WIN", "LOSS") and t.get("entry_ts")
            ]
        return _SELF_CACHE[key]
    return run


def _smc(symbol, value, df):
    from app.strategies.smc_swing_strategy import run_smc_swing_backtest
    return _self_data_runner(
        lambda s, v, p: run_smc_swing_backtest(symbol=s, days=700, rr_ratio=v),
        "smc")(symbol, value, df, "rr_ratio")


def _mom(symbol, value, df):
    from app.strategies.eth_momentum_strategy import run_eth_momentum_backtest
    return _self_data_runner(
        lambda s, v, p: run_eth_momentum_backtest(symbol=s, days=700, rsi_high=v),
        "mom")(symbol, value, df, "rsi_high")


# ── Donchian breakout ────────────────────────────────────────────────────
# Not a live SMT strategy — a lead from tools/trend_benchmark.py, where
# Donchian 300/150 long/short on ETH returned +18.4%/yr at +0.90%/trade, the
# best per-trade figure in that table. The hypothesis is that ETH's two-year
# 45% decline is capturable by a rule that can SHORT, which nothing live can:
# every current strategy is long-biased.
#
# The obvious confound is that a short-capable rule will flatter itself in any
# downtrend. Hence the long-only control below: if the edge is real trend
# capture rather than a one-directional bet on a falling market, the long/short
# version should beat long-only ACROSS folds, not just in aggregate.
#
# Position-based, so a "trade" is one position held between flips.
def _donchian_trades(symbol, value, df, allow_short):
    import pandas as pd
    lb = int(value)
    ex = max(10, lb // 2)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float).values
    ts = df["timestamp"].astype("int64").values
    # shift(1): the breakout level must come from bars BEFORE the current one,
    # or the rule peeks at the bar it is deciding on.
    roll_hi = high.rolling(lb).max().shift(1).values
    roll_lo = low.rolling(ex).min().shift(1).values

    trades, pos, e_px, e_ts = [], 0, None, None
    for i in range(lb + 1, len(close)):
        want = pos
        if roll_hi[i] == roll_hi[i] and close[i] > roll_hi[i]:
            want = 1
        elif roll_lo[i] == roll_lo[i] and close[i] < roll_lo[i]:
            want = -1 if allow_short else 0
        if want == pos:
            continue
        if pos != 0:
            pts = (close[i] - e_px) * pos
            trades.append({"signal": "BUY" if pos > 0 else "SELL",
                           "entry": e_px, "points": pts,
                           "outcome": "WIN" if pts > 0 else "LOSS",
                           "entry_ts": int(e_ts), "exit_ts": int(ts[i])})
        pos, e_px, e_ts = want, close[i], ts[i]
    return trades


def _donch_ls(symbol, value, df):
    return _donchian_trades(symbol, value, df, allow_short=True)


def _donch_long(symbol, value, df):
    return _donchian_trades(symbol, value, df, allow_short=False)


ADAPTERS = {
    "donchian_ls": {
        "fn":      _donch_ls,
        "param":   "lookback",
        "values":  [200, 300, 400, 500],
        "symbols": ["BTCUSDT", "ETHUSDT", "XAUUSD"],
    },
    "donchian_long": {
        "fn":      _donch_long,
        "param":   "lookback",
        "values":  [200, 300, 400, 500],
        "symbols": ["BTCUSDT", "ETHUSDT", "XAUUSD"],
    },
    "atr_trailing": {
        "fn":      _atr,
        "param":   "atr_mult",
        "values":  [2.0, 2.5, 3.0, 3.5],     # mirrors strategy_tuner.TUNABLE
        "symbols": ["BTCUSDT", "ETHUSDT", "XAUUSD"],
    },
    # As it trades today: a limit order at the pullback level.
    "ema20_pullback": {
        "fn":      _ema20,
        "param":   "rr_ratio",
        "values":  [2.0, 2.5, 3.0, 4.0],
        "symbols": ["BTCUSDT", "ETHUSDT", "XAUUSD"],
    },
    # Same setups taken at market instead. The signal-log replay put the
    # adverse selection of waiting for the retrace at -0.35R/trade; this tests
    # that on 2 years of data rather than 380 logged signals.
    "ema20_market": {
        "fn":      _ema20_market,
        "param":   "rr_ratio",
        "values":  [2.0, 2.5, 3.0, 4.0],
        "symbols": ["BTCUSDT", "ETHUSDT", "XAUUSD"],
    },
    "smc_swing": {
        "fn":        _smc,
        "param":     "rr_ratio",
        "values":    [2.5, 3.0, 4.0],        # mirrors strategy_tuner.TUNABLE
        "symbols":   ["BTCUSD", "ETHUSD"],
        "self_data": True,
    },
    "momentum": {
        "fn":        _mom,
        "param":     "rsi_high",
        "values":    [55.0, 60.0, 65.0, 70.0],
        "symbols":   ["BTCUSD", "ETHUSD"],
        "self_data": True,
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
        if spec.get("self_data"):
            # Engine fetches its own history; derive the span from its trades.
            df = None
            runs = {v: spec["fn"](sym, v, None) for v in spec["values"]}
            all_ts = [int(t["entry_ts"]) for r in runs.values() for t in r]
            if not all_ts:
                print(f"{sym}: no trades, skipped")
                continue
            t0, t1 = min(all_ts), max(all_ts)
        else:
            if sym not in INSTRUMENTS:
                continue
            rows = load_1h(sym, start_ms, now_ms)
            if not rows:
                print(f"{sym}: no data, skipped")
                continue
            df = to_frame(rows)
            # One backtest per parameter value over the frame; split later.
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
