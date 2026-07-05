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

    if signals:
        for s in signals:
            print(f"[MTF] 1H FVG signal: {s['signal']} score={s['quality_score']} "
                  f"entry={s['entry']} SL={abs(s['entry']-s['sl']):.1f}pts")
    else:
        print(f"[MTF] No 1H FVG setups found for {symbol}")

    return signals