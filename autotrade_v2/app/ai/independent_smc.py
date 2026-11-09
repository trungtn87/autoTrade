"""Deterministic cross-exchange SMC observations from independently sourced candles.

This module is READ ONLY and intentionally does not reuse the 20FINAL SMC engine.
Pivot confirmation uses later *closed* candles within the as-of window.
BOS/CHoCH labels are local swing heuristics, not an institutional SMC oracle.
"""
from __future__ import annotations

import math


def _finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _confirmed_swings(bars, radius=3):
    highs, lows = [], []
    for i in range(radius, len(bars)-radius):
        high, low = bars[i]["high"], bars[i]["low"]
        if all(high > bars[j]["high"] for j in range(i-radius, i+radius+1) if j != i):
            highs.append({"price": high, "at_ms": bars[i]["close_ms"],
                          "confirmed_ms": bars[i+radius]["close_ms"], "index": i})
        if all(low < bars[j]["low"] for j in range(i-radius, i+radius+1) if j != i):
            lows.append({"price": low, "at_ms": bars[i]["close_ms"],
                         "confirmed_ms": bars[i+radius]["close_ms"], "index": i})
    return highs, lows


def analyze_structure(rows, *, timeframe, as_of_ms):
    """Compute local swing breaks and three-candle FVG using only closed bars.

    Each row: {open_ms,close_ms,open,high,low,close,volume}.
    """
    bars = []
    for b in rows:
        try:
            entry = {
                "open_ms": int(b["open_ms"]), "close_ms": int(b["close_ms"]),
                **{k: _finite(b[k]) for k in ("open", "high", "low", "close", "volume")},
            }
        except (ValueError, KeyError, TypeError):
            continue
        if (entry["close_ms"] > int(as_of_ms) or
                any(entry[k] is None for k in ("open", "high", "low", "close", "volume")) or
                entry["low"] > entry["high"]):
            continue
        bars.append(entry)
    bars.sort(key=lambda b: b["open_ms"])
    out = {"timeframe": timeframe, "as_of_ms": int(as_of_ms), "bar_count": len(bars),
           "method": "confirmed_swings_radius3_close_break;three_closed_bar_FVG",
           "status": "INSUFFICIENT", "latest_confirmed_high": None,
           "latest_confirmed_low": None, "last_break": None,
           "recent_fvg": [], "liquidity_hypotheses": [],
           "trend": "UNKNOWN"}
    if len(bars) < 12:
        return out
    highs, lows = _confirmed_swings(bars)
    out["latest_confirmed_high"] = ({k:v for k,v in highs[-1].items() if k != "index"}
                                    if highs else None)
    out["latest_confirmed_low"] = ({k:v for k,v in lows[-1].items() if k != "index"}
                                   if lows else None)

    # Replay bars from oldest to newest; never use a swing before confirmation.
    hi, lo, high_id, low_id = None, None, None, None
    last_broken_high, last_broken_low = None, None
    trend = "UNKNOWN"
    events = []
    for bar in bars:
        close_ms = bar["close_ms"]
        for h in highs:
            if h["confirmed_ms"] == close_ms:
                hi, high_id = h["price"], h["at_ms"]
                last_broken_high = None
        for l in lows:
            if l["confirmed_ms"] == close_ms:
                lo, low_id = l["price"], l["at_ms"]
                last_broken_low = None
        if hi is not None and bar["close"] > hi and last_broken_high != high_id:
            kind = "CHOCH" if trend == "BEARISH" else "BOS"
            events.append({"type": kind, "direction": "BULLISH", "break_level": hi,
                           "break_ms": close_ms, "swing_ms": high_id})
            last_broken_high = high_id
            trend = "BULLISH"
        if lo is not None and bar["close"] < lo and last_broken_low != low_id:
            kind = "CHOCH" if trend == "BULLISH" else "BOS"
            events.append({"type": kind, "direction": "BEARISH", "break_level": lo,
                           "break_ms": close_ms, "swing_ms": low_id})
            last_broken_low = low_id
            trend = "BEARISH"
    out["last_break"] = events[-1] if events else None
    out["trend"] = trend
    fvgs = []
    for i in range(2, len(bars)):
        old, current = bars[i-2], bars[i]
        if current["low"] > old["high"]:
            fvgs.append({"direction": "BULLISH", "low": old["high"],
                         "high": current["low"], "formed_ms": current["close_ms"]})
        elif current["high"] < old["low"]:
            fvgs.append({"direction": "BEARISH", "low": current["high"],
                         "high": old["low"], "formed_ms": current["close_ms"]})
    out["recent_fvg"] = fvgs[-3:]
    # Equal swing clusters are merely potential liquidity, never confirmed orders.
    for side, swings in (("equal_highs", highs), ("equal_lows", lows)):
        if len(swings) >= 2:
            first, second = swings[-2], swings[-1]
            if max(first["price"], second["price"]) > 0 and (
                abs(first["price"] - second["price"]) /
                max(first["price"], second["price"]) <= 0.0015
            ):
                out["liquidity_hypotheses"].append({
                    "type": side, "level_approx": (first["price"]+second["price"])/2,
                    "swing_ms": [first["at_ms"], second["at_ms"]],
                    "confirmed": False,
                })
    out["status"] = "AVAILABLE"
    return out
