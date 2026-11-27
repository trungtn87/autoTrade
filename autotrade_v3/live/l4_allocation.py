"""L4 read-only shared-position accounting; never places, amends or cancels orders.

A BingX hedge position may contain several V3 combos. An exit order belongs to
the combo that submitted its trigger, but its *executed quantity* can close the
shared position belonging to another combo. Preserve that order attribution in
execution_results and allocate its economic impact to remaining combo capacity
in execution_finances.raw.

This is an auditable accounting allocation, NOT an exchange-side safety fix or
proof that there are no external/manual orders on the same position.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .sanitize import clean

CLOSE_KINDS = {"SL": "sl_order_id", "TP": "tp_order_id",
               "TRAILING": "trailing_order_id", "FORCE_CLOSE": None}


class UnverifiableAllocation(ValueError):
    """Never invent ownership when exact order/fill provenance is missing."""


def _dec(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise UnverifiableAllocation("missing or malformed quantity/price") from exc
    if not result.is_finite():
        raise UnverifiableAllocation("nonfinite quantity/price")
    return result


def _id(value: Any) -> str:
    """Use integer IDs as decimal text: floating point loses BingX order IDs."""
    if isinstance(value, float) or isinstance(value, bool):
        raise UnverifiableAllocation("lossy floating-point or boolean order ID")
    text = str(value or "")
    if not text.isdigit() or int(text) <= 0:
        raise UnverifiableAllocation("missing or invalid exact ID")
    return text


def _entry_position(ex: dict) -> str | None:
    raw = ex.get("raw_entry") or {}
    if not isinstance(raw, dict):
        return None
    order = ((raw.get("confirm") or {}).get("order") or {})
    if not isinstance(order, dict) or not order.get("positionID"):
        return None
    try:
        if _id(order.get("orderId")) != _id(ex.get("entry_order_id")):
            return None
        return _id(order["positionID"])
    except UnverifiableAllocation:
        return None


def _position_side(ex: dict) -> str:
    return "LONG" if ex.get("side") == "BUY" else "SHORT"


def _source_order(ex: dict, result: dict, position_id: str) -> tuple[dict, Decimal, int]:
    raw = result.get("raw") or {}
    order = raw.get("order") if isinstance(raw, dict) else None
    if not isinstance(order, dict):
        raise UnverifiableAllocation("missing original BingX close order JSON")
    if _id(order.get("orderId")) != _id(result.get("close_order_id")):
        raise UnverifiableAllocation("close order ID mismatch")
    if _id(order.get("positionID")) != position_id:
        raise UnverifiableAllocation("close position ID mismatch")
    if (str(order.get("symbol")) != str(ex.get("symbol"))
            or str(order.get("positionSide")) != _position_side(ex)
            or str(order.get("side")) != ("BUY" if ex.get("side") == "SELL" else "SELL")):
        raise UnverifiableAllocation("close identity mismatch")
    if str(order.get("status")).upper() not in {"FILLED", "PARTIALLY_FILLED"}:
        raise UnverifiableAllocation("exit is not an exchange fill")
    kind = str(result.get("close_type") or "")
    if kind not in CLOSE_KINDS:
        raise UnverifiableAllocation("unknown close kind")
    if kind == "FORCE_CLOSE":
        protection = ex.get("raw_protection") or {}
        parent = (protection.get("force_close") or {}).get("order_id")
        if _id(parent) != _id(order.get("orderId")):
            raise UnverifiableAllocation("force close ID mismatch")
    else:
        expected_parent = _id(ex.get(CLOSE_KINDS[kind]))
        actual_parent = order.get("triggerOrderId") or order.get("triggerOrderID")
        if _id(actual_parent or order.get("orderId")) != expected_parent:
            raise UnverifiableAllocation("triggerOrderId does not match owning protection")
    qty = _dec(result.get("closed_qty"))
    if qty <= 0 or qty != _dec(order.get("executedQty")):
        raise UnverifiableAllocation("exit fill quantity mismatch")
    updated_ms = int(_dec(order.get("updateTime") or order.get("time")))
    if updated_ms <= 0:
        raise UnverifiableAllocation("missing exchange execution timestamp")
    return order, qty, updated_ms


def allocate_group(entries: list[dict], results: list[dict]) -> dict[str, dict]:
    """Allocate known V3 exit fills: owner first, overfill to oldest peer capacity.

    Each child fill remains owned by the original combo in execution_results.
    This pure function produces only derived, inspectable finance views.
    """
    if len(entries) < 2:
        return {}
    positions = {_entry_position(ex) for ex in entries}
    if len(positions) != 1 or None in positions:
        raise UnverifiableAllocation("missing or conflicting exact Entry position IDs")
    pos = next(iter(positions))
    symbols = {str(e.get("symbol")) for e in entries}
    sides = {str(e.get("side")) for e in entries}
    if len(symbols) != 1 or len(sides) != 1:
        raise UnverifiableAllocation("mixed symbol or direction")
    ordered = sorted(entries, key=lambda e: (str(e.get("created_at") or ""), int(e["id"])))
    by_event = {str(ex["event_id"]): ex for ex in ordered}
    if len(by_event) != len(entries):
        raise UnverifiableAllocation("duplicate Entry event ID")
    qty_by = {event: _dec(e.get("executed_qty")) for event, e in by_event.items()}
    if any(q <= 0 for q in qty_by.values()):
        raise UnverifiableAllocation("missing Entry fill")
    remaining = dict(qty_by)
    alloc = {event: {"qty": Decimal(0), "pnl": Decimal(0),
                     "fee": Decimal(0), "sources": []} for event in by_event}
    seen = set()
    fills = []
    raw_qty_by_owner = defaultdict(lambda: Decimal(0))
    for r in results:
        source = str(r.get("event_id"))
        if source not in by_event:
            raise UnverifiableAllocation("close event not in group")
        order, qty, ms = _source_order(by_event[source], r, pos)
        oid = _id(r["close_order_id"])
        if oid in seen:
            raise UnverifiableAllocation("same BingX close order counted twice")
        seen.add(oid)
        pnl = _dec(order.get("profit"))
        fee = abs(_dec(order.get("commission")))
        fills.append((ms, oid, source, qty, pnl, fee, str(r["close_type"])))
        raw_qty_by_owner[source] += qty
    if not any(raw_qty_by_owner[e] > qty_by[e] for e in by_event):
        return {}  # Normal position, keep original V3 finance calculation.
    if sum((v[3] for v in fills), Decimal(0)) > sum(qty_by.values(), Decimal(0)):
        raise UnverifiableAllocation("total exchange exits exceed all known Entry quantities")
    fills.sort(key=lambda v: (v[0], v[1]))
    for _, oid, owner, qty, pnl, fee, kind in fills:
        left = qty
        for target in [owner] + [e["event_id"] for e in ordered if e["event_id"] != owner]:
            take = min(left, remaining[target])
            if take <= 0:
                continue
            ratio = take / qty
            alloc[target]["qty"] += take
            alloc[target]["pnl"] += pnl * ratio
            alloc[target]["fee"] += fee * ratio
            alloc[target]["sources"].append({
                "order_id": oid, "source_event": owner, "close_type": kind,
                "allocated_qty": str(take), "price_source": "original_exchange_order",
            })
            remaining[target] -= take
            left -= take
            if left == 0:
                break
        if left != 0:
            raise UnverifiableAllocation("insufficient known combo capacity for a close fill")
    out = {}
    for event, e in by_event.items():
        q = alloc[event]["qty"]
        margin = _dec(e.get("avg_price")) * qty_by[event] / _dec(e.get("leverage"))
        if margin <= 0:
            raise UnverifiableAllocation("invalid Entry margin")
        raw_entry = (e.get("raw_entry") or {}).get("confirm") or {}
        own_fee = e.get("existing_open_fee")
        if own_fee is None:
            own_fee = abs(_dec((raw_entry.get("order") or {}).get("commission")))
        else:
            own_fee = _dec(own_fee)
        # The allocation already validates close profit/commission. Never
        # certify an Entry fee that the older projection silently defaulted to zero.
        verified_fee = (e.get("existing_finance_raw") or {}).get("discord_finance_verified") is True
        if not verified_fee:
            try:
                verified_fee = abs(_dec((raw_entry.get("order") or {}).get("commission"))) == own_fee
            except UnverifiableAllocation:
                verified_fee = False
        net = alloc[event]["pnl"] - own_fee - alloc[event]["fee"]
        out[event] = {
            "execution_order_id": e["id"], "event_id": event, "margin": str(margin),
            "leverage": e["leverage"], "position_value": str(margin * _dec(e["leverage"])),
            "open_fee": str(own_fee), "close_fee": str(alloc[event]["fee"]),
            "funding": None, "gross_pnl": str(alloc[event]["pnl"]), "net_pnl": str(net),
            "roi_pct": str(net / margin * Decimal(100)), "closed_qty": str(q),
            "is_fully_closed": q == qty_by[event],
            "raw": clean({
                "source": "BingX exact close orders; logical shared-position allocation",
                "discord_finance_verified": verified_fee,
                "position_id": pos,
                "allocation_policy": "owner-first then oldest peer by Entry time and id",
                "exchange_owned_exit_qty": str(raw_qty_by_owner[event]),
                "allocated_closed_qty": str(q), "entry_qty": str(qty_by[event]),
                "all_known_v3_exits_qty": str(sum((v[3] for v in fills), Decimal(0))),
                "all_known_v3_entries_qty": str(sum(qty_by.values(), Decimal(0))),
                "exchange_position_closed": "NOT_PROVEN",
                "sources": alloc[event]["sources"],
            }),
        }
    return out


def reconcile_shared_positions(store, control, *, lookback_days: int = 7) -> dict:
    """Run AFTER original hourly projection; never write exchange or raw fills."""
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    entries, results = store.reconciliation_rows(since)
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for ex in entries:
        pos = _entry_position(ex)
        if pos is not None:
            groups[(str(ex.get("symbol")), str(ex.get("side")), pos)].append(ex)
    by_event: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_event[str(r["event_id"])].append(r)
    summary = {"groups_examined": 0, "groups_adjusted": 0, "finances_adjusted": 0, "unverified": 0}
    for (symbol, side, position_id), members in groups.items():
        if len(members) < 2:
            continue
        summary["groups_examined"] += 1
        events = {str(ex["event_id"]) for ex in members}
        try:
            allocation = allocate_group(members, [r for event in events for r in by_event[event]])
        except UnverifiableAllocation as exc:
            summary["unverified"] += 1
            summary.setdefault("unverified_events", []).extend(sorted(events))
            # No auto-modification from weak evidence. Existing raw fills remain intact.
            control.report("L4", "shared_position_allocation",
                           f"Không thể phân bổ đóng vị thế gộp {symbol} {side}: {exc}",
                           symbol=symbol, error_code="SHARED_POSITION_UNVERIFIED",
                           details={"position_id": position_id, "events": sorted(events)})
            continue
        if allocation:
            for row in allocation.values():
                store.upsert_finance(row)
                summary["finances_adjusted"] += 1
            summary["groups_adjusted"] += 1
    return summary
