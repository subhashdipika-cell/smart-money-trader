"""
smc_swing_strategy.py
---------------------
ICT / Smart Money Concepts Multi-Timeframe Swing Strategy for BTC & ETH.

Framework
---------
  HTF (4H) — Map swing highs/lows (liquidity pools) and detect liquidity sweeps
              (Judas swings): price wicks beyond a resting level but closes back inside.
  LTF (1H) — After a confirmed HTF sweep, scan for:
                1. A Fair Value Gap (FVG) — 3-candle imbalance pattern
                2. A Market Structure Shift (MSS) — aggressive close breaking the
                   most recent 10-bar high (bull) or low (bear) with a prior FVG

Entry
-----
  Bullish:  4H low wicks below SSL → 1H MSS + bullish FVG → enter at MSS close
  Bearish:  4H high wicks above BSL → 1H MSS + bearish FVG → enter at MSS close

Stop Loss
---------
  Placed 0.03% beyond the extreme wick of the sweep area (min/max of 12 LTF
  candles surrounding the sweep). Tight LTF stop + HTF target = high RR.

Take Profit  (partial exits)
----------------------------
  TP1 → 50% position at 1:2 RR; SL moves to breakeven.
  TP2 → remaining 50% at rr_ratio (default 3.0×).
  Effective average RR ≈ (2 + rr_ratio) / 2  → ~2.5× at default settings.

Assets:   BTC, ETH  (data via yfinance; no MT5 required)
Best fit: 60–90 day windows. Weekend noise reduces signal quality.
"""

import time
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# ── Ticker mapping ─────────────────────────────────────────────────────────────

TICKER_MAP = {
    "BTCUSD":  "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "BTC":     "BTC-USD",
    "ETHUSD":  "ETH-USD",
    "ETHUSDT": "ETH-USD",
    "ETH":     "ETH-USD",
}


# ── Data helpers ───────────────────────────────────────────────────────────────

def _fetch_1h(symbol: str, days: int) -> pd.DataFrame:
    """Fetch 1H OHLCV from yfinance and normalise columns."""
    ticker = TICKER_MAP.get(symbol.upper().replace("-", ""), "BTC-USD")
    try:
        df = yf.download(
            ticker,
            period=f"{min(days + 10, 729)}d",
            interval="1h",
            auto_adjust=True,
            progress=False,
        )
    except Exception as e:
        print(f"[SMC] yfinance error: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance sometimes returns MultiIndex columns — flatten them
    df.columns = [
        c[0].lower() if isinstance(c, tuple) else c.lower()
        for c in df.columns
    ]

    df = df.rename_axis("timestamp").reset_index()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Trim to requested window
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

    keep = ["timestamp", "open", "high", "low", "close", "volume"]
    return df[[c for c in keep if c in df.columns]].copy()


def _resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H DataFrame to 4H OHLCV."""
    df = df_1h.set_index("timestamp")
    df_4h = df.resample("4h").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna()
    return df_4h.reset_index()


# ── HTF: swing levels + sweep detection ──────────────────────────────────────

def _add_swing_levels(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Fractal swing high/low detection (window bars each side).
    Safe for backtesting historical data. Forward-fills resting liquidity levels.
    """
    df = df.copy()
    n = len(df)
    sh = [np.nan] * n
    sl = [np.nan] * n

    for i in range(window, n - window):
        h_win = df["high"].iloc[i - window: i + window + 1]
        l_win = df["low"].iloc[i - window: i + window + 1]
        if df["high"].iloc[i] == float(h_win.max()):
            sh[i] = float(df["high"].iloc[i])
        if df["low"].iloc[i] == float(l_win.min()):
            sl[i] = float(df["low"].iloc[i])

    df["swing_high"] = sh
    df["swing_low"]  = sl
    # Forward-fill: these become resting liquidity pools.
    # shift(window): a fractal swing needs `window` future bars to confirm, so
    # the level only becomes tradeable AFTER confirmation — no lookahead.
    df["resting_bsl"] = pd.Series(sh, index=df.index).ffill().shift(window)   # Buy-Side Liquidity
    df["resting_ssl"] = pd.Series(sl, index=df.index).ffill().shift(window)   # Sell-Side Liquidity
    return df


def _detect_sweeps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detect liquidity sweeps: wick pierces resting level, candle closes back inside.
    Bullish sweep = sweep of Sell-Side Liquidity (stop-hunt of longs → then reversal).
    Bearish sweep = sweep of Buy-Side Liquidity  (stop-hunt of shorts → then reversal).
    """
    df = df.copy()
    n = len(df)
    bull  = [False] * n
    bear  = [False] * n
    level = [np.nan] * n

    for i in range(1, n):
        ssl = df["resting_ssl"].iloc[i - 1]
        bsl = df["resting_bsl"].iloc[i - 1]

        if pd.notna(ssl) and float(df["low"].iloc[i]) < ssl and float(df["close"].iloc[i]) > ssl:
            bull[i]  = True
            level[i] = float(ssl)

        if pd.notna(bsl) and float(df["high"].iloc[i]) > bsl and float(df["close"].iloc[i]) < bsl:
            bear[i]  = True
            level[i] = float(bsl)

    df["sweep_bull"]  = bull
    df["sweep_bear"]  = bear
    df["sweep_level"] = level
    return df


# ── LTF: MSS + FVG setup scan ─────────────────────────────────────────────────

def _scan_ltf_for_setup(
    ltf_df:    pd.DataFrame,
    sweep_ts:  pd.Timestamp,
    direction: str,           # "bull" or "bear"
    scan_bars: int = 48,      # max 1H candles to wait after sweep (~2 trading days)
):
    """
    After an HTF sweep at sweep_ts, scan LTF for the first MSS + FVG confluence.
    Returns a setup dict or None.
    """
    after = ltf_df.index[ltf_df["timestamp"] > sweep_ts].tolist()
    if not after:
        return None

    start = int(after[0])
    end   = min(start + scan_bars, len(ltf_df) - 1)

    active_fvg = None  # dict with keys bottom, top; or None

    for i in range(max(start, 2), end):
        c  = ltf_df.iloc[i]
        p1 = ltf_df.iloc[i - 1]
        p2 = ltf_df.iloc[i - 2]

        c_hi  = float(c["high"])
        c_lo  = float(c["low"])
        c_cl  = float(c["close"])
        p1_hi = float(p1["high"])
        p1_lo = float(p1["low"])
        p1_op = float(p1["open"])
        p1_cl = float(p1["close"])
        p2_hi = float(p2["high"])
        p2_lo = float(p2["low"])

        if direction == "bull":
            # ── Bullish FVG: gap between p2.high and c.low (displacement in middle) ──
            if p2_hi < c_lo and p1_cl > p1_op:
                active_fvg = {"bottom": p2_hi, "top": c_lo}

            # ── Bullish MSS: aggressive close above recent 10-bar high ──
            if active_fvg is not None:
                window = ltf_df.iloc[max(0, i - 10): i]
                if window.empty:
                    continue
                recent_high = float(window["high"].max())
                if c_cl > recent_high:
                    # Compute SL: min low of candles from sweep start to now
                    sl_window = ltf_df.iloc[max(0, start - 4): i + 1]["low"]
                    sl = float(sl_window.min()) * 0.9997  # 0.03% buffer
                    risk = c_cl - sl
                    if risk > 0:
                        return {
                            "direction": "BUY",
                            "entry":     round(c_cl, 2),
                            "sl":        round(sl, 2),
                            "risk":      round(risk, 2),
                            "fvg":       active_fvg,
                            "entry_idx": i,
                            "entry_ts":  c["timestamp"],
                        }
                    active_fvg = None  # Invalid risk → reset

        else:  # bear
            # ── Bearish FVG: gap between p2.low and c.high ──
            if p2_lo > c_hi and p1_cl < p1_op:
                active_fvg = {"bottom": c_hi, "top": p2_lo}

            # ── Bearish MSS: aggressive close below recent 10-bar low ──
            if active_fvg is not None:
                window = ltf_df.iloc[max(0, i - 10): i]
                if window.empty:
                    continue
                recent_low = float(window["low"].min())
                if c_cl < recent_low:
                    sl_window = ltf_df.iloc[max(0, start - 4): i + 1]["high"]
                    sl = float(sl_window.max()) * 1.0003
                    risk = sl - c_cl
                    if risk > 0:
                        return {
                            "direction": "SELL",
                            "entry":     round(c_cl, 2),
                            "sl":        round(sl, 2),
                            "risk":      round(risk, 2),
                            "fvg":       active_fvg,
                            "entry_idx": i,
                            "entry_ts":  c["timestamp"],
                        }
                    active_fvg = None

    return None


# ── Trade simulation ───────────────────────────────────────────────────────────

def _simulate_trade(
    ltf_df:   pd.DataFrame,
    setup:    dict,
    rr_ratio: float,
    max_bars: int = 240,      # max 10 days on 1H
):
    """
    Simulate the trade from the bar AFTER entry.
    Partial TP logic:
      - TP1 at 1:2 RR → take 50%, move SL to breakeven
      - TP2 at rr_ratio → take remaining 50%
    Returns trade dict or None if data runs out before any resolution.
    """
    entry     = setup["entry"]
    sl_init   = setup["sl"]
    risk      = setup["risk"]
    direction = setup["direction"]
    start_i   = setup["entry_idx"] + 1

    tp1 = (entry + risk * 2.0)      if direction == "BUY"  else (entry - risk * 2.0)
    tp2 = (entry + risk * rr_ratio) if direction == "BUY"  else (entry - risk * rr_ratio)

    sl       = sl_init
    tp1_hit  = False
    end_i    = min(start_i + max_bars, len(ltf_df))

    for i in range(start_i, end_i):
        c  = ltf_df.iloc[i]
        hi = float(c["high"])
        lo = float(c["low"])
        ts = c["timestamp"]

        if direction == "BUY":
            # SL check takes priority (worst-case candle body behaviour)
            if lo <= sl:
                if tp1_hit:
                    # 50% banked at 2R; 50% exits at breakeven (sl=entry)
                    avg_pts = risk * 2.0 * 0.5 + 0.0 * 0.5
                    return _make_trade(setup, entry, sl_init, tp2, avg_pts, "WIN", ts, rr_ratio)
                pts = sl - entry   # negative
                return _make_trade(setup, entry, sl_init, tp2, pts, "LOSS", ts, rr_ratio)

            if not tp1_hit and hi >= tp1:
                tp1_hit = True
                sl = entry   # move to breakeven

            if tp1_hit and hi >= tp2:
                avg_pts = risk * 2.0 * 0.5 + risk * rr_ratio * 0.5
                return _make_trade(setup, entry, sl_init, tp2, avg_pts, "WIN", ts, rr_ratio)

        else:  # SELL
            if hi >= sl:
                if tp1_hit:
                    avg_pts = risk * 2.0 * 0.5 + 0.0 * 0.5
                    return _make_trade(setup, entry, sl_init, tp2, avg_pts, "WIN", ts, rr_ratio)
                pts = entry - sl   # negative
                return _make_trade(setup, entry, sl_init, tp2, pts, "LOSS", ts, rr_ratio)

            if not tp1_hit and lo <= tp1:
                tp1_hit = True
                sl = entry

            if tp1_hit and lo <= tp2:
                avg_pts = risk * 2.0 * 0.5 + risk * rr_ratio * 0.5
                return _make_trade(setup, entry, sl_init, tp2, avg_pts, "WIN", ts, rr_ratio)

    # Expired / still open at end of data
    if start_i >= len(ltf_df):
        return None
    last   = ltf_df.iloc[min(end_i - 1, len(ltf_df) - 1)]
    last_c = float(last["close"])
    raw_pts = (last_c - entry) if direction == "BUY" else (entry - last_c)
    avg_pts = (risk * 2.0 * 0.5 + raw_pts * 0.5) if tp1_hit else raw_pts
    return _make_trade(setup, entry, sl_init, tp2, avg_pts, "OPEN",
                       last["timestamp"], rr_ratio)


def _fmt_ts(ts) -> str:
    if ts is None:
        return "—"
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        return t.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "—"


def _make_trade(setup: dict, entry: float, sl: float, tp: float,
                pts: float, outcome: str, exit_ts, rr_ratio: float) -> dict:
    return {
        "signal":        setup["direction"],
        "entry":         round(entry, 2),
        "sl":            round(sl, 2),
        "tp":            round(tp, 2),
        "outcome":       outcome,
        "points":        round(pts, 2),
        "date":          _fmt_ts(setup["entry_ts"]),
        "exit_date":     _fmt_ts(exit_ts),
        "strategy":      "SMC_Swing",
        "quality_score": 8,
        "rr_ratio":      rr_ratio,
    }


# ── Main backtest entry point ─────────────────────────────────────────────────

def run_smc_swing_backtest(
    symbol:       str   = "BTCUSD",
    days:         int   = 90,
    rr_ratio:     float = 3.0,
    swing_window: int   = 5,
) -> dict:
    """
    Full SMC Liquidity Sweep backtest for BTC or ETH.
    Returns SMT-compatible result dict with summary, trades, and day_breakdown.

    Parameters
    ----------
    symbol       : "BTCUSD" | "BTCUSDT" | "ETHUSD" | "ETHUSDT"
    days         : lookback window (30–180 recommended)
    rr_ratio     : Take-Profit 2 target in multiples of risk (default 3.0)
    swing_window : bars each side for fractal swing confirmation (default 5)
    """
    t0 = time.time()

    # ── 1. Fetch data ──────────────────────────────────────────────────────────
    ltf_df = _fetch_1h(symbol, days)
    if ltf_df.empty or len(ltf_df) < 60:
        return {"error": f"Not enough 1H data for {symbol} ({len(ltf_df)} bars). "
                         "Try fewer days or check your internet connection."}

    htf_df = _resample_4h(ltf_df)
    if len(htf_df) < 20:
        return {"error": "Not enough 4H data to run SMC backtest."}

    # ── 2. Process HTF: swing levels + sweeps ─────────────────────────────────
    htf_df = _add_swing_levels(htf_df, window=swing_window)
    htf_df = _detect_sweeps(htf_df)

    sweep_rows = htf_df[htf_df["sweep_bull"] | htf_df["sweep_bear"]]
    print(f"[SMC] {symbol} {days}d — HTF sweeps detected: {len(sweep_rows)}")

    # ── 3. For each HTF sweep → scan LTF for MSS + FVG → simulate trade ───────
    trades    = []
    used_idxs: set[int] = set()  # prevent overlapping LTF trade regions

    for _, row in sweep_rows.iterrows():
        direction = "bull" if row["sweep_bull"] else "bear"
        sweep_ts  = row["timestamp"]

        setup = _scan_ltf_for_setup(ltf_df, sweep_ts, direction, scan_bars=48)
        if setup is None:
            continue

        eidx = setup["entry_idx"]

        # Skip if another trade was entered nearby (within 12 LTF candles)
        if any(abs(eidx - u) < 12 for u in used_idxs):
            continue

        trade = _simulate_trade(ltf_df, setup, rr_ratio, max_bars=240)
        if trade is None:
            continue

        trades.append(trade)
        used_idxs.add(eidx)

    # ── 4. Summary ─────────────────────────────────────────────────────────────
    wins   = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    open_t = [t for t in trades if t["outcome"] == "OPEN"]
    total  = len(wins) + len(losses)

    wr          = round(len(wins) / total * 100, 1) if total else 0.0
    net         = round(sum(t["points"] for t in trades), 2)
    gross_win   = sum(t["points"] for t in wins)
    gross_loss  = abs(sum(t["points"] for t in losses))
    pf          = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0
    avg_win     = round(gross_win  / len(wins),   2) if wins   else 0.0
    avg_loss_v  = round(-gross_loss / len(losses), 2) if losses else 0.0

    # Max drawdown
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        cum += t["points"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    expectancy = round(
        (wr / 100 * avg_win) + ((1 - wr / 100) * avg_loss_v), 2
    ) if total else 0.0

    # Day breakdown
    day_map: dict[str, dict] = {}
    for t in trades:
        d = t["date"][:10] if t["date"] != "—" else "unknown"
        if d not in day_map:
            day_map[d] = {"date": d, "trades": 0, "wins": 0, "pnl": 0.0}
        day_map[d]["trades"] += 1
        if t["outcome"] == "WIN":
            day_map[d]["wins"] += 1
        day_map[d]["pnl"] = round(day_map[d]["pnl"] + t["points"], 2)

    day_breakdown = sorted(day_map.values(), key=lambda x: x["date"])
    elapsed       = round(time.time() - t0, 2)

    print(f"[SMC] {symbol} done — {len(trades)} trades, WR={wr}%, PF={pf}, "
          f"net={net:.2f} pts, {elapsed}s")

    return {
        "strategy":      "SMC_Swing",
        "strategy_id":   "smc_swing",
        "symbol":        symbol.upper(),
        "days":          days,
        "ran_at":        datetime.now(timezone.utc).isoformat(),
        "elapsed_s":     elapsed,
        "params": {
            "rr_ratio":      rr_ratio,
            "swing_window":  swing_window,
            "partial_tp":    True,
            "tp1_rr":        2.0,
            "tp2_rr":        rr_ratio,
        },
        "summary": {
            "total":          len(trades),
            "wins":           len(wins),
            "losses":         len(losses),
            "open":           len(open_t),
            "win_rate":       wr,
            "net_points":     net,
            "profit_factor":  pf,
            "avg_win":        avg_win,
            "avg_loss":       avg_loss_v,
            "max_drawdown":   round(max_dd, 2),
            "best_trade":     round(max((t["points"] for t in trades), default=0), 2),
            "worst_trade":    round(min((t["points"] for t in trades), default=0), 2),
            "expectancy":     expectancy,
        },
        "trades":        trades,
        "day_breakdown": day_breakdown,
    }
