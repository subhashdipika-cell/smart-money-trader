"""
vault_reader.py
---------------
SMT's read-side connection to the Obsidian Trading_Mind vault — the shared
"trading brain" all four apps write into. Until now SMT only WROTE its
journal there; this module closes the loop by reading back:

  1. apps        — YAML frontmatter of every app's latest monthly trade
                   export (raw/trades/<app>/<YYYY-MM>.md): broker-realized
                   trades / win rate / net. Cross-app evidence about how the
                   same instruments are ACTUALLY paying right now.
  2. discipline  — the mitigation bullet rules from the trade-review wiki
                   page (wiki/psychology/trade-review.md), distilled from
                   the user's live-account audits.

`conviction_adjustment()` turns that into a small, capped, evidence-based
nudge on the trader brain's conviction score; `narrative_block()` gives the
human-readable version for the journal narrative. Everything is cached per
IST day and fails soft — a missing vault never blocks a signal.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

VAULT_TRADES = Path(r"E:\Obsidian\Trading_Mind\raw\trades")
TRADE_REVIEW = Path(r"E:\Obsidian\Trading_Mind\wiki\psychology\trade-review.md")

from app.services.clock import IST   # canonical; see clock.py
_CACHE: dict = {"day": None, "ctx": None}

_FM_KEYS = ("trades", "wins", "losses", "win_rate", "net_r",
            "mt5_trades", "mt5_win_rate", "mt5_net_usd")


def _frontmatter(path: Path) -> dict:
    try:
        m = re.match(r"^---\n(.*?)\n---", path.read_text(encoding="utf-8", errors="replace"), re.S)
        if not m:
            return {}
        out = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip() in _FM_KEYS and v.strip():
                    out[k.strip()] = v.strip()
        return out
    except Exception:
        return {}


def _app_stats() -> list[dict]:
    out = []
    if not VAULT_TRADES.exists():
        return out
    for app_dir in sorted(p for p in VAULT_TRADES.iterdir() if p.is_dir()):
        months = sorted(app_dir.glob("2*.md"))
        if not months:
            continue
        fm = _frontmatter(months[-1])
        if fm:
            out.append({"app": app_dir.name, "month": months[-1].stem, **fm})
    return out


def _discipline_rules(max_rules: int = 6) -> list[str]:
    """Numbered mitigation bullets from the trade-review audit page."""
    try:
        text = TRADE_REVIEW.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"## Mitigation routines(.*?)(?:\n## |\Z)", text, re.S)
        if not m:
            return []
        rules = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", m.group(1), re.M)
        return rules[:max_rules]
    except Exception:
        return []


def get_vault_context() -> dict:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    if _CACHE["day"] == today and _CACHE["ctx"] is not None:
        return _CACHE["ctx"]
    ctx = {"apps": _app_stats(), "discipline": _discipline_rules(), "day": today}
    _CACHE.update(day=today, ctx=ctx)
    return ctx


def _realized(stats: dict) -> tuple[float | None, int]:
    """(win_rate, n) preferring broker-realized (mt5_*) figures."""
    try:
        if stats.get("mt5_trades") and float(stats["mt5_trades"]) > 0 and stats.get("mt5_win_rate"):
            return float(stats["mt5_win_rate"]), int(float(stats["mt5_trades"]))
        if stats.get("trades") and stats.get("win_rate"):
            return float(stats["win_rate"]), int(float(stats["trades"]))
    except Exception:
        pass
    return None, 0


def conviction_adjustment() -> tuple[float, list[str], list[str]]:
    """(adjustment, reasons, warnings) from vault evidence. Capped [-1.5, +1]:
    the vault informs conviction, it never dominates it."""
    ctx = get_vault_context()
    adj, reasons, warnings = 0.0, [], []
    for row in ctx["apps"]:
        wr, n = _realized(row)
        if wr is None or n < 5:
            continue
        weight = 1.0 if row["app"] == "smart-money-trader" else 0.5
        if wr < 30:
            adj -= 1.0 * weight
            warnings.append(f"Vault: {row['app']} realized WR {wr:.0f}% over {n} trades ({row['month']})")
        elif wr >= 55:
            adj += 0.5 * weight
            reasons.append(f"Vault: {row['app']} realized WR {wr:.0f}% over {n} trades ({row['month']})")
    return max(-1.5, min(1.0, adj)), reasons, warnings


def narrative_block() -> str:
    ctx = get_vault_context()
    if not ctx["apps"] and not ctx["discipline"]:
        return ""
    lines = ["", "📚 Vault (Trading_Mind) evidence:"]
    for row in ctx["apps"]:
        wr, n = _realized(row)
        if wr is not None and n:
            lines.append(f"  • {row['app']} {row['month']}: {n} realized trades, {wr:.0f}% WR")
    if ctx["discipline"]:
        lines.append("  Audit rules in force: " + "; ".join(ctx["discipline"][:4]))
    return "\n".join(lines)
