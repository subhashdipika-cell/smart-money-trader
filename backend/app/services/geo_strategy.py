"""
geo_strategy.py
---------------
Maps geopolitical and macroeconomic events to directional market bias.

Logic is based on established market relationships:
  - Geopolitical conflict  → Gold UP (safe haven)
  - Fed rate hike          → USD UP → Crypto DOWN, Gold pressure
  - Inflation data high    → Gold UP, Risk assets DOWN
  - Risk-off sentiment     → Gold UP, Crypto DOWN
  - Risk-on sentiment      → Crypto UP, Gold DOWN

Returns per-asset directional bias adjustments that combine
with technical signals for stronger confluence.
"""

import json
from datetime import datetime, timezone

# ── Geopolitical keyword → market impact mapping ──────────────────────────────

GEO_IMPACTS = {
    # Conflict / War → Gold safe haven bid
    "war":        {"XAUUSD": +2, "BTCUSDT": -1, "ETHUSDT": -1},
    "conflict":   {"XAUUSD": +2, "BTCUSDT": -1, "ETHUSDT": -1},
    "invasion":   {"XAUUSD": +2, "BTCUSDT": -1, "ETHUSDT": -1},
    "attack":     {"XAUUSD": +1, "BTCUSDT": -1, "ETHUSDT": -1},
    "missile":    {"XAUUSD": +2, "BTCUSDT": -1, "ETHUSDT": -1},
    "nuclear":    {"XAUUSD": +3, "BTCUSDT": -2, "ETHUSDT": -2},
    "terrorism":  {"XAUUSD": +1, "BTCUSDT":  0, "ETHUSDT":  0},
    "sanction":   {"XAUUSD": +1, "BTCUSDT":  0, "ETHUSDT":  0},
    "escalation": {"XAUUSD": +2, "BTCUSDT": -1, "ETHUSDT": -1},
    "ceasefire":  {"XAUUSD": -1, "BTCUSDT": +1, "ETHUSDT": +1},

    # Monetary policy → rate hikes hurt crypto, gold mixed
    "rate hike":        {"XAUUSD": -1, "BTCUSDT": -2, "ETHUSDT": -2},
    "interest rate":    {"XAUUSD": -1, "BTCUSDT": -1, "ETHUSDT": -1},
    "fed":              {"XAUUSD":  0, "BTCUSDT": -1, "ETHUSDT": -1},
    "federal reserve":  {"XAUUSD":  0, "BTCUSDT": -1, "ETHUSDT": -1},
    "hawkish":          {"XAUUSD": -1, "BTCUSDT": -2, "ETHUSDT": -2},
    "dovish":           {"XAUUSD": +1, "BTCUSDT": +2, "ETHUSDT": +2},
    "rate cut":         {"XAUUSD": +1, "BTCUSDT": +2, "ETHUSDT": +2},
    "quantitative easing": {"XAUUSD": +2, "BTCUSDT": +1, "ETHUSDT": +1},

    # Inflation → Gold hedge
    "inflation":        {"XAUUSD": +2, "BTCUSDT":  0, "ETHUSDT":  0},
    "cpi":              {"XAUUSD": +1, "BTCUSDT": -1, "ETHUSDT": -1},
    "stagflation":      {"XAUUSD": +2, "BTCUSDT": -2, "ETHUSDT": -2},
    "deflation":        {"XAUUSD": -1, "BTCUSDT":  0, "ETHUSDT":  0},

    # Recession / economic fear → Gold safe haven
    "recession":        {"XAUUSD": +2, "BTCUSDT": -2, "ETHUSDT": -2},
    "default":          {"XAUUSD": +1, "BTCUSDT": -1, "ETHUSDT": -1},
    "debt":             {"XAUUSD": +1, "BTCUSDT":  0, "ETHUSDT":  0},
    "banking crisis":   {"XAUUSD": +2, "BTCUSDT": +1, "ETHUSDT": +1},  # BTC as alt to banking

    # Trade / tariffs → risk off
    "tariff":           {"XAUUSD": +1, "BTCUSDT": -1, "ETHUSDT": -1},
    "trade war":        {"XAUUSD": +1, "BTCUSDT": -1, "ETHUSDT": -1},
    "embargo":          {"XAUUSD": +1, "BTCUSDT":  0, "ETHUSDT":  0},

    # Crypto specific
    "etf":              {"XAUUSD":  0, "BTCUSDT": +2, "ETHUSDT": +1},
    "adoption":         {"XAUUSD":  0, "BTCUSDT": +2, "ETHUSDT": +2},
    "ban":              {"XAUUSD":  0, "BTCUSDT": -2, "ETHUSDT": -2},
    "regulation":       {"XAUUSD":  0, "BTCUSDT": -1, "ETHUSDT": -1},
    "sec":              {"XAUUSD":  0, "BTCUSDT": -1, "ETHUSDT": -1},
    "hack":             {"XAUUSD":  0, "BTCUSDT": -1, "ETHUSDT": -2},
    "institutional":    {"XAUUSD":  0, "BTCUSDT": +2, "ETHUSDT": +1},

    # Risk sentiment
    "risk off":         {"XAUUSD": +2, "BTCUSDT": -2, "ETHUSDT": -2},
    "risk on":          {"XAUUSD": -1, "BTCUSDT": +2, "ETHUSDT": +2},
    "safe haven":       {"XAUUSD": +2, "BTCUSDT":  0, "ETHUSDT":  0},
    "rally":            {"XAUUSD":  0, "BTCUSDT": +1, "ETHUSDT": +1},
    "crash":            {"XAUUSD": +1, "BTCUSDT": -2, "ETHUSDT": -2},
}

# ── Fear & Greed → asset bias (contrarian ICT view) ──────────────────────────

def fear_greed_asset_bias(fg_score, symbol):
    """
    ICT / smart money view:
    Extreme Fear  = institutions accumulating → bullish for risk assets
    Extreme Greed = distribution → bearish for risk assets
    Gold is inversely affected (fear → gold up)
    """
    if fg_score is None:
        return 0

    if symbol == "XAUUSD":
        # Gold benefits from fear
        if fg_score <= 20: return +2   # extreme fear → strong gold bid
        if fg_score <= 35: return +1   # fear → mild gold bid
        if fg_score >= 80: return -2   # extreme greed → gold sells off
        if fg_score >= 65: return -1   # greed → mild gold weakness
        return 0
    else:
        # Crypto: contrarian — buy fear, sell greed
        if fg_score <= 20: return +2   # extreme fear = buy opportunity
        if fg_score <= 35: return +1   # fear = mild bullish
        if fg_score >= 80: return -2   # extreme greed = distribution
        if fg_score >= 65: return -1   # greed = mild bearish
        return 0


def compute_geo_bias(headlines, symbol, fg_score=None):
    """
    Analyses news headlines and returns:
    {
        "symbol":        str,
        "geo_score":     int,     # sum of geo impacts for this asset
        "fg_bias":       int,     # fear/greed contribution
        "total_bias":    int,     # combined score
        "bias_label":   str,     # "Strongly Bullish" / "Bullish" / "Neutral" / "Bearish" / "Strongly Bearish"
        "signal_align":  dict,   # { "BUY": bool, "SELL": bool }
        "key_events":    list,   # which keywords triggered bias
        "reasoning":     str     # human-readable explanation
    }
    """
    geo_score  = 0
    key_events = []
    all_text   = " ".join(headlines).lower()

    for keyword, impacts in GEO_IMPACTS.items():
        if keyword in all_text and symbol in impacts:
            score = impacts[symbol]
            if score != 0:
                geo_score  += score
                key_events.append(f"{keyword} ({'+' if score > 0 else ''}{score})")

    fg_bias    = fear_greed_asset_bias(fg_score, symbol)
    total_bias = geo_score + fg_bias

    # Label
    if total_bias >=  3: label = "Strongly Bullish"
    elif total_bias >= 1: label = "Bullish"
    elif total_bias == 0: label = "Neutral"
    elif total_bias >= -2: label = "Bearish"
    else:                label = "Strongly Bearish"

    # Signal alignment — only block on EXTREME geo opposition
    # Normal bearish news shouldn't override a bullish technical setup
    # Geo filter should complement, not override, sentiment + technical
    allow_buy  = total_bias >= -4   # only block if very strongly bearish (-4 or worse)
    allow_sell = total_bias <=  4   # only block if very strongly bullish (+4 or better)

    # Build reasoning
    reasoning_parts = []
    if key_events:
        reasoning_parts.append(f"Geo events: {', '.join(key_events[:4])}")
    if fg_bias != 0:
        reasoning_parts.append(f"F&G bias: {'+' if fg_bias > 0 else ''}{fg_bias}")
    reasoning = " | ".join(reasoning_parts) or "No significant geo events detected."

    return {
        "symbol":       symbol,
        "geo_score":    geo_score,
        "fg_bias":      fg_bias,
        "total_bias":   total_bias,
        "bias_label":   label,
        "signal_align": {"BUY": allow_buy, "SELL": allow_sell},
        "key_events":   key_events[:6],
        "reasoning":    reasoning
    }


def get_all_asset_biases(headlines, fg_score=None):
    """Returns geo bias for all three assets."""
    symbols = ["BTCUSDT", "ETHUSDT", "XAUUSD"]
    return {s: compute_geo_bias(headlines, s, fg_score) for s in symbols}
