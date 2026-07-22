"""
break_retest_strategy.py — break a level, come BACK to it, enter on the hold.

Mirror of IntelliTrade's app/strategies/break_retest.py. Same state machine,
same defaults, deliberately kept in step so a result measured in one app means
something in the other.

Why retest rather than plain breakout. A naive breakout buys the bar that
clears the level — the worst price of the move, and exactly what a false break
punishes. Waiting for price to return to the broken level and hold gives a
tighter stop (just through the level) and a defined invalidation, so the same
target is a shorter distance in risk terms.

It also suits how this executor places orders. A retest entry sits BEHIND
price, so it is a genuine LIMIT — unlike the breakout strategies that were
sending buys above market as buy-limits and being rejected with retcode 10015
until execute_signal learned to pick STOP vs LIMIT.

State machine, all causal:

  1. level  = highest high / lowest low of the prior `lookback` bars, measured
              to the bar BEFORE the break so the forming bar cannot define the
              level it breaks.
  2. break  = a close beyond that level.
  3. retest = within `retest_window` bars price trades back INTO the level but
              still CLOSES beyond it — the level held.
  4. entry  = that bar's close; SL `sl_buffer_atr` ATR through the level, so
              the trade is wrong precisely when the level fails; TP = rr x risk.
  5. abort  = a close back through the level before any retest.

MEASURED, NOT ENABLED. Backtested 2026-07-22 on 20,000 H1 bars per asset
through IntelliTrade's engine (identical logic):

    GOLD  409 trades  31.05% win  -32.99%  PF 0.86
    BTC   423 trades  32.39% win  -28.70%  PF 0.88
    ETH   378 trades  30.69% win  -52.05%  PF 0.72

At 1:2 the breakeven win rate is 33.3% and this lands near 31% on every asset,
so it loses across the board. A 2000-bar slice showed BTC at PF 1.32, which is
what small samples do — the full history says 0.88.

RAISING THE TARGET DOES NOT RESCUE IT, and this is the useful finding. The
obvious fix is to widen rr until the observed hit rate clears breakeven — 1:2's
~31% would break even at 2.25. It does not work, because a wider target is hit
less often and the win rate falls in lockstep with the bar it has to clear:

    rr        2.00          2.25          2.50          3.00     (win% / breakeven%)
    BTC    31.2 / 33.3   28.6 / 30.8   25.9 / 28.6   23.2 / 25.0
    GOLD   31.5 / 33.3   29.1 / 30.8   27.5 / 28.6   24.7 / 25.0
    ETH    30.3 / 33.3   27.2 / 30.8   25.0 / 28.6   21.6 / 25.0

The shortfall stays near 2 points at every distance, and gross return per trade
is flat across rr (BTC -0.02% to -0.03%). The market prices the
target-distance tradeoff close to fairly, so this setup has no target at which
it is positive — the edge is missing, not mis-sized. Gold at rr=3.0 comes
closest (24.7 vs 25.0, -0.006%/trade out of sample) and is still negative.

Registered so it can be backtested and tuned from the UI; NOT wired into
asset_strategy_config.json. Do not enable without fresh evidence from
tools/walk_forward.py.
"""
from __future__ import annotations


def _atr(highs, lows, closes, period):
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
            continue
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    out = [None] * len(trs)
    run = 0.0
    for i, v in enumerate(trs):
        run += v
        if i >= period:
            run -= trs[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _break_meta(i, level, direction, highs, lows, closes, atr_i, lookback):
    """Facts about the break itself, for selectivity research: how decisively
    the bar closed beyond the level (in ATR), and how many times that level was
    tested beforehand (a proxy for how much it matters)."""
    beyond = (closes[i] - level) if direction == 1 else (level - closes[i])
    tol = 0.25 * atr_i if atr_i else 0.0
    touches = 0
    for j in range(max(0, i - lookback), i):
        if direction == 1 and abs(highs[j] - level) <= tol:
            touches += 1
        elif direction == -1 and abs(lows[j] - level) <= tol:
            touches += 1
    return {"break_atr": (beyond / atr_i) if atr_i else None, "touches": touches}


def generate_break_retest_signals(df, rr_ratio: float = 2.0, lookback: int = 20,
                                  retest_window: int = 10, atr_period: int = 14,
                                  sl_buffer_atr: float = 0.5,
                                  scan_bars: int = 3, symbol: str = "") -> list:
    """Signals in SMT's dict shape. `scan_bars` limits how far back a returned
    setup may sit, matching the live trigger window used by the other
    generators — the full scan still runs so state is correct."""
    if df is None or len(df) < lookback + atr_period + 5:
        return []

    highs = [float(x) for x in df["high"]]
    lows = [float(x) for x in df["low"]]
    closes = [float(x) for x in df["close"]]
    atr = _atr(highs, lows, closes, atr_period)
    n = len(closes)

    # EMA-200 trend reference, emitted as a diagnostic on each signal so
    # selectivity filters can be evaluated without regenerating setups.
    ema200 = [None] * n
    k = 2 / 201
    cur = closes[0]
    for i, c in enumerate(closes):
        cur = c if i == 0 else c * k + cur * (1 - k)
        ema200[i] = cur

    out = []
    pend_dir, pend_level, pend_age = 0, None, 0
    pend_meta = {}

    for i in range(lookback + atr_period + 1, n):
        a = atr[i]
        if not a or a <= 0:
            continue
        # Level from bars strictly before i, so the breaking bar never helps
        # define the level it breaks.
        res = max(highs[i - lookback:i])
        sup = min(lows[i - lookback:i])

        fired = None
        if pend_dir != 0:
            pend_age += 1
            if pend_age > retest_window:
                pend_dir = 0
            elif pend_dir == 1:
                if closes[i] < pend_level:
                    pend_dir = 0
                elif lows[i] <= pend_level and closes[i] > pend_level:
                    entry = closes[i]
                    sl = pend_level - sl_buffer_atr * a
                    if entry - sl > 0:
                        fired = ("BUY", entry, sl, entry + rr_ratio * (entry - sl))
                    pend_dir = 0
            else:
                if closes[i] > pend_level:
                    pend_dir = 0
                elif highs[i] >= pend_level and closes[i] < pend_level:
                    entry = closes[i]
                    sl = pend_level + sl_buffer_atr * a
                    if sl - entry > 0:
                        fired = ("SELL", entry, sl, entry - rr_ratio * (sl - entry))
                    pend_dir = 0

        if pend_dir == 0 and fired is None:
            if closes[i] > res:
                pend_dir, pend_level, pend_age = 1, res, 0
                pend_meta = _break_meta(i, res, 1, highs, lows, closes, a, lookback)
            elif closes[i] < sup:
                pend_dir, pend_level, pend_age = -1, sup, 0
                pend_meta = _break_meta(i, sup, -1, highs, lows, closes, a, lookback)

        if fired and i >= n - scan_bars:
            side, entry, sl, tp = fired
            out.append({
                "signal":        side,
                "entry":         round(entry, 4),
                "sl":            round(sl, 4),
                "tp":            round(tp, 4),
                "rr":            rr_ratio,
                "timeframe":     "1H",
                "confidence":    "Medium",
                "quality_score": 6,
                "raw_score":     6,
                "index":         i,
                "strategy_tag":  "Break_Retest",
                "setup":         "Break & Retest",
                "confluences":   [
                    f"{'Broke above' if side == 'BUY' else 'Broke below'} "
                    f"{lookback}-bar level {round(pend_level or entry, 4)}",
                    "Retested and held",
                    f"SL {sl_buffer_atr}x ATR through level, {rr_ratio}R target",
                ],
                # Diagnostics for selectivity research (tools/br_filters.py).
                # Ignored by the live engine; they just ride along.
                "br_trend":       1 if closes[i] > ema200[i] else -1,
                "br_aligned":     int((1 if closes[i] > ema200[i] else -1)
                                      == (1 if side == "BUY" else -1)),
                "br_break_atr":   pend_meta.get("break_atr"),
                "br_touches":     pend_meta.get("touches"),
                "br_retest_bars": pend_age,
                "br_atr_rel":     (a / (sum(x for x in atr[max(0, i - 200):i] if x)
                                        / max(1, len([x for x in atr[max(0, i - 200):i] if x])))
                                   if i > 200 else None),
            })
    return out
