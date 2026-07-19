"""
atr_trailing_strategy.py
------------------------
ATR Chandelier Trailing Stop Strategy

Philosophy:
  Unlike a fixed TP, this strategy rides the trend as far as it goes and only
  exits when the market takes back a meaningful chunk (2.5 × ATR) of the peak
  profit — giving trades room to breathe during pullbacks while locking in gains.

Entry rules (same as 1H FVG + EMA):
  BUY  — 20 EMA rising + price touches/bounces off EMA + bullish candle close
  SELL — 20 EMA falling + price rallies to EMA + bearish candle close

Exit rules (ATR Trailing Stop):
  BUY  — trail SL at: highest_price_since_entry − (2.5 × ATR)
          Close when price drops through the trailing SL.
  SELL — trail SL at: lowest_price_since_entry  + (2.5 × ATR)
          Close when price rises through the trailing SL.

Market regime gate (per-day rolling check):
  Regime is re-evaluated every 24 candles (~1 trading day) on a 50-candle window.
  SIDEWAYS day  → skip new entries; open trades continue unaffected.
  WEAK day      → ATR multiplier bumped to 3.0 for extra breathing room.
  TRENDING day  → normal entry + default ATR multiplier.

Why 2.5 × ATR?
  ATR measures the average candle range. Trailing 2.5× ensures the stop
  is never taken by a normal-sized pullback — only by a genuine trend reversal.

Asset-specific ATR periods:
  BTC/ETH crypto : 14-period ATR on 1H candles
  Gold           : 14-period ATR on 1H candles (higher noise → consider 3.0×)
"""

import pandas as pd
from app.strategies.market_regime import detect_regime_from_df


# ── ATR calculation ───────────────────────────────────────────────────────────

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.astype(float).ewm(span=period, adjust=False).mean()


# ── Entry detector ────────────────────────────────────────────────────────────

def _bullish_entry(df: pd.DataFrame, i: int, ema20: pd.Series) -> bool:
    """Price touches EMA and closes above it — bullish entry."""
    if i < 1:
        return False
    low   = float(df["low"].iloc[i])
    close = float(df["close"].iloc[i])
    e20   = float(ema20.iloc[i])
    return low <= e20 and close > e20


def _bearish_entry(df: pd.DataFrame, i: int, ema20: pd.Series) -> bool:
    """Price touches EMA and closes below it — bearish entry."""
    if i < 1:
        return False
    high  = float(df["high"].iloc[i])
    close = float(df["close"].iloc[i])
    e20   = float(ema20.iloc[i])
    return high >= e20 and close < e20


# ── Backtest engine ───────────────────────────────────────────────────────────

def run_atr_trailing_backtest(
    df: pd.DataFrame,
    symbol: str  = "BTCUSDT",
    atr_period:  int   = 14,
    atr_mult:    float = 2.5,
    ema_period:  int   = 20,
    lookback:    int   = 200,
) -> list:
    """
    Run the ATR Trailing Stop strategy on a 1H DataFrame.
    Returns a list of trade result dicts compatible with the existing backtest format.
    """
    if df is None or len(df) < max(atr_period + 5, ema_period + 5, 50):
        return []

    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)

    ema20  = _ema(closes, ema_period)
    ema50  = _ema(closes, 50)
    atr    = _atr(df, atr_period)

    trades = []
    start  = max(atr_period + 5, ema_period + 5, len(df) - lookback)

    in_trade    = False
    direction   = None
    entry_price = 0.0
    trail_sl    = 0.0
    best_price  = 0.0
    entry_idx   = 0
    entry_ts    = None

    # ── Rolling regime — recomputed every 24 candles (≈ 1 trading day on 1H) ─
    # Only checked before new entries; open trades are never affected.
    REGIME_UPDATE_INTERVAL = 24   # candles between checks (~1 day on 1H)
    REGIME_WINDOW          = 50   # look-back window (~2 trading days)
    current_regime    = "WEAK"    # safe default until first check
    effective_mult    = atr_mult
    last_regime_check = start - 1  # forces a check on the very first candle

    for i in range(start, len(df)):
        c   = float(closes.iloc[i])
        h   = float(highs.iloc[i])
        l   = float(lows.iloc[i])
        e20 = float(ema20.iloc[i])
        e50 = float(ema50.iloc[i])
        cur_atr = float(atr.iloc[i])
        ts  = df["timestamp"].iloc[i] if "timestamp" in df.columns else None

        # ── Per-day regime refresh (only when flat / not in a trade) ─────────
        if not in_trade and (i - last_regime_check) >= REGIME_UPDATE_INTERVAL:
            regime_window = df.iloc[max(0, i - REGIME_WINDOW):i]
            if len(regime_window) >= 30:
                regime_info    = detect_regime_from_df(regime_window)
                current_regime = regime_info["regime"]
                effective_mult = atr_mult if current_regime == "TRENDING" else atr_mult + 0.5
                print(f"[ATR] {symbol} i={i} regime={current_regime} | "
                      f"ADX={regime_info['adx']:.1f} | mult={effective_mult:.1f}x")
            last_regime_check = i

        if not in_trade:
            # ── Skip new entries on sideways days ─────────────────────────
            if current_regime == "SIDEWAYS":
                continue

            # ── Entry check ───────────────────────────────────────────────
            macro_bull = e20 > e50
            macro_bear = e20 < e50

            if macro_bull and _bullish_entry(df, i, ema20):
                in_trade    = True
                direction   = "BUY"
                entry_price = c
                best_price  = c
                trail_sl    = c - effective_mult * cur_atr
                entry_idx   = i
                entry_ts    = ts

            elif macro_bear and _bearish_entry(df, i, ema20):
                in_trade    = True
                direction   = "SELL"
                entry_price = c
                best_price  = c
                trail_sl    = c + effective_mult * cur_atr
                entry_idx   = i
                entry_ts    = ts

        else:
            # ── Manage open trade: update trailing SL ─────────────────────
            # Exit is checked against the trail level from PREVIOUS bars first —
            # raising the stop with this bar's high and then stopping out on this
            # bar's low would assume the high happened before the low (lookahead).
            if direction == "BUY":
                # Exit if price drops through trailing SL
                if l <= trail_sl:
                    outcome   = "WIN" if trail_sl > entry_price else "LOSS"
                    pts       = round(trail_sl - entry_price, 4)
                    trades.append(_make_trade(direction, entry_price, trail_sl, pts,
                                              outcome, entry_ts, ts, current_regime, effective_mult))
                    in_trade = False
                else:
                    # Not stopped — now update the trail with this bar's high
                    if h > best_price:
                        best_price = h
                    new_sl = best_price - effective_mult * cur_atr
                    trail_sl = max(trail_sl, new_sl)  # only move SL up

            elif direction == "SELL":
                # Exit if price rises through trailing SL (previous bars' level)
                if h >= trail_sl:
                    outcome   = "WIN" if trail_sl < entry_price else "LOSS"
                    pts       = round(entry_price - trail_sl, 4)
                    trades.append(_make_trade(direction, entry_price, trail_sl, pts,
                                              outcome, entry_ts, ts, current_regime, effective_mult))
                    in_trade = False
                else:
                    # Not stopped — now update the trail with this bar's low
                    if l < best_price:
                        best_price = l
                    new_sl = best_price + effective_mult * cur_atr
                    trail_sl = min(trail_sl, new_sl)  # only move SL down

    # Close any still-open trade at last bar
    if in_trade:
        last_c = float(closes.iloc[-1])
        pts    = round(last_c - entry_price, 4) if direction == "BUY" \
                 else round(entry_price - last_c, 4)
        outcome = "OPEN"
        trades.append(_make_trade(direction, entry_price, trail_sl, pts,
                                  outcome, entry_ts, None, current_regime, effective_mult))

    return trades


def _make_trade(direction, entry, exit_price, pts, outcome, entry_ts, exit_ts,
                regime, mult):
    from app.services.clock import ist_str

    def _ms(ts):
        """Raw UTC epoch ms. `date` below is an IST DISPLAY string — anything
        that buckets by time (sessions, day grouping) must use this instead."""
        if ts is None:
            return None
        try:
            v = int(ts)
            return v if v > 1e12 else v * 1000
        except (TypeError, ValueError):
            return None

    return {
        "entry_ts":     _ms(entry_ts),
        "exit_ts":      _ms(exit_ts),
        "signal":       direction,
        "entry":        round(entry, 4),
        "sl":           round(exit_price, 4),   # trailing SL at exit
        "tp":           None,                    # no fixed TP — trail handles it
        "outcome":      outcome,
        "points":       round(pts, 4),
        "date":         ist_str(entry_ts),   # IST, display only
        "exit_date":    ist_str(exit_ts),    # IST, display only
        "regime":       regime,
        "atr_mult":     mult,
        "strategy":     "ATR_Trailing",
        "quality_score": 7,
    }


# ── Summary builder ───────────────────────────────────────────────────────────

def summarise_atr(trades: list, symbol: str, days: int, atr_mult: float) -> dict:
    wins   = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total  = len(wins) + len(losses)
    wr     = round(len(wins) / total * 100, 1) if total else 0
    net    = round(sum(float(t.get("points", 0) or 0) for t in trades), 4)

    # Max drawdown: worst consecutive loss sequence
    cumulative = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cum += float(t.get("points", 0) or 0)
        cumulative.append(cum)
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    avg_win  = round(sum(float(t["points"]) for t in wins) / len(wins), 4) if wins else 0
    avg_loss = round(sum(float(t["points"]) for t in losses) / len(losses), 4) if losses else 0

    return {
        "strategy":    "ATR Chandelier Trailing Stop",
        "symbol":      symbol,
        "days":        days,
        "atr_mult":    atr_mult,
        "total":       len(trades),
        "wins":        len(wins),
        "losses":      len(losses),
        "win_rate":    wr,
        "net_points":  net,
        "avg_win":     avg_win,
        "avg_loss":    avg_loss,
        "best_trade":  round(max((float(t.get("points", 0) or 0) for t in trades), default=0), 4),
        "worst_trade": round(min((float(t.get("points", 0) or 0) for t in trades), default=0), 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": (
            round(sum(float(t["points"]) for t in wins) /
                  abs(sum(float(t["points"]) for t in losses)), 2)
            if losses and sum(float(t["points"]) for t in losses) != 0
            else (999.0 if wins else 0)
        ),
    }
