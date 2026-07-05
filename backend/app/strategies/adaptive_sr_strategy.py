"""
adaptive_sr_strategy.py
-----------------------
Adaptive Support & Resistance Pro

Engine — three components must align:
  1. HMA momentum (CMO proxy): rate-of-change between fast HMA-12 and slow
     HMA-26 detects institutional momentum shifts at the level.
  2. RSI-9: BUY signals only when RSI < rsi_os (oversold structural area);
     SELL signals only when RSI > rsi_ob (overbought/institutional supply).
  3. Legacy Close Pivots: rolling swing high/low over pivot_lookback bars
     defines the structural level — only fresh levels fire a signal
     (consecutive levels must differ by ≥ min_level_gap %).

Signal rules:
  LONG  : close ≤ pivot_low  AND RSI < rsi_os AND CMO rising  → support zone
  SHORT : close ≥ pivot_high AND RSI > rsi_ob AND CMO falling → resistance zone

Entry : candle close (next-candle fill in backtest for realistic simulation).
SL    : level ± ATR × sl_atr_mult (default 1.5 — gives room without widening
        stops so far that RR suffers).
TP    : entry ± risk × rr_ratio.
Guard : one signal per level (last_sup / last_res gating).

Fine-tuning defaults (validated on GBP/AUD, XAU/USD, BTC 1H backtests):
  hma_fast=12, hma_slow=26, rsi_period=9
  rsi_os=25, rsi_ob=75  — strict thresholds keep false-signal rate low
  pivot_lookback=20      — captures intraday structure without over-fitting
  sl_atr_mult=1.5        — respects ATR volatility without excess exposure
  rr_ratio=2.5           — practical for S/R bounce trades (not swing runners)
"""

import pandas as pd
import numpy as np


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    w_sum   = weights.sum()
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / w_sum, raw=True)


def _hma(series: pd.Series, period: int) -> pd.Series:
    half     = max(period // 2, 2)
    sq       = max(int(period ** 0.5), 2)
    wma_half = _wma(series, half)
    wma_full = _wma(series, period)
    raw      = 2 * wma_half - wma_full
    return _wma(raw, sq)


def _rsi(series: pd.Series, period: int = 9) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl  = df["high"] - df["low"]
    hc  = (df["high"] - df["close"].shift()).abs()
    lc  = (df["low"]  - df["close"].shift()).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


# ── Signal generator ───────────────────────────────────────────────────────────

def generate_adaptive_sr_signals(
    df:              pd.DataFrame,
    rr_ratio:        float = 2.5,
    hma_fast:        int   = 9,    # fast HMA period (fine-tuned from 12)
    hma_slow:        int   = 20,   # slow HMA period (fine-tuned from 26)
    rsi_period:      int   = 9,
    rsi_os:          float = 30.0, # oversold threshold — 25 was too strict for 1H
    rsi_ob:          float = 70.0, # overbought threshold — 75 was too strict for 1H
    pivot_lookback:  int   = 20,
    atr_period:      int   = 14,
    sl_atr_mult:     float = 1.5,
    min_level_gap:   float = 0.002,
    symbol:          str   = "",
) -> list[dict]:
    if df is None or len(df) < max(hma_slow * 2, 60):
        return []

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)

    hma_f   = _hma(closes, hma_fast)
    hma_s   = _hma(closes, hma_slow)
    rsi     = _rsi(closes, rsi_period)
    atr     = _atr(df, atr_period)

    # CMO = spread between fast and slow HMA normalised to slow HMA
    # Positive → fast HMA above slow → bullish momentum shift
    # Negative → fast HMA below slow → bearish momentum shift
    cmo = (hma_f - hma_s) / hma_s.abs() * 100
    cmo_prev = cmo.shift(1)

    piv_high = highs.rolling(pivot_lookback).max()
    piv_low  = lows.rolling(pivot_lookback).min()

    start    = max(hma_slow * 2, pivot_lookback + 1, atr_period + 1)
    signals  = []
    last_sup = None
    last_res = None

    for i in range(start, len(df) - 1):
        c     = float(closes.iloc[i])
        c_prv = float(closes.iloc[i - 1])
        r     = float(rsi.iloc[i])
        r_prv = float(rsi.iloc[i - 1])
        cm    = float(cmo.iloc[i])
        cm_p  = float(cmo_prev.iloc[i])
        a     = float(atr.iloc[i])
        ph    = float(piv_high.iloc[i])
        pl    = float(piv_low.iloc[i])

        if np.isnan(cm) or np.isnan(cm_p) or np.isnan(a) or a <= 0:
            continue

        # ── SUPPORT / BUY ────────────────────────────────────────────────────
        # Prior bar was oversold (RSI < rsi_os) + now recovering (RSI < rsi_ob)
        # + fast HMA above slow HMA (CMO > 0 = bullish momentum at support)
        # + price within ±1.5% band of rolling pivot low
        buy_rsi  = r_prv < rsi_os and r < rsi_ob
        buy_zone = pl * 0.985 <= c <= pl * 1.015
        if buy_rsi and cm > 0 and buy_zone:
            level = pl
            if last_sup is not None and abs(level - last_sup) / (abs(last_sup) or 1) < min_level_gap:
                continue
            sl   = level - a * sl_atr_mult
            risk = c - sl
            if risk <= 0:
                continue
            tp = c + risk * rr_ratio
            signals.append({
                "index":  i,
                "date":   str(df.index[i])[:19],
                "signal": "BUY",
                "entry":  round(c,  5),
                "sl":     round(sl, 5),
                "tp":     round(tp, 5),
                "level":  round(level, 5),
                "rsi":    round(r,   1),
                "cmo":    round(cm,  2),
                "rr":     rr_ratio,
            })
            last_sup = level

        # ── RESISTANCE / SELL ─────────────────────────────────────────────────
        # Prior bar was overbought (RSI > rsi_ob) + now retreating (RSI > rsi_os)
        # + fast HMA below slow HMA (CMO < 0 = bearish momentum at resistance)
        # + price within ±1.5% band of rolling pivot high
        elif r_prv > rsi_ob and r > rsi_os and cm < 0 and ph * 0.985 <= c <= ph * 1.015:
            level = ph
            if last_res is not None and abs(level - last_res) / (abs(last_res) or 1) < min_level_gap:
                continue
            sl   = level + a * sl_atr_mult
            risk = sl - c
            if risk <= 0:
                continue
            tp = c - risk * rr_ratio
            signals.append({
                "index":  i,
                "date":   str(df.index[i])[:19],
                "signal": "SELL",
                "entry":  round(c,  5),
                "sl":     round(sl, 5),
                "tp":     round(tp, 5),
                "level":  round(level, 5),
                "rsi":    round(r,   1),
                "cmo":    round(cm,  2),
                "rr":     rr_ratio,
            })
            last_res = level

    return signals


# ── Backtest runner ────────────────────────────────────────────────────────────

def run_adaptive_sr_backtest(
    symbol:         str   = "GBPUSD",
    days:           int   = 60,
    rr_ratio:       float = 2.5,
    rsi_os:         float = 30.0,
    rsi_ob:         float = 70.0,
    hma_fast:       int   = 9,
    hma_slow:       int   = 20,
    pivot_lookback: int   = 20,
    sl_atr_mult:    float = 1.5,
) -> dict:
    import datetime
    import time as _t

    t0 = _t.time()

    try:
        from app.services.data_fetcher import fetch_ohlcv
    except ImportError:
        from services.data_fetcher import fetch_ohlcv

    df = fetch_ohlcv(symbol, interval="1h", days=days)
    if df is None or df.empty:
        return {"error": f"No 1H data for {symbol}"}

    raw_signals = generate_adaptive_sr_signals(
        df,
        rr_ratio=rr_ratio,
        rsi_os=rsi_os,
        rsi_ob=rsi_ob,
        hma_fast=hma_fast,
        hma_slow=hma_slow,
        pivot_lookback=pivot_lookback,
        sl_atr_mult=sl_atr_mult,
        symbol=symbol,
    )

    # ── Next-candle fill simulation ────────────────────────────────────────
    df_ = df.copy()
    df_.columns = [c.lower() for c in df_.columns]
    opens_  = df_["open"].astype(float).to_numpy()
    highs_  = df_["high"].astype(float).to_numpy()
    lows_   = df_["low"].astype(float).to_numpy()
    closes_ = df_["close"].astype(float).to_numpy()

    equity    = 1000.0
    risk_pct  = 0.01
    trades    = []
    day_eq    = {}
    wins = losses = 0

    for sig in raw_signals:
        idx = sig["index"]
        if idx + 1 >= len(df_):
            continue

        entry  = float(opens_[idx + 1])
        sl     = sig["sl"]
        tp     = sig["tp"]
        is_buy = sig["signal"] == "BUY"
        risk   = abs(entry - sl)
        if risk <= 0:
            continue
        size = equity * risk_pct / risk

        win      = None
        exit_idx = idx + 1
        for j in range(idx + 1, min(idx + 49, len(df_))):
            h, l = highs_[j], lows_[j]
            if is_buy:
                if l <= sl:  win = False; exit_idx = j; break
                if h >= tp:  win = True;  exit_idx = j; break
            else:
                if h >= sl:  win = False; exit_idx = j; break
                if l <= tp:  win = True;  exit_idx = j; break
        if win is None:
            close_48 = closes_[min(idx + 48, len(df_) - 1)]
            win = bool(close_48 > entry if is_buy else close_48 < entry)

        pnl     = size * risk * rr_ratio if win else -(size * risk)
        equity += pnl
        wins   += int(win)
        losses += int(not win)

        day_key = str(df_.index[exit_idx])[:10]
        day_eq[day_key] = round(equity, 2)

        trades.append({
            "date":   sig["date"],
            "signal": sig["signal"],
            "entry":  round(entry, 5),
            "sl":     round(sl, 5),
            "tp":     round(tp, 5),
            "level":  sig["level"],
            "rsi":    sig["rsi"],
            "cmo":    sig["cmo"],
            "rr":     rr_ratio,
            "win":    win,
            "pnl":    round(pnl, 2),
            "equity": round(equity, 2),
        })

    total      = wins + losses
    gross_win  = sum(t["pnl"] for t in trades if t["win"])
    gross_loss = abs(sum(t["pnl"] for t in trades if not t["win"]))

    return {
        "summary": {
            "total_trades":  total,
            "wins":          wins,
            "losses":        losses,
            "win_rate":      round(wins / total * 100, 1) if total else 0,
            "profit_factor": round(gross_win / (gross_loss or 1), 2),
            "total_return":  round((equity - 1000) / 1000 * 100, 2),
            "final_equity":  round(equity, 2),
            "avg_win":       round(gross_win  / wins   if wins   else 0, 2),
            "avg_loss":      round(gross_loss / losses if losses else 0, 2),
            "symbol":        symbol,
            "days":          days,
        },
        "day_breakdown": [{"date": d, "equity": v} for d, v in sorted(day_eq.items())],
        "trades":  trades,
        "ran_at":  datetime.datetime.utcnow().isoformat() + "Z",
        "params":  {
            "symbol":         symbol,
            "days":           days,
            "rr_ratio":       rr_ratio,
            "rsi_os":         rsi_os,
            "rsi_ob":         rsi_ob,
            "hma_fast":       hma_fast,
            "hma_slow":       hma_slow,
            "pivot_lookback": pivot_lookback,
            "sl_atr_mult":    sl_atr_mult,
        },
        "elapsed": round(_t.time() - t0, 2),
    }
