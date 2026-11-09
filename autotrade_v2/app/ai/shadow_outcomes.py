"""Deterministic, read-only shadow check for a signal's STATIC TP/SL.

This is not a BingX execution replay: entry is assumed at the signal price;
trailing, fees, funding, latency, order admission and merged positions are NOT
modelled. Never count shadow percentages as realized ROI or PnL.
"""
from __future__ import annotations

import math

SIM_VERSION = "shadow.static_15m.v1"
BAR_MS = 15 * 60 * 1000
HORIZON_MS = 72 * 60 * 60 * 1000
STATUS_TERMINAL = frozenset(("TP", "SL", "AMBIGUOUS", "EXPIRED_NO_EXIT"))


def _price(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def classification(decision: str, status: str) -> str:
    """Compare hypothetical AI advice to a hypothetical static TP/SL result."""
    if decision not in ("ALLOW", "BLOCK") or status not in ("TP", "SL"):
        return "INCONCLUSIVE"
    correct = (decision == "ALLOW" and status == "TP") or (
        decision == "BLOCK" and status == "SL"
    )
    return "CORRECT" if correct else "INCORRECT"


def evaluate_shadow(signal: dict, bars: list[dict], now_ms: int) -> dict | None:
    """Evaluate only consecutive fully CLOSED 15m bars after signal close.

    Returns None for invalid signals or when the FIRST historical candle is
    unavailable. Missing intermediate bars stop evaluation; an incomplete
    window is never labelled EXPIRED and no later hit is assumed to be first.
    """
    if not isinstance(signal, dict):
        return None
    entry, tp, sl = (_price(signal.get(x)) for x in ("entry", "tp", "sl"))
    side = signal.get("side")
    try:
        signal_close = int(signal["close_time"])
        now_ms = int(now_ms)
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if not all((entry, tp, sl)) or (signal_close + 1) % BAR_MS:
        return None
    if side == "BUY":
        if not sl < entry < tp:
            return None
    elif side == "SELL":
        if not tp < entry < sl:
            return None
    else:
        return None
    start = signal_close + 1
    finish = signal_close + HORIZON_MS
    if now_ms < start:
        return None
    expected = start
    mfe, mae = 0.0, 0.0
    status, exit_ms, exit_price, reason = "PENDING", None, None, "NO_TRIGGER"
    candles_seen = 0
    rows = sorted((b for b in bars if isinstance(b, dict)),
                  key=lambda b: int(b.get("open_ms", -1)))
    for bar in rows:
        try:
            open_ms, close_ms = int(bar["open_ms"]), int(bar["close_ms"])
        except (ValueError, TypeError, KeyError, OverflowError):
            break
        if open_ms < expected:
            continue  # duplicate / earlier candle
        if open_ms != expected or close_ms != open_ms + BAR_MS - 1:
            break  # historical gap: never jump forward
        if close_ms > now_ms or close_ms > finish:
            break  # incomplete, future or beyond bounded window
        high, low, open_price = (_price(bar.get(x)) for x in ("high", "low", "open"))
        if not all((high, low, open_price)) or low > high or not low <= open_price <= high:
            break
        candles_seen += 1
        expected += BAR_MS
        if side == "BUY":
            mfe = max(mfe, 100 * (high - entry) / entry)
            mae = min(mae, 100 * (low - entry) / entry)
            tp_hit, sl_hit = high >= tp, low <= sl
        else:
            mfe = max(mfe, 100 * (entry - low) / entry)
            mae = min(mae, 100 * (entry - high) / entry)
            tp_hit, sl_hit = low <= tp, high >= sl
        # A gap across the assumed entry invalidates a fill at signal price.
        if candles_seen == 1 and not min(sl, tp) < open_price < max(sl, tp):
            status, reason = "AMBIGUOUS", "FIRST_BAR_GAP"
        elif tp_hit and sl_hit:
            status, reason = "AMBIGUOUS", "BOTH_LEVELS_SAME_BAR"
        elif tp_hit:
            status, reason, exit_price = "TP", "STATIC_TP_TOUCH", tp
        elif sl_hit:
            status, reason, exit_price = "SL", "STATIC_SL_TOUCH", sl
        if status in ("TP", "SL", "AMBIGUOUS"):
            exit_ms = close_ms  # candle close timestamp, NOT actual fill time
            break
    if not candles_seen:
        return None
    if status == "PENDING" and expected > finish:
        status, reason = "EXPIRED_NO_EXIT", "FULL_72H_WINDOW_NO_TOUCH"
    gross = (100 * (exit_price - entry) / entry *
             (1 if side == "BUY" else -1)) if exit_price is not None else None
    return {
        "entry": entry, "tp": tp, "sl": sl, "exit_mode": "STATIC_TP_SL_ASSUMED",
        "status": status, "exit_ms": exit_ms, "exit_price": exit_price,
        "gross_pct": gross, "est_fee_pct": None, "net_pct": None,
        "mfe_pct": mfe, "mae_pct": mae, "sim_version": SIM_VERSION,
        "evidence": {
            "source": "Binance USD-M 15m closed klines", "reason": reason,
            "first_open_ms": start, "last_closed_ms": expected - 1,
            "candles_seen": candles_seen, "window_end_ms": finish,
            "complete_window": expected > finish,
            "assumed_entry_not_verified_fill": True,
            "trailing_not_modelled": True, "fees_not_modelled": True,
            "not_realized_pnl_or_roi": True,
        },
    }
