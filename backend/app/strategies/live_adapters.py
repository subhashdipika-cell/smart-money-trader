"""
live_adapters.py
----------------
Live signal generators for strategies that were previously backtest-only.

Each function mirrors the exact entry rules of its backtest counterpart
but operates on the most recent bars — no historical iteration.

Functions
---------
  generate_atr_trailing_signal(data, symbol, rr)
      ATR Chandelier: EMA-20 touch on 1H Binance data → trail SL at 2.5×ATR.

  generate_smc_swing_signal(symbol, rr)
      SMC Liquidity Sweep: 4H sweep + 1H MSS from yfinance.

  generate_momentum_signal(symbol, rr)
      Dual-TF Momentum (BTC or ETH): Daily EMA regime + 4H MACD cross from yfinance.
"""

from __future__ import annotations
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.astype(float).ewm(span=period, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h  = df["high"].astype(float)
    l  = df["low"].astype(float)
    c  = df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.astype(float).diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


_TICKER = {
    "BTCUSD": "BTC-USD", "BTCUSDT": "BTC-USD", "BTC": "BTC-USD",
    "ETHUSD": "ETH-USD", "ETHUSDT": "ETH-USD", "ETH": "ETH-USD",
}


def _tuned(tag: str, param: str, default: float) -> float:
    """
    Return the value the Strategy Tuner applied for this strategy
    (Learning page → 'Apply to live engine'), or the built-in default.
    """
    try:
        import json, os
        wf = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "learned_weights.json"))
        with open(wf) as f:
            applied = (json.load(f).get("applied_params") or {}).get(tag, {})
        if applied.get("param") == param:
            return float(applied["value"])
    except Exception:
        pass
    return default


def _yf(ticker: str, period: str, interval: str) -> pd.DataFrame:
    import yfinance as yf
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
    except Exception as e:
        print(f"[LiveAdapter] yfinance error ({ticker} {interval}): {e}")
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                  for c in df.columns]
    return df.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1. ATR Trailing — uses Binance 1H data already in `data` dict
# ─────────────────────────────────────────────────────────────────────────────

def generate_atr_trailing_signal(
    data:   dict,
    symbol: str,
    rr:     float = 2.0,
    atr_mult: float = None,   # None → Strategy-Tuner-applied value (default 2.5)
) -> list:
    if atr_mult is None:
        atr_mult = _tuned("ATR_Trailing", "atr_mult", 2.5)
    """
    Entry: 20-EMA touch-and-close (price low ≤ EMA, close > EMA for long;
           price high ≥ EMA, close < EMA for short).
    SL  : 2.5 × ATR(14) from entry.
    TP  : rr × SL-distance from entry (informational — live engine trails).
    Data: Binance 1H klines already fetched by check_symbol.
    """
    from app.strategies.market_regime import detect_regime_from_df

    df_1h = data.get("1h")
    if df_1h is None or len(df_1h) < 50:
        return []

    df     = df_1h.copy()
    closes = df["close"].astype(float)
    highs  = df["high"].astype(float)
    lows   = df["low"].astype(float)
    ema20  = _ema(closes, 20)
    atr_s  = _atr(df, 14)

    # Use second-to-last bar to avoid a partial/open candle
    i = len(df) - 2
    if i < 25:
        return []

    close   = float(closes.iloc[i])
    low     = float(lows.iloc[i])
    high    = float(highs.iloc[i])
    e20     = float(ema20.iloc[i])
    cur_atr = float(atr_s.iloc[i])

    if cur_atr <= 0:
        return []

    # Regime gate — skip new entries in sideways markets
    regime_slice = df.iloc[max(0, i - 50):i]
    if len(regime_slice) >= 30:
        try:
            regime = detect_regime_from_df(regime_slice).get("regime", "TRENDING")
            if regime == "SIDEWAYS":
                print(f"[ATR Live] {symbol}: sideways regime — skipping entry")
                return []
        except Exception:
            pass

    sl_dist = atr_mult * cur_atr
    signals = []

    if low <= e20 and close > e20:
        sl = round(close - sl_dist, 4)
        tp = round(close + rr * sl_dist, 4)
        signals.append({
            "signal":        "BUY",
            "entry":         round(close, 4),
            "sl":            sl,
            "tp":            tp,
            "rr":            rr,
            "timeframe":     "1H",
            "quality_score": 6,
            "raw_score":     6,
            "strategy_tag":  "ATR_Trailing",
            "confluences":   ["EMA-20 touch", f"ATR {atr_mult}× trail SL"],
            "setup":         "EMA20 Bounce + ATR Chandelier",
        })

    elif high >= e20 and close < e20:
        sl = round(close + sl_dist, 4)
        tp = round(close - rr * sl_dist, 4)
        signals.append({
            "signal":        "SELL",
            "entry":         round(close, 4),
            "sl":            sl,
            "tp":            tp,
            "rr":            rr,
            "timeframe":     "1H",
            "quality_score": 6,
            "raw_score":     6,
            "strategy_tag":  "ATR_Trailing",
            "confluences":   ["EMA-20 rejection", f"ATR {atr_mult}× trail SL"],
            "setup":         "EMA20 Rejection + ATR Chandelier",
        })

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# 2. SMC Liquidity Sweep — fetches its own yfinance 4H + 1H data
# ─────────────────────────────────────────────────────────────────────────────

def generate_smc_swing_signal(symbol: str, rr: float = None) -> list:
    if rr is None:
        rr = _tuned("SMC_Swing", "rr_ratio", 3.0)
    """
    Bullish: 4H low wicks below swing SSL → closes back inside → 1H MSS (close
             above recent 10-bar high) → enter at current price.
    Bearish: 4H high wicks above swing BSL → closes back inside → 1H MSS (close
             below recent 10-bar low) → enter at current price.
    SL: 0.03% beyond wick extreme of surrounding 12 1H candles.
    TP: entry ± rr × (entry − SL).
    """
    ticker = _TICKER.get(symbol.upper().replace("-", ""), "BTC-USD")

    df_4h = _yf(ticker, "30d", "4h")
    df_1h = _yf(ticker, "10d", "1h")

    if df_4h.empty or df_1h.empty:
        return []
    if len(df_4h) < 25 or len(df_1h) < 15:
        return []

    high_4h  = df_4h["high"].astype(float)
    low_4h   = df_4h["low"].astype(float)
    close_4h = df_4h["close"].astype(float)

    # ── Swing reference: bars [−25 … −3] to avoid the sweep bars themselves ──
    ref_end   = len(df_4h) - 3
    ref_start = max(0, ref_end - 20)
    swing_high = float(high_4h.iloc[ref_start:ref_end].max())
    swing_low  = float(low_4h.iloc[ref_start:ref_end].min())

    # ── Detect sweep in last 3 completed 4H bars ──────────────────────────────
    sweep_dir = None
    for j in range(len(df_4h) - 3, len(df_4h) - 1):  # skip last open bar
        h = float(high_4h.iloc[j])
        l = float(low_4h.iloc[j])
        c = float(close_4h.iloc[j])
        if h > swing_high and c <= swing_high:
            sweep_dir = "BEAR"   # wick above BSL, closed back → expect short
            break
        if l < swing_low and c >= swing_low:
            sweep_dir = "BULL"   # wick below SSL, closed back → expect long
            break

    if sweep_dir is None:
        return []

    print(f"[SMC Live] {symbol}: 4H {sweep_dir} sweep detected")

    # ── 1H MSS: aggressive close beyond recent 10-bar high/low ───────────────
    high_1h  = df_1h["high"].astype(float)
    low_1h   = df_1h["low"].astype(float)
    close_1h = df_1h["close"].astype(float)

    mss_found = False
    for j in range(len(df_1h) - 5, len(df_1h) - 1):
        if j < 10:
            continue
        c         = float(close_1h.iloc[j])
        ref_high  = float(high_1h.iloc[j - 10:j].max())
        ref_low   = float(low_1h.iloc[j - 10:j].min())
        if sweep_dir == "BULL" and c > ref_high:
            mss_found = True
            break
        if sweep_dir == "BEAR" and c < ref_low:
            mss_found = True
            break

    if not mss_found:
        print(f"[SMC Live] {symbol}: no 1H MSS confirmation")
        return []

    # ── SL from wick extreme of surrounding 12 1H bars ────────────────────────
    entry = float(close_1h.iloc[-1])
    win   = df_1h.iloc[max(0, len(df_1h) - 12):]

    if sweep_dir == "BULL":
        sl_anchor = float(win["low"].astype(float).min())
        sl  = round(sl_anchor * 0.9997, 4)
        tp  = round(entry + (entry - sl) * rr, 4)
        sig = "BUY"
        confluences = ["4H SSL sweep", "1H MSS bull confirmation"]
    else:
        sl_anchor = float(win["high"].astype(float).max())
        sl  = round(sl_anchor * 1.0003, 4)
        tp  = round(entry - (sl - entry) * rr, 4)
        sig = "SELL"
        confluences = ["4H BSL sweep", "1H MSS bear confirmation"]

    return [{
        "signal":        sig,
        "entry":         round(entry, 4),
        "sl":            sl,
        "tp":            tp,
        "rr":            rr,
        "timeframe":     "1H",
        "quality_score": 7,
        "raw_score":     7,
        "strategy_tag":  "SMC_Swing",
        "confluences":   confluences,
        "setup":         f"SMC 4H Liquidity Sweep + 1H MSS ({sweep_dir})",
    }]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Dual-TF Momentum — BTC or ETH, fetches yfinance Daily + 4H
# ─────────────────────────────────────────────────────────────────────────────

def generate_momentum_signal(symbol: str, rr: float = None) -> list:
    if rr is None:
        tag = "BTC_Momentum" if symbol.upper().startswith("BTC") else "ETH_Momentum"
        rr  = _tuned(tag, "rr", 2.0)
    """
    Macro filter (Daily): 50 EMA > 200 EMA → Bull; else Bear (1-day lag).
    Entry    (4H)       : MACD(12,26,9) crosses in macro direction.
    Filter   (4H RSI14) : 45–65 for longs; 35–55 for shorts.
    SL: lowest low / highest high of prior 10 4H bars × 0.1% buffer.
    TP: entry ± rr × SL-distance.
    """
    ticker = _TICKER.get(symbol.upper().replace("-", ""), "BTC-USD")
    is_eth = "ETH" in symbol.upper()
    tag    = "ETH_Momentum" if is_eth else "BTC_Momentum"

    df_daily = _yf(ticker, "400d", "1d")
    df_4h    = _yf(ticker, "60d",  "4h")

    if df_daily.empty or df_4h.empty:
        return []
    if len(df_daily) < 210 or len(df_4h) < 30:
        return []

    # ── Daily macro regime (use bar −2 → confirmed prior-day close) ───────────
    daily_c = df_daily["close"].astype(float)
    ema50_d  = _ema(daily_c, 50)
    ema200_d = _ema(daily_c, 200)
    macro = "BULL" if float(ema50_d.iloc[-2]) > float(ema200_d.iloc[-2]) else "BEAR"

    # ── 4H MACD ───────────────────────────────────────────────────────────────
    c4    = df_4h["close"].astype(float)
    macd  = _ema(c4, 12) - _ema(c4, 26)
    sig_l = _ema(macd, 9)

    # Crossover: previous bar negative/positive → current bar positive/negative
    prev = float(macd.iloc[-3]) - float(sig_l.iloc[-3])
    curr = float(macd.iloc[-2]) - float(sig_l.iloc[-2])
    bull_cross = prev < 0 and curr > 0
    bear_cross = prev > 0 and curr < 0

    if not (bull_cross or bear_cross):
        return []     # no fresh crossover — nothing to signal

    # ── RSI filter ────────────────────────────────────────────────────────────
    rsi4 = _rsi(c4, 14)
    cur_rsi = float(rsi4.iloc[-2])

    entry = float(c4.iloc[-1])
    sl_lookback = 10

    signals = []

    if macro == "BULL" and bull_cross and 45 <= cur_rsi <= 65:
        sl  = round(float(df_4h["low"].astype(float).iloc[-sl_lookback:-1].min()) * 0.999, 4)
        tp  = round(entry + (entry - sl) * rr, 4)
        qs  = 7 + (1 if 50 <= cur_rsi <= 60 else 0)
        signals.append({
            "signal":        "BUY",
            "entry":         round(entry, 4),
            "sl":            sl,
            "tp":            tp,
            "rr":            rr,
            "timeframe":     "4H",
            "quality_score": qs,
            "raw_score":     qs,
            "strategy_tag":  tag,
            "confluences":   [
                "Daily bull regime (50>200 EMA)",
                "4H MACD bull crossover",
                f"RSI {cur_rsi:.0f} in acceleration zone",
            ],
            "setup": "DualTF Momentum — Bull Cross",
        })

    elif macro == "BEAR" and bear_cross and 35 <= cur_rsi <= 55:
        sl  = round(float(df_4h["high"].astype(float).iloc[-sl_lookback:-1].max()) * 1.001, 4)
        tp  = round(entry - (sl - entry) * rr, 4)
        qs  = 7 + (1 if 40 <= cur_rsi <= 50 else 0)
        signals.append({
            "signal":        "SELL",
            "entry":         round(entry, 4),
            "sl":            sl,
            "tp":            tp,
            "rr":            rr,
            "timeframe":     "4H",
            "quality_score": qs,
            "raw_score":     qs,
            "strategy_tag":  tag,
            "confluences":   [
                "Daily bear regime (50<200 EMA)",
                "4H MACD bear crossover",
                f"RSI {cur_rsi:.0f} in exhaustion zone",
            ],
            "setup": "DualTF Momentum — Bear Cross",
        })

    return signals


# ─────────────────────────────────────────────────────────────────────────────
# 4. H4 Break-and-Retest — Gold only, fetches MT5/yfinance 4H + Daily
# ─────────────────────────────────────────────────────────────────────────────

def generate_h4_break_retest_signal(
    rr:                  float = 2.5,
    swing_lookback:      int   = 20,
    min_body_atr:        float = 0.3,
    zone_tolerance:      float = 0.5,
    retest_timeout_days: int   = 15,
    sl_breathing:        float = 0.5,
) -> list:
    """
    Live adapter for H4 Break-and-Retest (Gold).

    Replays the last ~40 H4 bars through the same state machine as the backtest
    to find any active retest zone, then checks the latest completed H4 bar for
    PA confirmation (Pin Bar / Doji / Engulfing).

    Data: MT5 Gold history (preferred) → yfinance GC=F fallback.
    """
    from app.strategies.gold_advanced_strategies import (
        _atr as _g_atr, _ema as _g_ema, _pin_bar, _doji, _engulfing, _fetch,
    )

    df4  = _fetch("4h", 60)
    df1d = _fetch("1d", 300)

    if df4 is None or df4.empty or df1d is None or df1d.empty:
        print("[H4 BnR Live] No data — skipping")
        return []
    if len(df4) < swing_lookback + 5 or len(df1d) < 210:
        return []

    # ── Daily: EMA regime + ATR ───────────────────────────────────────────────
    df1d = df1d.copy()
    df1d["ema50"]  = _g_ema(df1d["close"], 50)
    df1d["ema200"] = _g_ema(df1d["close"], 200)
    df1d["atr_d"]  = _g_atr(df1d, 14)
    df1d["_date"]  = pd.to_datetime(df1d["timestamp"]).dt.date

    daily_trend: dict = {}
    daily_atr:   dict = {}
    for _, r in df1d.iterrows():
        d = r["_date"]
        if pd.isna(r["ema50"]) or pd.isna(r["ema200"]):
            daily_trend[d] = "neutral"
        else:
            daily_trend[d] = "bull" if r["ema50"] > r["ema200"] else "bear"
        daily_atr[d] = float(r["atr_d"]) if not pd.isna(r["atr_d"]) else 30.0

    # ── H4 ATR ────────────────────────────────────────────────────────────────
    df4 = df4.copy()
    df4["atr"]   = _g_atr(df4, 14)
    df4["_date"] = pd.to_datetime(df4["timestamp"]).dt.date

    # ── Replay state machine over last ~40 bars to find active zone ───────────
    start = max(swing_lookback + 1, len(df4) - 40)
    state      = "scanning"
    zone_hi    = zone_lo = zone_dir = breakout_dt = None

    for i in range(start, len(df4) - 1):   # skip the live open bar
        row  = df4.iloc[i]
        d    = row["_date"]
        atr4 = float(row["atr"]) if not pd.isna(row["atr"]) else 5.0
        trend = daily_trend.get(d, "neutral")

        if state == "scanning":
            if trend == "neutral":
                continue
            window = df4.iloc[max(0, i - swing_lookback):i]
            sh_val = float(window["high"].max())
            sl_val = float(window["low"].min())
            body   = abs(float(row["close"]) - float(row["open"]))

            if trend == "bull" and float(row["close"]) > sh_val:
                if body >= min_body_atr * atr4:
                    zone_hi     = sh_val + atr4 * 0.15
                    zone_lo     = sh_val - atr4 * zone_tolerance
                    zone_dir    = "bull"
                    breakout_dt = d
                    state       = "waiting_retest"

            elif trend == "bear" and float(row["close"]) < sl_val:
                if body >= min_body_atr * atr4:
                    zone_hi     = sl_val + atr4 * zone_tolerance
                    zone_lo     = sl_val - atr4 * 0.15
                    zone_dir    = "bear"
                    breakout_dt = d
                    state       = "waiting_retest"

        elif state == "waiting_retest":
            if breakout_dt and (d - breakout_dt).days > retest_timeout_days:
                state = "scanning"
                zone_hi = zone_lo = zone_dir = breakout_dt = None
                continue

            in_zone = (zone_lo is not None and zone_hi is not None and
                       (zone_lo <= float(row["low"]) <= zone_hi or
                        zone_lo <= float(row["close"]) <= zone_hi))

            if in_zone:
                confirmed = False
                prev_row = df4.iloc[i - 1]
                if zone_dir == "bull":
                    confirmed = _pin_bar(row, "bull") or _doji(row) or _engulfing(prev_row, row, "bull")
                else:
                    confirmed = _pin_bar(row, "bear") or _doji(row) or _engulfing(prev_row, row, "bear")
                if confirmed:
                    # Trade fired historically — reset and keep scanning
                    state = "scanning"
                    zone_hi = zone_lo = zone_dir = breakout_dt = None

    # ── Check current state ───────────────────────────────────────────────────
    if state != "waiting_retest" or zone_dir is None:
        print("[H4 BnR Live] No active retest zone")
        return []

    # Evaluate the most recent completed H4 bar (index −2)
    i    = len(df4) - 2
    row  = df4.iloc[i]
    prev = df4.iloc[i - 1]
    d    = row["_date"]
    atr4 = float(row["atr"]) if not pd.isna(row["atr"]) else 5.0
    d_atr = daily_atr.get(d, atr4 * 5)

    if breakout_dt and (d - breakout_dt).days > retest_timeout_days:
        print("[H4 BnR Live] Retest zone timed out")
        return []

    in_zone = (zone_lo <= float(row["low"]) <= zone_hi or
               zone_lo <= float(row["close"]) <= zone_hi)
    if not in_zone:
        print(f"[H4 BnR Live] Price not in retest zone ({zone_lo:.2f}–{zone_hi:.2f})")
        return []

    if zone_dir == "bull":
        confirmed = _pin_bar(row, "bull") or _doji(row) or _engulfing(prev, row, "bull")
    else:
        confirmed = _pin_bar(row, "bear") or _doji(row) or _engulfing(prev, row, "bear")

    if not confirmed:
        print("[H4 BnR Live] In zone but no PA confirmation yet")
        return []

    entry = float(row["close"])

    if zone_dir == "bull":
        sl      = round(float(row["low"]) - sl_breathing * d_atr, 2)
        sl_dist = entry - sl
        if sl_dist <= 0:
            return []
        tp  = round(entry + sl_dist * rr, 2)
        sig = "BUY"
        pa  = "Pin Bar" if _pin_bar(row, "bull") else ("Doji" if _doji(row) else "Engulfing")
        confluences = [
            "Daily 50>200 EMA bull regime",
            f"H4 swing-high breakout, retest zone {zone_lo:.0f}–{zone_hi:.0f}",
            f"PA confirmation: {pa}",
        ]
    else:
        sl      = round(float(row["high"]) + sl_breathing * d_atr, 2)
        sl_dist = sl - entry
        if sl_dist <= 0:
            return []
        tp  = round(entry - sl_dist * rr, 2)
        sig = "SELL"
        pa  = "Pin Bar" if _pin_bar(row, "bear") else ("Doji" if _doji(row) else "Engulfing")
        confluences = [
            "Daily 50<200 EMA bear regime",
            f"H4 swing-low breakout, retest zone {zone_lo:.0f}–{zone_hi:.0f}",
            f"PA confirmation: {pa}",
        ]

    print(f"[H4 BnR Live] {sig} @ {entry:.2f}  SL {sl:.2f}  TP {tp:.2f}")
    return [{
        "signal":        sig,
        "entry":         round(entry, 2),
        "sl":            sl,
        "tp":            tp,
        "rr":            rr,
        "timeframe":     "4H",
        "quality_score": 8,
        "raw_score":     8,
        "strategy_tag":  "h4_break_retest",
        "confluences":   confluences,
        "setup":         f"H4 Break-and-Retest ({zone_dir.upper()})",
    }]
