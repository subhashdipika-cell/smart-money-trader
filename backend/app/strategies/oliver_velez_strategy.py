"""
oliver_velez_strategy.py
------------------------
SMT Strategy Tester wrapper for the Oliver Velez Swing Trading Strategy.

Wraps the standalone OliverVelezSwing class and converts results into
the standard SMT format (_wrap / _summary / _day_breakdown).

Parameters exposed to the tester
---------------------------------
symbol          : Any Yahoo Finance ticker  (e.g. AAPL, MSFT, SPY, NVDA)
days            : Backtest window in calendar days
elephant_mult   : Body multiplier for Elephant Bar detection   (default 2.0)
bar_count_target: New-high bars needed before first half exit  (default 4)

All other Oliver Velez parameters use their defaults but can be overridden
by passing extra kwargs to run_oliver_velez_backtest().
"""

import os
import sys
import pandas as pd

# ── Make the root project folder importable so we can re-use
#    oliver_velez_swing.py without duplicating the code ────────────────────────
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from oliver_velez_swing import OliverVelezSwing           # root-level file
from app.strategies.gold_advanced_strategies import (     # SMT helpers
    _summary, _day_breakdown, _wrap,
)


# ── Ticker normalisation ───────────────────────────────────────────────────────
# SMT sometimes passes "AAPL" already correct; normalise common variations

def _yfin_ticker(symbol: str) -> str:
    """Return the Yahoo Finance ticker string for a given symbol."""
    mapping = {
        # Crypto
        "BTCUSD":   "BTC-USD",
        "BTCUSDT":  "BTC-USD",
        "BTC":      "BTC-USD",
        "ETHUSD":   "ETH-USD",
        "ETHUSDT":  "ETH-USD",
        "ETH":      "ETH-USD",
        # Stocks — common generic names
        "APPLE":     "AAPL",
        "GOOGLE":    "GOOGL",
        "ALPHABET":  "GOOGL",
        "AMAZON":    "AMZN",
        "NVIDIA":    "NVDA",
        "TESLA":     "TSLA",
        "META":      "META",
        "MICROSOFT": "MSFT",
    }
    s = symbol.strip().upper()
    return mapping.get(s, s)   # pass through unknown tickers as-is


# ── SMT-compatible trade record converter ────────────────────────────────────

def _to_smt_trade(t: dict, symbol: str) -> dict:
    """
    Convert one Oliver Velez trade record to the shape _summary / _day_breakdown expect.

    Required by _summary:  pnl, date
    Optional but useful:   signal, entry, exit, outcome, setup, rr_ratio
    """
    # Normalise date to "YYYY-MM-DD" string
    raw_date = str(t.get("entry_date", ""))
    date_str  = raw_date[:10] if len(raw_date) >= 10 else raw_date

    pnl       = float(t.get("total_pnl", 0.0))
    outcome   = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

    # Build a readable setup label
    trigger   = t.get("trigger", "")
    half      = t.get("half_exit_price") is not None
    reason    = t.get("exit_reason", "")
    setup     = f"OV {trigger.replace('_', ' ')} {'(partial + runner)' if half else ''} → {reason}"

    return {
        "date":          date_str,
        "signal":        "BUY",
        "entry":         t.get("entry_price", 0.0),
        "exit":          t.get("exit_price", 0.0),
        "sl":            t.get("stop_loss", 0.0),
        "pnl":           round(pnl, 2),
        "pnl_pips":      round(pnl, 2),          # dollar P&L for stocks
        "outcome":       outcome,
        "setup":         setup,
        "trigger":       trigger,
        "bars_held":     t.get("bars_held", 0),
        "new_high_bars": t.get("new_high_bars", 0),
        "half_exit":     t.get("half_exit_price"),
        "quality_score": 8,
        "rr_ratio":      None,                    # dynamic per trade
        "symbol":        symbol,
        "exit_reason":   reason,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def run_oliver_velez_backtest(
    symbol:          str   = "AAPL",
    days:            int   = 365,
    elephant_mult:   float = 2.0,
    bar_count_target: int  = 4,
    **kwargs,
) -> dict:
    """
    Run the Oliver Velez Swing backtest and return an SMT-compatible result dict.

    Parameters
    ----------
    symbol          : Yahoo Finance ticker (e.g. "AAPL", "SPY", "NVDA")
    days            : Calendar days of history to fetch and backtest
    elephant_mult   : Elephant Bar body multiplier  (1.5 = looser, 3.0 = stricter)
    bar_count_target: Bars to first partial exit    (3 = faster, 5 = slower)
    **kwargs        : Any other OliverVelezSwing constructor parameters

    Returns
    -------
    SMT-format dict with keys: strategy, summary, trades, day_breakdown, ran_at, params
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance is not installed. Run: pip install yfinance"}

    ticker = _yfin_ticker(symbol)

    # ── Fetch data via yfinance ───────────────────────────────────────────────
    # Use explicit start/end dates — Yahoo can reject period strings like "365d"
    # with an HTTP 500 (this broke the AAPL backtest). Fall back to Ticker.history.
    from datetime import datetime as _dt, timedelta as _td
    start_date = (_dt.utcnow() - _td(days=days + 7)).strftime("%Y-%m-%d")
    raw = None
    try:
        raw = yf.download(
            ticker,
            start=start_date,
            auto_adjust=True,
            progress=False,
        )
    except Exception:
        raw = None
    if raw is None or raw.empty:
        try:
            raw = yf.Ticker(ticker).history(start=start_date, auto_adjust=True)
        except Exception as e:
            return {"error": f"Failed to download data for '{ticker}': {e}"}

    if raw is None or raw.empty:
        return {"error": f"No data returned for '{ticker}'. Check the ticker symbol."}

    # Flatten multi-level columns yfinance sometimes returns
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]

    # Minimum bars needed (200 MA warmup + some trading room)
    if len(raw) < 220:
        return {
            "error": (
                f"Only {len(raw)} bars returned for '{ticker}'. "
                f"Try a longer 'days' window (at least 365 days recommended)."
            )
        }

    # ── Run strategy ──────────────────────────────────────────────────────────
    strategy = OliverVelezSwing(
        elephant_mult    = elephant_mult,
        bar_count_target = bar_count_target,
        **kwargs,
    )

    try:
        results = strategy.backtest(raw, initial_capital=1000.0)
    except Exception as e:
        return {"error": f"Backtest error: {e}"}

    ov_trades = results.get("trades", pd.DataFrame())
    if isinstance(ov_trades, pd.DataFrame):
        ov_trades = ov_trades.to_dict("records")

    # ── Convert to SMT format ─────────────────────────────────────────────────
    smt_trades = [_to_smt_trade(t, ticker) for t in ov_trades]

    params = {
        "strategy_id":    "oliver_velez_swing",
        "symbol":         ticker,
        "elephant_mult":  elephant_mult,
        "bar_count_target": bar_count_target,
        "timeframe":      "Daily",
    }

    return _wrap("oliver_velez_swing", days, smt_trades, params)
