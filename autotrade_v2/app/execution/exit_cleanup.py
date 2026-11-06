"""Conservative BingX-native exit cleanup.

BingX executes exits; the bot only cancels *owned* orphan orders after a
combo is exchange-confirmed flat. No SL resizing and no cancelReplace.
Polling is externally paced (60 seconds); one open-orders and one positions
snapshot per active symbol each pass. Missing orders are queried by ID only.

Completion requires the exact sum of this combo's owned exchange exit fills
 to equal its entry fill. A flat/missing positionId is diagnostic, never proof.
If exchange evidence is missing/ambiguous, no mutation is permitted.
"""
from __future__ import annotations

from autotrade_v2.analytics.evidence import order_evidence
from .identity import position_id as exchange_position_id

from decimal import Decimal, InvalidOperation
import logging
import re
import time

KINDS = {"sl": "STOP_MARKET", "tp": "TAKE_PROFIT_MARKET",
         "trailing": "TRAILING_STOP_MARKET"}
ACTIVE = {"NEW", "PARTIALLY_FILLED"}
TERMINAL = {"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED"}
MAX_CANCEL_ATTEMPTS = 2
LOG = logging.getLogger(__name__)


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


def own_orders(state, require_complete=True):
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
    if require_complete and ("sl" not in entries.values() or "tp" not in entries.values()):
        raise CleanupUncertain("required SL/TP missing")
    if require_complete and state.get("exit_mode") == "split_trailing" and "trailing" not in entries.values():
        raise CleanupUncertain("expected trailing order missing")
    return entries


def eligible(state):
    return bool(
        state.get("entry_filled")
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
    position_id = exchange_position_id(order)
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
        if exchange_position_id(row) == str(state["position_id"]):
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
        "analytics_order": order_evidence(row),
        "status": str(row["status"]).upper(),
        "executed_qty": str(positive(row.get("executedQty"), "executedQty")),
        "orig_qty": str(positive(row.get("origQty") or row.get("quantity"), "origQty")),
    }


def triggered_execution(historical_by_id, parent_id, state, kind):
    """Prove an original conditional exit filled via BingX triggerOrderId.

    Trigger execution orderId differs from the parent's orderId. Accept only
    ONE exact child with a validated identity and an actual FILLED quantity;
    never infer this linkage from price, position only, or timestamps.
    """
    matches = [
        row for row in historical_by_id.values()
        if str(row.get("triggerOrderId") or row.get("triggerOrderID") or "") == parent_id
        and oid(row) != parent_id  # conditional parents may self-reference
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise CleanupUncertain("multiple executions for one trigger order; manual reconcile required")
    child = matches[0]
    if not oid(child) or oid(child) == parent_id:
        raise CleanupUncertain("invalid triggered execution ID")
    validate_order(state, kind, child)
    if str(child.get("status") or "").upper() != "FILLED":
        raise CleanupUncertain("triggered execution not confirmed FILLED")
    executed = positive(child.get("executedQty"), "triggered executedQty")
    if executed <= 0:
        raise CleanupUncertain("triggered execution without filled quantity")
    return {"analytics_order":order_evidence(child), "status": "FILLED", "executed_qty": str(executed),
            "orig_qty": str(executed)}


def abort_close_quantity(state):
    """Count only the exact deterministic MARKET abort identity, never side delta."""
    row=(state.get("abort") or {}).get("close_order")
    if not row:
        return Decimal(0)
    intent=state["intent"];journal=state.get("emergency_close") or {}
    cid=str(row.get("clientOrderId") or row.get("clientOrderID") or "")
    if (not oid(row) or cid!=journal.get("client_order_id")
            or row.get("symbol")!=intent["symbol"]
            or str(row.get("side") or "").upper()!=("SELL" if intent["side"]=="BUY" else "BUY")
            or str(row.get("positionSide") or "").upper()!=("LONG" if intent["side"]=="BUY" else "SHORT")
            or str(row.get("type") or "").upper()!="MARKET"
            or exchange_position_id(row)!=str(state["position_id"])
            or str(row.get("status") or "").upper() not in ACTIVE|TERMINAL):
        raise CleanupUncertain("abort close identity unverified")
    original=positive(row.get("origQty") or row.get("quantity"),"abort origQty")
    filled=positive(row.get("executedQty"),"abort executedQty")
    if original!=positive(journal.get("requested_qty"),"abort requested qty") or filled>original:
        raise CleanupUncertain("abort close quantity unverified")
    return filled


def force_close_quantity(state):
    allocation=state.get("force_close_allocation")
    if not allocation:
        return Decimal(0)
    if (allocation.get("epoch")!=state.get("force_close_epoch")
            or allocation.get("position_id")!=str(state.get("position_id"))):
        raise CleanupUncertain("force close allocation identity mismatch")
    amount=positive(allocation.get("quantity"),"force close allocation")
    total=Decimal(0);seen=set()
    intent=state["intent"]
    for row in allocation.get("orders") or []:
        if (not oid(row) or oid(row) in seen
                or row.get("symbol")!=intent["symbol"]
                or str(row.get("side","")).upper()!=("SELL" if intent["side"]=="BUY" else "BUY")
                or str(row.get("positionSide","")).upper()!=("LONG" if intent["side"]=="BUY" else "SHORT")
                or exchange_position_id(row)!=str(state["position_id"])
                or str(row.get("type","")).upper()!="MARKET"
                or str(row.get("status","")).upper() not in TERMINAL):
            raise CleanupUncertain("force close fill proof invalid")
        seen.add(oid(row))
        executed=positive(row.get("executedQty"),"force close fill")
        if executed>positive(row.get("origQty"),"force close original"):
            raise CleanupUncertain("force close overfill")
        total+=executed
    if amount>total:
        raise CleanupUncertain("force close allocation exceeds verified fills")
    return amount


def inspect_one(client, state, open_by_id, positions, *, now_ms=None, historical_by_id=None, allow_incomplete=False):
    """Inspect one trade without mutation. Return (new monitor state, cancel IDs, alerts)."""
    old = state.get("exit_cleanup") or {}
    if old.get("status") == "done":
        return old, [], []
    monitor = {
        "status": old.get("status") if old.get("status") in {"watching", "cleanup"} else "watching",
        "orders": dict(old.get("orders") or {}),
        "flat_observations": int(old.get("flat_observations") or 0),
        "last_flat_seen_ms": int(old.get("last_flat_seen_ms") or 0),
        "cancel_attempts": dict(old.get("cancel_attempts") or {}),
        "last_cancel_at_ms": dict(old.get("last_cancel_at_ms") or {}),
        "warnings": list(old.get("warnings") or []),
    }
    now_ms = int(now_ms if now_ms is not None else time.time()*1000)
    own = own_orders(state,require_complete=not allow_incomplete)
    # The previous lifecycle could cancelReplace SL into an ID that was never
    # durably resolved. This cleanup cannot safely claim/delete that unknown
    # order; alert once so it can be investigated on BingX.
    legacy_pending = (state.get("lifecycle") or {}).get("pending_sl_replace")
    if legacy_pending and "LEGACY_SL_REPLACEMENT_UNVERIFIED" not in monitor["warnings"]:
        monitor["warnings"].append("LEGACY_SL_REPLACEMENT_UNVERIFIED")
    outstanding = []
    unknown_exit_ids = []
    total_filled = abort_close_quantity(state)+force_close_quantity(state)
    alerts = (
        ["LEGACY_SL_REPLACEMENT_UNVERIFIED"] if legacy_pending
        and (state.get("exit_cleanup") or {}).get("status") is None
        else []
    )
    for order_id, kind in own.items():
        row = open_by_id.get(order_id)
        previous = monitor["orders"].get(order_id)
        if row is None:
            if previous and previous["status"] in TERMINAL:
                view = previous
            else:
                # The BingX single-order GET was observed returning an
                # unrelated orderId with HTTP 200. Never use it in live
                # cleanup. A symbol-wide historical snapshot provides
                # cross-checkable exact IDs; absence means no mutation.
                if historical_by_id is not None:
                    row = historical_by_id.get(order_id)
                    if row is None:
                        # BingX often creates a different execution orderId
                        # for filled TP/SL/trailing. Its exact triggerOrderId
                        # identifies the ORIGINAL owned protection ID.
                        view = triggered_execution(
                            historical_by_id, order_id, state, kind
                        )
                        if view is None:
                            # No verified own exit means no closure proof.
                            unknown_exit_ids.append(order_id)
                            continue
                        row = None  # synthetic, exact-linked FILLED view
                else:
                    # Offline legacy tests only: live service always passes
                    # a historical_by_id snapshot.
                    payload = client.get_order_detail(
                        state["intent"]["symbol"],order_id=order_id
                    )
                    row = client.extract_order(payload)
                if row is not None:
                    if oid(row) != order_id:
                        raise CleanupUncertain("historical orderId mismatch")
                    validate_order(state, kind, row)
                    view = _order_view(row)
        else:
            validate_order(state, kind, row)
            view = _order_view(row)
        if historical_by_id is not None:
            child=triggered_execution(historical_by_id,order_id,state,kind)
            if child is not None:
                if view["status"] in ACTIVE:
                    raise CleanupUncertain("trigger execution conflicts with active parent snapshot")
                if positive(view["executed_qty"],"parent fill") not in {
                    Decimal(0),positive(child["executed_qty"],"child fill")}:
                    raise CleanupUncertain("parent/child filled quantities disagree")
                view=child  # count the parent-linked execution once, never twice
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
    if flat:
        if now_ms - monitor["last_flat_seen_ms"] >= 30_000:
            monitor["flat_observations"] = min(
                2,monitor["flat_observations"]+1
            )
            monitor["last_flat_seen_ms"] = now_ms
    else:
        monitor["flat_observations"] = 0
        monitor["last_flat_seen_ms"] = 0

    remaining = initial-total_filled
    monitor["entry_filled_qty"] = str(initial)
    monitor["exit_filled_qty"] = str(total_filled)
    monitor["remaining_qty"] = str(remaining)
    monitor["fills_verified"] = not unknown_exit_ids
    # Missing history cannot prove combo closure, even if the shared
    # container disappeared from repeated snapshots.
    if unknown_exit_ids:
        if not flat:
            # Missing historical conditional IDs is not itself evidence of
            # a bad order. Fail closed and emit bounded, persisted warnings
            # rather than repeated exception alerts on every poll.
            for warning in ("EXIT_HISTORY_INCOMPLETE_OPEN_POSITION",
                            "NO_TRACKED_ACTIVE_SL"):
                if warning == "NO_TRACKED_ACTIVE_SL":
                    sl_id = next((oid_ for oid_, k in own.items() if k == "sl"), "")
                    if sl_id in open_by_id or (
                        sl_id in monitor["orders"]
                        and monitor["orders"][sl_id]["status"] in ACTIVE
                    ):
                        continue
                if warning not in monitor["warnings"]:
                    monitor["warnings"].append(warning)
                    alerts.append(warning)
        return monitor, [], alerts

    # A missing/rotated shared positionId is never per-combo fill evidence.
    # Flat snapshots remain diagnostic only, even after repeated observations.
    monitor["entry_filled_qty"] = str(initial)
    monitor["exit_filled_qty"] = str(total_filled)
    monitor["remaining_qty"] = str(remaining)
    monitor["fills_verified"] = not unknown_exit_ids
    confirmed_closed = not unknown_exit_ids and remaining == 0
    if not confirmed_closed:
        active_sl = [
            positive(monitor["orders"][oid_]["orig_qty"],"SL qty")
            -positive(monitor["orders"][oid_]["executed_qty"],"SL executed")
            for oid_,kind in own.items() if kind == "sl"
            and monitor["orders"][oid_]["status"] in ACTIVE
        ]
        condition = (
            "NO_TRACKED_ACTIVE_SL" if not active_sl else
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


def clean_one(client, state, open_by_id, positions, persist, alert, *, now_ms=None, historical_by_id=None):
    """Write-ahead cleanup; cancel only IDs previously verified as belonging to this combo."""
    monitor, candidates, warnings = inspect_one(
        client,state,open_by_id,positions,
        now_ms=now_ms,historical_by_id=historical_by_id,
    )
    for warning in warnings:
        alert("L3.EXEC.EXIT_CLEANUP_RISK", "CRITICAL", warning)
    if state.get("exit_cleanup") != monitor:
        state["exit_cleanup"] = monitor
        persist(state)
    completed = []
    now_ms = int(now_ms if now_ms is not None else time.time()*1000)
    for order_id in candidates:
        # Never DELETE an order unseen in BingX's *current openOrders*
        # snapshot. Historical NEW status alone is insufficient to cancel.
        if order_id not in open_by_id:
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
