from __future__ import annotations

import json
import logging
from typing import Any

from .final_config import get_case, case_name

log = logging.getLogger(__name__)

STATE_KEY = "final18_active_positions_v1"


def _load(state) -> list[dict[str, Any]]:
    raw = state.get_runtime_value(STATE_KEY, "[]")
    try:
        data = json.loads(raw or "[]")
    except Exception:
        data = []
    return [x for x in data if isinstance(x, dict)]


def _save(state, rows: list[dict[str, Any]]) -> None:
    state.set_runtime_value(STATE_KEY, json.dumps(rows, ensure_ascii=False, separators=(",", ":")))


def active_case_ids(state) -> set[str]:
    return {str(x.get("case_id")) for x in _load(state) if x.get("active", True)}


def case_id(symbol: str, combo: int) -> str:
    return f"{symbol.upper()}|{case_name(combo)}"


def is_case_active(state, symbol: str, combo: int) -> bool:
    wanted = case_id(symbol, combo)
    return wanted in active_case_ids(state)


def register_execution(state, signal, result: dict) -> dict:
    cfg = get_case(signal.symbol, signal.combo)
    if not cfg:
        raise RuntimeError(f"cannot register disabled FINAL18 case {signal.symbol} {signal.combo}")
    if not result.get("ok") or not result.get("entry_filled"):
        raise RuntimeError("cannot register FINAL18 position before successful protected entry")

    rows = _load(state)
    cid = case_id(signal.symbol, signal.combo)
    rows = [x for x in rows if x.get("case_id") != cid]

    side = str(signal.side).upper()
    position_side = "LONG" if side == "BUY" else "SHORT"
    record = {
        "case_id": cid,
        "event_id": signal.event_id,
        "symbol": signal.symbol,
        "combo": int(signal.combo),
        "side": side,
        "position_side": position_side,
        "entry": float(result["avg_price"]),
        "entry_close_time": int(signal.close_time),
        "last_processed_close_time": int(signal.close_time),
        "price_precision": int(result.get("price_precision", 2)),
        "base_sl": float(result["sl"]),
        "protect_price": result.get("protect_price"),
        "protect_active": False,
        "active": True,
        "legs": {
            "1": {
                "live": True,
                "active": False,
                "qty": float(result["leg1_qty"]),
                "activation": float(result["trail1_activation"]),
                "callback": float(result["trail1_callback"]),
                "extreme": None,
                "order_id": str(result["leg1_order_id"]),
                "current_stop": float(result["leg1_stop_price"]),
            },
            "2": {
                "live": True,
                "active": False,
                "qty": float(result["leg2_qty"]),
                "activation": float(result["trail2_activation"]),
                "callback": float(result["trail2_callback"]),
                "extreme": None,
                "order_id": str(result["leg2_order_id"]),
                "current_stop": float(result["leg2_stop_price"]),
            },
        },
    }
    rows.append(record)
    _save(state, rows)
    log.info("FINAL18_POSITION_REGISTERED case_id=%s event_id=%s", cid, signal.event_id)
    return record


def _is_filled(status: str) -> bool:
    return status.upper() == "FILLED"


def _is_bad_terminal(status: str) -> bool:
    return status.upper() in {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}


def _favorable_stop(side: str, old: float, new: float) -> bool:
    if side == "BUY":
        return new > old
    return new < old


def _trail_stop(side: str, extreme: float, callback: float) -> float:
    if side == "BUY":
        return extreme * (1.0 - callback)
    return extreme * (1.0 + callback)


def _round_price(value: float, precision: int) -> float:
    return round(float(value), int(precision))


def _cleanup_slice(executor, rec: dict, reason: str) -> dict:
    cancel_results = []
    qty = 0.0
    for leg in rec.get("legs", {}).values():
        if leg.get("live"):
            qty += float(leg.get("qty") or 0)
            oid = leg.get("order_id")
            if oid:
                try:
                    cancel_results.append(executor.final18_cancel_order(rec["symbol"], str(oid)))
                except Exception as exc:
                    cancel_results.append({"error": str(exc), "order_id": str(oid)})
    close_result = None
    close_ok = False
    if qty > 0:
        try:
            close_result = executor.final18_close_slice(
                rec["symbol"], rec["position_side"], qty
            )
            close_ok = True
        except Exception as exc:
            close_result = {"error": str(exc)}
    rec["active"] = False
    rec["cleanup_reason"] = reason
    return {
        "case_id": rec.get("case_id"),
        "ok": False,
        "action": "slice_cleanup",
        "reason": reason,
        "qty": qty,
        "cancel_results": cancel_results,
        "close_ok": close_ok,
        "close_result": close_result,
    }


def manage_symbol_positions(state, executor, symbol: str, latest_row) -> list[dict]:
    """Advance FINAL18 trailing state once per newly closed 15m candle.

    Existing stop orders protect the just-closed candle. Only after confirming
    those orders did not fill do we use that candle high/low to compute the next
    candle's stop levels. This mirrors the conservative backtest semantics.
    """
    rows = _load(state)
    if not rows:
        return []

    close_time = int(latest_row["close_time"])
    high = float(latest_row["high"])
    low = float(latest_row["low"])
    changed = False
    results = []
    kept = []

    for rec in rows:
        if rec.get("symbol") != symbol or not rec.get("active", True):
            kept.append(rec)
            continue
        if close_time <= int(rec.get("last_processed_close_time", 0)):
            kept.append(rec)
            continue
        if close_time <= int(rec.get("entry_close_time", 0)):
            rec["last_processed_close_time"] = close_time
            kept.append(rec)
            changed = True
            continue

        side = rec["side"]
        precision = int(rec.get("price_precision", 2))
        unexpected_terminal = None

        # First: determine whether stops that were active during this candle filled.
        for leg_name, leg in rec["legs"].items():
            if not leg.get("live"):
                continue
            try:
                detail = executor.final18_order_detail(symbol, str(leg["order_id"]))
                status = str(detail.get("status") or "").upper()
            except Exception as exc:
                results.append({
                    "case_id": rec["case_id"],
                    "ok": False,
                    "action": "order_status_error",
                    "leg": leg_name,
                    "error": str(exc),
                })
                kept.append(rec)
                break

            if _is_filled(status):
                leg["live"] = False
                leg["filled_close_time"] = close_time
                changed = True
            elif _is_bad_terminal(status):
                unexpected_terminal = f"leg{leg_name}_status_{status}"
                break
        else:
            # Executed only when status loop did not break.
            if not any(bool(x.get("live")) for x in rec["legs"].values()):
                rec["active"] = False
                results.append({
                    "case_id": rec["case_id"],
                    "ok": True,
                    "action": "position_complete",
                })
                changed = True
                continue

            favorable = high if side == "BUY" else low
            leg1 = rec["legs"]["1"]
            leg2 = rec["legs"]["2"]

            # Activation/update uses the candle that just closed; new stop is for
            # the NEXT candle only.
            if leg1.get("live"):
                if not leg1.get("active"):
                    hit = favorable >= leg1["activation"] if side == "BUY" else favorable <= leg1["activation"]
                    if hit:
                        leg1["active"] = True
                        leg1["extreme"] = favorable
                        if rec.get("protect_price") is not None:
                            rec["protect_active"] = True
                        changed = True
                else:
                    old = float(leg1["extreme"])
                    leg1["extreme"] = max(old, favorable) if side == "BUY" else min(old, favorable)

            if leg2.get("live"):
                if not leg2.get("active"):
                    hit = favorable >= leg2["activation"] if side == "BUY" else favorable <= leg2["activation"]
                    if hit:
                        leg2["active"] = True
                        leg2["extreme"] = favorable
                        changed = True
                else:
                    old = float(leg2["extreme"])
                    leg2["extreme"] = max(old, favorable) if side == "BUY" else min(old, favorable)

            # Compute and atomically replace each live leg stop only if it improves.
            for leg_name, leg in rec["legs"].items():
                if not leg.get("live"):
                    continue
                desired = float(rec["base_sl"])
                if rec.get("protect_active") and rec.get("protect_price") is not None:
                    pp = float(rec["protect_price"])
                    desired = max(desired, pp) if side == "BUY" else min(desired, pp)
                if leg.get("active") and leg.get("extreme") is not None:
                    ts = _trail_stop(side, float(leg["extreme"]), float(leg["callback"]))
                    desired = max(desired, ts) if side == "BUY" else min(desired, ts)
                desired = _round_price(desired, precision)
                current = float(leg["current_stop"])
                if not _favorable_stop(side, current, desired):
                    continue
                try:
                    repl = executor.final18_replace_stop(
                        symbol=symbol,
                        old_order_id=str(leg["order_id"]),
                        side="SELL" if side == "BUY" else "BUY",
                        position_side=rec["position_side"],
                        qty=float(leg["qty"]),
                        stop_price=desired,
                    )
                    leg["order_id"] = str(repl["order_id"])
                    leg["current_stop"] = desired
                    changed = True
                    results.append({
                        "case_id": rec["case_id"],
                        "ok": True,
                        "action": "stop_promoted",
                        "leg": leg_name,
                        "stop": desired,
                    })
                except Exception as exc:
                    unexpected_terminal = f"leg{leg_name}_replace_failed:{exc}"
                    break

            if unexpected_terminal is None:
                rec["last_processed_close_time"] = close_time
                kept.append(rec)
                changed = True
                continue

        # Any lost/unreplaceable stop is treated as an isolated slice failure.
        cleanup = _cleanup_slice(executor, rec, unexpected_terminal or "manager_failure")
        results.append(cleanup)
        changed = True

    if changed:
        _save(state, kept)
    return results
