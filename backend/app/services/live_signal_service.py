"""
live_signal_service.py
----------------------
Every 15 minutes:
  1. Fetch fresh sentiment
  2. Check BTC, ETH, GOLD for quality signals
  3. Block duplicates using BOTH persistent state file AND log inspection
  4. Resolve open signal outcomes
  5. Run learning engine
"""

import os
import json
import time
from datetime import datetime

from app.services.binance_service    import get_multi_timeframe_data as binance_get_mtf
from app.services.binance_service    import get_recent_candles_df
from app.services.gold_service       import get_multi_timeframe_data as gold_get_mtf
from app.strategies.mtf_analysis     import analyze_multi_timeframe
from app.services.telegram_service   import send_alert
from app.services.strategy_learner   import run_learning, load_weights
from app.services.sessions           import session_from_ts
from app.services.sentiment_service  import get_sentiment
from app.services.geo_strategy       import compute_geo_bias
from app.services.web_researcher     import run_research, get_research_bias
from app.services.chart_observer     import observe_chart
from app.services.signal_optimizer   import optimize_parameters
from app.services.events_calendar    import (
    fetch_and_store_today_events, is_high_impact_window,
    add_headline_event, load_today_events
)
from app.services.trader_brain       import think
from app.services.self_improvement   import generate_suggestions
from app.services.trading_journal    import add_journal_entry, check_daily_loss_cap, get_quadrant_stats
from app.services.strategy_selector  import run_monthly_selection, is_last_day_of_month, analyse_strategies

# MT4 executor — import safely (won't break if MetaTrader5 not installed)
try:
    from trading_executor import execute_signal as _mt4_execute, get_mode as _mt4_mode
    _MT4_AVAILABLE = True
except Exception:
    _MT4_AVAILABLE = False

GOLD_SYMBOLS = {"XAUUSD"}

def get_multi_timeframe_data(symbol):
    """Route to correct data source based on symbol."""
    if symbol in GOLD_SYMBOLS:
        return gold_get_mtf(symbol)
    return binance_get_mtf(symbol)

# All symbols the engine knows about (full list).
# At runtime, get_active_symbols() filters this down to user-enabled assets.
_ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSD"]

# Map UI asset name → signal engine symbol
_ASSET_TO_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "Gold": "XAUUSD"}
_SYMBOL_TO_ASSET = {v: k for k, v in _ASSET_TO_SYMBOL.items()}

_ASSET_CONFIG_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "asset_signals_config.json")
)

def _load_asset_config() -> dict:
    """Returns dict like {"BTC": true, "ETH": true, "Gold": false}."""
    try:
        with open(_ASSET_CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        # Default: BTC and ETH on, Gold on (MT5 live feed now available)
        return {"BTC": True, "ETH": True, "Gold": True}

def _save_asset_config(cfg: dict):
    with open(_ASSET_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def toggle_asset(asset: str) -> dict:
    """Toggle signal generation for 'BTC', 'ETH', or 'Gold'. Returns new config."""
    cfg = _load_asset_config()
    cfg[asset] = not cfg.get(asset, True)
    _save_asset_config(cfg)
    return cfg

def get_asset_config() -> dict:
    return _load_asset_config()

def get_active_symbols() -> list:
    """Returns the symbol list filtered by which assets are enabled."""
    cfg = _load_asset_config()
    return [sym for sym in _ALL_SYMBOLS if cfg.get(_SYMBOL_TO_ASSET.get(sym, ""), True)]

SYMBOLS = _ALL_SYMBOLS  # kept for any external imports; runtime uses get_active_symbols()

# ── Per-asset strategy assignment ────────────────────────────────────────────

_ASSET_STRATEGY_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "asset_strategy_config.json")
)

# Strategies available for LIVE signal generation (not just backtest)
LIVE_STRATEGIES = {
    "HTF_ICT_Intraday": {
        "label":   "HTF ICT Intraday",
        "desc":    "1H FVG + 20 EMA. Entry at FVG top/bottom. TP = 3R.",
        "assets":  ["BTC", "ETH", "Gold"],
        "live":    True,
    },
    "EMA20_Pullback": {
        "label":   "EMA 20 Pullback",
        "desc":    "Price pulls back to rising/falling 20 EMA with rejection candle.",
        "assets":  ["BTC", "ETH", "Gold"],
        "live":    True,
    },
    "gold_frvp_liquidity_trap": {
        "label":   "Gold FRVP Trap",
        "desc":    "Fixed Range Volume Profile liquidity traps above VAH / below VAL.",
        "assets":  ["Gold"],
        "live":    False,   # backtest only — no live signal adapter yet
    },
    "CHoCH_Scalp": {
        "label":   "CHoCH Scalp",
        "desc":    "M15 Change of Character with H4 equilibrium filter. 1:2 RR.",
        "assets":  ["BTC", "ETH"],
        "live":    False,
    },
    "ATR_Trailing": {
        "label":   "ATR Trailing",
        "desc":    "ATR-based trailing stop on 1H trend. Rides moves until ATR reversal.",
        "assets":  ["BTC", "ETH", "Gold"],
        "live":    True,   # ✅ live signal adapter active
    },
    # ── Gold-only advanced strategies (backtest + deploy via Gold page) ───────
    "london_breakout": {
        "label":   "London Session Breakout",
        "desc":    "Asian box high/low breakout during London open (08:00–11:00 GMT). Volume filter + ATR SL. Max 1 trade/day.",
        "assets":  ["Gold"],
        "live":    True,   # ✅ live signal generator active
    },
    "m5_mean_reversion": {
        "label":   "M5 Mean Reversion (RSI+BB)",
        "desc":    "RSI 80/20 extreme + Bollinger Band spike reversal during NY session. Counter-trend entry on RSI crossback.",
        "assets":  ["Gold"],
        "live":    False,
    },
    "h4_break_retest": {
        "label":   "H4 Break-and-Retest",
        "desc":    "Daily 50/200 EMA trend filter → H4 structural breakout → pullback → PA reversal entry. Swing strategy.",
        "assets":  ["Gold"],
        "live":    True,   # ✅ live signal adapter active
    },
    "gold_m5_pullback": {
        "label":   "Gold M5 Pullback Scalp",
        "desc":    "M5 trend-aligned pullback to EMA9 (EMA9>21, close>EMA50), ATR-scaled SL 1.2× / TP 1.8×. Backtest PF~1.7, ~5 setups/day. Forward-testing on demo.",
        "assets":  ["Gold"],
        "live":    True,   # ✅ live signal adapter active
    },
    # ── SMC / ICT Swing (BTC & ETH) ──────────────────────────────────────────
    "smc_swing": {
        "label":   "SMC Liquidity Sweep",
        "desc":    "ICT/SMC: HTF (4H) sweep of liquidity pools → LTF (1H) MSS + FVG entry. Partial TP at 1:2 then target RR. High-probability swing for BTC and ETH.",
        "assets":  ["BTC", "ETH"],
        "live":    True,   # ✅ live signal adapter active
    },
    "eth_momentum": {
        "label":   "ETH Dual-TF Momentum",
        "desc":    "Daily 50/200 EMA macro filter + 4H MACD crossover entry. RSI 45–65 exhaustion guard. Trailing exit on MACD cross-back. ETH only.",
        "assets":  ["ETH"],
        "live":    True,   # ✅ live signal adapter active
    },
    "btc_momentum": {
        "label":   "BTC Dual-TF Momentum",
        "desc":    "Same dual-timeframe momentum strategy as ETH applied to Bitcoin. Daily 50/200 EMA macro filter + 4H MACD crossover. RSI 45–65 acceleration zone guard. Trailing exit on MACD cross-back.",
        "assets":  ["BTC"],
        "live":    True,   # ✅ live signal adapter active
    },
}

def _load_asset_strategy_config() -> dict:
    """
    Returns dict like:
      {"BTC": ["HTF_ICT_Intraday", "EMA20_Pullback"], "ETH": ["HTF_ICT_Intraday"], "Gold": ["HTF_ICT_Intraday"]}
    Handles legacy single-string format by upgrading it to a list automatically.
    """
    try:
        with open(_ASSET_STRATEGY_FILE) as f:
            raw = json.load(f)
        # Upgrade any legacy single-string values to lists
        upgraded = {}
        changed  = False
        for asset, val in raw.items():
            if isinstance(val, str):
                upgraded[asset] = [val]
                changed = True
            else:
                upgraded[asset] = val
        if changed:
            _save_asset_strategy_config(upgraded)
        return upgraded
    except Exception:
        return {"BTC": ["HTF_ICT_Intraday"], "ETH": ["HTF_ICT_Intraday"], "Gold": ["HTF_ICT_Intraday"]}

def _save_asset_strategy_config(cfg: dict):
    with open(_ASSET_STRATEGY_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def toggle_asset_strategy(asset: str, strategy_id: str) -> dict:
    """
    Add strategy_id to the asset's list if not present, remove it if already present.
    Always keeps at least one strategy per asset (won't remove the last one).
    Returns the updated config.
    """
    cfg  = _load_asset_strategy_config()
    curr = cfg.get(asset, ["HTF_ICT_Intraday"])
    if strategy_id in curr:
        if len(curr) > 1:           # only remove if not the last one
            curr = [s for s in curr if s != strategy_id]
        # else: silently ignore — must keep at least one
    else:
        curr = curr + [strategy_id]
    cfg[asset] = curr
    _save_asset_strategy_config(cfg)
    return cfg

def get_asset_strategy_config() -> dict:
    return _load_asset_strategy_config()

def get_strategy_catalogue() -> dict:
    """Return all known strategies with metadata (built-in + custom)."""
    catalogue = dict(LIVE_STRATEGIES)
    try:
        from app.strategies.strategy_builder_engine import load_registry
        for s in load_registry().get("strategies", []):
            catalogue[s["id"]] = {
                "label":  s.get("name", s["id"]),
                "desc":   f"Custom strategy ({s.get('timeframe','?')} · "
                          f"{len(s.get('conditions', []))} condition(s))",
                "assets": [s.get("asset", "Gold")],
                "live":   True,
                "custom": True,
            }
    except Exception as exc:
        print(f"[Catalogue] custom strategies unavailable: {exc}")
    return catalogue

def _run_one_strategy(strategy_id: str, symbol: str, data: dict) -> list:
    """Run a single strategy and return its signals (tagged with strategy_id)."""

    # ── Custom strategies from the Strategy Builder ──────────────────────────
    if strategy_id.startswith("custom_"):
        try:
            from app.strategies.strategy_builder_engine import (
                load_custom_strategy, generate_live_signals,
            )
            definition = load_custom_strategy(strategy_id)
            if not definition:
                print(f"[{symbol}] custom strategy '{strategy_id}' not found — skipping")
                return []
            sigs = generate_live_signals(definition, data, symbol)
            print(f"[{symbol}] {definition.get('name', strategy_id)}: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] custom strategy '{strategy_id}' error: {exc}")
            return []

    strat_info = LIVE_STRATEGIES.get(strategy_id, {})

    if not strat_info.get("live", True):
        print(f"[{symbol}] '{strategy_id}' is backtest-only — skipping for live signals")
        return []

    if strategy_id == "EMA20_Pullback":
        try:
            from app.strategies.ema_strategy import generate_ema_signals
            htf_df = data.get("1h")
            if htf_df is None or htf_df.empty:
                return []
            sigs = generate_ema_signals(htf_df, symbol=symbol)
            for s in sigs:
                s["strategy_tag"] = "EMA20_Pullback"
            print(f"[{symbol}] EMA20 Pullback: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] EMA20 error: {exc}")
            return []

    if strategy_id == "london_breakout":
        try:
            from app.strategies.london_breakout_live import generate_london_breakout_signal
            sigs = generate_london_breakout_signal(data, symbol=symbol)
            print(f"[{symbol}] London Breakout: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] London Breakout live error: {exc}")
            return []

    if strategy_id == "ATR_Trailing":
        try:
            from app.strategies.live_adapters import generate_atr_trailing_signal
            sigs = generate_atr_trailing_signal(data, symbol=symbol)
            print(f"[{symbol}] ATR Trailing: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] ATR Trailing live error: {exc}")
            return []

    if strategy_id == "smc_swing":
        try:
            from app.strategies.live_adapters import generate_smc_swing_signal
            sigs = generate_smc_swing_signal(symbol=symbol)
            print(f"[{symbol}] SMC Swing: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] SMC Swing live error: {exc}")
            return []

    if strategy_id in ("eth_momentum", "btc_momentum"):
        try:
            from app.strategies.live_adapters import generate_momentum_signal
            sigs = generate_momentum_signal(symbol=symbol)
            print(f"[{symbol}] Momentum: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] Momentum live error: {exc}")
            return []

    if strategy_id == "h4_break_retest":
        try:
            from app.strategies.live_adapters import generate_h4_break_retest_signal
            sigs = generate_h4_break_retest_signal()
            print(f"[{symbol}] H4 Break-and-Retest: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] H4 Break-and-Retest live error: {exc}")
            return []

    if strategy_id == "gold_m5_pullback":
        try:
            from app.strategies.live_adapters import generate_gold_m5_pullback_signal
            sigs = generate_gold_m5_pullback_signal(data, symbol=symbol)
            print(f"[{symbol}] Gold M5 Pullback Scalp: {len(sigs)} signal(s)")
            return sigs
        except Exception as exc:
            print(f"[{symbol}] Gold M5 Pullback live error: {exc}")
            return []

    # Default: HTF_ICT_Intraday
    return _ict_signals(symbol, data)


def _get_signals_for_symbol(symbol: str, data: dict) -> list:
    """
    Run all strategies assigned to this asset, merge their signals.
    The existing quality filter + brain + direction-conflict logic
    in check_symbol will pick the best one from the combined pool.
    """
    asset      = _SYMBOL_TO_ASSET.get(symbol, "BTC")
    cfg        = _load_asset_strategy_config()
    strategies = cfg.get(asset, ["HTF_ICT_Intraday"])

    labels = [LIVE_STRATEGIES.get(s, {}).get("label", s) for s in strategies]
    print(f"[{symbol}] Running {len(strategies)} strategy/strategies: {', '.join(labels)}")

    all_signals: list = []

    # Does this asset have at least one live-capable strategy assigned?
    has_live_assigned = any(
        LIVE_STRATEGIES.get(s, {}).get("live", False) or s.startswith("custom_")
        for s in strategies
    )

    for strat_id in strategies:
        sigs = _run_one_strategy(strat_id, symbol, data)
        if sigs:
            all_signals.extend(sigs)

    # ICT fallback ONLY when no live-capable strategy is assigned at all.
    # "My strategies found nothing this cycle" is a valid outcome — we no longer
    # override the user's selection with fallback signals.
    if not has_live_assigned:
        print(f"[{symbol}] No live-capable strategies assigned — running ICT fallback")
        all_signals = _ict_signals(symbol, data)

    return all_signals


def _ict_signals(symbol: str, data: dict) -> list:
    """Run the default HTF ICT Intraday (FVG+EMA) strategy."""
    from app.strategies.mtf_analysis import analyze_multi_timeframe
    return analyze_multi_timeframe(data, symbol=symbol)

# ── Cooldown settings ────────────────────────────────────────────────────────
COOLDOWN_BY_STYLE = {
    "Scalping":  45 * 60,    # 45 minutes
    "Intraday":  90 * 60,    # 90 minutes
    "Swing":     4 * 60 * 60   # 4 hours (reduced from 12h)
}
COOLDOWN_SECONDS = 90 * 60  # default fallback

# ── Session windows (UTC) ─────────────────────────────────────────────────────
# First 30 minutes of each session = high opportunity window
SESSIONS = {
    "Asia":    {"open": (0,  0),  "close": (9,  0)},   # 05:30-14:30 IST
    "London":  {"open": (7,  0),  "close": (16, 0)},   # 12:30-21:30 IST
    "New York":{"open": (13, 0),  "close": (22, 0)},   # 18:30-03:30 IST
    "Overlap": {"open": (13, 0),  "close": (16, 0)},   # 18:30-21:30 IST
}
SESSION_OPEN_WINDOW = 30  # minutes after session open = high priority window

def _get_current_session_info():
    """Returns (session_name, is_session_open, minutes_since_open)"""
    now_utc = datetime.utcnow()
    h, m    = now_utc.hour, now_utc.minute
    total_m = h * 60 + m

    for name, times in SESSIONS.items():
        open_m  = times["open"][0]  * 60 + times["open"][1]
        close_m = times["close"][0] * 60 + times["close"][1]
        if open_m <= total_m < close_m:
            mins_since_open = total_m - open_m
            is_opening = mins_since_open <= SESSION_OPEN_WINDOW
            return name, True, mins_since_open, is_opening
    return "Off-session", False, 0, False

def _get_cooldown_seconds(timeframe, conviction_score=0, is_session_opening=False):
    """
    Dynamic cooldown based on:
    - Trade style (scalping vs intraday vs swing)
    - Signal quality (high conviction = shorter cooldown)
    - Session timing (session opens get shorter cooldown)
    """
    base = COOLDOWN_BY_STYLE.get(timeframe, COOLDOWN_SECONDS)

    # High conviction signal — reduce cooldown by 30%
    if conviction_score >= 8:
        base = int(base * 0.5)   # halve cooldown for very strong signals
    elif conviction_score >= 7:
        base = int(base * 0.7)

    # Session opening — reduce by 25% to catch session momentum
    if is_session_opening:
        base = int(base * 0.75)

    return base
CHECK_INTERVAL   = 3 * 60         # check every 3 minutes
MIN_RR           = 2.0

# Daily signal limits — set high to allow learning engine to collect data
# Brain conviction + quality filters are the primary gatekeepers
DAILY_LIMITS = {
    "Scalping":  20,
    "Intraday":  10,
    "Swing":     5
}

# Exchange name per symbol (shown in Telegram)
SYMBOL_EXCHANGE = {
    "BTCUSDT":  "Binance",
    "ETHUSDT":  "Binance",
    "XAUUSD":   "Vantage (XAU/USD)"
}

_BASE      = os.path.join(os.path.dirname(__file__), "..", "..")
LOG_FILE   = os.path.abspath(os.path.join(_BASE, "signals_log.json"))
STATE_FILE        = os.path.abspath(os.path.join(_BASE, "signal_state.json"))
DAILY_COUNT_FILE  = os.path.abspath(os.path.join(_BASE, "daily_signal_counts.json"))


# ── Daily signal counter ──────────────────────────────────────────────────────

def _load_daily_counts():
    try:
        with open(DAILY_COUNT_FILE, "r") as f:
            data = json.load(f)
        # Reset if it's a new day (UTC date)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if data.get("date") != today:
            return {"date": today, "Scalping": 0, "Intraday": 0, "Swing": 0}
        return data
    except Exception:
        return {"date": datetime.utcnow().strftime("%Y-%m-%d"), "Scalping": 0, "Intraday": 0, "Swing": 0}


def _save_daily_counts(counts):
    with open(DAILY_COUNT_FILE, "w") as f:
        json.dump(counts, f, indent=2)


def _daily_limit_reached(timeframe):
    counts = _load_daily_counts()
    limit  = DAILY_LIMITS.get(timeframe, 99)
    return counts.get(timeframe, 0) >= limit


def _increment_daily_count(timeframe):
    counts = _load_daily_counts()
    counts[timeframe] = counts.get(timeframe, 0) + 1
    _save_daily_counts(counts)
    remaining = DAILY_LIMITS.get(timeframe, 99) - counts[timeframe]
    print(f"[Counter] {timeframe}: {counts[timeframe]}/{DAILY_LIMITS.get(timeframe, '?')} today ({remaining} remaining)")


def _daily_counts_summary():
    counts = _load_daily_counts()
    parts  = []
    for tf, limit in DAILY_LIMITS.items():
        parts.append(f"{tf}: {counts.get(tf, 0)}/{limit}")
    return " | ".join(parts)


# ── Persistent state ──────────────────────────────────────────────────────────
# Survives backend restarts so cooldowns are never forgotten.

def _load_state():
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            # Validate state entries have required fields
            valid = {}
            for k, v in data.items():
                if isinstance(v, dict) and "last_signal_time" in v:
                    valid[k] = v
            return valid
    except Exception:
        return {}


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_symbol_state(state, symbol):
    return state.get(symbol, {"last_signal_id": None, "last_signal_time": 0})


def _update_symbol_state(state, symbol, signal_id, ts):
    state[symbol] = {"last_signal_id": signal_id, "last_signal_time": ts}
    _save_state(state)


# ── Log helpers ───────────────────────────────────────────────────────────────

def load_log():
    try:
        with open(LOG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_log(entries):
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def append_to_log(signal_entry):
    log = load_log()
    log.insert(0, signal_entry)
    save_log(log)


# ── Duplicate guard ───────────────────────────────────────────────────────────

def _already_in_log(symbol, signal_type, entry_price, cooldown_secs=None):
    """
    Duplicate guard — checks TWO conditions:
    1. Same symbol + direction + entry within cooldown window
    2. Same symbol + direction + entry within same calendar day (IST)
       regardless of cooldown — prevents restart-bypass duplicates
    """
    if cooldown_secs is None:
        cooldown_secs = COOLDOWN_SECONDS
    log    = load_log()
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(cooldown_secs * 1000)

    # Today's date in IST
    from datetime import timezone, timedelta
    today_ist = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")

    for s in log:
        if s.get("symbol")  != symbol:  continue
        if s.get("signal")  != signal_type: continue
        if s.get("outcome") == "EXPIRED": continue  # expired signals don't block

        prev_entry = float(s.get("entry", 0))
        ts         = int(s.get("timestamp", 0))
        if ts < 1e12: ts = ts * 1000

        # Price tolerance per asset
        if prev_entry > 30000:   tol = 400.0   # BTC
        elif prev_entry > 3000:  tol = 10.0    # Gold
        else:                    tol = 30.0    # ETH

        same_setup = abs(prev_entry - entry_price) < tol

        # Check 1: within cooldown window
        if ts >= cutoff and same_setup:
            return True

        # Check 2: same day in IST (prevents restart bypass)
        try:
            sig_day = (datetime.utcfromtimestamp(ts/1000)
                       + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            if sig_day == today_ist and same_setup:
                return True
        except Exception:
            pass

    return False


# ── Points calculator ─────────────────────────────────────────────────────────

def calculate_points(signal_type, entry, sl, tp, outcome):
    try:
        entry = float(entry); sl = float(sl); tp = float(tp)
    except (ValueError, TypeError):
        return None
    if outcome == "WIN":  return round(abs(tp - entry), 4)
    if outcome == "LOSS": return round(-abs(sl - entry), 4)
    return None


# ── Outcome resolver ──────────────────────────────────────────────────────────

MAX_SIGNAL_AGE_MS = 24 * 60 * 60 * 1000  # expire signals after 24 hours

def _realized_usd(symbol: str, points):
    """$ result at the configured trading lot (same conversion as backtests)."""
    try:
        from app.strategies.strategy_builder_engine import get_configured_lot, CONTRACT_SIZES
        asset = _SYMBOL_TO_ASSET.get(symbol)
        if asset is None or points is None:
            return None
        return round(float(points) * get_configured_lot(asset)
                     * CONTRACT_SIZES.get(asset, 1.0), 2)
    except Exception:
        return None


def resolve_open_outcomes():
    log     = load_log()
    changed = False
    now_ms  = int(time.time() * 1000)

    # Backfill realized_usd on already-resolved signals (runs once per signal)
    for s in log:
        if (s.get("outcome") in ("WIN", "LOSS") and s.get("points") is not None
                and s.get("realized_usd") is None):
            usd = _realized_usd(s.get("symbol"), s.get("points"))
            if usd is not None:
                s["realized_usd"] = usd
                changed = True

    # ── Smart expiry rules (retuned 2026-07-05 from a 1000-bar H1 backtest) ──
    # The old 2h pending / 8h hard limits were killing the strategies: 88% of
    # signals EXPIRED unfilled (a 1H limit-retrace setup got only 2 candles to
    # fill), and filled trades still working toward a 3R target were zeroed at
    # 8h — slow WINNERS were recorded as EXPIRED while fast SL hits logged as
    # losses (that asymmetry produced the 1W/28L record). Backtest: extending
    # the fill window to 24h turned BTC FVG from -3R into +5R, ETH into +13.7R.
    #   1. Pending (entry never hit): expire after 24 hours.
    #   2. Early expiry only when price truly ran away (>=1.5% past entry, >4h)
    #      AND the walk has never seen the entry touched (entry_hit persisted).
    #   3. Filled trades: resolved by the candle-walk (SL/TP/trail); 48h safety.

    MAX_AGE         = 48 * 60 * 60 * 1000  # filled-trade safety net
    MAX_PENDING_AGE = 24 * 60 * 60 * 1000  # awaiting entry
    RUNAWAY_AGE     = 4 * 60 * 60 * 1000   # min age for the run-away early expiry
    RUNAWAY_PCT     = 0.015                # price this far past entry = missed

    # Get current prices for smart expiry (one fetch per symbol)
    live_prices_for_expiry = {}
    open_syms = {s.get("symbol") for s in log if s.get("outcome") == "OPEN"}
    for sym in open_syms:
        try:
            if sym not in GOLD_SYMBOLS:
                from app.services.binance_service import get_recent_candles_df as _grc
                _df = _grc(symbol=sym, interval="1m", limit=1)
                if not _df.empty:
                    live_prices_for_expiry[sym] = float(_df["close"].iloc[-1])
        except Exception:
            pass

    for s in log:
        if s.get("outcome") != "OPEN":
            continue
        ts = s.get("timestamp", 0)
        try:
            ts_ms = int(ts) if int(ts) > 1e12 else int(ts) * 1000
        except Exception:
            continue
        age_ms      = now_ms - ts_ms
        entry       = float(s.get("entry", 0))
        sig_type    = s.get("signal", "BUY")
        symbol      = s.get("symbol", "")
        live_price  = live_prices_for_expiry.get(symbol)

        # entry_hit is persisted by the candle-walk below the first time it sees
        # price touch the limit entry — a FILLED trade must never be expired as
        # "pending" or "missed" (that's how slow winners were being erased).
        was_filled = bool(s.get("entry_hit"))

        # Rule 1: age limits — 24h for un-filled setups, 48h safety for filled.
        if age_ms > (MAX_AGE if was_filled else MAX_PENDING_AGE):
            s["outcome"] = "EXPIRED"
            s["points"]  = 0
            changed = True
            print(f"[Resolver] {symbol} @ {entry} expired "
                  f"({'48h filled-trade safety' if was_filled else '24h pending limit'})")
            continue

        # Rule 2: early expiry when the setup truly ran away without filling —
        # price >=1.5% beyond entry after 4h and the walk never saw a touch.
        if not was_filled and age_ms > RUNAWAY_AGE and live_price:
            entry_distance_pct = abs(live_price - entry) / entry
            entry_missed = (
                (sig_type == "BUY"  and live_price > entry * (1 + RUNAWAY_PCT)) or
                (sig_type == "SELL" and live_price < entry * (1 - RUNAWAY_PCT))
            )
            if entry_missed:
                s["outcome"] = "EXPIRED"
                s["points"]  = 0
                changed = True
                print(f"[Resolver] {symbol} @ {entry} expired (entry missed — price moved {entry_distance_pct:.1%} away)")
                continue

        # Rule 3 (removed): price moving 1.5%+ against the signal means price went
        # THROUGH the limit entry — the trade was filled and is a real WIN/LOSS.
        # Expiring it here recorded losses as "EXPIRED" (0 pts) and skewed the
        # win rate + learning stats. The candle-walk resolver below now decides.

    open_signals = [s for s in log if s.get("outcome") == "OPEN"]
    if not open_signals:
        if changed:
            save_log(log)
        return

    by_symbol = {}
    for s in open_signals:
        by_symbol.setdefault(s.get("symbol", "BTCUSDT"), []).append(s)

    for symbol, signals in by_symbol.items():
        try:
            # Calculate how many 1m candles we need to cover oldest open signal
            oldest_ts = min(
                int(s.get("timestamp", 0))
                for s in signals
                if s.get("symbol") == symbol and s.get("outcome") == "OPEN"
            )
            now_ms        = int(time.time() * 1000)
            age_minutes   = max(200, int((now_ms - oldest_ts) / 60000) + 30)
            candle_limit  = min(age_minutes, 1500)  # max 1500 candles (~25 hours)

            # Fetch enough candles to cover the signal age
            if symbol in GOLD_SYMBOLS:
                data = get_multi_timeframe_data(symbol=symbol)
                df   = data["1m"]
            else:
                df = get_recent_candles_df(
                    symbol=symbol, interval="1m", limit=candle_limit
                )

            if df is None or df.empty:
                print(f"[Resolver] Empty data for {symbol}, skipping.")
                continue
            print(f"[Resolver] {symbol}: fetched {len(df)} candles (covering ~{candle_limit} min)")
        except Exception as e:
            print(f"[Resolver] Could not fetch {symbol}: {e}")
            continue

        for signal in signals:
            entry       = float(signal.get("entry", 0))
            sl          = float(signal.get("sl",    0))
            tp          = float(signal.get("tp",    0))
            signal_type = signal.get("signal", "BUY")
            sent_ts_ms  = signal.get("timestamp", 0)

            entry_hit = False
            outcome   = "OPEN"

            # Get trailing SL config from signal
            trail_cfg     = signal.get("trailing_sl", {})
            trail_enabled = trail_cfg.get("enabled", False)
            trail_activate = float(trail_cfg.get("activate_at", 0))
            trail_step    = float(trail_cfg.get("step", 0))
            current_sl    = sl  # starts at original SL
            trail_active  = False
            best_price    = entry  # tracks best price reached

            for _, row in df.iterrows():
                candle_ts = row.get("timestamp", 0) or 0
                try:
                    if int(candle_ts) <= int(sent_ts_ms):
                        continue
                except Exception:
                    continue

                high  = float(row["high"])
                low   = float(row["low"])
                close = float(row["close"])

                if signal_type == "BUY":
                    if not entry_hit:
                        # Touch fill — matches how an MT5 BUY LIMIT actually fills
                        if low <= entry:
                            entry_hit = True
                        else: continue

                    # Update trailing SL
                    if trail_enabled and entry_hit:
                        if high > best_price:
                            best_price = high
                        # Activate trailing when price reaches activate_at
                        if best_price >= trail_activate:
                            trail_active = True
                        if trail_active and trail_step > 0:
                            gain       = best_price - entry
                            new_sl     = entry + (gain * 0.5)  # trail at 50% of gain
                            current_sl = max(current_sl, round(new_sl, 4))

                    if low  <= current_sl: outcome = "LOSS"; break
                    if high >= tp:         outcome = "WIN";  break
                else:
                    if not entry_hit:
                        # Touch fill — matches how an MT5 SELL LIMIT actually fills
                        if high >= entry:
                            entry_hit = True
                        else: continue

                    # Update trailing SL
                    if trail_enabled and entry_hit:
                        if low < best_price:
                            best_price = low
                        if best_price <= trail_activate:
                            trail_active = True
                        if trail_active and trail_step > 0:
                            gain       = entry - best_price
                            new_sl     = entry - (gain * 0.5)  # trail at 50% of gain
                            current_sl = min(current_sl, round(new_sl, 4))

                    if high >= current_sl: outcome = "LOSS"; break
                    if low  <= tp:         outcome = "WIN";  break

            if outcome != "OPEN":
                points = calculate_points(signal_type, entry, current_sl, tp, outcome)
                for entry_in_log in log:
                    if (
                        entry_in_log.get("timestamp") == signal.get("timestamp") and
                        entry_in_log.get("symbol")    == signal.get("symbol")
                    ):
                        entry_in_log["outcome"] = outcome
                        entry_in_log["points"]  = points
                        entry_in_log["realized_usd"] = _realized_usd(
                            signal.get("symbol"), points)
                        changed = True
                        pts_str = f"{points:+.4f} pts" if points is not None else ""
                        print(f"[Resolver] {symbol} @ {signal.get('entry')} → {outcome} ({pts_str})")
                        # Add to trading journal
                        try:
                            add_journal_entry(entry_in_log, outcome, points)
                        except Exception as je:
                            pass
                        break
            else:
                if entry_hit:
                    # Persist the fill so the expiry rules treat this as a live
                    # TRADE (48h safety) and never as a stale pending setup.
                    if not signal.get("entry_hit"):
                        signal["entry_hit"] = True
                        changed = True
                    print(f"[Resolver] {symbol} @ {signal.get('entry')} — in trade, TP/SL not hit yet.")
                else:
                    print(f"[Resolver] {symbol} @ {signal.get('entry')} — waiting for entry to be touched.")

    if changed:
        save_log(log)


# ── Quality + sentiment filter ────────────────────────────────────────────────

def is_quality_signal(signal, sentiment, symbol="BTCUSDT"):
    weights   = load_weights()

    # ── Economic event window check ───────────────────────────────────────────
    try:
        paused, reason = is_high_impact_window()
        if paused:
            print(f"[Calendar] ⚠️  Signal paused — {reason}")
            return False, f"High-impact event window: {reason}"
    except Exception:
        pass

    # Use per-asset threshold if available, fall back to global
    min_score = weights.get(f"min_quality_score_{symbol}",
                weights.get("min_quality_score", 6))

    # Confidence gate (fixed 2026-07-05): the old hard `!= "High"` check
    # silently killed EVERY signal from ATR Trailing / SMC Swing / Momentum /
    # H4 Break-Retest (no confidence field) and all custom strategies
    # ("Medium") — 9 of 11 assigned strategies never traded because of this
    # one line. Now: "High" passes as before; anything else passes on a
    # quality_score of 7+ (backtested: those strategies are net positive).
    conf = signal.get("confidence")
    if conf != "High" and (signal.get("quality_score") or 0) < 7:
        return False, f"Confidence {conf or 'unset'} with score {signal.get('quality_score')} < 7"

    # Intraday signals have wider SL — harder to pile on 6 confluences
    # Allow score=5 for Intraday if brain conviction is high
    effective_min = min_score
    if signal.get("timeframe") == "Intraday" and min_score >= 6:
        effective_min = 5

    if signal.get("quality_score", 0) < effective_min:
        return False, f"Score {signal.get('quality_score')} below {symbol} threshold ({effective_min})"
    try:
        if float(signal.get("rr", 0)) < MIN_RR:
            return False, f"RR below {MIN_RR}"
    except (ValueError, TypeError):
        return False, "Invalid RR"

    signal_direction = signal.get("signal", "BUY")

    # Sentiment filter — only block on EXTREME opposing sentiment
    # News often lags price — allow signals unless sentiment strongly opposes
    allowed    = sentiment.get("signal_filter", {})
    geo_risk   = sentiment.get("geo_risk", "LOW")
    total_score = sentiment.get("total_score", 0)

    # Only hard-block if:
    # - Geo risk is HIGH (genuine crisis)
    # - OR sentiment STRONGLY opposes (score >= +4 for SELL, <= -4 for BUY)
    geo_block = geo_risk == "HIGH"

    if signal_direction == "SELL" and total_score >= 4 and geo_block:
        return False, f"Strongly bullish sentiment + high geo risk blocks SELL"
    if signal_direction == "BUY"  and total_score <= -4 and geo_block:
        return False, f"Strongly bearish sentiment + high geo risk blocks BUY"

    # Standard block only for geo HIGH
    if geo_block:
        return False, f"Geo risk HIGH — all signals blocked temporarily"

    # Geo strategy filter — per asset
    headlines  = sentiment.get("_headlines", [])
    fg_score   = sentiment.get("fear_greed_score")
    geo_bias   = compute_geo_bias(headlines, symbol, fg_score)
    geo_align  = geo_bias["signal_align"]

    if not geo_align.get(signal_direction, True):
        return False, f"Geo bias blocks {signal_direction} on {symbol}: {geo_bias['bias_label']}"

    # Research bias check
    research   = get_research_bias(symbol)
    res_score  = research.get("score", 0)
    if signal_direction == "BUY"  and res_score <= -3:
        return False, f"Research strongly bearish on {symbol}"
    if signal_direction == "SELL" and res_score >= 3:
        return False, f"Research strongly bullish on {symbol}"

    # Direction block check — learning engine blocks directions with <30% win rate
    direction_blocks = weights.get("direction_blocks", {})
    block_key = f"{symbol}_{signal_direction}"
    if direction_blocks.get(block_key, {}).get("blocked"):
        blk = direction_blocks[block_key]
        return False, f"Direction blocked by learning engine: {blk['reason']}"

    # Daily limit check
    timeframe = signal.get("timeframe", "Scalping")
    if _daily_limit_reached(timeframe):
        limit = DAILY_LIMITS.get(timeframe, "?")
        return False, f"Daily {timeframe} limit reached ({limit}/day)"

    return True, "OK"


def sentiment_bonus_note(signal_type, sentiment):
    fg    = sentiment.get("fear_greed_score")
    label = sentiment.get("overall_label", "Neutral")
    geo   = sentiment.get("geo_risk", "LOW")
    bias  = sentiment.get("fear_greed_bias", "")

    lines = [f"🌍 Sentiment: {label}"]
    if fg is not None:
        lines.append(f"😰 Fear & Greed: {fg}/100 — {bias}")
    if geo != "LOW":
        lines.append(f"⚠️ Geopolitical risk: {geo}")

    bullish = sentiment.get("bullish_hits", [])
    bearish = sentiment.get("bearish_hits", [])
    if signal_type == "BUY" and bullish:
        lines.append(f"📰 Bullish news: {', '.join(bullish[:3])}")
    elif signal_type == "SELL" and bearish:
        lines.append(f"📰 Bearish news: {', '.join(bearish[:3])}")

    return "\n".join(lines)


# ── Per-symbol check ──────────────────────────────────────────────────────────

def check_symbol(symbol, sentiment, state):
    try:
        data            = get_multi_timeframe_data(symbol=symbol)
        signals         = _get_signals_for_symbol(symbol, data)

        # Observe chart for trader brain
        try:
            from app.strategies.market_structure import detect_htf_support_resistance
            htf_df = data.get("1h")
            if htf_df is not None and not htf_df.empty:
                htf_sr = detect_htf_support_resistance(htf_df, lookback=5, min_touches=2)
            else:
                htf_sr = []
            chart_obs = observe_chart(data, htf_sr)
        except Exception as co_err:
            print(f"[{symbol}] Chart observation failed: {co_err}")
            chart_obs = {
                "overall_bias": "Neutral", "bias_score": 0,
                "patterns": [], "momentum_1m": {}, "momentum_5m": {},
                "momentum_1h": {}, "tf_alignment": {"aligned": False},
                "level_tests": [], "current_price": None
            }

        quality_signals = []
        for s in signals:
            passed, reason = is_quality_signal(s, sentiment, symbol)
            if passed:
                # Run through trader brain for conviction check
                brain_result = think(
                    symbol, s, chart_obs, sentiment,
                    geo_headlines=sentiment.get("_headlines", [])
                )
                if brain_result["approved"]:
                    s["_conviction"]  = brain_result["conviction"]
                    s["_narrative"]   = brain_result["narrative"]
                    s["_geo_bias"]    = brain_result["geo_bias"]
                    quality_signals.append(s)
                else:
                    conv = brain_result["conviction"]
                    print(f"[{symbol}] Brain rejected: conviction={conv['score']}/10 | {' | '.join(conv['warnings'][:2])}")
            else:
                print(f"[{symbol}] Signal filtered: {reason}")

        # ── Direction conflict resolution ─────────────────────────────────────
        # If both BUY and SELL approved, keep only the highest conviction direction
        if quality_signals:
            buy_sigs  = [s for s in quality_signals if s["signal"] == "BUY"]
            sell_sigs = [s for s in quality_signals if s["signal"] == "SELL"]
            if buy_sigs and sell_sigs:
                best_buy  = max(buy_sigs,  key=lambda s: s["_conviction"]["score"])
                best_sell = max(sell_sigs, key=lambda s: s["_conviction"]["score"])
                if best_buy["_conviction"]["score"] >= best_sell["_conviction"]["score"]:
                    quality_signals = buy_sigs
                    print(f"[{symbol}] Direction conflict — keeping BUY (conv={best_buy['_conviction']['score']})")
                else:
                    quality_signals = sell_sigs
                    print(f"[{symbol}] Direction conflict — keeping SELL (conv={best_sell['_conviction']['score']})")

        if not quality_signals:
            print(f"[{symbol}] No signals passed quality + sentiment filter.")
            return

        # ── Direction conflict resolution ─────────────────────────────────────
        # Never send both BUY and SELL — keep only highest conviction direction
        buy_sigs  = [s for s in quality_signals if s["signal"] == "BUY"]
        sell_sigs = [s for s in quality_signals if s["signal"] == "SELL"]
        if buy_sigs and sell_sigs:
            best_buy  = max(buy_sigs,  key=lambda s: s["_conviction"]["score"])
            best_sell = max(sell_sigs, key=lambda s: s["_conviction"]["score"])
            if best_buy["_conviction"]["score"] >= best_sell["_conviction"]["score"]:
                quality_signals = buy_sigs
                print(f"[{symbol}] Direction conflict — keeping BUY (conv={best_buy['_conviction']['score']})")
            else:
                quality_signals = sell_sigs
                print(f"[{symbol}] Direction conflict — keeping SELL (conv={best_sell['_conviction']['score']})")

        # Pick highest scoring signal from winning direction
        latest = max(quality_signals, key=lambda s: s.get("quality_score", 0))
        signal_id   = f"{latest['signal']}_{latest['entry']}"
        signal_type = latest["signal"]
        entry_price = float(latest["entry"])
        now         = time.time()

        sym_state       = _get_symbol_state(state, symbol)
        last_id         = sym_state["last_signal_id"]
        last_time       = sym_state["last_signal_time"]
        cooldown_passed = (now - last_time) >= COOLDOWN_SECONDS
        is_new_id       = signal_id != last_id

        # ── Gate 1: cooldown + signal ID check ──────────────────────────────
        # Dynamic cooldown based on signal quality + session timing
        session_name, in_session, mins_open, is_opening = _get_current_session_info()
        conv_score   = latest.get("_conviction", {}).get("score", 5) if latest else 5
        timeframe    = latest.get("timeframe", "Intraday") if latest else "Intraday"
        dynamic_cd   = _get_cooldown_seconds(timeframe, conv_score, is_opening)
        cooldown_passed = (now - last_time) >= dynamic_cd

        if not cooldown_passed:
            remaining  = int((dynamic_cd - (now - last_time)) / 60)
            cd_minutes = int(dynamic_cd / 60)
            print(f"[{symbol}] Cooldown active — {remaining} min remaining (dynamic: {cd_minutes} min, conv={conv_score}, session={session_name}{'🔔' if is_opening else ''})")
            return

        # After cooldown passes, allow same signal ID (market may still be at same setup)
        # Only skip if cooldown hasn't passed AND it's the same ID
        if not is_new_id and not cooldown_passed:
            print(f"[{symbol}] Same signal ID as last sent — skipping.")
            return

        # ── Gate 2: log duplicate check ───────────────────────────────────────
        if _already_in_log(symbol, signal_type, entry_price, dynamic_cd):
            print(f"[{symbol}] Duplicate found in log within cooldown window — skipping.")
            # Re-sync state file to prevent this check firing every cycle
            _update_symbol_state(state, symbol, signal_id, now - COOLDOWN_SECONDS + 300)
            return

        # ── All gates passed — send signal ────────────────────────────────────
        session_name, _, _, is_opening = _get_current_session_info()
        _update_symbol_state(state, symbol, signal_id, now)

        weights    = load_weights()
        bonuses    = weights.get("confluence_bonuses", {})
        boost_tags = [t for t in latest.get("confluences", []) if bonuses.get(t, 0) > 0]
        boost_note = f"\n📚 Learned boost: {', '.join(boost_tags)}" if boost_tags else ""
        sent_note  = sentiment_bonus_note(signal_type, sentiment)
        htf_line   = (
            f"\n📐 HTF Level: {latest['htf_level']}"
            if latest.get("htf_level") else ""
        )

        # Safe session defaults in case not set by cooldown block
        if 'session_name' not in dir():
            session_name, _, _, is_opening = _get_current_session_info()

        # Safe defaults for conviction/narrative in case brain vars not set
        conviction  = latest.get("_conviction", {})
        narrative   = latest.get("_narrative", "")
        conv_score  = conviction.get("score", "")
        conv_label  = conviction.get("label", "")
        conv_str    = f" | Conviction: {conv_score}/10 ({conv_label})" if conv_score else ""

        # Geo bias note
        geo_bias = latest.get("_geo_bias", {})
        geo_note = ""
        if geo_bias.get("key_events"):
            geo_note = f"\n🌍 Geo: {geo_bias['bias_label']} — {', '.join(geo_bias['key_events'][:3])}"

        # Build discrete entry levels text
        entries    = latest.get("entries", {})
        trail_cfg  = latest.get("trailing_sl", {})
        e1 = entries.get("e1", {})
        e2 = entries.get("e2", {})
        e3 = entries.get("e3", {})

        entries_text = ""
        if e1 and e2 and e3:
            entries_text = f"""
📊 DISCRETE ENTRIES (Scale In):
  🥇 E1 (50%): {e1.get('price')} — {e1.get('label')}
  🥈 E2 (30%): {e2.get('price')} — {e2.get('label')}
  🥉 E3 (20%): {e3.get('price')} — {e3.get('label')}"""

        trail_text = ""
        if trail_cfg.get("enabled"):
            trail_text = f"""
🎯 TRAILING SL:
  Activates at: {trail_cfg.get('activate_at')} (+1R)
  Trails at 50% of gain"""

        # Level-engine context ("human touch") — room to structure / freshness.
        struct = latest.get("structure") or {}
        struct_bits = []
        if struct.get("rr_structure") is not None:
            struct_bits.append(f"Room {struct['rr_structure']}R to {struct.get('barrier')}")
        if struct.get("tp_capped"):
            struct_bits.append("TP capped to structure")
        if struct.get("extension") is not None:
            struct_bits.append(f"Freshness {struct['extension']:+.1f}xATR")
        struct_text = ("\n🧭 Structure: " + " · ".join(struct_bits)) if struct_bits else ""

        message = f"""
⚡ TRADE SIGNAL — Smart Money Trader

📌 Symbol:      {symbol}
🕐 Session:     {session_name}{'  🔔 Opening' if is_opening else ''}
📊 Signal:      {latest["signal"]}
⏱ Style:       {latest.get("timeframe", "—")}
🎯 Confidence:  {latest.get("confidence", "—")}{conv_str}
🏆 Score:       {latest.get("quality_score", "—")}

💰 Primary Entry: {latest["entry"]}
🛑 SL:     {latest["sl"]}
🎯 TP:     {latest["tp"]}
📈 RR:     {latest["rr"]}{entries_text}{trail_text}

✅ Setup: {latest.get("setup", "ICT")}
✅ Confluences: {", ".join(latest.get("confluences", []))}{htf_line}{geo_note}{struct_text}

📋 Analysis:
{narrative[:350] if narrative else "—"}

──────────────────────
{sent_note}
"""
        exchange = SYMBOL_EXCHANGE.get(symbol, "Binance")
        print(f"[{symbol}] Signal passed all gates.")
        # Telegram alerts are sent only on actual trade open/close (see
        # trading_executor._notify_trade) — NOT on signal generation.
        # (signal message kept above for logging/UI but no longer broadcast)

        # ── Execute on MT4 if enabled ─────────────────────────────────────
        if _MT4_AVAILABLE:
            try:
                mode = _mt4_mode()
                if mode in ("paper", "demo", "live"):
                    mt4_signal = {
                        "symbol":  symbol,
                        "signal":  latest["signal"],
                        "entry":   latest["entry"],
                        "sl":      latest["sl"],
                        "tp":      latest["tp"],
                        "rr":      latest["rr"],
                        "setup":   latest.get("setup") or latest.get("strategy_tag") or "unlabeled",
                        "structure": latest.get("structure"),  # level-engine context for the open alert
                    }
                    label = {"paper": "Paper", "demo": "Demo Live", "live": "Real Live"}.get(mode, mode)
                    result = _mt4_execute(mt4_signal)
                    if result.get("success"):
                        print(f"[MT4] {'📋 Paper' if mode=='paper' else '🔴 Live'} trade: #{result.get('ticket')}")
                    else:
                        print(f"[MT4] Order failed: {result.get('error')}")
            except Exception as mt4_err:
                print(f"[MT4] Error: {mt4_err}")

        # Increment daily counter for this timeframe
        _increment_daily_count(latest.get("timeframe", "Scalping"))

        append_to_log({
            "timestamp":     int(now * 1000),
            "sent_at":       datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "symbol":        symbol,
            "signal":        latest["signal"],
            "timeframe":     latest.get("timeframe", "--"),
            "confidence":    latest.get("confidence", "--"),
            "quality_score": latest.get("quality_score", "--"),
            "raw_score":     latest.get("raw_score", "--"),
            "entry":         latest["entry"],
            "sl":            latest["sl"],
            "tp":            latest["tp"],
            "rr":            latest["rr"],
            "confluences":   latest.get("confluences", []),
            # Honest attribution: never default an unlabeled signal to a real
            # strategy's name — that made "1H FVG + EMA" look like the only
            # strategy ever firing.
            "setup":         latest.get("setup") or latest.get("strategy_tag") or "unlabeled",
            "strategy_tag":  latest.get("strategy_tag") or "unlabeled",
            "outcome":       "OPEN",
            "points":        None,
            "sentiment":     sentiment.get("overall_label", "--"),
            "fear_greed":    sentiment.get("fear_greed_score"),
            "geo_risk":      sentiment.get("geo_risk", "LOW"),
            # Persisted so the record means what downstream code assumes it
            # means. This whitelist silently dropped `session` for the life of
            # the log, which is why every stored signal read "" and
            # trader_brain's session matching could never fire. Derived, not
            # copied from the strategy's own field — most set "Unknown".
            "session":       session_from_ts(int(time.time() * 1000)),
        })

    except Exception as e:
        print(f"[{symbol}] Error: {e}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def start_live_signal_engine():
    print("Live Signal Engine started — checking BTC, ETH, GOLD every 3 minutes.")

    # Fetch calendar in background thread — don't block startup
    import threading
    def _bg_calendar():
        try:
            fetch_and_store_today_events()
        except Exception as e:
            print(f"[Calendar] Background fetch failed: {e}")
    threading.Thread(target=_bg_calendar, daemon=True).start()
    print(f"State file: {STATE_FILE}")
    print(f"Log file:   {LOG_FILE}")

    RESOLVE_INTERVAL = 3 * 60    # resolve outcomes every 3 minutes (same as check)
    last_full_cycle  = 0         # track when we last ran a full signal check

    while True:
        now = time.time()

        # ── Resolve open outcomes every 5 minutes ────────────────────────────
        print(f"\n[Resolver] Checking open signal outcomes...")
        resolve_open_outcomes()

        # ── Reconcile pending orders past their expiry ───────────────────────
        # MT5 auto-deletes ORDER_TIME_SPECIFIED pendings, but only while the
        # terminal is running, and nothing ever wrote the result back to our
        # store — the 2026-07-19 audit found 82 of 85 records stuck at
        # status="open" (median 20 days old). Cancels genuine terminal-outage
        # survivors and fixes the bookkeeping either way.
        if _MT4_AVAILABLE:
            try:
                from trading_executor import expire_stale_pending
                expire_stale_pending()
            except Exception as exc:
                print(f"[Resolver] expiry reconcile skipped: {exc}")

        # ── Full signal check every 15 minutes ───────────────────────────────
        if now - last_full_cycle >= CHECK_INTERVAL:
            last_full_cycle = now

            print(f"\n{'='*50}")
            print(f"Cycle start: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"Daily limits: {_daily_counts_summary()}")
            print(f"{'='*50}")

            state = _load_state()

            print("\n[Sentiment] Fetching market sentiment...")
            try:
                sentiment = get_sentiment()
                # Store headlines for geo strategy per-asset analysis
                try:
                    import urllib.request, xml.etree.ElementTree as ET
                    req = urllib.request.Request(
                        "https://www.coindesk.com/arc/outboundfeeds/rss/",
                        headers={"User-Agent": "SmartMoneyTrader/1.0"}
                    )
                    raw = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", errors="ignore")
                    root = ET.fromstring(raw)
                    headlines = [
                        ((item.findtext("title") or "") + " " + (item.findtext("description") or "")).lower()
                        for item in root.iter("item")
                    ]
                    sentiment["_headlines"] = headlines
                except Exception:
                    sentiment["_headlines"] = []
            except Exception as e:
                print(f"[Sentiment] Failed: {e} — using neutral defaults")
                sentiment = {
                    "overall_label": "Neutral", "total_score": 0,
                    "fear_greed_score": None, "fear_greed_label": "Unknown",
                    "fear_greed_bias": "Unknown", "geo_risk": "LOW",
                    "signal_filter": {"BUY": True, "SELL": True},
                    "_headlines": [],
                }

            print()
            active_syms = get_active_symbols()
            print(f"[Engine] Active assets: {[_SYMBOL_TO_ASSET.get(s,s) for s in active_syms]}")
            for symbol in active_syms:
                check_symbol(symbol, sentiment, state)

        time.sleep(RESOLVE_INTERVAL)
