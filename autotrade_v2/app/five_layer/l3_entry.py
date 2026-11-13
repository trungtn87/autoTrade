"""Small L3 Entry execution path for the clean five-layer implementation.

One signal -> at most one MARKET POST. No sibling checks, legacy gates,
historical recovery scans, merged-position admission or forced market close.

The caller owns the decision to route live signals here. This module does not
install itself into the current production application.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from typing import Any, Protocol

from ..contracts import TradeIntent
from .l4_entry import EntryEvidence, EntryReader


ENTRY_NOTIONAL_USDT = Decimal("100")
ENTRY_LEVERAGE = 50


class EntryLedger(Protocol):
    """Durable storage. A claim must be unique and committed *before* POST."""

    def claim(self, intent: TradeIntent, client_order_id: str) -> bool: ...
    def mark_sending(self, event_id: str, client_order_id: str) -> None: ...
    def record(self, event_id: str, evidence: EntryEvidence, status: str) -> None: ...


@dataclass(frozen=True)
class EntryOutcome:
    event_id: str
    status: str
    client_order_id: str
    order_id: str | None = None
    executed_qty: Decimal | None = None
    avg_price: Decimal | None = None
    position_id: str | None = None
    reason: str | None = None


def _cid(event_id: str) -> str:
    return "v5e-" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:32]


class SimpleL3Entry:
    """Submit a new Entry or reconcile that same signal by exact CID only."""

    def __init__(self, client: Any, ledger: EntryLedger, l4: EntryReader | None = None):
        self.client = client
        self.ledger = ledger
        self.l4 = l4 or EntryReader(client)

    def execute(self, intent: TradeIntent) -> EntryOutcome:
        cid = _cid(intent.event_id)
        claimed = self.ledger.claim(intent, cid)
        if not claimed:
            # An earlier POST might have reached BingX: never POST this CID twice.
            return self.reconcile(intent)
        if intent.side.upper() not in {"BUY", "SELL"}:
            raise ValueError("invalid Entry side")
        if not Decimal(str(intent.entry)).is_finite() or intent.entry <= 0:
            raise ValueError("invalid Entry reference price")
        qty = round(float(ENTRY_NOTIONAL_USDT) / float(intent.entry), 4)
        if qty <= 0:
            raise ValueError("Entry quantity rounded to zero")
        # Persist SENDING before issuing the sole MARKET mutation.
        self.ledger.mark_sending(intent.event_id, cid)
        send_error = None
        try:
            self.client.place_market_entry(
                intent.symbol, intent.side, qty, ENTRY_LEVERAGE,
                client_order_id=cid,
            )
        except Exception as exc:
            # Response loss does not prove the POST failed. L4 must GET exact CID.
            send_error = f"{type(exc).__name__}: {exc}"
        return self.reconcile(intent, send_error=send_error)

    def reconcile(self, intent: TradeIntent, *, send_error: str | None = None) -> EntryOutcome:
        """L4 is the only GET owner. This method never retries MARKET."""
        cid = _cid(intent.event_id)
        evidence = self.l4.read(intent, cid)
        status = (
            "ENTRY_FILLED" if evidence.state == "FOUND" and evidence.exchange_status == "FILLED"
            else "ENTRY_PARTIAL" if evidence.state == "FOUND" and evidence.exchange_status == "PARTIALLY_FILLED"
            else "ENTRY_NOT_FILLED" if evidence.state == "FOUND" and evidence.exchange_status in {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}
            else "ENTRY_PENDING"
        )
        # ABSENT after possible POST is NOT permission to POST again.
        reason = evidence.reason or send_error or (
            "Exact Entry not observed yet; outcome unknown" if evidence.state == "ABSENT"
            else None
        )
        self.ledger.record(intent.event_id, evidence, status)
        return EntryOutcome(
            intent.event_id, status, cid, order_id=evidence.order_id,
            executed_qty=evidence.executed_qty, avg_price=evidence.avg_price,
            position_id=evidence.position_id, reason=reason,
        )
