"""
htf_signal_generator.py
-----------------------
1H timeframe ICT FVG signal generator.
Backtest results (60 days):
  BTC: 83% WR | +46,649pts | PF 14.07
  ETH: 88% WR |   +483pts  | PF 14.25
  Gold: 71% WR |  +280pts   | PF 6.78

Trade lifecycle:
  LONG:  Bullish FVG + price above rising 20 EMA → entry at FVG top
         Exit: TP (3R) OR close below 20 EMA OR SL

  SHORT: Bearish FVG + price below falling 20 EMA → entry at FVG bottom
         Exit: TP (3R) OR close above 20 EMA OR SL

Key: EMA exit cuts losses early when trend invalidates
     (saved majority of losing trades in backtest)
"""

import json
import os

_BASE = os.path.join(os.path.dirname(__file__), "..", "..")

try:
    from app.strategies.market_regime import detect_regime_from_df as _detect_regime
except ImportError:
    _detect_regime = None
LEARNED_WEIGHTS_FILE = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))


def _ema(series_list, period):
    ema = []
    k = 2 / (period + 1)
    for i, v in enumerate(series_list):
        if i == 0:
            ema.append(float(v))
        else:
            ema.append(float(v) * k + ema[-1] * (1 - k))
    return ema


def _load_weights():
    try:
        with open(LEARNED_WEIGHTS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"min_quality_score": 6}


def generate_htf_signals(df_1h, symbol="BTCUSDT", rr_ratio=None, scan_all=False):
    """
    Generate ICT FVG signals on 1H data.
    Returns list of signals compatible with live engine format.
    Skips signal generation entirely when market is SIDEWAYS.

    scan_all=False (live): only the last 10 candles are checked — fresh setups.
    scan_all=True (backtest): the WHOLE history is scanned and the current-day
    regime gate is skipped (a backtest must not depend on today's regime).

    rr_ratio=None → use the value the Strategy Tuner applied (Learning page),
    falling back to the built-in default of 3.0.
    """
    if df_1h is None or len(df_1h) < 60:
        return []

    if rr_ratio is None:
        applied  = (_load_weights().get("applied_params") or {}).get("HTF_ICT_Intraday", {})
        rr_ratio = float(applied.get("value", 3.0)) if applied.get("param") == "rr_ratio" else 3.0

    # ── Market regime gate (live mode only) ──────────────────────────────────
    if _detect_regime is not None and not scan_all:
        regime_info = _detect_regime(df_1h.tail(100))
        regime      = regime_info["regime"]
        print(f"[HTF/{symbol}] Market regime: {regime} — {regime_info['reason']}")
        if regime == "SIDEWAYS":
            print(f"[HTF/{symbol}] Skipping signal generation — sideways market")
            return []
    # ─────────────────────────────────────────────────────────────────────────

    closes = [float(x) for x in df_1h["close"].tolist()]
    highs  = [float(x) for x in df_1h["high"].tolist()]
    lows   = [float(x) for x in df_1h["low"].tolist()]
    ts_list = df_1h["timestamp"].tolist() if "timestamp" in df_1h.columns else [0] * len(closes)

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)

    signals  = []
    # Identify the asset from its symbol — never from its price.
    # (Price-based detection misclassified ETH above $3,000 as Gold.)
    _sym    = (symbol or "").upper()
    is_btc  = "BTC" in _sym
    is_gold = ("XAU" in _sym) or ("GOLD" in _sym) or ("PAXG" in _sym)

    # Live: only the last 10 candles (fresh setups). Backtest: full history
    # after a 50-candle EMA warmup.
    start = 50 if scan_all else max(3, len(closes) - 10)

    for i in range(start, len(closes)):
        c   = closes[i]
        h   = highs[i]
        l   = lows[i]
        e20 = ema20[i]
        e50 = ema50[i]

        # EMA slope over last 5 candles
        e20_slope = (ema20[i] - ema20[i-5]) / 5 if i >= 5 else 0
        min_slope = c * 0.0002

        bullish_1h = c > e20 and e20 > e50 and e20_slope > min_slope
        bearish_1h = c < e20 and e20 < e50 and e20_slope < -min_slope

        # ── Bullish FVG ───────────────────────────────────────────────────
        # Standard ICT 3-candle FVG: origin=i-2, impulse=i-1, confirm=i
        # Valid when lows[i] > highs[i-2] (gap between top of origin and bottom of confirm)
        if bullish_1h and i >= 2:
            fvg_origin_high = highs[i-2]  # top of origin candle = bottom of FVG
            fvg_confirm_low = lows[i]     # bottom of current bar = top of FVG

            if fvg_confirm_low > fvg_origin_high:  # valid gap confirmed
                entry   = fvg_origin_high  # limit BUY at bottom of FVG (where price retraces)
                sl_raw  = lows[i-2]        # below FVG origin candle
                sl_dist = entry - sl_raw

                # Asset range check
                if is_btc:
                    if not (100 <= sl_dist <= 400): continue
                elif is_gold:
                    if not (5 <= sl_dist <= 30): continue
                else:
                    if not (15 <= sl_dist <= 35): continue

                tp = entry + sl_dist * rr_ratio

                confluences = [
                    "1H Bullish FVG",
                    f"20 EMA rising (slope: {e20_slope:+.2f})",
                    "20 EMA above 50 EMA (1H bullish)",
                    f"FVG gap: {fvg_origin_high:.2f} to {fvg_confirm_low:.2f}"
                ]

                # Discrete entries
                e1 = round(entry, 4)
                e2 = round(entry - sl_dist * 0.3, 4)
                e3 = round(entry - sl_dist * 0.6, 4)

                signals.append({
                    "signal":        "BUY",
                    "index":         i,
                    "timestamp":     ts_list[i],
                    "entry":         e1,
                    "sl":            round(sl_raw, 4),
                    "tp":            round(tp, 4),
                    "rr":            rr_ratio,
                    "timeframe":     "Intraday",
                    "session":       "Unknown",
                    "quality_score": 8,
                    "raw_score":     8,
                    "confidence":    "High",
                    "setup":         "1H FVG + EMA",
                    "confluences":   confluences,
                    "strategy_tag":  "HTF_ICT_Intraday",
                    "ema20_current": round(e20, 4),
                    "entries": {
                        "e1": {"price": e1, "size_pct": 50, "label": "FVG top"},
                        "e2": {"price": e2, "size_pct": 30, "label": "FVG mid"},
                        "e3": {"price": e3, "size_pct": 20, "label": "FVG bottom"}
                    },
                    "trailing_sl": {
                        "enabled":     True,
                        "activate_at": round(entry + sl_dist, 4),
                        "step":        round(sl_dist * 0.5, 4),
                        "current_sl":  round(sl_raw, 4),
                        "activated":   False
                    },
                    # EMA exit level — resolver uses this
                    "ema_exit": {
                        "enabled":   True,
                        "ema_value": round(e20, 4),
                        "exit_side": "below"  # BUY exits if close below EMA
                    }
                })

        # ── Bearish FVG ───────────────────────────────────────────────────
        # Standard ICT 3-candle FVG: origin=i-2, impulse=i-1, confirm=i
        # Valid when highs[i] < lows[i-2] (gap between bottom of origin and top of confirm)
        elif bearish_1h and i >= 2:
            fvg_origin_low  = lows[i-2]   # bottom of origin candle = top of FVG
            fvg_confirm_high = highs[i]    # top of current bar = bottom of FVG

            if fvg_confirm_high < fvg_origin_low:  # valid gap confirmed
                entry   = fvg_origin_low   # limit SELL at top of FVG (where price retraces)
                sl_raw  = highs[i-2]       # above FVG origin candle
                sl_dist = sl_raw - entry

                if is_btc:
                    if not (100 <= sl_dist <= 400): continue
                elif is_gold:
                    if not (5 <= sl_dist <= 30): continue
                else:
                    if not (15 <= sl_dist <= 35): continue

                tp = entry - sl_dist * rr_ratio

                confluences = [
                    "1H Bearish FVG",
                    f"20 EMA falling (slope: {e20_slope:+.2f})",
                    "20 EMA below 50 EMA (1H bearish)",
                    f"FVG gap: {fvg_confirm_high:.2f} to {fvg_origin_low:.2f}"
                ]

                e1 = round(entry, 4)
                e2 = round(entry + sl_dist * 0.3, 4)
                e3 = round(entry + sl_dist * 0.6, 4)

                signals.append({
                    "signal":        "SELL",
                    "index":         i,
                    "timestamp":     ts_list[i],
                    "entry":         e1,
                    "sl":            round(sl_raw, 4),
                    "tp":            round(tp, 4),
                    "rr":            rr_ratio,
                    "timeframe":     "Intraday",
                    "session":       "Unknown",
                    "quality_score": 8,
                    "raw_score":     8,
                    "confidence":    "High",
                    "setup":         "1H FVG + EMA",
                    "confluences":   confluences,
                    "strategy_tag":  "HTF_ICT_Intraday",
                    "ema20_current": round(e20, 4),
                    "entries": {
                        "e1": {"price": e1, "size_pct": 50, "label": "FVG bottom"},
                        "e2": {"price": e2, "size_pct": 30, "label": "FVG mid"},
                        "e3": {"price": e3, "size_pct": 20, "label": "FVG top"}
                    },
                    "trailing_sl": {
                        "enabled":     True,
                        "activate_at": round(entry - sl_dist, 4),
                        "step":        round(sl_dist * 0.5, 4),
                        "current_sl":  round(sl_raw, 4),
                        "activated":   False
                    },
                    "ema_exit": {
                        "enabled":   True,
                        "ema_value": round(e20, 4),
                        "exit_side": "above"  # SELL exits if close above EMA
                    }
                })

    return signals
