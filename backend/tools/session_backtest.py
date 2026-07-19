"""
session_backtest.py — which trading session pays, for which instrument?

Runs SMT's own strategy engines over a long, common history and buckets every
resulting trade by the UTC session it was ENTERED in. Answers the question the
signal-log replay could not: that log holds 380 signals with gold cells of n=2,
which is not enough to separate a session effect from noise. Two years of 1H
bars produces enough trades to say something.

Method
------
  data      Binance 1H for BTCUSDT/ETHUSDT (their live feed), MT5 XAUUSD+ H1
            for gold, converted off the broker's UTC+3 server clock. Cached
            under tools/_cache and reused.
  window    One common window for all three instruments, so a session verdict
            is not an artifact of one asset having seen a different market.
  sessions  Derived from each trade's `entry_ts` (raw UTC epoch ms) through
            app.services.sessions — never from the engine's `date` string,
            which is IST for display.
  metric    Percentage return (points / entry), NOT R-multiples. The ATR
            engine records `sl` as the TRAILING stop at exit rather than the
            initial risk, so |entry - sl| is not a risk unit and any R figure
            built from it would be wrong. Percent return is directly
            comparable across BTC at ~64,000 and gold at ~4,100.

Reading it
----------
Per-session sample sizes get thin once split by instrument AND strategy, so
the per-strategy table is reported with n and should be treated as indicative.
The instrument x session table is the one with enough trades to lean on.

Run:  backend/.venv/Scripts/python.exe -m tools.session_backtest
      ... --years 2 --refresh
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.sessions import session_from_ts, UNKNOWN          # noqa: E402
from app.services.clock import ist_str                              # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")
SESSION_ORDER = ["Asia", "London", "New York", "Off-session"]

# Instruments and the engines that can be run over a supplied 1H frame.
# smc_swing / eth_momentum fetch their own yfinance data on a different
# calendar and are excluded — mixing feeds would confound the comparison.
INSTRUMENTS = {
    "BTCUSDT": {"source": "binance", "mt5": None},
    "ETHUSDT": {"source": "binance", "mt5": None},
    "XAUUSD":  {"source": "mt5",     "mt5": "XAUUSD+"},
}


# ─────────────────────────────── data ────────────────────────────────────
def _fetch_binance_1h(symbol, start_ms, end_ms):
    out, cur = [], start_ms
    while cur < end_ms:
        url = ("https://api.binance.com/api/v3/klines?"
               f"symbol={symbol}&interval=1h&startTime={cur}&limit=1000")
        for attempt in range(5):
            try:
                rows = json.load(urllib.request.urlopen(url, timeout=30))
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt + 1))
        if not rows:
            break
        for r in rows:
            if r[0] <= end_ms:
                out.append((int(r[0]), float(r[1]), float(r[2]),
                            float(r[3]), float(r[4])))
        cur = int(rows[-1][0]) + 3600_000
        if len(rows) < 1000:
            break
    return out


def _fetch_mt5_h1(mt5_symbol, start_ms, end_ms):
    """H1 bars off the MT5 terminal, returned on a UTC clock.

    Read-only, and deliberately uses whichever terminal MetaTrader5 attaches
    to: IntelliTrade's and SMT's are different accounts on the same
    VantageMarkets-Demo server, so the history is identical, and initialising
    SMT's own terminal can wedge both SMT and AlphaEdge.
    """
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        off = None
        for probe in ("BTCUSD", "ETHUSD", "EURUSD"):
            if mt5.symbol_select(probe, True):
                tick = mt5.symbol_info_tick(probe)
                if tick and tick.time:
                    off = round((tick.time - time.time()) / 3600)
                    break
        if off is None:
            raise RuntimeError("could not measure MT5 server offset")
        if not mt5.symbol_select(mt5_symbol, True):
            raise RuntimeError(f"symbol {mt5_symbol} unavailable")
        bars = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_H1, 0, 40000)
        print(f"  {mt5_symbol}: {0 if bars is None else len(bars)} H1 bars "
              f"(server UTC{off:+d})")
    finally:
        mt5.shutdown()
    out = []
    for b in bars if bars is not None else []:
        ts = (int(b["time"]) - off * 3600) * 1000
        if start_ms <= ts <= end_ms:
            out.append((ts, float(b["open"]), float(b["high"]),
                        float(b["low"]), float(b["close"])))
    return sorted(out)


def load_1h(symbol, start_ms, end_ms, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_1h.csv")
    if os.path.exists(path) and not refresh:
        with open(path) as fh:
            rows = [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
                    for r in csv.reader(fh)]
        if rows and rows[0][0] <= start_ms + 7200_000:
            return rows
    cfg = INSTRUMENTS[symbol]
    print(f"  fetching {symbol} 1H…")
    rows = (_fetch_mt5_h1(cfg["mt5"], start_ms, end_ms) if cfg["source"] == "mt5"
            else _fetch_binance_1h(symbol, start_ms, end_ms))
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    print(f"  {symbol}: {len(rows)} bars cached")
    return rows


def to_frame(rows):
    import pandas as pd
    return pd.DataFrame(
        {"timestamp": [r[0] for r in rows],
         "open":  [r[1] for r in rows], "high": [r[2] for r in rows],
         "low":   [r[3] for r in rows], "close": [r[4] for r in rows],
         "volume": [0.0] * len(rows)})


# ───────────────────────────── aggregation ───────────────────────────────
class Cell:
    __slots__ = ("n", "win", "pct", "pts", "rets")

    def __init__(self):
        self.n = self.win = 0
        self.pct = self.pts = 0.0
        self.rets = []          # per-trade % returns, kept for significance

    def add(self, trade):
        entry = float(trade.get("entry") or 0)
        pts   = float(trade.get("points") or 0)
        if entry <= 0:
            return
        r = pts / entry * 100.0
        self.n   += 1
        self.win += trade.get("outcome") == "WIN"
        self.pct += r
        self.pts += pts
        self.rets.append(r)

    def ci95(self):
        """95% CI half-width on the mean return. A session 'edge' whose CI
        spans zero is not distinguishable from noise at this sample size."""
        if self.n < 2:
            return float("inf")
        m = self.avg
        var = sum((x - m) ** 2 for x in self.rets) / (self.n - 1)
        return 1.96 * (var ** 0.5) / (self.n ** 0.5)

    @property
    def winpct(self):
        return 100 * self.win / self.n if self.n else 0.0

    @property
    def avg(self):
        return self.pct / self.n if self.n else 0.0


def table(title, cells, rows, cols):
    print(f"── {title} ──")
    print(f"{'':>12}" + "".join(f"{c:>22}" for c in cols))
    for r in rows:
        line = f"{r:>12}"
        for c in cols:
            cell = cells.get((r, c))
            line += (f"{cell.n:>5} {cell.winpct:>4.0f}% {cell.avg:>+7.2f}%"
                     if cell and cell.n else f"{'—':>22}")
        print(line)
    print()


# ──────────────────────────────── main ───────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - int(args.years * 365 * 86400_000)
    print(f"window: {ist_str(start_ms)} -> {ist_str(now_ms)} IST "
          f"({args.years}y)\n")

    data = {}
    for sym in INSTRUMENTS:
        try:
            rows = load_1h(sym, start_ms, now_ms, args.refresh)
            if rows:
                data[sym] = rows
                print(f"  {sym}: {len(rows)} bars, "
                      f"{ist_str(rows[0][0])} -> {ist_str(rows[-1][0])} IST")
        except Exception as e:
            print(f"  {sym}: SKIPPED — {type(e).__name__}: {e}")
    print()

    from app.strategies.atr_trailing_strategy import run_atr_trailing_backtest

    by_sess   = defaultdict(Cell)   # (instrument, session)
    by_strat  = defaultdict(Cell)   # (instrument+strategy, session)
    overall   = defaultdict(Cell)   # (instrument, "ALL")
    no_ts     = 0

    for sym, rows in data.items():
        df = to_frame(rows)
        # lookback MUST be passed explicitly. The engine computes
        #   start = max(warmup, len(df) - lookback)
        # and defaults lookback=200, so it scans only the last 200 bars no
        # matter how much history it is handed — a 2-year frame and a 1000-bar
        # frame both yield ~9 trades. Every caller that omits this (including
        # strategy_tuner) is running a 200-bar backtest.
        trades = run_atr_trailing_backtest(df, symbol=sym, atr_mult=2.5,
                                           lookback=len(df))
        print(f"{sym}: ATR_Trailing produced {len(trades)} trades")
        for t in trades:
            ts = t.get("entry_ts")
            if not ts:
                no_ts += 1
                continue
            sess = session_from_ts(ts)
            if sess == UNKNOWN:
                no_ts += 1
                continue
            by_sess[(sym, sess)].add(t)
            by_strat[(f"{sym}/{t.get('strategy','?')}", sess)].add(t)
            overall[(sym, "ALL")].add(t)
    print()

    if no_ts:
        print(f"note: {no_ts} trades had no usable entry_ts and were skipped\n")

    syms = list(data)
    table("ATR_Trailing — avg % return per trade, by session "
          "(n / win% / avg%)", by_sess, syms, SESSION_ORDER)
    table("all sessions combined", overall, syms, ["ALL"])

    print("significance — mean % return with 95% CI (CI spanning 0 = noise):")
    print(f"{'instrument':>10} {'session':>12} {'n':>5} {'mean':>8} "
          f"{'95% CI':>20} {'verdict':>12}")
    for sym in syms:
        for s in SESSION_ORDER:
            c = by_sess.get((sym, s))
            if not c or c.n < 10:
                continue
            h = c.ci95()
            sig = "signal" if abs(c.avg) > h else "noise"
            print(f"{sym:>10} {s:>12} {c.n:>5} {c.avg:>+7.2f}% "
                  f"[{c.avg-h:>+6.2f}, {c.avg+h:>+6.2f}] {sig:>12}")
    print()

    # Win rate is a proportion, so it carries far less variance than a
    # fat-tailed mean return — if a session effect exists at all, this is the
    # test most likely to see it. Two-proportion z-test, session vs the rest of
    # that instrument's trades.
    print("win-rate test — session vs that instrument's other sessions:")
    print(f"{'instrument':>10} {'session':>12} {'win%':>6} {'rest%':>6} "
          f"{'z':>6} {'p':>7} {'verdict':>10}")
    for sym in syms:
        for s in SESSION_ORDER:
            c = by_sess.get((sym, s))
            if not c or c.n < 10:
                continue
            rw = sum(by_sess[(sym, o)].win for o in SESSION_ORDER
                     if o != s and by_sess.get((sym, o)))
            rn = sum(by_sess[(sym, o)].n for o in SESSION_ORDER
                     if o != s and by_sess.get((sym, o)))
            if rn < 10:
                continue
            p1, p2 = c.win / c.n, rw / rn
            pool   = (c.win + rw) / (c.n + rn)
            se     = (pool * (1 - pool) * (1 / c.n + 1 / rn)) ** 0.5
            z      = (p1 - p2) / se if se > 0 else 0.0
            # two-sided normal p-value without scipy
            import math
            p      = math.erfc(abs(z) / (2 ** 0.5))
            print(f"{sym:>10} {s:>12} {100*p1:>5.0f}% {100*p2:>5.0f}% "
                  f"{z:>+6.2f} {p:>7.3f} "
                  f"{'SIGNIFICANT' if p < 0.05 else 'ns':>10}")
    print()

    print("best session per instrument (by avg % return, n>=10):")
    for sym in syms:
        ranked = sorted(
            ((s, by_sess[(sym, s)]) for s in SESSION_ORDER
             if by_sess.get((sym, s)) and by_sess[(sym, s)].n >= 10),
            key=lambda x: -x[1].avg)
        if not ranked:
            print(f"  {sym:>9}: no session reached n>=10")
            continue
        best, worst = ranked[0], ranked[-1]
        # Difference of means, with its own CI — the ranking is only meaningful
        # if best and worst are actually separable.
        hb, hw = best[1].ci95(), worst[1].ci95()
        diff   = best[1].avg - worst[1].avg
        dh     = (hb ** 2 + hw ** 2) ** 0.5
        sep    = "separable" if diff > dh else "NOT separable"
        print(f"  {sym:>9}: BEST {best[0]:<12} {best[1].avg:+.2f}% "
              f"(n={best[1].n}, win {best[1].winpct:.0f}%)"
              f"   WORST {worst[0]:<12} {worst[1].avg:+.2f}% (n={worst[1].n})")
        print(f"  {'':>9}  spread {diff:+.2f}% ± {dh:.2f} -> {sep}")
    print()

    print("total points by instrument (absolute, not comparable across rows):")
    for sym in syms:
        c = overall[(sym, "ALL")]
        print(f"  {sym:>9}: {c.n:>4} trades  {c.pts:>+12.1f} pts  "
              f"{c.pct:>+8.1f}% cumulative")


if __name__ == "__main__":
    main()
