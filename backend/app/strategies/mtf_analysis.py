"""
mtf_analysis.py
---------------
Simplified to use ONLY the 1H FVG + EMA strategy.
Backtest results: BTC 83% WR | ETH 88% WR | Gold 71% WR

Strategy rules:
  LONG:  Bullish FVG + price above rising 20 EMA on 1H
  SHORT: Bearish FVG + price below falling 20 EMA on 1H
  EXIT:  TP (3R) OR close beyond 20 EMA OR SL
"""

from app.strategies.htf_signal_generator import generate_htf_signals
from app.strategies.level_engine import apply_human_touch


def analyze_multi_timeframe(data, symbol='', scan_all=False, rr_ratio=None):
    """
    Run 1H FVG + EMA strategy only.
    Returns filtered signals ready for brain + quality filter.
    scan_all=True  → backtest mode: scan the whole history, not just fresh setups.
    rr_ratio=None  → live mode uses the Strategy-Tuner-applied RR (default 3.0).
    """
    htf_df = data.get("1h")

    if htf_df is None or htf_df.empty:
        print(f"[MTF] No 1H data available for {symbol}")
        return []

    signals = generate_htf_signals(htf_df, symbol=symbol, rr_ratio=rr_ratio, scan_all=scan_all)

    # ── Context awareness ("human touch") — live signals only ────────────────
    # Level map (swings + prev-day H/L + rounds) → skip stale/chasing setups,
    # skip entries into a barrier, and cap the TP just before the next wall.
    # Backtests (scan_all=True) are left untouched — historical signals would
    # need an as-of-then map, and the baseline must stay comparable.
    if signals and not scan_all:
        kept = []
        for s in signals:
            try:
                s, skip = apply_human_touch(s, htf_df, symbol)
            except Exception as e:
                print(f"[LEVELS/{symbol}] check failed (signal passed through): {e}")
                skip = False
            if not skip:
                kept.append(s)
        signals = kept

    if signals:
        for s in signals:
            print(f"[MTF] 1H FVG signal: {s['signal']} score={s['quality_score']} "
                  f"entry={s['entry']} SL={abs(s['entry']-s['sl']):.1f}pts")
    else:
        print(f"[MTF] No 1H FVG setups found for {symbol}")

    return signals