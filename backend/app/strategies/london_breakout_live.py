"""
london_breakout_live.py
-----------------------
Live signal generator for the London Session Breakout strategy (Gold / XAUUSD).

How it works
------------
  1. Asian Session Range (00:00–08:00 GMT):
       Scan today's 15m candles before 08:00 GMT → record the HIGH and LOW.
       This is the "Asian box" — a zone of resting liquidity.

  2. London Breakout Window (08:00–11:00 GMT):
       Watch for the FIRST 15m candle that closes decisively outside the Asian box.
         BUY  — close > asian_high AND volume > 5-bar MA
         SELL — close < asian_low  AND volume > 5-bar MA

  3. Entry / SL / TP:
       Entry = close of the breakout candle
       SL    = entry − atr_mult × ATR(14)  [BUY]
             = entry + atr_mult × ATR(14)  [SELL]
       TP    = entry + rr_ratio × risk
       (default: atr_mult = 1.5, rr_ratio = 2.0)

  4. Quality:
       quality_score = 7 base
       +1 if breakout size > 0.5 × ATR  (strong candle)
       +1 if it's the first 30 min of London  (prime window)

  5. One signal per day — the caller's duplicate guard handles this.

Constraints
-----------
  - Only fires between 08:00 and 11:00 GMT.
  - Only fires if Asian box was cleanly formed (≥ 8 candles before 08:00).
  - NEVER calls mt5.login() or mt5.shutdown().
"""

from datetime import datetime, timezone


# ── ATR helper ─────────────────────────────────────────────────────────────────

def _atr14(df, period: int = 14) -> float:
    """Wilder-smoothed ATR on a 15m DataFrame (last `period` candles)."""
    import pandas as pd
    if len(df) < period + 1:
        return 0.0
    h  = df["high"].astype(float)
    l  = df["low"].astype(float)
    c  = df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


# ── Volume filter ──────────────────────────────────────────────────────────────

def _vol_ok(df, idx: int, lookback: int = 5) -> bool:
    """True if volume at `idx` > mean of previous `lookback` bars."""
    if idx < lookback:
        return False  # insufficient history — skip rather than admit unconfirmed breakout
    vol_now  = float(df["volume"].iloc[idx])
    vol_mean = float(df["volume"].iloc[idx - lookback: idx].mean())
    return vol_mean > 0 and vol_now > vol_mean


# ── Main live signal function ──────────────────────────────────────────────────

def generate_london_breakout_signal(
    data:       dict,                # multi-timeframe dict {"15m": df, "1h": df, ...}
    symbol:     str   = "XAUUSD",
    rr_ratio:   float = 2.0,
    atr_mult:   float = 1.5,
) -> list:
    """
    Examine the most recent 15m candles for a London Session Breakout signal.
    Called once per engine tick (~every 15 minutes).

    Returns a list with 0 or 1 signal dicts in the SMT live format.
    """
    df_15m = data.get("15m")
    if df_15m is None or df_15m.empty or len(df_15m) < 20:
        print(f"[LondonBreakout] No 15m data for {symbol}")
        return []

    df = df_15m.copy().reset_index(drop=True)

    # ── Resolve timestamps ──────────────────────────────────────────────────────
    # gold_service stores timestamps in milliseconds (epoch ms)
    try:
        import pandas as pd
        if "timestamp" not in df.columns:
            # Try index
            df = df.rename_axis("timestamp").reset_index()
        ts_col = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    except Exception:
        try:
            ts_col = pd.to_datetime(df["timestamp"], utc=True)
            if ts_col.dt.tz is None:
                ts_col = ts_col.dt.tz_localize("UTC")
        except Exception as e:
            print(f"[LondonBreakout] Timestamp parse error: {e}")
            return []

    df["_dt"]   = ts_col
    df["_hour"] = ts_col.dt.hour
    df["_min"]  = ts_col.dt.minute

    # ── Check current UTC time ──────────────────────────────────────────────────
    now_utc  = datetime.now(timezone.utc)
    h_now    = now_utc.hour
    m_now    = now_utc.minute

    # Only run during London window: 08:00–11:00 GMT
    # Add small buffer: allow up to 11:15 so the 11:00 candle isn't missed
    london_open  = 8
    london_close = 11
    if not (london_open <= h_now < london_close + 1):
        print(f"[LondonBreakout] Outside London window (UTC {h_now:02d}:{m_now:02d}) — skipping")
        return []

    # ── Build Asian box from today's candles before 08:00 ──────────────────────
    today_str   = now_utc.strftime("%Y-%m-%d")
    asian_mask  = (
        df["_dt"].dt.strftime("%Y-%m-%d") == today_str
    ) & (df["_hour"] < london_open)

    asian_df = df[asian_mask]
    if len(asian_df) < 8:
        print(f"[LondonBreakout] Asian box too thin ({len(asian_df)} candles) — skipping")
        return []

    asian_high = float(asian_df["high"].max())
    asian_low  = float(asian_df["low"].min())
    box_size   = asian_high - asian_low

    if box_size <= 0:
        return []

    print(f"[LondonBreakout] Asian box: {asian_low:.2f} – {asian_high:.2f} "
          f"(range = {box_size:.2f} pts)")

    # ── ATR on last 14 candles preceding London open ──────────────────────────
    pre_london = df[
        (df["_dt"].dt.strftime("%Y-%m-%d") == today_str) & (df["_hour"] < london_open)
    ].tail(20)
    atr_val = _atr14(pre_london)
    if atr_val <= 0:
        atr_val = box_size * 0.3   # fallback: 30% of box

    # ── Scan London candles for breakout ───────────────────────────────────────
    london_mask = (
        df["_dt"].dt.strftime("%Y-%m-%d") == today_str
    ) & (df["_hour"] >= london_open) & (df["_hour"] < london_close + 1)

    london_df = df[london_mask].reset_index(drop=True)
    if london_df.empty:
        print("[LondonBreakout] No London candles yet")
        return []

    # Look at the most recent completed candle (index -1 is live / forming)
    # We check the last CLOSED candle = second-to-last if the last is still forming
    # Safe approach: check all London candles; signals are de-duped by the caller
    for idx in range(len(london_df)):
        candle  = london_df.iloc[idx]
        c_close = float(candle["close"])
        c_open  = float(candle["open"])
        c_high  = float(candle["high"])
        c_low   = float(candle["low"])
        c_hour  = int(candle["_hour"])
        c_min   = int(candle["_min"])

        # Global index into df for volume check
        global_idx = df.index[
            (df["_hour"] == c_hour) &
            (df["_min"]  == c_min)  &
            (df["_dt"].dt.strftime("%Y-%m-%d") == today_str)
        ]
        g_idx = int(global_idx[0]) if len(global_idx) > 0 else idx

        # ── Bullish breakout: close > Asian high ─────────────────────────────
        if c_close > asian_high:
            breakout_size = c_close - asian_high
            if not _vol_ok(df, g_idx):
                print(f"[LondonBreakout] BUY candidate at {c_close:.2f} — volume filter failed")
                continue

            sl     = round(c_close - atr_mult * atr_val, 2)
            risk   = c_close - sl
            if risk <= 0:
                continue
            tp     = round(c_close + rr_ratio * risk, 2)

            # Quality score
            score  = 7
            if breakout_size > 0.5 * atr_val:
                score += 1  # strong breakout candle
            if c_hour == london_open and c_min < 30:
                score += 1  # prime London window (first 30 min)

            print(f"[LondonBreakout] ✅ BUY signal | entry={c_close} SL={sl} TP={tp} "
                  f"score={score} | box={asian_low:.1f}–{asian_high:.1f}")

            return [_make_signal(
                direction="BUY",
                entry=c_close,
                sl=sl,
                tp=tp,
                rr=rr_ratio,
                score=score,
                session_hour=c_hour,
                asian_high=asian_high,
                asian_low=asian_low,
                atr=atr_val,
            )]

        # ── Bearish breakout: close < Asian low ──────────────────────────────
        elif c_close < asian_low:
            breakout_size = asian_low - c_close
            if not _vol_ok(df, g_idx):
                print(f"[LondonBreakout] SELL candidate at {c_close:.2f} — volume filter failed")
                continue

            sl     = round(c_close + atr_mult * atr_val, 2)
            risk   = sl - c_close
            if risk <= 0:
                continue
            tp     = round(c_close - rr_ratio * risk, 2)

            score  = 7
            if breakout_size > 0.5 * atr_val:
                score += 1
            if c_hour == london_open and c_min < 30:
                score += 1

            print(f"[LondonBreakout] ✅ SELL signal | entry={c_close} SL={sl} TP={tp} "
                  f"score={score} | box={asian_low:.1f}–{asian_high:.1f}")

            return [_make_signal(
                direction="SELL",
                entry=c_close,
                sl=sl,
                tp=tp,
                rr=rr_ratio,
                score=score,
                session_hour=c_hour,
                asian_high=asian_high,
                asian_low=asian_low,
                atr=atr_val,
            )]

    print(f"[LondonBreakout] No breakout candle found in London window yet")
    return []


# ── Signal dict builder ────────────────────────────────────────────────────────

def _make_signal(
    direction:    str,
    entry:        float,
    sl:           float,
    tp:           float,
    rr:           float,
    score:        int,
    session_hour: int,
    asian_high:   float,
    asian_low:    float,
    atr:          float,
) -> dict:
    confidence = "High" if score >= 7 else "Medium"
    return {
        "signal":        direction,
        "entry":         round(entry, 2),
        "sl":            round(sl, 2),
        "tp":            round(tp, 2),
        "rr":            rr,
        "timeframe":     "Intraday",
        "session":       "London",
        "quality_score": score,
        "raw_score":     score,
        "confidence":    confidence,
        "setup":         "London Session Breakout",
        "confluences":   [
            f"Asian range: {asian_low:.1f}–{asian_high:.1f}",
            f"ATR(14): {atr:.2f}",
            f"London open hour: {session_hour:02d}:xx GMT",
        ],
        "strategy_tag":  "london_breakout",
        # No trailing stop for this strategy — fixed SL from ATR
        "trailing_sl": {
            "enabled":     False,
            "activate_at": tp,
            "step":        0.0,
            "current_sl":  round(sl, 2),
            "activated":   False,
        },
        "ema_exit": {
            "enabled": False,
        },
    }
