"""L4 only: read and verify the exact BingX MARKET Entry just submitted by L3.

No trading mutation, no historical sibling scans, no position admission policy.
An absent or ambiguous GET is *not* proof that a POST did not reach BingX.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ..contracts import TradeIntent


@dataclass(frozen=True)
class EntryEvidence:
    state: str  # FOUND / ABSENT / UNKNOWN
    event_id: str
    client_order_id: str
    order_id: str | None = None
    exchange_status: str | None = None
    executed_qty: Decimal | None = None
    avg_price: Decimal | None = None
    position_id: str | None = None
    raw_response: dict | None = None
    reason: str | None = None


def _decimal(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("invalid finite, nonnegative BingX quantity/price")
    return number


class EntryReader:
    """The only L4 GET needed for the current Entry. Never POST or DELETE."""

    def __init__(self, client: Any):
        self.client = client

    def read(self, intent: TradeIntent, client_order_id: str) -> EntryEvidence:
        base = dict(event_id=intent.event_id, client_order_id=client_order_id)
        try:
            payload = self.client.find_order_by_client_id(
                intent.symbol, client_order_id
            )
        except Exception as exc:
            return EntryEvidence("UNKNOWN", **base, reason=f"GET failed: {type(exc).__name__}")
        if payload is None:
            return EntryEvidence("ABSENT", **base)
        try:
            order = self.client.extract_order(payload)
            if not isinstance(order, dict):
                raise ValueError("order payload is not an object")
            observed_cid = str(order.get("clientOrderId") or order.get("clientOrderID") or "")
            order_id = str(order.get("orderId") or order.get("orderID") or "")
            expected_position_side = "LONG" if intent.side.upper() == "BUY" else "SHORT"
            if (
                observed_cid != client_order_id
                or not order_id
                or str(order.get("symbol") or "") != intent.symbol
                or str(order.get("side") or "").upper() != intent.side.upper()
                or str(order.get("positionSide") or "").upper() != expected_position_side
                or str(order.get("type") or "").upper() != "MARKET"
            ):
                raise ValueError("Entry GET identity mismatch")
            status = str(order.get("status") or "").upper()
            if status not in {
                "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "CANCELLED",
                "EXPIRED", "REJECTED",
            }:
                raise ValueError("Entry status unrecognized")
            executed = _decimal(order.get("executedQty") or "0")
            average = _decimal(order.get("avgPrice") or "0")
            position_id = str(order.get("positionId") or order.get("positionID") or "") or None
            return EntryEvidence(
                "FOUND", **base, order_id=order_id, exchange_status=status,
                executed_qty=executed, avg_price=average,
                position_id=position_id, raw_response=payload,
            )
        except (ValueError, TypeError, InvalidOperation, KeyError) as exc:
            return EntryEvidence("UNKNOWN", **base, reason=f"Invalid GET proof: {exc}")
