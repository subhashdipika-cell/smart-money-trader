"""
strategy_tuner.py
─────────────────
The "tweak the strategies" half of the learning system.

For a given strategy it backtests a GRID of parameter values (e.g. RR ratios),
ranks them by real profitability, saves the findings to learned_weights.json,
and can APPLY the winning value so the live signal engine starts using it.

Used by:
  POST /learning/optimize       → run_tuning(strategy_id, symbol, days)
  POST /learning/apply-tuning   → apply_tuning(strategy_id, symbol)
  GET  /learning/insights       → get_tuning_state()

Applied values land in learned_weights.json under "applied_params", keyed by
strategy tag. htf_signal_generator reads them for the live HTF ICT strategy.
"""

import json
import os
from datetime import datetime, timezone

_BASE = os.path.join(os.path.dirname(__file__), "..", "..")
LEARNED_WEIGHTS_FILE = os.path.abspath(os.path.join(_BASE, "learned_weights.json"))


# ── Weights I/O ───────────────────────────────────────────────────────────────

def _load_weights():
    try:
        with open(LEARNED_WEIGHTS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_weights(w):
    with open(LEARNED_WEIGHTS_FILE, "w") as f:
        json.dump(w, f, indent=2)


# ── Tunable-strategy registry ─────────────────────────────────────────────────
# param  = the knob being tuned
# values = grid to test
# tag    = strategy_tag used by the live engine / History page

TUNABLE = {
    "htf_ict_intraday": {"param": "rr_ratio", "values": [2.0, 2.5, 3.0, 4.0],
                         "tag": "HTF_ICT_Intraday", "symbols": ["BTCUSDT", "ETHUSDT"],
                         "label": "HTF ICT Intraday (live strategy)"},
    "choch_scalp":      {"param": "rr",       "values": [1.5, 2.0, 2.5, 3.0],
                         "tag": "ICT_Scalping", "symbols": ["BTCUSDT", "ETHUSDT"],
                         "label": "SMC CHoCH Scalp"},
    "choch_intraday":   {"param": "rr",       "values": [1.5, 2.0, 2.5, 3.0],
                         "tag": "CHoCH_Intraday", "symbols": ["BTCUSDT", "ETHUSDT"],
                         "label": "SMC CHoCH Intraday"},
    "choch_swing":      {"param": "rr",       "values": [2.0, 3.0, 4.0],
                         "tag": "CHoCH_Swing", "symbols": ["BTCUSDT", "ETHUSDT"],
                         "label": "SMC CHoCH Swing"},
    "atr_trailing":     {"param": "atr_mult", "values": [2.0, 2.5, 3.0, 3.5],
                         "tag": "ATR_Trailing", "symbols": ["BTCUSDT", "ETHUSDT"],
                         "label": "ATR Chandelier Trailing"},
    "momentum_scalp":   {"param": "rr",       "values": [1.0, 1.5, 2.0, 2.5],
                         "tag": "BB_RSI_Scalper", "symbols": ["BTCUSDT", "ETHUSDT"],
                         "label": "M1 Momentum Scalper"},
    "smc_swing":        {"param": "rr_ratio", "values": [2.5, 3.0, 4.0],
                         "tag": "SMC_Swing", "symbols": ["BTCUSD", "ETHUSD"],
                         "label": "SMC Liquidity Sweep"},
    "btc_momentum":     {"param": "rsi_high", "values": [55.0, 60.0, 65.0, 70.0],
                         "tag": "BTC_Momentum", "symbols": ["BTCUSDT"],
                         "label": "Dual-TF Momentum BTC"},
    "eth_momentum":     {"param": "rsi_high", "values": [55.0, 60.0, 65.0, 70.0],
                         "tag": "ETH_Momentum", "symbols": ["ETHUSD"],
                         "label": "Dual-TF Momentum ETH"},
    "gold_frvp_liquidity_trap": {"param": "rr_ratio", "values": [1.5, 2.0, 3.0],
                         "tag": "gold_frvp_liquidity_trap", "symbols": ["XAUUSD"],
                         "label": "FRVP Liquidity Trap (Gold)"},
    "london_breakout":  {"param": "rr_ratio", "values": [1.5, 2.0, 3.0],
                         "tag": "london_breakout", "symbols": ["XAUUSD"],
                         "label": "London Breakout (Gold)"},
    "m5_mean_reversion": {"param": "rr_ratio", "values": [1.5, 2.0, 3.0],
                         "tag": "m5_mean_reversion", "symbols": ["XAUUSD"],
                         "label": "M5 Mean Reversion (Gold)"},
    "h4_break_retest":  {"param": "rr_ratio", "values": [1.5, 2.0, 3.0],
                         "tag": "h4_break_retest", "symbols": ["XAUUSD"],
                         "label": "H4 Break-Retest (Gold)"},
}


# ── Metric normalisation ──────────────────────────────────────────────────────

def _metrics_from_summary(s, trades_list=None):
    """Normalise the different summary shapes into one metrics dict."""
    trades = s.get("total_trades", s.get("total", 0)) or 0
    net    = s.get("net_pnl", s.get("net_points", s.get("net_pips", 0))) or 0
    pf     = s.get("profit_factor")
    if pf is None and trades_list:
        pl = [float(t.get("pnl", t.get("points", t.get("pnl_pips", 0))) or 0) for t in trades_list]
        gw = sum(v for v in pl if v > 0)
        gl = abs(sum(v for v in pl if v < 0))
        pf = round(gw / gl, 2) if gl > 0 else (999.0 if gw > 0 else 0)
    return {
        "trades":        int(trades),
        "wins":          int(s.get("wins", 0) or 0),
        "losses":        int(s.get("losses", 0) or 0),
        "win_rate":      float(s.get("win_rate", 0) or 0),
        "net":           round(float(net), 2),
        "profit_factor": round(float(pf), 2) if pf is not None else None,
    }


def _metrics_from_trades(trades_list, pl_key="points"):
    pl     = [float(t.get(pl_key, 0) or 0) for t in trades_list]
    wins   = [v for v in pl if v > 0]
    losses = [v for v in pl if v < 0]
    total  = len(wins) + len(losses)
    gl     = abs(sum(losses))
    return {
        "trades":        len(trades_list),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / total * 100, 1) if total else 0,
        "net":           round(sum(pl), 2),
        "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else (999.0 if wins else 0),
    }


# ── Per-family runners ────────────────────────────────────────────────────────

def _run_one(strategy_id, symbol, days, value, _cache):
    """Run ONE backtest with the given parameter value. Returns metrics dict."""
    from app.services.binance_service import get_historical_multi_timeframe_data as bhist

    if strategy_id == "htf_ict_intraday":
        import bisect
        from app.strategies.mtf_analysis import analyze_multi_timeframe
        from app.backtests.simple_backtest import run_backtest as _rb
        if "data" not in _cache:
            _cache["data"] = bhist(symbol, days, intervals=["1m", "1h"])
        data = _cache["data"]
        df   = data.get("1m")
        sigs = analyze_multi_timeframe(data, symbol=symbol, scan_all=True, rr_ratio=value)
        if df is not None and not df.empty and "timestamp" in df.columns:
            ts_1m = [int(t) for t in df["timestamp"].tolist()]
            for s in sigs:
                if s.get("timestamp"):
                    s["index"] = bisect.bisect_left(ts_1m, int(s["timestamp"]) + 3_600_000) - 1
        results = _rb(df, sigs)
        return _metrics_from_trades(
            [r for r in results if r["outcome"] in ("WIN", "LOSS")], "points")

    if strategy_id in ("choch_scalp", "choch_intraday", "choch_swing"):
        from app.strategies.choch_backtest import (
            run_choch_backtest, summarise, _resample_h4, _resample_d1)
        style = {"choch_scalp": "Scalping", "choch_intraday": "Intraday",
                 "choch_swing": "Swing"}[strategy_id]
        if "data" not in _cache:
            _cache["data"] = bhist(symbol, days + 5,
                intervals=(["1h"] if style == "Swing" else ["15m", "1h"]))
        data = _cache["data"]
        if style == "Swing":
            df_exec, df_htf = data.get("1h"), _resample_d1(data.get("1h"))
        else:
            df_exec, df_htf = data.get("15m"), _resample_h4(data.get("1h"))
        trades = run_choch_backtest(df_exec, df_htf, rr=value, style=style)
        s, _ = summarise(trades, value)
        return _metrics_from_summary(s, trades)

    if strategy_id == "atr_trailing":
        from app.strategies.atr_trailing_strategy import run_atr_trailing_backtest, summarise_atr
        if "data" not in _cache:
            _cache["data"] = bhist(symbol, days, intervals=["1h"])
        trades = run_atr_trailing_backtest(_cache["data"].get("1h"), symbol=symbol, atr_mult=value)
        return _metrics_from_summary(summarise_atr(trades, symbol, days, value), trades)

    if strategy_id == "momentum_scalp":
        from app.strategies.momentum_scalper import run_scalper_backtest
        if "data" not in _cache:
            _cache["data"] = bhist(symbol, min(days, 14), intervals=["1m"])
        trades = run_scalper_backtest(_cache["data"].get("1m"), symbol=symbol, rr=value)
        return _metrics_from_trades(trades, "points")

    if strategy_id == "smc_swing":
        from app.strategies.smc_swing_strategy import run_smc_swing_backtest
        r = run_smc_swing_backtest(symbol=symbol, days=days, rr_ratio=value)
        if "error" in r:
            raise RuntimeError(r["error"])
        return _metrics_from_summary(r.get("summary", {}), r.get("trades"))

    if strategy_id in ("btc_momentum", "eth_momentum"):
        from app.strategies.eth_momentum_strategy import run_eth_momentum_backtest
        r = run_eth_momentum_backtest(symbol=symbol, days=max(days, 180), rsi_high=value)
        if "error" in r:
            raise RuntimeError(r["error"])
        return _metrics_from_summary(r.get("summary", {}), r.get("trades"))

    if strategy_id == "gold_frvp_liquidity_trap":
        from app.strategies.gold_frvp_strategy import run_backtest as frvp
        r = frvp(days=days, sl_distance=8.0, rr_ratio=value)
        if "error" in r:
            raise RuntimeError(r["error"])
        return _metrics_from_summary(r.get("summary", {}), r.get("trades"))

    if strategy_id in ("london_breakout", "m5_mean_reversion", "h4_break_retest"):
        from app.strategies.gold_advanced_strategies import (
            run_london_breakout_backtest, run_m5_mean_reversion_backtest,
            run_h4_break_retest_backtest)
        fn = {"london_breakout": run_london_breakout_backtest,
              "m5_mean_reversion": run_m5_mean_reversion_backtest,
              "h4_break_retest": run_h4_break_retest_backtest}[strategy_id]
        r = fn(days=(180 if strategy_id == "h4_break_retest" else days), rr_ratio=value)
        if "error" in r:
            raise RuntimeError(r["error"])
        return _metrics_from_summary(r.get("summary", {}), r.get("trades"))

    raise ValueError(f"Strategy '{strategy_id}' is not tunable")


# ── Public API ────────────────────────────────────────────────────────────────

MIN_TRADES_FOR_CONFIDENCE = 4


def run_tuning(strategy_id, symbol=None, days=60):
    """
    Backtest the strategy across its parameter grid, rank by net profitability
    (profit factor as tiebreak), and save the findings.
    """
    cfg = TUNABLE.get(strategy_id)
    if cfg is None:
        return {"error": f"Unknown/untunable strategy: {strategy_id}. "
                         f"Tunable: {sorted(TUNABLE)}"}
    symbol = symbol or cfg["symbols"][0]

    results, cache = [], {}
    for value in cfg["values"]:
        try:
            m = _run_one(strategy_id, symbol, days, value, cache)
            m["value"] = value
            results.append(m)
            print(f"[Tuner] {strategy_id} {symbol} {cfg['param']}={value} → "
                  f"{m['trades']} trades, WR {m['win_rate']}%, net {m['net']}")
        except Exception as e:
            results.append({"value": value, "error": str(e)})
            print(f"[Tuner] {strategy_id} {cfg['param']}={value} failed: {e}")

    valid = [r for r in results if "error" not in r]
    if not valid:
        return {"error": "All grid runs failed", "results": results}

    confident = [r for r in valid if r["trades"] >= MIN_TRADES_FOR_CONFIDENCE]
    pool      = confident or valid
    best      = sorted(pool, key=lambda r: (r["net"], r.get("profit_factor") or 0),
                       reverse=True)[0]

    entry = {
        "strategy_id": strategy_id,
        "symbol":      symbol,
        "param":       cfg["param"],
        "days":        days,
        "results":     results,
        "best":        best,
        "low_sample":  best["trades"] < MIN_TRADES_FOR_CONFIDENCE,
        "ran_at":      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    w = _load_weights()
    w.setdefault("strategy_tuning", {})[f"{strategy_id}|{symbol}"] = entry
    _save_weights(w)
    return entry


def apply_tuning(strategy_id, symbol=None):
    """Make the live engine use the best value found by run_tuning."""
    cfg = TUNABLE.get(strategy_id)
    if cfg is None:
        return {"error": f"Unknown strategy: {strategy_id}"}
    symbol = symbol or cfg["symbols"][0]

    w      = _load_weights()
    entry  = (w.get("strategy_tuning") or {}).get(f"{strategy_id}|{symbol}")
    if not entry or "best" not in entry:
        return {"error": "Run the optimizer first — no tuning result saved."}

    applied = {
        "strategy_id": strategy_id,
        "symbol":      symbol,
        "param":       cfg["param"],
        "value":       entry["best"]["value"],
        "expected":    {k: entry["best"].get(k) for k in
                        ("trades", "win_rate", "net", "profit_factor")},
        "applied_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    w.setdefault("applied_params", {})[cfg["tag"]] = applied
    _save_weights(w)
    print(f"[Tuner] ✅ Applied {cfg['param']}={applied['value']} to {cfg['tag']}")
    return {"success": True, "applied": applied}


def remove_tuning(strategy_id):
    """Revert a strategy to its built-in default parameters."""
    cfg = TUNABLE.get(strategy_id)
    if cfg is None:
        return {"error": f"Unknown strategy: {strategy_id}"}
    w = _load_weights()
    removed = (w.get("applied_params") or {}).pop(cfg["tag"], None)
    _save_weights(w)
    return {"success": True, "removed": removed}


def get_tuning_state():
    w = _load_weights()
    return {
        "tunable":  {k: {"param": v["param"], "values": v["values"],
                         "symbols": v["symbols"], "label": v["label"], "tag": v["tag"]}
                     for k, v in TUNABLE.items()},
        "tuning":   w.get("strategy_tuning", {}),
        "applied":  w.get("applied_params", {}),
    }
