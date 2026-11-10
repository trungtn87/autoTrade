"""V3-00 offline audit regression tests (stdlib; no network)."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from autotrade_v3.audit import audit, seal

SCHEMA = {
    "signal_links": ["entry_order_id", "signal_id", "symbol", "side", "combo", "signal_time_ms"],
    "bingx_orders": ["order_id", "symbol", "side", "position_side", "type", "filled_qty"],
    "bingx_fills": ["trade_id", "order_id", "symbol", "side", "position_side", "qty"],
    "trade_results": ["event_id", "payload"],
    "ai_reviews": ["event_id", "symbol"],
    "v2_states": ["event_id", "payload"],
    "processed_targets": ["event_id", "target", "payload"],
}


class EvidenceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows = {
            "signal_links": [
                dict(entry_order_id="e1", signal_id="s1", symbol="BTC-USDT", side="BUY",
                     combo="C15", signal_time_ms="1790334899999")
            ],
            "bingx_orders": [
                dict(order_id="e1", symbol="BTC-USDT", side="BUY", position_side="LONG",
                     type="MARKET", filled_qty="1")
            ],
            "bingx_fills": [
                dict(trade_id="t1", order_id="e1", symbol="BTC-USDT", side="BUY",
                     position_side="LONG", qty="1")
            ],
        }

    def _write(self):
        for name, rows in self.rows.items():
            with (self.root / (name + ".csv")).open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SCHEMA[name])
                writer.writeheader()
                writer.writerows(rows)
        seal(self.root, "2026-11-10T00:00:00Z")

    def test_entry_fill_is_not_exit_verification(self):
        self._write()
        result = audit(self.root)
        self.assertEqual(result["signal_link_rows"], 1)
        self.assertEqual(result["entry_fill_supported"], 1)
        self.assertEqual(result["exits_verified"], 0)
        self.assertEqual(result["exit_mode_missing_rows"], 1)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("trade_results", result["missing_optional_tables"])

    def test_multiple_entries_for_signal_not_chosen(self):
        self.rows["signal_links"].append(dict(self.rows["signal_links"][0],
                                             entry_order_id="e2"))
        self._write()
        result = audit(self.root)
        self.assertEqual(result["entry_status_counts"]["MULTIPLE_LINKED_ENTRIES_UNRESOLVED"], 2)
        self.assertEqual(result["entry_fill_supported"], 0)

    def test_wrong_position_side_fails(self):
        self.rows["bingx_orders"][0]["position_side"] = "SHORT"
        self._write()
        result = audit(self.root)
        self.assertEqual(result["entry_status_counts"]["ENTRY_POSITION_SIDE_MISMATCH"], 1)

    def test_no_exact_fill_does_not_pass(self):
        self.rows["bingx_fills"] = []
        self._write()
        self.assertEqual(audit(self.root)["entry_status_counts"]["ENTRY_FILL_MISSING"], 1)

    def test_partial_fill_is_not_complete(self):
        self.rows["bingx_fills"][0]["qty"] = "0.7"
        self._write()
        self.assertEqual(audit(self.root)["entry_status_counts"]["ENTRY_FILL_QTY_MISMATCH"], 1)

    def test_closed_trade_result_is_only_candidate_not_verified(self):
        self.rows["trade_results"] = [
            {"event_id": "s1", "payload": json.dumps({
                "status": "CLOSED", "exit_mode": "SPLIT_TP_TRAILING",
                "pnl": 100, "money_verified": True, "api_key": "should-not-leak"
            })}
        ]
        self._write()
        report = audit(self.root)
        self.assertEqual(report["exit_mode_missing_rows"], 0)
        self.assertEqual(report["closed_result_candidates_unverified"], 1)
        self.assertEqual(report["exits_verified"], 0)
        self.assertNotIn("should-not-leak", json.dumps(report))
        self.assertNotIn("100", json.dumps(report))

    def test_exit_mode_from_processed_targets_is_recognized_but_not_verified(self):
        self.rows["processed_targets"] = [
            {"event_id": "s1", "target": "bingx_state",
             "payload": json.dumps({"exit_mode": "SPLIT_TP_TRAILING",
                                    "protection": {"tp": {"order_id": "p1"}}})}
        ]
        self._write()
        report = audit(self.root)
        self.assertEqual(report["exit_mode_missing_rows"], 0)
        self.assertEqual(report["exits_verified"], 0)
        self.assertIn("trade_results", report["missing_optional_tables"])

    def test_ai_review_no_link_is_not_no_trade_claim(self):
        self.rows["ai_reviews"] = [{"event_id": "s2", "symbol": "ETH-USDT"}]
        self._write()
        self.assertEqual(audit(self.root)["ai_reviews_without_confirmed_entry_link"], 1)

    def test_checksum_tampering_rejected(self):
        self._write()
        with (self.root / "bingx_orders.csv").open("a") as f:
            f.write("\n")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            audit(self.root)

    def test_missing_required_export_and_bad_timestamp_rejected(self):
        self._write()
        with self.assertRaisesRegex(ValueError, "UTC"):
            seal(self.root, "2026-11-10T07:00:00+07:00")
        (self.root / "bingx_fills.csv").unlink()
        with self.assertRaisesRegex(ValueError, "required export missing"):
            seal(self.root, "2026-11-10T00:00:00Z")

    def test_manifest_rows_mismatch(self):
        self._write()
        m = json.loads((self.root / "manifest.json").read_text())
        m["files"]["bingx_orders"]["rows"] = 999
        (self.root / "manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "row count mismatch"):
            audit(self.root)


if __name__ == "__main__":
    unittest.main()
