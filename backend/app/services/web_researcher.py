"""
web_researcher.py
-----------------
Fetches strategy insights from trusted free sources and extracts
actionable trading rules that the engine can apply.

Sources:
  - BabyPips RSS (forex/macro education)
  - TradingView ideas RSS (community setups)
  - Investopedia market news RSS
  - Reuters markets RSS

Saves extracted insights to research_insights.json which the
signal engine consults when filtering signals.
"""

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import json
import os
import time
from datetime import datetime, timezone

_BASE          = os.path.join(os.path.dirname(__file__), "..", "..")
INSIGHTS_FILE  = os.path.abspath(os.path.join(_BASE, "research_insights.json"))

RESEARCH_FEEDS = [
    {
        "name":    "BabyPips",
        "url":     "https://www.babypips.com/feed",
        "type":    "education"
    },
    {
        "name":    "Investopedia",
        "url":     "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline",
        "type":    "macro"
    },
    {
        "name":    "MarketWatch",
        "url":     "https://feeds.marketwatch.com/marketwatch/marketpulse/",
        "type":    "markets"
    }
]

# Keywords that indicate actionable strategy insights
BULLISH_PATTERNS = [
    "breakout", "support holds", "bounce", "accumulation", "buy the dip",
    "golden cross", "higher low", "demand zone", "bullish engulfing",
    "morning star", "hammer", "inverse head and shoulders", "cup and handle"
]

BEARISH_PATTERNS = [
    "breakdown", "resistance", "distribution", "sell rally", "death cross",
    "lower high", "supply zone", "bearish engulfing", "evening star",
    "shooting star", "head and shoulders", "double top"
]

GOLD_KEYWORDS   = ["gold", "xau", "precious metal", "safe haven", "bullion"]
BTC_KEYWORDS    = ["bitcoin", "btc", "crypto", "digital asset"]
ETH_KEYWORDS    = ["ethereum", "eth", "defi", "altcoin"]
MACRO_KEYWORDS  = ["fed", "rate", "inflation", "recession", "dollar", "dxy"]


def _fetch_rss(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SmartMoneyTrader/1.0 Research Bot"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


def _parse_feed(raw):
    headlines = []
    try:
        root = ET.fromstring(raw)
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            desc  = item.findtext("description") or ""
            headlines.append(f"{title} {desc}".lower())
    except Exception:
        pass
    return headlines


def _extract_insights(headlines):
    """Extract structured insights from headlines."""
    insights = {
        "BTCUSDT":  {"bullish_signals": [], "bearish_signals": [], "macro_context": []},
        "ETHUSDT":  {"bullish_signals": [], "bearish_signals": [], "macro_context": []},
        "XAUUSD":   {"bullish_signals": [], "bearish_signals": [], "macro_context": []},
        "global":   {"bullish_signals": [], "bearish_signals": [], "macro_context": []}
    }

    for headline in headlines:
        # Determine which asset
        asset = None
        if any(k in headline for k in GOLD_KEYWORDS):
            asset = "XAUUSD"
        elif any(k in headline for k in BTC_KEYWORDS):
            asset = "BTCUSDT"
        elif any(k in headline for k in ETH_KEYWORDS):
            asset = "ETHUSDT"

        # Bullish patterns found
        for pattern in BULLISH_PATTERNS:
            if pattern in headline:
                target = asset or "global"
                if len(insights[target]["bullish_signals"]) < 5:
                    snippet = headline[:80].strip()
                    if snippet not in insights[target]["bullish_signals"]:
                        insights[target]["bullish_signals"].append(snippet)

        # Bearish patterns found
        for pattern in BEARISH_PATTERNS:
            if pattern in headline:
                target = asset or "global"
                if len(insights[target]["bearish_signals"]) < 5:
                    snippet = headline[:80].strip()
                    if snippet not in insights[target]["bearish_signals"]:
                        insights[target]["bearish_signals"].append(snippet)

        # Macro context
        if any(k in headline for k in MACRO_KEYWORDS):
            target = asset or "global"
            if len(insights[target]["macro_context"]) < 5:
                snippet = headline[:80].strip()
                if snippet not in insights[target]["macro_context"]:
                    insights[target]["macro_context"].append(snippet)

    return insights


def _market_bias_from_insights(insights, symbol):
    """
    Convert research insights into a directional score.
    More bullish signals than bearish = positive score.
    """
    data = insights.get(symbol, {})
    glb  = insights.get("global", {})

    bullish = len(data.get("bullish_signals", [])) + len(glb.get("bullish_signals", []))
    bearish = len(data.get("bearish_signals", [])) + len(glb.get("bearish_signals", []))

    score = bullish - bearish
    if score >=  2: return "Bullish",  score
    if score <= -2: return "Bearish",  score
    return "Neutral", score


def run_research():
    """
    Fetch research from all sources, extract insights,
    compute per-asset bias, save to file.
    """
    print("[Research] Fetching strategy insights from trusted sources...")
    all_headlines = []

    for feed in RESEARCH_FEEDS:
        try:
            raw       = _fetch_rss(feed["url"])
            headlines = _parse_feed(raw)
            all_headlines.extend(headlines)
            print(f"[Research] {feed['name']}: {len(headlines)} headlines fetched")
            time.sleep(1)
        except Exception as e:
            print(f"[Research] {feed['name']} failed: {type(e).__name__}")

    if not all_headlines:
        print("[Research] No headlines fetched — skipping.")
        return {}

    insights = _extract_insights(all_headlines)

    # Compute bias per asset
    biases = {}
    for symbol in ["BTCUSDT", "ETHUSDT", "XAUUSD"]:
        label, score = _market_bias_from_insights(insights, symbol)
        biases[symbol] = {"label": label, "score": score}
        print(f"[Research] {symbol} research bias: {label} (score={score:+d})")

    result = {
        "insights":     insights,
        "biases":       biases,
        "fetched_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_headlines": len(all_headlines)
    }

    with open(INSIGHTS_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[Research] Insights saved ({len(all_headlines)} headlines processed)")
    return result


def load_research():
    """Load latest research insights."""
    try:
        with open(INSIGHTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def get_research_bias(symbol):
    """Get research-based directional bias for a symbol."""
    data = load_research()
    return data.get("biases", {}).get(symbol, {"label": "Neutral", "score": 0})
