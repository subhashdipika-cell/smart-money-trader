"""
sentiment_service.py
--------------------
Fetches market sentiment from multiple free sources with fallbacks:

  1. Crypto Fear & Greed Index  — alternative.me  (primary)
  2. CoinDesk RSS               — reliable crypto news
  3. CoinTelegraph RSS          — backup crypto news  
  4. Investing.com RSS          — macro / geopolitical news
  5. Yahoo Finance RSS          — macro backup

No API keys required. Each source has its own fallback so one
failure never breaks the whole sentiment check.
"""

import urllib.request
import urllib.error
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import time

# ── RSS sources with fallbacks ────────────────────────────────────────────────

CRYPTO_RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://bitcoinmagazine.com/.rss/full/",
]

MACRO_RSS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://www.investing.com/rss/news.rss",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
]

# ── Keyword lists ─────────────────────────────────────────────────────────────

BULLISH_KEYWORDS = [
    "rally", "surge", "rise", "bullish", "adoption", "approve", "approval",
    "etf", "institutional", "breakout", "record high", "recovery", "gains",
    "growth", "optimism", "positive", "buy", "long", "upgrade", "invest",
    "partnership", "launch", "milestone", "accumulate", "support", "boost",
    "soar", "jump", "climb", "green", "profit", "demand", "inflow"
]

BEARISH_KEYWORDS = [
    "crash", "plunge", "fall", "drop", "bear", "ban", "hack", "fraud",
    "lawsuit", "sec", "regulation", "crackdown", "fear", "panic", "sell",
    "short", "downgrade", "collapse", "liquidation", "bankruptcy", "loss",
    "warning", "risk", "concern", "dump", "rug", "scam", "investigation",
    "slump", "decline", "tumble", "outflow", "retreat", "red", "bleed"
]

GEOPOLITICAL_KEYWORDS = [
    "war", "conflict", "sanction", "crisis", "attack", "tension", "nuclear",
    "invasion", "military", "missile", "terrorism", "coup", "escalation",
    "trade war", "tariff", "embargo", "ceasefire", "recession", "inflation",
    "rate hike", "fed", "federal reserve", "interest rate", "stagflation",
    "geopolit", "unrest", "protest", "election", "default", "debt ceiling"
]


# ── Fetch helpers ─────────────────────────────────────────────────────────────

def _fetch_url(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*"
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def _fetch_fear_greed():
    """Returns (score: int, label: str) or (None, None) on failure."""
    try:
        raw   = _fetch_url("https://api.alternative.me/fng/?limit=1", timeout=6)
        data  = json.loads(raw)
        entry = data["data"][0]
        return int(entry["value"]), entry["value_classification"]
    except Exception as e:
        print(f"[Sentiment] Fear & Greed fetch failed: {e}")
        return None, None


def _fetch_rss_with_fallback(feed_list, label, max_items=15):
    """
    Try each RSS URL in order. Return headlines from the first one that works.
    """
    for url in feed_list:
        try:
            raw       = _fetch_url(url, timeout=8)
            root      = ET.fromstring(raw)
            headlines = []

            for item in root.iter("item"):
                title = item.findtext("title") or ""
                desc  = item.findtext("description") or ""
                headlines.append(f"{title} {desc}".lower())
                if len(headlines) >= max_items:
                    break

            if headlines:
                print(f"[Sentiment] {label} fetched from {url.split('/')[2]}")
                return headlines

        except urllib.error.HTTPError as e:
            print(f"[Sentiment] {label} HTTP {e.code} from {url.split('/')[2]} — trying next")
            time.sleep(1)
        except Exception as e:
            print(f"[Sentiment] {label} failed ({url.split('/')[2]}): {type(e).__name__} — trying next")

    print(f"[Sentiment] All {label} feeds failed — using empty headlines")
    return []


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_headlines(headlines):
    bullish_hits = []
    bearish_hits = []
    geo_hits     = []

    for headline in headlines:
        for kw in BULLISH_KEYWORDS:
            if kw in headline and kw not in bullish_hits:
                bullish_hits.append(kw)
        for kw in BEARISH_KEYWORDS:
            if kw in headline and kw not in bearish_hits:
                bearish_hits.append(kw)
        for kw in GEOPOLITICAL_KEYWORDS:
            if kw in headline and kw not in geo_hits:
                geo_hits.append(kw)

    score = len(bullish_hits) - len(bearish_hits)
    return score, bullish_hits, bearish_hits, geo_hits


def _fear_greed_bias(fg_score):
    """
    Smart-money / ICT perspective:
      Extreme Fear  → institutions accumulate → bullish bias
      Extreme Greed → distribution zone       → bearish bias
    """
    if fg_score is None:
        return 0, "Unknown"
    if fg_score <= 24: return  2, "Extreme Fear — contrarian BUY bias"
    if fg_score <= 49: return  1, "Fear — mild BUY bias"
    if fg_score <= 55: return  0, "Neutral"
    if fg_score <= 75: return -1, "Greed — mild SELL bias"
    return                    -2, "Extreme Greed — contrarian SELL bias"


# High-impact speakers/events that should pause signals
HIGH_IMPACT_SPEAKERS = [
    "bessent", "powell", "lagarde", "yellen", "fed chair",
    "fomc", "ecb", "boe", "rba", "rate decision", "nfp",
    "non-farm", "cpi", "gdp", "pce"
]

def _geo_risk_level(geo_hits):
    if len(geo_hits) >= 10: return "HIGH"    # only block on truly extreme events
    if len(geo_hits) >= 6:  return "MEDIUM"  # warn but don't block
    return "LOW"

def check_high_impact_events(headlines):
    """
    Returns True if a high-impact speaker or economic event
    is mentioned in recent news — signals should be paused.
    """
    all_text = " ".join(headlines).lower()
    for keyword in HIGH_IMPACT_SPEAKERS:
        if keyword in all_text:
            return True, keyword
    return False, None


def _overall_label(total_score):
    if total_score >=  3: return "Strongly Bullish"
    if total_score >=  1: return "Bullish"
    if total_score == 0:  return "Neutral"
    if total_score >= -2: return "Bearish"
    return "Strongly Bearish"


# ── Public API ────────────────────────────────────────────────────────────────

def get_sentiment(symbol="BTCUSDT"):
    """
    Returns a full sentiment dict used by live_signal_service to
    confirm or block signals.
    """

    # 1. Fear & Greed
    fg_score, fg_label      = _fetch_fear_greed()
    fg_bias_score, fg_bias  = _fear_greed_bias(fg_score)

    # 2. Crypto news (tries CoinDesk → CoinTelegraph → Bitcoin Magazine)
    crypto_headlines = _fetch_rss_with_fallback(CRYPTO_RSS_FEEDS, "Crypto news")

    # 3. Macro news (tries Yahoo Finance → Investing.com → MarketWatch)
    macro_headlines  = _fetch_rss_with_fallback(MACRO_RSS_FEEDS,  "Macro news")

    all_headlines = crypto_headlines + macro_headlines
    news_score, bullish_hits, bearish_hits, geo_hits = _score_headlines(all_headlines)

    total_score   = news_score + fg_bias_score
    overall_label = _overall_label(total_score)
    geo_risk      = _geo_risk_level(geo_hits)

    # ── Signal filter ─────────────────────────────────────────────────────────
    # Only EXTREME geo risk (HIGH) blocks signals
    # Strongly Bullish/Bearish sentiment still allows aligned signals
    geo_blocks = geo_risk == "HIGH"
    allow_buy  = total_score >= -2 and not geo_blocks   # allow even mildly bearish news
    allow_sell = total_score <=  2 and not geo_blocks   # allow even mildly bullish news

    result = {
        "overall_label":    overall_label,
        "total_score":      total_score,
        "fear_greed_score": fg_score,
        "fear_greed_label": fg_label,
        "fear_greed_bias":  fg_bias,
        "news_score":       news_score,
        "bullish_hits":     bullish_hits[:8],
        "bearish_hits":     bearish_hits[:8],
        "geo_risk":         geo_risk,
        "geo_hits":         geo_hits[:6],
        "signal_filter":    {"BUY": allow_buy, "SELL": allow_sell},
        "fetched_at":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    }

    print(
        f"[Sentiment] {overall_label} | "
        f"F&G: {fg_score} ({fg_label}) | "
        f"News: {news_score:+d} | "
        f"Geo risk: {geo_risk} | "
        f"Filter → BUY={allow_buy} SELL={allow_sell}"
    )

    return result
