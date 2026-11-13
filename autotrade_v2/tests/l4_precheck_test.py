"""Offline tests for the entry pre-check decision built on L4EntryReader; no network access."""
from __future__ import annotations

import unittest

from app.control.l4_entry import L4EntryReader


class _Client:
    def __init__(self, lookup=None, exc=None):
        self._lookup = lookup
        self._exc = exc

    def find_order_by_client_id(self, symbol, client_order_id):
        if self._exc is not None:
            raise self._exc
        return self._lookup

    @staticmethod
    def extract_order(payload):
        return {"orderId": payload["orderId"], "executedQty": "0", "avgPrice": "0"}


class L4PrecheckTest(unittest.TestCase):
    CID = "entry-precheck-1"

    def test_found_returns_status_and_raw_payload(self):
        payload = {"orderId": 42, "executedQty": "1", "avgPrice": "10"}
        result = L4EntryReader(_Client(lookup=payload)).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["raw"], payload)

    def test_absent_returns_absent(self):
        result = L4EntryReader(_Client(lookup=None)).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["status"], "ABSENT")
        self.assertIsNone(result["raw"])

    def test_exception_is_unknown_not_absent(self):
        result = L4EntryReader(_Client(exc=TimeoutError("boom"))).get_entry("BTC-USDT", self.CID)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertNotEqual(result["status"], "ABSENT")
        self.assertIsNone(result["raw"])


if __name__ == "__main__":
    unittest.main()
