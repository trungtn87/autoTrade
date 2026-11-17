"""Offline safety tests for the manual BingX ETH backfill action."""
from __future__ import annotations

import unittest

from scripts.backfill_v3_eth_m15 import STEP, HistoryWindowEmpty, extract, fetch, validate_existing


class BackfillGuardTests(unittest.TestCase):
    def test_existing_window_must_be_contiguous(self):
        validate_existing(3, 100 * STEP, 102 * STEP)
        with self.assertRaisesRegex(RuntimeError, "gaps"):
            validate_existing(2, 100 * STEP, 102 * STEP)

    def test_cannot_backfill_without_anchor(self):
        with self.assertRaisesRegex(RuntimeError, "No existing"):
            validate_existing(0, None, None)

    def test_exact_thousand_candles_only(self):
        first = 100 * STEP
        candles = [
            {"openTime": first + i * STEP, "open": "100", "high": "110", "low": "95",
             "close": "105", "volume": "4.25"}
            for i in reversed(range(1000))
        ]
        out = extract({"data": candles}, first, 1000)
        self.assertEqual(len(out), 1000)
        self.assertEqual(out[0][2], first)
        self.assertEqual(out[-1][2], first + 999 * STEP)
        self.assertEqual(out[0][-1], first + STEP - 1)

    def test_short_or_misaligned_response_fails_closed(self):
        first = 100 * STEP
        candle = [first, "100", "110", "95", "105", "4.25", first + STEP - 1]
        with self.assertRaisesRegex(RuntimeError, "window mismatch"):
            extract({"data": [candle]}, first, 2)

    def test_short_contiguous_suffix_is_accepted_only_explicitly(self):
        first = 100 * STEP
        candles = [
            [first + i * STEP, "100", "110", "95", "105", "4.25", first + (i + 1) * STEP - 1]
            for i in range(806, 1000)
        ]
        rows = extract({"data": list(reversed(candles))}, first, 1000, allow_suffix=True)
        self.assertEqual(len(rows), 194)
        self.assertEqual(rows[0][2], first + 806 * STEP)
        self.assertEqual(rows[-1][2], first + 999 * STEP)

    def test_short_prefix_or_hole_is_not_accepted(self):
        first = 100 * STEP
        candle = [first, "100", "110", "95", "105", "4.25", first + STEP - 1]
        with self.assertRaisesRegex(RuntimeError, "window mismatch"):
            extract({"data": [candle]}, first, 2, allow_suffix=True)
        last = first + 999 * STEP
        end = [last, "100", "110", "95", "105", "4.25", last + STEP - 1]
        previous = [last - 2 * STEP, "100", "110", "95", "105", "4.25", last - STEP - 1]
        with self.assertRaisesRegex(RuntimeError, "window mismatch"):
            extract({"data": [previous, end]}, first, 1000, allow_suffix=True)

    def test_empty_history_is_reported_not_inserted(self):
        with self.assertRaises(HistoryWindowEmpty):
            extract({"data": []}, 100 * STEP, 1000, allow_suffix=True)

    def test_fetch_uses_strict_suffix_validation(self):
        first = 100 * STEP
        last = first + 999 * STEP
        candle = [last, "100", "110", "95", "105", "4.25", last + STEP - 1]

        class Response:
            status_code = 200
            def json(self):
                return {"code": 0, "data": [candle]}

        class Client:
            def get(self, endpoint, params):
                self.params = params
                return Response()

        client = Client()
        rows = fetch(client, first, 1000, attempts=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], last)
        self.assertEqual(client.params["limit"], 1000)

    def test_invalid_ohlcv_rejected(self):
        first = 100 * STEP
        candle = [first, "100", "80", "95", "105", "4.25", first + STEP - 1]
        with self.assertRaisesRegex(RuntimeError, "Invalid price"):
            extract({"data": [candle]}, first, 1)

    def test_duplicate_rejected(self):
        first = 100 * STEP
        candle = [first, "100", "110", "95", "105", "4.25", first + STEP - 1]
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            extract({"data": [candle, candle]}, first, 2)


if __name__ == "__main__":
    unittest.main()
