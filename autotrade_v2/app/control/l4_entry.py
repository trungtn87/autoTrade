"""Read-only lookup of an entry order's state by client order id.

Used by layer L4 only. This module never places, modifies or cancels orders:
its sole network-facing calls are ``find_order_by_client_id`` and
``extract_order`` on the injected client, which is accepted by duck typing.
"""
from __future__ import annotations

from typing import Any


class L4EntryReader:
    def __init__(self, client: Any):
        self.client = client

    def get_entry(self, symbol: str, client_order_id: str) -> dict:
        try:
            payload = self.client.find_order_by_client_id(symbol, client_order_id)
        except Exception as exc:
            return self._result("UNKNOWN", client_order_id, error=str(exc))
        if payload is None:
            return self._result("ABSENT", client_order_id)
        try:
            order = self.client.extract_order(payload)
        except Exception as exc:
            return self._result("UNKNOWN", client_order_id, error=str(exc))
        return {
            "status": "FOUND",
            "client_order_id": client_order_id,
            "order_id": _to_str(order.get("orderId")),
            "executed_qty": _to_float(order.get("executedQty")),
            "avg_price": _to_float(order.get("avgPrice")),
            "error": None,
        }

    @staticmethod
    def _result(status: str, client_order_id: str, error: str | None = None) -> dict:
        return {
            "status": status,
            "client_order_id": client_order_id,
            "order_id": None,
            "executed_qty": None,
            "avg_price": None,
            "error": error,
        }


def _to_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
