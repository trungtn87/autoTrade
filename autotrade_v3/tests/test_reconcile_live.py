"""V3-04 offline-only evidence tests: no private APIs, no DB client."""
import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from autotrade_v3.data_backtest.reconcile_live import (
    REQUIRED, seal, load, reconcile, _compatible_states, _fill_match,
)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "export"
        self.out = Path(tmp.name) / "report"
        self.root.mkdir()
        self.link = {"entry_order_id":"entry1", "signal_id":"sig1", "symbol":"BTC-USDT",
                     "side":"BUY", "combo":"C15", "signal_time_ms":"1793145600000"}
        self.orders = [
            {"order_id":"entry1", "symbol":"BTC-USDT","side":"BUY","position_side":"LONG",
             "type":"MARKET","status":"FILLED","filled_qty":"1"},
            {"order_id":"sl1", "symbol":"BTC-USDT","side":"SELL","position_side":"LONG",
             "type":"STOP_MARKET","status":"FILLED","filled_qty":"1"},
            {"order_id":"tp1", "symbol":"BTC-USDT","side":"SELL","position_side":"LONG",
             "type":"TAKE_PROFIT_MARKET","status":"CANCELLED","filled_qty":"0"},
        ]
        self.fills = [
            {"trade_id":"te1","order_id":"entry1","symbol":"BTC-USDT","side":"BUY",
             "position_side":"LONG","qty":"1","price":"100","fee":"0.05",
             "realized_pnl":"0","timestamp_ms":"1793145660000"},
            {"trade_id":"ts1","order_id":"sl1","symbol":"BTC-USDT","side":"SELL",
             "position_side":"LONG","qty":"1","price":"99","fee":"0.05",
             "realized_pnl":"-1","timestamp_ms":"1793149260000"},
        ]
        self.state = {"exit_mode":"hard_tp", "protection":{"sl":{"order_id":"sl1"},
                     "tp":{"order_id":"tp1"}}}
        self.data = {
            "signal_links":[self.link],"bingx_orders":self.orders,"bingx_fills":self.fills,
            "bingx_income":[{"income_key":"i1","symbol":"BTC-USDT","income_type":"TRADING_FEE",
                             "amount":"-0.05","asset":"USDT","timestamp_ms":"1793145660000",
                             "trade_id":"does-not-match"}],
            "processed_targets":[{"event_id":"sig1","target":"bingx_state","payload":json.dumps(self.state)}],
        }

    def write(self):
        for name, required in REQUIRED.items():
            with (self.root/(name+".csv")).open("w",newline="",encoding="utf-8") as f:
                writer=csv.DictWriter(f,fieldnames=sorted(required));writer.writeheader()
                writer.writerows(self.data[name])
        seal(self.root,"2026-11-10T04:30:00Z")

    def report(self,**kwargs):
        self.write()
        report = reconcile(self.root,self.out,**kwargs)
        lines=[json.loads(x) for x in (self.out/"reconciled_evidence.jsonl").read_text().splitlines()]
        return report,lines

    def test_valid_exact_entry_and_child_not_auto_verified_profit(self):
        report,rows=self.report()
        self.assertEqual(report["total_signals"],1)
        self.assertEqual(report["has_entry_fill"],1)
        self.assertEqual(report["explicit_exit_mode"],1)
        self.assertEqual(report["verified_net_pnl_pair_count"],0)
        self.assertEqual(report["status"],"INCONCLUSIVE")
        self.assertEqual(rows[0]["evidence_status"],"EXIT_FILL_SUPPORTED_FINANCE_INCONCLUSIVE")
        self.assertNotIn("entry1",json.dumps(report))
        self.assertNotIn("entry1",json.dumps(rows))
        self.assertIn("INCOME_CANNOT_BE_LINKED_BY_TRADE_ID",rows[0]["reason_codes"])

    def test_absent_entry_fill_is_unresolved_even_if_status_filled(self):
        self.data["bingx_fills"].pop(0)
        _,rows=self.report()
        self.assertEqual(rows[0]["evidence_status"],"ENTRY_UNRESOLVED")
        self.assertIn("ENTRY_EXACT_FILL_MISSING",rows[0]["reason_codes"])

    def test_wrong_exit_type_is_unresolved(self):
        self.orders[1]["type"]="TAKE_PROFIT_MARKET"
        _,rows=self.report()
        self.assertIn("EXIT_ORDER_IDENTITY_INVALID",rows[0]["reason_codes"])
        self.assertEqual(rows[0]["evidence_status"],"EXIT_UNRESOLVED")

    def test_wrong_exit_position_side_is_unresolved(self):
        self.orders[1]["position_side"]="SHORT"
        _,rows=self.report()
        self.assertIn("EXIT_ORDER_IDENTITY_INVALID",rows[0]["reason_codes"])

    def test_missing_protection_owner_unresolved(self):
        self.state["protection"].pop("tp")
        self.data["processed_targets"][0]["payload"]=json.dumps(self.state)
        _,rows=self.report()
        self.assertIn("PROTECTION_CHILD_IDS_INCOMPLETE",rows[0]["reason_codes"])

    def test_shared_exit_child_between_combos_fails(self):
        link={**self.link,"entry_order_id":"entry2","signal_id":"sig2","combo":"C14"}
        self.data["signal_links"].append(link)
        self.data["bingx_orders"].append({**self.orders[0],"order_id":"entry2"})
        self.data["bingx_fills"].append({**self.fills[0],"order_id":"entry2","trade_id":"te2"})
        self.data["processed_targets"].append({"event_id":"sig2","target":"bingx_state","payload":json.dumps(self.state)})
        _,rows=self.report()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(x["evidence_status"]=="MERGED_OR_SHARED_UNRESOLVED" for x in rows))
        self.assertTrue(all("EXIT_ORDER_SHARED_BETWEEN_COMBOS" in x["reason_codes"] for x in rows))

    def test_distinct_child_ids_do_not_hide_same_side_overlap(self):
        # Even uniquely owned exits can be inside a shared BingX hedge container.
        self.data["signal_links"].append({**self.link,"entry_order_id":"entry2",
                                          "signal_id":"sig2","combo":"C14",
                                          "signal_time_ms":"1793149200000"})
        self.data["bingx_orders"].extend([
            {**self.orders[0],"order_id":"entry2"},
            {**self.orders[1],"order_id":"sl2"},
            {**self.orders[2],"order_id":"tp2"},
        ])
        self.data["bingx_fills"].extend([
            {**self.fills[0],"trade_id":"te2","order_id":"entry2",
             "timestamp_ms":"1793148000000"},
            {**self.fills[1],"trade_id":"ts2","order_id":"sl2",
             "timestamp_ms":"1793150000000"},
        ])
        state2={"exit_mode":"hard_tp","protection":{"sl":{"order_id":"sl2"},
                "tp":{"order_id":"tp2"}}}
        self.data["processed_targets"].append({"event_id":"sig2","target":"bingx_state",
                                               "payload":json.dumps(state2)})
        _,rows=self.report()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(x["evidence_status"]=="MERGED_OR_SHARED_UNRESOLVED" for x in rows))
        self.assertTrue(all("POTENTIAL_MERGED_POSITION_OVERLAP" in x["reason_codes"] for x in rows))

    def test_reused_child_id_for_sl_and_tp_is_invalid(self):
        self.state["protection"]["tp"]["order_id"]="sl1"
        self.data["processed_targets"][0]["payload"]=json.dumps(self.state)
        _,rows=self.report()
        self.assertIn("EXIT_PROTECTION_ORDER_REUSED_FOR_MULTIPLE_LEGS",rows[0]["reason_codes"])
        self.assertEqual(rows[0]["evidence_status"],"EXIT_UNRESOLVED")

    def test_holdout_membership_determined_without_future_looking(self):
        self.link["signal_time_ms"]="1793145600000"
        _,rows=self.report(holdout_start="2026-10-15")
        self.assertEqual(rows[0]["evaluation_set"],"HOLDOUT")
        _,rows=self.report(holdout_start="2026-10-29")
        self.assertEqual(rows[0]["evaluation_set"],"CALIBRATION")

    def test_state_conflict_blocks_exit(self):
        alt={**self.state,"exit_mode":"split_trailing"}
        self.data["processed_targets"].append({"event_id":"sig1","target":"bingx_account","payload":json.dumps(alt)})
        _,rows=self.report()
        self.assertIn("STATE_CONFLICT_EXIT_MODE",rows[0]["reason_codes"])
        self.assertEqual(rows[0]["evidence_status"],"ENTRY_UNRESOLVED")

    def test_state_fallback_if_only_account_has_mode(self):
        primary={"protection":self.state["protection"]}
        self.data["processed_targets"][0]["payload"]=json.dumps(primary)
        self.data["processed_targets"].append({"event_id":"sig1","target":"bingx_account","payload":json.dumps(self.state)})
        r,_=self.report()
        self.assertEqual(r["explicit_exit_mode"],1)

    def test_entry_fill_wrong_qty_blocks(self):
        self.fills[0]["qty"]="0.1"
        _,rows=self.report()
        self.assertIn("ENTRY_FILL_QTY_DIFFERS_FROM_ORDER",rows[0]["reason_codes"])

    def test_duplicate_trades_blocks(self):
        self.fills.append(dict(self.fills[0]))
        _,rows=self.report()
        self.assertIn("ENTRY_DUPLICATE_TRADE_ID",rows[0]["reason_codes"])

    def test_source_window_non_bingx_is_explicit(self):
        self.link["signal_time_ms"]="1792800000000" # outside Oct28 venue (but in dataset)
        _,rows=self.report()
        self.assertEqual(rows[0]["source_venue"],"BINANCE")
        self.assertIn("CROSS_VENUE_PRICE_SERIES",rows[0]["reason_codes"])

    def test_november_live_event_is_outside_historical_window(self):
        self.link["signal_time_ms"]="1794000000000"
        report,rows=self.report()
        self.assertEqual(report["outside_historical_M1"],1)
        self.assertEqual(rows[0]["source_venue"],"NO_M1")

    def test_unmatched_scenario_is_not_linked_by_nearest_time(self):
        sim={"symbol":"BTC-USDT","combo":"C15","side":"BUY","signal_close_ms":1793145600001,
             "scenario_quality":"SIMULATED","closed_in_both":True}
        p=Path(self.root.parent)/"scenario.jsonl"
        p.write_text(json.dumps(sim)+"\n")
        _,rows=self.report(scenario_hard=p)
        self.assertFalse(rows[0]["exact_sim_match"])
        self.assertIn("EXACT_SIM_SIGNAL_NOT_MATCHED",rows[0]["reason_codes"])

    def test_duplicate_scenario_event_is_rejected(self):
        sim={"symbol":"BTC-USDT","combo":"C15","side":"BUY","signal_close_ms":1793145600000,
             "scenario_quality":"SIMULATED","closed_in_both":True}
        p=Path(self.root.parent)/"scenario.jsonl"
        p.write_text(json.dumps(sim)+"\n"+json.dumps(sim)+"\n")
        report,rows=self.report(scenario_hard=p)
        self.assertEqual(report["duplicate_sim_records"],1)
        self.assertFalse(rows[0]["exact_sim_match"])

    def test_manifest_tampering_blocks(self):
        self.write()
        with (self.root/"bingx_fills.csv").open("a") as f:f.write("\n")
        with self.assertRaisesRegex(ValueError,"checksum mismatch"):
            load(self.root)

    def test_non_utc_seal_rejected(self):
        self.write()
        with self.assertRaisesRegex(ValueError,"UTC"):
            seal(self.root,"2026-11-10T11:30:00+07:00")

    def test_robust_payload_sanitization(self):
        state,_=_compatible_states([("bingx_state",{"exit_mode":"hard_tp","protection":None}),
                                    ("bingx_account",{"protection":{"sl":{"order_id":"1"}}})])
        self.assertEqual(state["exit_mode"],"hard_tp")
        self.assertIsInstance(state["protection"],dict)

if __name__=="__main__": unittest.main()
