"""
level_engine.py
---------------
Context awareness ("human touch") for the signal engine. Fixes the three
mechanical flaws of emotion-free systems:

  1. Chasing — taking a signal after the move already happened.
  2. No map  — shorting into support / longing into resistance.
  3. Targets beyond barriers — a 3R target that needs price to break a wall.

Before each live signal is accepted, a level map is built (swing highs/lows,
previous-day high/low, round numbers) and three checks run:

  freshness   : price must not already be > ext_max_atr ATR beyond the entry
  location    : no BUY within loc_atr ATR below resistance (or SELL above support)
  R:R to structure : (room to the opposing barrier − buffer) / SL distance ≥ min_rr

The target is additionally CAPPED to sit just before the barrier, never beyond.

Config: backend/level_config.json (auto-defaults if absent).
  {"enforce": true}  → violations skip the signal (default)
  {"enforce": false} → shadow mode: violations only tagged on the signal,
                        nothing skipped — for A/B comparison in the journal.

Every signal (taken or skipped) carries a `structure` dict so the learning
engine can later separate winners from losers by these features.
"""

import json
import os

_BASE = os.path.join(os.path.dirname(__file__), "..", "..")
CONFIG_FILE = os.path.abspath(os.path.join(_BASE, "level_config.json"))

DEFAULTS = {
    "enforce": True,        # False = shadow mode (tag, don't skip)
    "min_rr_structure": 1.2,
    "buffer_atr": 0.25,     # target buffer before the barrier (ATR units)
    "loc_atr": 0.35,        # "too close to the barrier" proximity (ATR units)
    "ext_max_atr": 1.5,     # skip if price already ran this far past the entry
    "min_barrier_strength": 1.0,  # minor round numbers are speed bumps, not walls
}

# Round-number gravity per asset class: (minor step, major step). Majors count
# as walls (strength 1.2); minors are context only (0.6).
ROUND_STEPS = {
    "BTC":  (500, 1000),
    "ETH":  (50, 100),
    "GOLD": (10, 50),
}


def get_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def _asset_class(symbol):
    s = (symbol or "").upper()
    if "BTC" in s:
        return "BTC"
    if "XAU" in s or "GOLD" in s or "PAXG" in s:
        return "GOLD"
    return "ETH"


def _atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if len(trs) < period:
        return (sum(trs) / len(trs)) if trs else 0.0
    return sum(trs[-period:]) / period


def _swings(highs, lows, lb=3):
    """Fractal swing highs/lows (strictly higher/lower than lb bars each side)."""
    sh, sl = [], []
    for i in range(lb, len(highs) - lb):
        if all(highs[i] > highs[j] for j in range(i - lb, i + lb + 1) if j != i):
            sh.append(highs[i])
        if all(lows[i] < lows[j] for j in range(i - lb, i + lb + 1) if j != i):
            sl.append(lows[i])
    return sh[-8:], sl[-8:]


def _prev_day_levels(df):
    """Previous UTC day's high/low from the timestamp column (ms epoch or datetime)."""
    try:
        import pandas as pd
        ts = df["timestamp"]
        if str(ts.dtype).startswith(("int", "float")):
            days = pd.to_datetime(ts, unit="ms", utc=True).dt.date
        else:
            days = pd.to_datetime(ts, utc=True).dt.date
        unique = sorted(set(days))
        if len(unique) < 2:
            return None, None
        prev = unique[-2]
        mask = (days == prev).values
        return float(df["high"][mask].max()), float(df["low"][mask].min())
    except Exception:
        return None, None


def build_level_map(df, symbol):
    """Level map from 1H candles: swings + prev-day H/L + round numbers.
    Returns {ok, spot, atr, levels:[{price, kind, strength}]}."""
    if df is None or len(df) < 60:
        return {"ok": False}
    highs = [float(x) for x in df["high"].tolist()]
    lows = [float(x) for x in df["low"].tolist()]
    closes = [float(x) for x in df["close"].tolist()]
    spot = closes[-1]
    atr = _atr(highs, lows, closes) or spot * 0.002

    raw = []
    sh, sl = _swings(highs, lows)
    raw += [{"price": p, "kind": "swing-high", "strength": 1.0} for p in sh]
    raw += [{"price": p, "kind": "swing-low", "strength": 1.0} for p in sl]

    pdh, pdl = _prev_day_levels(df)
    if pdh:
        raw.append({"price": pdh, "kind": "pdh", "strength": 1.3})
    if pdl:
        raw.append({"price": pdl, "kind": "pdl", "strength": 1.3})

    step, major = ROUND_STEPS[_asset_class(symbol)]
    base = round(spot / step) * step
    for k in range(-3, 4):
        p = base + k * step
        if p > 0:
            raw.append({"price": p, "kind": "round",
                        "strength": 1.2 if p % major == 0 else 0.6})

    # Cluster near-identical levels (within 0.1% of spot): sum strength.
    tol = spot * 0.001
    raw.sort(key=lambda l: l["price"])
    levels = []
    for l in raw:
        if levels and abs(l["price"] - levels[-1]["price"]) <= tol:
            levels[-1]["strength"] = round(levels[-1]["strength"] + l["strength"], 2)
            if l["kind"] not in levels[-1]["kind"]:
                levels[-1]["kind"] += "+" + l["kind"]
            levels[-1]["price"] = round((levels[-1]["price"] + l["price"]) / 2, 4)
        else:
            levels.append(dict(l))
    return {"ok": True, "spot": spot, "atr": round(atr, 4), "levels": levels}


def human_check(signal, df, symbol, cfg=None):
    """Run the three context checks on one signal (dict with entry/sl/tp/signal).
    Returns {violations[], rr_structure, barrier, tp_capped, tp_new, extension}."""
    cfg = cfg or get_config()
    out = {"violations": [], "rr_structure": None, "barrier": None,
           "tp_capped": False, "tp_new": None, "extension": None}
    lm = build_level_map(df, symbol)
    if not lm["ok"]:
        return out
    atr, spot = lm["atr"], lm["spot"]
    entry = float(signal["entry"])
    sl_dist = abs(entry - float(signal["sl"]))
    is_buy = str(signal.get("signal", "")).upper() == "BUY"
    buffer = cfg["buffer_atr"] * atr

    # 1) Freshness — price already ran past the (limit) entry = stale setup.
    ext = (spot - entry) / atr if is_buy else (entry - spot) / atr
    out["extension"] = round(ext, 2)
    if ext > cfg["ext_max_atr"]:
        out["violations"].append({
            "code": "chasing",
            "reason": f"Stale setup — price already {ext:.1f}xATR past the entry; the move happened",
        })

    # Barriers = STRONG levels on the opposing side of the ENTRY.
    strong = [l for l in lm["levels"] if l["strength"] >= cfg["min_barrier_strength"]]
    eps = spot * 0.0002
    if is_buy:
        opposing = [l for l in strong if l["price"] > entry + eps]
        barrier = min(opposing, key=lambda l: l["price"]) if opposing else None
    else:
        opposing = [l for l in strong if l["price"] < entry - eps]
        barrier = max(opposing, key=lambda l: l["price"]) if opposing else None

    if barrier:
        dist = abs(barrier["price"] - entry)
        out["barrier"] = {"price": barrier["price"], "kind": barrier["kind"],
                          "strength": barrier["strength"]}
        # 2) Location — never long right under resistance / short right above support.
        if dist <= cfg["loc_atr"] * atr:
            out["violations"].append({
                "code": "location",
                "reason": (f"{'Longing into resistance' if is_buy else 'Shorting into support'}"
                           f" @ {barrier['price']} ({barrier['kind']}) only {dist:.1f} away"),
            })
        # 3) R:R to structure.
        headroom = max(0.0, dist - buffer)
        rr_structure = headroom / sl_dist if sl_dist > 0 else 0.0
        out["rr_structure"] = round(rr_structure, 2)
        if rr_structure < cfg["min_rr_structure"]:
            out["violations"].append({
                "code": "rr-structure",
                "reason": (f"Only {rr_structure:.2f}R of room to {barrier['price']}"
                           f" ({barrier['kind']}) — target would sit beyond structure"),
            })
        # Barrier-aware target: cap the TP just before the wall, never beyond it.
        tp = float(signal["tp"])
        capped = (barrier["price"] - buffer) if is_buy else (barrier["price"] + buffer)
        if (is_buy and tp > capped) or (not is_buy and tp < capped):
            out["tp_capped"] = True
            out["tp_new"] = round(capped, 4)
    return out


def apply_human_touch(signal, df, symbol, cfg=None):
    """Gate + annotate one signal. Returns (signal, skip: bool).
    enforce=True  → violations skip; TP capped to structure.
    enforce=False → shadow: everything tagged on the signal, nothing changed."""
    cfg = cfg or get_config()
    chk = human_check(signal, df, symbol, cfg)
    signal["structure"] = {
        "rr_structure": chk["rr_structure"],
        "extension": chk["extension"],
        "barrier": (chk["barrier"] or {}).get("price"),
        "violations": [v["code"] for v in chk["violations"]],
        "tp_capped": False,
        "enforced": bool(cfg["enforce"]),
    }
    if not cfg["enforce"]:
        if chk["violations"]:
            print(f"[LEVELS/{symbol}] SHADOW would skip: "
                  + " | ".join(v["reason"] for v in chk["violations"]))
        return signal, False
    if chk["violations"]:
        print(f"[LEVELS/{symbol}] SKIP {signal.get('signal')} @ {signal.get('entry')}: "
              + " | ".join(v["reason"] for v in chk["violations"]))
        return signal, True
    if chk["tp_capped"] and chk["tp_new"] is not None:
        old_tp = signal["tp"]
        signal["tp"] = chk["tp_new"]
        signal["structure"]["tp_capped"] = True
        entry = float(signal["entry"])
        sl_dist = abs(entry - float(signal["sl"]))
        if sl_dist > 0:
            signal["rr"] = round(abs(float(signal["tp"]) - entry) / sl_dist, 2)
        signal.setdefault("confluences", []).append(
            f"TP capped to structure: {old_tp} -> {signal['tp']} (barrier {signal['structure']['barrier']})")
        print(f"[LEVELS/{symbol}] TP capped {old_tp} -> {signal['tp']} "
              f"(barrier {signal['structure']['barrier']})")
    return signal, False


# ── quick self-test: python -m app.strategies.level_engine ────────────────────
if __name__ == "__main__":
    import pandas as pd
    import numpy as np
    n = 120
    px = np.concatenate([np.linspace(64000, 67800, 90), np.full(30, 67800) + np.tile([40, -40], 15)])
    df = pd.DataFrame({
        "timestamp": (pd.date_range("2026-07-13", periods=n, freq="h", tz="UTC").astype("int64") // 10**6),
        "open": px - 20, "high": px + 60, "low": px - 60, "close": px,
    })
    lm = build_level_map(df, "BTCUSDT")
    print("map ok:", lm["ok"], "spot:", lm["spot"], "atr:", round(lm["atr"], 1), "levels:", len(lm["levels"]))
    # BUY right under the 68,000 major round → location + rr-structure must fire.
    sig = {"signal": "BUY", "entry": 67900.0, "sl": 67650.0, "tp": 68650.0, "rr": 3.0}
    chk = human_check(sig, df, "BTCUSDT")
    print("violations:", [v["code"] for v in chk["violations"]], "rr_struct:", chk["rr_structure"])
    assert any(v["code"] == "rr-structure" for v in chk["violations"])
    # BUY retest with ~1.3R of room below the PDH wall → tradeable, TP capped.
    sig2 = {"signal": "BUY", "entry": 67600.0, "sl": 67400.0, "tp": 68400.0, "rr": 3.0}
    s2, skip2 = apply_human_touch(dict(sig2), df, "BTCUSDT", {**DEFAULTS})
    print("sig2 skip:", skip2, "tp:", s2["tp"], "structure:", s2["structure"])
    assert not skip2 and s2["tp"] < 68400.0
    # Stale chase: price 67760 already ~11 ATR past a 66200 entry ⇒ chasing.
    chk3 = human_check({"signal": "BUY", "entry": 66200.0, "sl": 66000.0, "tp": 67000.0}, df, "BTCUSDT")
    print("chase ext:", chk3["extension"], "violations:", [v["code"] for v in chk3["violations"]])
    assert any(v["code"] == "chasing" for v in chk3["violations"])
    print("SELF-TEST PASS")
