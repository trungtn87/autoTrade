"""Conservative BingX-native exit cleanup.

BingX executes exits; the bot only cancels *owned* orphan orders after a
combo is exchange-confirmed flat. No SL resizing and no cancelReplace.
Polling is externally paced (60 seconds); one open-orders and one positions
snapshot per active symbol each pass. Missing orders are queried by ID only.

A completion proof is either:
  (1) exact sum of this combo's own exchange exit fills equals its entry fill;
  (2) this exact positionId is flat for TWO separate passes.
If exchange evidence is missing/ambiguous, no mutation is permitted.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import time

KINDS = {"sl": "STOP_MARKET", "tp": "TAKE_PROFIT_MARKET",
         "trailing": "TRAILING_STOP_MARKET"}
ACTIVE = {"NEW", "PARTIALLY_FILLED"}
TERMINAL = {"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}
MAX_CANCEL_ATTEMPTS = 2


class CleanupUncertain(RuntimeError):
    pass


def positive(value, label):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise CleanupUncertain(f"invalid {label}") from e
    if not d.is_finite() or d < 0:
        raise CleanupUncertain(f"invalid {label}")
    return d


def oid(row):
    # BingX JSON numeric orderId can be rounded in external clients.
    # Prefer the exact string orderID property.
    v = str(row.get("orderID") or row.get("orderId") or "").strip()
    return v if re.fullmatch(r"[1-9][0-9]*", v) else ""


def own_orders(state):
    protection = state.get("protection")
    if not isinstance(protection, dict):
        raise CleanupUncertain("protection missing")
    entries = {}
    for kind in KINDS:
        item = protection.get(kind)
        if not item:
            continue
        v = str(item.get("order_id") or "")
        if not re.fullmatch(r"[1-9][0-9]*", v):
            raise CleanupUncertain(f"{kind} orderId invalid")
        if v in entries:
            raise CleanupUncertain("duplicate protection orderId")
        entries[v] = kind
    if "sl" not in entries.values() or "tp" not in entries.values():
        raise CleanupUncertain("required SL/TP missing")
    if state.get("exit_mode") == "split_trailing" and "trailing" not in entries.values():
        raise CleanupUncertain("expected trailing order missing")
    return entries


def eligible(state):
    return bool(
        state.get("ok") and state.get("terminal")
        and re.fullmatch(r"[1-9][0-9]*", str(state.get("position_id") or ""))
        and str(state.get("intent", {}).get("symbol") or "")
        and state.get("executed_qty") is not None
        and (state.get("exit_cleanup") or {}).get("status") != "done"
    )


def validate_order(state, kind, order):
    intent = state["intent"]
    expected_side = "BUY" if str(intent["side"]).upper() == "SELL" else "SELL"
    expected_position_side = "SHORT" if expected_side == "BUY" else "LONG"
    if (
        str(order.get("symbol") or "") != str(intent["symbol"])
        or str(order.get("side") or "").upper() != expected_side
        or str(order.get("positionSide") or "").upper() != expected_position_side
        or str(order.get("type") or "").upper() != KINDS[kind]
    ):
        raise CleanupUncertain("protection order identity mismatch")
    position_id = str(order.get("positionId") or order.get("positionID") or "")
    if position_id not in ("", "0") and position_id != str(state["position_id"]):
        raise CleanupUncertain("protection positionId mismatch")
    if str(order.get("status") or "").upper() not in ACTIVE | TERMINAL:
        raise CleanupUncertain("unrecognized BingX order status")


def snapshot_position(positions, state):
    intent = state["intent"]
    side = "SHORT" if str(intent["side"]).upper() == "SELL" else "LONG"
    chosen = []
    for row in positions:
        if row.get("symbol") != intent["symbol"] or str(row.get("positionSide") or "").upper() != side:
            continue
        if str(row.get("positionId") or "") == str(state["position_id"]):
            chosen.append(row)
    if len(chosen)>1:
        raise CleanupUncertain("duplicate positionId in exchange snapshot")
    if not chosen:
        return True
    try:
        amt = Decimal(str(chosen[0].get("positionAmt")))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise CleanupUncertain("BingX positionAmt missing") from exc
    if not amt.is_finite():
        raise CleanupUncertain("nonfinite positionAmt")
    return amt == 0


def _order_view(row):
    return {
        "status": str(row["status"]).upper(),
        "executed_qty": str(positive(row.get("executedQty"), "executedQty")),
        "orig_qty": str(positive(row.get("origQty") or row.get("quantity"), "origQty")),
    }


def inspect_one(client, state, open_by_id, positions, *, now_ms=None):
    """Inspect one trade without mutation. Return (new monitor state, cancel IDs, alerts)."""
    old = state.get("exit_cleanup") or {}
    if old.get("status") == "done":
        return old, [], []
    monitor = {
        "status": old.get("status") if old.get("status") in {"watching", "cleanup"} else "watching",
        "orders": dict(old.get("orders") or {}),
        "flat_observations": int(old.get("flat_observations") or 0),
        "cancel_attempts": dict(old.get("cancel_attempts") or {}),
        "last_cancel_at_ms": dict(old.get("last_cancel_at_ms") or {}),
        "warnings": list(old.get("warnings") or []),
    }
    now_ms = int(now_ms if now_ms is not None else time.time()*1000)
    own = own_orders(state)
    outstanding = []
    total_filled = Decimal(0)
    alerts = []
    for order_id, kind in own.items():
        row = open_by_id.get(order_id)
        previous = monitor["orders"].get(order_id)
        if row is None:
            if previous and previous["status"] in TERMINAL:
                view = previous
            else:
                payload = client.get_order_detail(state["intent"]["symbol"], order_id=order_id)
                row = client.extract_order(payload)
                if oid(row) != order_id:
                    raise CleanupUncertain("exchange returned wrong orderId")
                validate_order(state, kind, row)
                view = _order_view(row)
        else:
            validate_order(state, kind, row)
            view = _order_view(row)
        if view["status"] not in ACTIVE | TERMINAL:
            raise CleanupUncertain("unrecognized status in cached order")
        count = positive(view["executed_qty"], "executedQty")
        if count > positive(view["orig_qty"], "origQty"):
            raise CleanupUncertain("order executedQty exceeds origQty")
        # Terminal status must not silently revert to NEW; refuse mutation.
        if previous and previous["status"] in TERMINAL and view["status"] in ACTIVE:
            raise CleanupUncertain("terminal order returned to active state")
        monitor["orders"][order_id] = view
        total_filled += count
        if view["status"] in ACTIVE:
            outstanding.append(order_id)

    initial = positive(state["executed_qty"], "own entry executedQty")
    if initial == 0 or total_filled > initial:
        raise CleanupUncertain("invalid entry/exit fill accounting")
    flat = snapshot_position(positions, state)
    monitor["flat_observations"] = min(2,monitor["flat_observations"]+1) if flat else 0

    remaining = initial-total_filled
    # Exact own fills = strongest closure proof, independent of other BTC combos.
    # PositionId flat twice handles manual close/liquidation.
    confirmed_closed = remaining == 0 or monitor["flat_observations"] >= 2
    if not confirmed_closed:
        active_sl = [
            positive(monitor["orders"][oid_]["orig_qty"],"SL qty")
            -positive(monitor["orders"][oid_]["executed_qty"],"SL executed")
            for oid_,kind in own.items() if kind == "sl"
            and monitor["orders"][oid_]["status"] in ACTIVE
        ]
        condition = (
            "NO_ACTIVE_SL" if not active_sl else
            "SL_OVER_REMAINING" if sum(active_sl)>remaining else
            ""
        )
        if condition and condition not in monitor["warnings"]:
            monitor["warnings"].append(condition)
            alerts.append(condition)
        return monitor, [], alerts

    monitor["status"] = "cleanup"
    if not outstanding:
        monitor["status"] = "done"
        return monitor, [], alerts
    safe = []
    for order_id in outstanding:
        attempts = int(monitor["cancel_attempts"].get(order_id, 0))
        last = int(monitor["last_cancel_at_ms"].get(order_id, 0))
        if attempts < MAX_CANCEL_ATTEMPTS and now_ms-last>=60_000:
            safe.append(order_id)
        elif attempts>=MAX_CANCEL_ATTEMPTS and "CANCEL_RETRY_EXHAUSTED" not in monitor["warnings"]:
            monitor["warnings"].append("CANCEL_RETRY_EXHAUSTED")
            alerts.append("CANCEL_RETRY_EXHAUSTED")
    return monitor, safe, alerts


def clean_one(client, state, open_by_id, positions, persist, alert, *, now_ms=None):
    """Write-ahead cleanup; cancel only IDs previously verified as belonging to this combo."""
    monitor, candidates, warnings = inspect_one(client, state, open_by_id, positions, now_ms=now_ms)
    for warning in warnings:
        alert("L3.EXEC.EXIT_CLEANUP_RISK", "CRITICAL", warning)
    if state.get("exit_cleanup") != monitor:
        state["exit_cleanup"] = monitor
        persist(state)
    completed = []
    now_ms = int(now_ms if now_ms is not None else time.time()*1000)
    for order_id in candidates:
        # Never DELETE based on a stale cache. If the openOrders endpoint
        # omits conditional stops, reconfirm via an exact-ID GET before DELETE.
        if order_id not in open_by_id:
            payload = client.get_order_detail(state["intent"]["symbol"],order_id=order_id)
            fresh = client.extract_order(payload)
            if oid(fresh) != order_id:
                raise CleanupUncertain("cannot confirm orphan by exact ID")
            validate_order(state,own_orders(state)[order_id],fresh)
            if str(fresh.get("status") or "").upper() not in ACTIVE:
                continue
        monitor["cancel_attempts"][order_id] = int(monitor["cancel_attempts"].get(order_id,0))+1
        monitor["last_cancel_at_ms"][order_id] = now_ms
        state["exit_cleanup"] = monitor
        persist(state)  # WRITE-AHEAD before DELETE
        try:
            client.cancel_order(state["intent"]["symbol"], order_id=order_id)
            completed.append(order_id)
        except Exception as exc:
            alert("L3.EXEC.EXIT_CLEANUP_RISK","CRITICAL",
                  f"cancel uncertain for order {order_id}: {type(exc).__name__}")
            # Never assume cancellation on a lost DELETE reply; next pass
            # checks the exchange before a bounded retry.
    return {"status":monitor["status"],"cancel_requested":completed,
            "warnings":warnings}
