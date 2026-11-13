"""Offline tests for the read-only L4 entry reader; no network access."""
from __future__ import annotations

import unittest

from app.control.l4_entry import L4EntryReader


class FakeClient:
    """Records every attribute access made through the public interface."""

    def __init__(self, lookup=None, lookup_exc=None):
        object.__setattr__(self, "_accessed", [])
        object.__setattr__(self, "_lookup", lookup)
        object.__setattr__(self, "_lookup_exc", lookup_exc)

    def __getattribute__(self, name):
        if not name.startswith("_"):
            object.__getattribute__(self, "_accessed").append(name)
        return object.__getattribute__(self, name)

    def find_order_by_client_id(self, symbol, client_order_id):
        exc = self._lookup_exc
        if exc is not None:
            raise exc
        return self._lookup

    @staticmethod
    def extract_order(payload):
        return {
            "orderId": payload["orderId"],
            "executedQty": payload["executedQty"],
            "avgPrice": payload["avgPrice"],
        }

    def _recorded(self):
        return set(object.__getattribute__(self, "_accessed"))


class L4EntryReaderTest(unittest.TestCase):
    CID = "entry-abc-123"

    def test_found_copies_order_fields(self):
        client = FakeClient(lookup={"orderId": 987654, "executedQty": "1.5", "avgPrice": "100.25"})
        result = L4EntryReader(client).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["client_order_id"], self.CID)
        self.assertEqual(result["order_id"], "987654")
        self.assertEqual(result["executed_qty"], 1.5)
        self.assertEqual(result["avg_price"], 100.25)
        self.assertIsNone(result["error"])

    def test_found_raw_is_original_payload(self):
        payload = {"orderId": 987654, "executedQty": "1.5", "avgPrice": "100.25"}
        result = L4EntryReader(FakeClient(lookup=payload)).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["raw"], payload)

    def test_none_lookup_is_absent(self):
        result = L4EntryReader(FakeClient(lookup=None)).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["status"], "ABSENT")
        self.assertEqual(result["client_order_id"], self.CID)
        self.assertIsNone(result["order_id"])
        self.assertIsNone(result["error"])

    def test_exception_is_unknown_never_absent(self):
        client = FakeClient(lookup_exc=RuntimeError("timeout"))
        result = L4EntryReader(client).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("timeout", result["error"])
        self.assertNotEqual(result["status"], "ABSENT")
        self.assertIsNone(result["order_id"])

    def test_only_lookup_and_extract_are_used(self):
        client = FakeClient(lookup={"orderId": 1, "executedQty": 0, "avgPrice": 0})
        L4EntryReader(client).get_entry("BTC-USDT", self.CID)
        self.assertEqual(client._recorded(), {"find_order_by_client_id", "extract_order"})


if __name__ == "__main__":
    unittest.main()
