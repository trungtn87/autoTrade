"""V3-04B deterministic offline evidence/anti-misattribution regression suite."""
import copy
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from autotrade_v3.data_backtest.verify_bingx_exits import (
    V2_FILES, EXCHANGE_FILES, OPTIONAL_EXCHANGE, _sha_file, _load_exchange,
    _state_for, reconcile, seal_exchange,
)


class ExitLineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.v2 = base / "v2"
        self.ex = base / "ex"
        self.out = base / "out"
        self.v2.mkdir()
        self.ex.mkdir()
        self.entry_id = "90071992547409998765"  # > JS Number.MAX_SAFE_INTEGER
        self.event_id = "BTC-USDT|1h|C15|SELL|1793145599999"
        self.v2rows = {
            "signal_links": [dict(entry_order_id=self.entry_id, signal_id=self.event_id,
                                  symbol="BTC-USDT", side="SELL", combo="C15",
                                  signal_time_ms="1793145599999")],
            "bingx_orders": [dict(order_id=self.entry_id, symbol="BTC-USDT", side="SELL",
                                  position_side="SHORT", type="MARKET", status="FILLED",
                                  filled_qty="1", position_id="p1")],
            "bingx_fills": [],
            "bingx_income": [],
            "processed_targets": [dict(event_id=self.event_id, target="bingx_state",
                                       payload="")],
        }
        self.state = dict(exit_mode="hard_tp", position_id="p1", shared_position=False,
                          merged_reconcile={"shared": None}, protection={
                              "sl": {"order_id": "sl1"}, "tp": {"order_id": "tp1"}})
        self.exrows = {
            "orders": [
                self.order(self.entry_id, "SELL", "MARKET", "FILLED", "1"),
                self.order("sl1", "BUY", "STOP_MARKET", "FILLED", "1", trigger="child1"),
                self.order("tp1", "BUY", "TAKE_PROFIT_MARKET", "CANCELLED", "0"),
                self.order("child1", "BUY", "MARKET", "FILLED", "1", main="sl1"),
            ],
            "fills": [self.fill("entrytrade", self.entry_id, "SELL", "1", "1793145600000"),
                      self.fill("exittrade", "child1", "BUY", "1", "1793145660000")],
            "explicit_links": [dict(symbol="BTC-USDT", parent_order_id="sl1",
                                    child_order_id="child1", proof_source="BINGX_ORDER_DETAIL",
                                    proof_sha256="", proof_path="proofs/sl1.json")],
        }

    def order(self, oid, side, typ, status, qty, *, trigger="", main="", pos="p1"):
        return dict(symbol="BTC-USDT", order_id=oid, side=side, position_side="SHORT",
                    type=typ, status=status, position_id=pos, filled_qty=qty,
                    trigger_order_id=trigger, main_order_id=main, working_type="MARK_PRICE")

    def fill(self, tid, oid, side, qty, ts):
        return dict(symbol="BTC-USDT", trade_id=tid, order_id=oid, side=side,
                    position_side="SHORT", qty=qty, price="100", fee="-0.05",
                    realized_pnl="1.00", timestamp_ms=ts)

    def write(self):
        proofdir = self.ex / "proofs"
        proofdir.mkdir(exist_ok=True)
        raw = json.dumps({"data":{"orderID":"sl1","triggerOrderId":"child1"}}).encode()
        (proofdir / "sl1.json").write_bytes(raw)
        if "explicit_links" in self.exrows:
            if not self.exrows["explicit_links"][0]["proof_sha256"]:
                self.exrows["explicit_links"][0]["proof_sha256"] = hashlib.sha256(raw).hexdigest()
        self.v2rows["processed_targets"][0]["payload"] = json.dumps(self.state)
        for root, rows, required, optional in (
            (self.v2, self.v2rows, V2_FILES, {}),
            (self.ex, self.exrows, EXCHANGE_FILES, OPTIONAL_EXCHANGE),
        ):
            files = {}
            for name, records in rows.items():
                with (root / (name+".csv")).open("w", newline="", encoding="utf-8") as f:
                    headers = sorted((required | optional)[name])
                    w = csv.DictWriter(f, fieldnames=headers)
                    w.writeheader()
                    w.writerows(records)
                files[name] = {"sha256": _sha_file(root / (name+".csv")), "rows": len(records)}
            if root == self.v2:
                (root / "manifest-v3-04.json").write_text(json.dumps({
                    "schema_version": 1, "read_only": True, "files": files}))
            else:
                seal_exchange(root, "2026-11-10T04:00:00Z")

    def audit(self, exchange=True):
        self.write()
        report = reconcile(self.v2, self.ex if exchange else None, self.out)
        rows = [json.loads(line) for line in (self.out/"exit_lineage.jsonl").read_text().splitlines()]
        return report, rows

    def test_exact_parent_trigger_child_and_fill_supported_but_net_unverified(self):
        report, rows = self.audit()
        self.assertEqual(rows[0]["status"], "EXIT_FILL_SUPPORTED_FINANCE_UNRESOLVED")
        self.assertEqual(rows[0]["matched_exit_fill_trades"], 1)
        self.assertTrue(rows[0]["full_exit_qty_supported"])
        self.assertIsNone(rows[0]["net_pnl_verified"])
        self.assertIsNone(rows[0]["roi_verified"])
        self.assertEqual(report["verified_net_pnl_pairs"], 0)
        self.assertEqual(report["status"], "INCONCLUSIVE")
        self.assertNotIn(self.entry_id, json.dumps(rows))
        self.assertNotIn(self.event_id, json.dumps(rows))

    def test_missing_exchange_export_remains_unresolved(self):
        report, rows = self.audit(exchange=False)
        self.assertFalse(report["exchange_export_present"])
        self.assertFalse(rows[0]["full_exit_qty_supported"])
        self.assertEqual(report["verified_net_pnl_pairs"], 0)

    def test_trigger_id_alone_is_insufficient(self):
        self.exrows.pop("explicit_links")
        _, rows = self.audit()
        self.assertIn("TRIGGER_PARENT_CHILD_PROOF_MISSING", rows[0]["reason_codes"])
        self.assertEqual(rows[0]["status"], "EXIT_LINEAGE_UNRESOLVED")

    def test_bad_proof_digest_rejected(self):
        self.exrows["explicit_links"][0]["proof_sha256"]="notsha256"
        with self.assertRaisesRegex(ValueError,"checksum mismatch"):
            self.write()

    def test_proof_original_json_tamper_rejected(self):
        self.write()
        path=self.ex/"proofs"/"sl1.json"
        path.write_text(json.dumps({"data":{"orderID":"sl1","triggerOrderId":"wrong"}}))
        with self.assertRaisesRegex(ValueError,"disagree"):
            _load_exchange(self.ex)

    def test_proof_path_traversal_rejected(self):
        self.exrows["explicit_links"][0]["proof_path"]="../private.json"
        with self.assertRaisesRegex(ValueError,"proof_path"):
            self.write()

    def test_no_exchange_child_order_rejected(self):
        self.exrows["orders"].pop()
        _, rows=self.audit()
        self.assertIn("TRIGGER_CHILD_ORDER_NOT_EXPORTED",rows[0]["reason_codes"])

    def test_child_wrong_position_id_rejected(self):
        self.exrows["orders"][3]["position_id"]="p2"
        _, rows=self.audit()
        self.assertIn("TRIGGER_CHILD_IDENTITY_CONFLICT",rows[0]["reason_codes"])

    def test_child_wrong_position_side_rejected(self):
        self.exrows["orders"][3]["position_side"]="LONG"
        _, rows=self.audit()
        self.assertIn("TRIGGER_CHILD_IDENTITY_CONFLICT",rows[0]["reason_codes"])

    def test_child_fill_wrong_side_rejected(self):
        self.exrows["fills"][1]["side"]="SELL"
        _, rows=self.audit()
        self.assertIn("EXIT_FILL_SIDE_POSITION_MISMATCH",rows[0]["reason_codes"])
        self.assertFalse(rows[0]["full_exit_qty_supported"])

    def test_partial_close_not_promoted(self):
        self.exrows["orders"][3]["filled_qty"]="0.5"
        self.exrows["fills"][1]["qty"]="0.5"
        _, rows=self.audit()
        self.assertIn("EXIT_QUANTITY_CANNOT_PROVE_FULL_CLOSE",rows[0]["reason_codes"])
        self.assertFalse(rows[0]["full_exit_qty_supported"])

    def test_entry_fill_missing_does_not_promote(self):
        self.exrows["fills"].pop(0)
        _, rows=self.audit()
        self.assertEqual(rows[0]["status"],"ENTRY_EVIDENCE_UNRESOLVED")

    def test_duplicate_exit_trade_id_rejected(self):
        self.exrows["fills"].append(copy.deepcopy(self.exrows["fills"][1]))
        _, rows=self.audit()
        self.assertIn("EXIT_DUPLICATE_OR_EMPTY_TRADE_ID",rows[0]["reason_codes"])

    def test_no_time_proximity_fake_link(self):
        self.exrows["fills"][1]["order_id"]="unrelated"
        _, rows=self.audit()
        self.assertIn("EXIT_NO_EXACT_EXECUTION_FILL", rows[0]["reason_codes"])

    def test_fill_before_entry_rejected(self):
        self.exrows["fills"][1]["timestamp_ms"]="1793145590000"
        _, rows=self.audit()
        self.assertIn("EXIT_EXIT_FILL_BEFORE_ENTRY_OR_INVALID_TIME",rows[0]["reason_codes"])

    def test_shared_position_blocks_even_with_perfect_child(self):
        self.state["shared_position"]=True
        _, rows=self.audit()
        self.assertEqual(rows[0]["status"],"MERGED_POSITION_UNRESOLVED")
        self.assertFalse(rows[0]["full_exit_qty_supported"])

    def test_empty_merged_reconcile_does_not_false_flag(self):
        _, rows=self.audit()
        self.assertNotIn("V2_SHARED_OR_MERGED_POSITION",rows[0]["reason_codes"])

    def test_reused_position_id_by_two_signals_blocks(self):
        link2 = dict(self.v2rows["signal_links"][0], signal_id="sig2",combo="C14",
                     entry_order_id="entry2")
        self.v2rows["signal_links"].append(link2)
        self.v2rows["bingx_orders"].append(dict(self.v2rows["bingx_orders"][0],order_id="entry2"))
        self.exrows["orders"].append(self.order("entry2","SELL","MARKET","FILLED","1"))
        self.exrows["fills"].append(self.fill("entrytrade2","entry2","SELL","1","1793145600000"))
        self.v2rows["processed_targets"].append(dict(self.v2rows["processed_targets"][0],event_id="sig2",payload=json.dumps(self.state)))
        _, rows=self.audit()
        self.assertTrue(all(x["status"]=="MERGED_POSITION_UNRESOLVED" for x in rows))
        self.assertTrue(all(not x["full_exit_qty_supported"] for x in rows))

    def test_original_checksum_tamper_rejected(self):
        self.write()
        with (self.ex/"orders.csv").open("a") as f:
            f.write("\n")
        with self.assertRaisesRegex(ValueError,"checksum mismatch"):
            _load_exchange(self.ex)

    def test_manifest_does_not_claim_pagination_complete(self):
        self.write()
        m=json.loads((self.ex/"manifest-v3-04b.json").read_text())
        self.assertIs(m["complete_history_attested"],False)

    def test_invalid_timestamp_timezone_rejected(self):
        self.write()
        with self.assertRaisesRegex(ValueError,"UTC"):
            seal_exchange(self.ex,"2026-11-10T11:00:00+07:00")

    def test_output_inside_export_rejected(self):
        self.write()
        with self.assertRaisesRegex(ValueError,"outside"):
            reconcile(self.v2,self.ex,self.v2/"reports")

    def test_no_http_or_exchange_clients_in_module(self):
        from autotrade_v3.data_backtest import verify_bingx_exits as m
        code=Path(m.__file__).read_text()
        for danger in ("import requests", "import httpx", "import ccxt", "urllib.request", "import supabase", "API_SECRET"):
            self.assertNotIn(danger,code)


if __name__=="__main__":
    unittest.main()