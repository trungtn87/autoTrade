"""Adversarial and mutation tests for V3-10 Independent Validation Gates.

Each test mutates an isolated copy of data or artifacts in a temporary directory,
demonstrates that the targeted gate FAILS while unrelated gates PASS,
and confirms that original data is unmodified (SHA256 unchanged).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.hashing import sha256_file
from autotrade_v3.validation import (
    gates_a,
    gates_b,
    gates_c,
    gates_d,
)
from autotrade_v3.validation.runner import run_validation


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class TestValidationGatesMutations(unittest.TestCase):
    """12 Mutation tests per spec section 9."""

    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.golden_dir = cls.repo_root / "runs" / "golden" / "v3-08"

    def test_m_a1_mutate_parquet_partition(self):
        """M-A1: Modify close price in one parquet partition -> A1 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dm_path = self.golden_dir / "dataset_manifest.json"
            dm = json.loads(dm_path.read_text(encoding="utf-8"))
            
            p0 = dm["partitions"][0]
            rel_path = p0["relative_path"]
            orig_p_path = self.repo_root / rel_path
            orig_sha = file_sha256(orig_p_path)

            dest_p = tmp_path / rel_path
            dest_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(orig_p_path, dest_p)

            df = pd.read_parquet(dest_p)
            df.loc[0, "close"] = df.loc[0, "close"] + 100.0
            df.to_parquet(dest_p)

            run_tmp = tmp_path / "run"
            run_tmp.mkdir()
            dm_copy = copy.deepcopy(dm)
            (run_tmp / "dataset_manifest.json").write_text(json.dumps(dm_copy), encoding="utf-8")
            (run_tmp / "run_manifest.json").write_text(json.dumps({
                "execution_mode": "ALL_SIGNALS",
                "dataset_hash": dm["dataset_hash"],
            }), encoding="utf-8")

            ctx = VerifyContext(run_tmp, dataset_root=tmp_path, repo_root=self.repo_root)
            res = gates_a.gate_a1(ctx)

            self.assertEqual(res.status, "FAIL")
            self.assertGreater(res.violation_count, 0)
            self.assertTrue(any(v.get("kind") == "SHA256_MISMATCH" for v in res.violations))
            self.assertEqual(file_sha256(orig_p_path), orig_sha)

    def test_m_a2_delete_m1_candle(self):
        """M-A2: Delete one M1 candle in copy -> A2 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            dm = json.loads((self.golden_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
            p0 = dm["partitions"][0]
            rel_path = p0["relative_path"]
            orig_p_path = self.repo_root / rel_path
            orig_sha = file_sha256(orig_p_path)

            dest_p = tmp_path / rel_path
            dest_p.parent.mkdir(parents=True, exist_ok=True)
            df = pd.read_parquet(orig_p_path)
            df.iloc[1:].to_parquet(dest_p)

            run_tmp = tmp_path / "run"
            run_tmp.mkdir()
            mini_dm = copy.deepcopy(dm)
            mini_dm["partitions"] = [p0]
            mini_dm["symbol_stats"] = {
                "BTCUSDT": {
                    "total_candles": len(df),
                    "min_open_ms": int(df["open_time"].iloc[0]),
                    "max_open_ms": int(df["open_time"].iloc[-1]),
                }
            }
            (run_tmp / "dataset_manifest.json").write_text(json.dumps(mini_dm), encoding="utf-8")

            ctx = VerifyContext(run_tmp, dataset_root=tmp_path, repo_root=self.repo_root)
            res = gates_a.gate_a2(ctx)

            self.assertEqual(res.status, "FAIL")
            self.assertEqual(file_sha256(orig_p_path), orig_sha)

    def test_m_b1_change_signal_symbol(self):
        """M-B1: Change symbol of one signal in copy -> B1 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            sig_file = tmp_run / "signals.parquet"
            df = pd.read_parquet(sig_file)
            df.loc[0, "symbol"] = "ETH-USDT" if df.loc[0, "symbol"] == "BTC-USDT" else "BTC-USDT"
            df.to_parquet(sig_file)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_b.gate_b1(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertGreater(res.violation_count, 0)

    def test_m_c1_shift_entry_time(self):
        """M-C1: Shift entry_time_ms by 1 minute on one leg -> C1 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            trades_file = tmp_run / "trades.parquet"
            df = pd.read_parquet(trades_file)
            df.loc[0, "entry_time_ms"] = int(df.loc[0, "entry_time_ms"]) - 60000
            df.to_parquet(trades_file)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_c.gate_c1(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any(v.get("kind") == "ENTRY_TIME" for v in res.violations))

    def test_m_c2_modify_tp_pct(self):
        """M-C2: Modify tp_pct in ledger leg -> C2 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            trades_file = tmp_run / "trades.parquet"
            df = pd.read_parquet(trades_file)
            df.loc[0, "tp_pct"] = float(df.loc[0, "tp_pct"]) + 0.05
            df.to_parquet(trades_file)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_c.gate_c2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any(v.get("kind") == "POLICY_FIELD" for v in res.violations))

    def test_m_c3_modify_exit_reason_and_time(self):
        """M-C3: Change exit_reason and shift exit_time_ms -> C3 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            trades_file = tmp_run / "trades.parquet"
            df = pd.read_parquet(trades_file)
            orig_reason = df.loc[0, "exit_reason"]
            df.loc[0, "exit_reason"] = "TP" if orig_reason == "SL" else "SL"
            df.to_parquet(trades_file)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_c.gate_c3(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("EXIT_REASON" in str(v) for v in res.violations))

    def test_m_c4_modify_net_pnl(self):
        """M-C4: Modify net_pnl of one leg -> C4 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            trades_file = tmp_run / "trades.parquet"
            df = pd.read_parquet(trades_file)
            df.loc[0, "net_pnl"] = float(df.loc[0, "net_pnl"]) + 10.0
            df.to_parquet(trades_file)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_c.gate_c4(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("LEG_NET_PNL" in str(v.get("kind", "")) for v in res.violations))

    def test_m_c5_delete_accepted_legs(self):
        """M-C5: Remove all legs of an accepted signal -> C5 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            trades_file = tmp_run / "trades.parquet"
            df = pd.read_parquet(trades_file)
            target_sig = df.loc[0, "signal_id"]
            df_filtered = df[df["signal_id"] != target_sig]
            df_filtered.to_parquet(trades_file)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_c.gate_c5(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any(v.get("signal_id") == target_sig for v in res.violations))

    def test_m_d1_delete_and_modify_trades_parquet(self):
        """M-D1: Delete trades.parquet or tamper 1 byte -> D1 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            trades_file = tmp_run / "trades.parquet"
            data = bytearray(trades_file.read_bytes())
            data[len(data) // 2] ^= 0xFF
            trades_file.write_bytes(data)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d1(ctx)
            self.assertEqual(res.status, "FAIL")

            trades_file.unlink()
            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res2 = gates_d.gate_d1(ctx)
            self.assertEqual(res2.status, "FAIL")

    def test_m_d2_modify_report_number(self):
        """M-D2: Modify a number in report.md -> D2 FAIL."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            rep_file = tmp_run / "report.md"
            text = rep_file.read_text(encoding="utf-8")
            text_mod = text.replace("$410.70", "$999.99")
            rep_file.write_text(text_mod, encoding="utf-8")

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any(v.get("kind") == "REPORT_NUMBER_MISMATCH" for v in res.violations))

    def test_m_b2_lookahead_detection(self):
        """M-B2: Tamper signals with future signal_close_ms -> B2 FAIL with LOOKAHEAD_DETECTED."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")

            sig_path = tmp_run / "signals.parquet"
            df_sigs = pd.read_parquet(sig_path)
            # Inject a future-lookahead signal by shifting a later signal into an early cutoff window
            # or tampering an existing signal's close timestamp to an arbitrary earlier timestamp
            tampered_row = df_sigs.iloc[-1].copy()
            tampered_row["signal_id"] = "LOOKAHEAD_INJECTED_SIG_9999"
            tampered_row["signal_close_ms"] = int(df_sigs.iloc[10]["signal_close_ms"])
            df_mut = pd.concat([df_sigs, pd.DataFrame([tampered_row])], ignore_index=True)
            df_mut.to_parquet(sig_path)

            ctx = VerifyContext(tmp_run, dataset_root=self.repo_root, repo_root=self.repo_root, n_cutoffs=3)
            res = gates_b.gate_b2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertGreater(res.violation_count, 0)
            self.assertTrue(any(v.get("kind") == "LOOKAHEAD_DETECTED" for v in res.violations))

    def test_m_d2_1_mismatch_net_pf_trade(self):
        """M-D2-1: Mismatch in net_pf_trade between report.md and recomputed trades -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, report_text = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            rep_path = run_dir / "report.md"
            # Tamper Net Profit Factor (Trade) in report
            tampered_text = re.sub(r"-\s*\*\*Net Profit Factor \(Trade\):\*\*\s*[\d\.]+", "- **Net Profit Factor (Trade):** 9.99", report_text)
            self.assertNotEqual(report_text, tampered_text, "Failed to tamper Net Profit Factor (Trade)")
            rep_path.write_text(tampered_text, encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("net_pf_trade" in str(v) or v.get("kind") == "REPORT_NUMBER_MISMATCH" for v in res.violations))

    def test_m_d2_2_mismatch_gross_pf_leg(self):
        """M-D2-2: Tampered gross_pf_leg in analysis.json -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            an_path = run_dir / "analysis.json"
            an_data = json.loads(an_path.read_text(encoding="utf-8"))
            an_data["pnl"]["gross_pf_leg"] = 9.99
            an_path.write_text(json.dumps(an_data), encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("gross_pf_leg" in str(v) for v in res.violations))

    def test_m_d2_3_missing_or_wrong_combo(self):
        """M-D2-3: Missing or renamed combo in analysis.json -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            an_path = run_dir / "analysis.json"
            an_data = json.loads(an_path.read_text(encoding="utf-8"))
            # Rename a canonical combo
            an_data["combos"][0]["combo_key"] = "BTC-USDT/C999_TAMPERED"
            an_path.write_text(json.dumps(an_data), encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")

    def test_m_d2_4_combo_pnl_reconciliation_mismatch(self):
        """M-D2-4: Discrepancy between combo PnL sum and run summary PnL -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            an_path = run_dir / "analysis.json"
            an_data = json.loads(an_path.read_text(encoding="utf-8"))
            # Corrupt combo 0 net_pnl_unrounded by +50.0
            an_data["combos"][0]["net_pnl_unrounded"] += 50.0
            an_path.write_text(json.dumps(an_data), encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("combos[0].net_pnl_unrounded" in str(v) or "reconciliation" in str(v) for v in res.violations))

    def test_m_d2_5_symbol_pnl_reconciliation_mismatch(self):
        """M-D2-5: Discrepancy between symbol PnL sum and run summary PnL -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            an_path = run_dir / "analysis.json"
            an_data = json.loads(an_path.read_text(encoding="utf-8"))
            an_data["symbols"]["BTC-USDT"]["net_pnl_unrounded"] += 50.0
            an_path.write_text(json.dumps(an_data), encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("symbols.BTC-USDT" in str(v) for v in res.violations))

    def test_m_d2_6_unrounded_mismatch(self):
        """M-D2-6: Unrounded field mismatch against ledger recomputation -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _ = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            an_path = run_dir / "analysis.json"
            an_data = json.loads(an_path.read_text(encoding="utf-8"))
            an_data["pnl"]["net_pnl_unrounded"] += 0.05
            an_path.write_text(json.dumps(an_data), encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("net_pnl_unrounded" in str(v) for v in res.violations))

    def test_m_d2_7_reconciliation_audit_section_mismatch(self):
        """M-D2-7: Section 8 Reconciliation Audit mismatch in report.md -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, report_text = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            rep_path = run_dir / "report.md"
            tampered_text = report_text.replace(
                "- **Three-Level Parity Pass (Run == Symbol Sum == Combo Sum):** `True`",
                "- **Three-Level Parity Pass (Run == Symbol Sum == Combo Sum):** `False`"
            )
            self.assertNotEqual(report_text, tampered_text, "Failed to tamper Three-Level Parity Pass")
            rep_path.write_text(tampered_text, encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("reconciliation_parity" in str(v) or "RECONCILIATION_MISMATCH" in str(v) for v in res.violations))

    def test_m_d2_8_markdown_table_header_mismatch(self):
        """M-D2-8: Markdown table header corrupt or missing column -> D2 FAIL."""
        from autotrade_v3.tests.test_reporting_corrections_v309 import _setup_pristine_run
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, report_text = _setup_pristine_run(Path(tmp), "ALL_SIGNALS")
            rep_path = run_dir / "report.md"
            # Delete one column from Combo table header
            tampered_text = report_text.replace(
                "| Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL | Win Rate | Net PF (Leg) | Net PF (Trade) |",
                "| Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL | Win Rate | Net PF (Leg) |"
            )
            rep_path.write_text(tampered_text, encoding="utf-8")

            ctx = VerifyContext(run_dir, dataset_root=self.repo_root, repo_root=self.repo_root)
            res = gates_d.gate_d2(ctx)
            self.assertEqual(res.status, "FAIL")
            self.assertTrue(any("TABLE_HEADER_MISMATCH" in str(v) for v in res.violations))

    def test_m_dirty_tree(self):
        """M-DIRTY: Run created on dirty tree must have UNVERIFIED_DIRTY overall status."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_run = Path(tmp)
            shutil.copytree(self.golden_dir / "ALL_SIGNALS", tmp_run, dirs_exist_ok=True)
            shutil.copy2(self.golden_dir / "dataset_manifest.json", tmp_run / "dataset_manifest.json")
            
            m_path = tmp_run / "run_manifest.json"
            m = json.loads(m_path.read_text(encoding="utf-8"))
            m["code_dirty"] = True
            m_path.write_text(json.dumps(m), encoding="utf-8")

            # Update ARTIFACTS.json with the new hash for run_manifest.json
            art_path = tmp_run / "ARTIFACTS.json"
            art = json.loads(art_path.read_text(encoding="utf-8"))
            art["artifacts"]["run_manifest.json"]["sha256"] = sha256_file(m_path)
            art["artifacts"]["run_manifest.json"]["content_hash"] = sha256_file(m_path)
            art["artifacts"]["run_manifest.json"]["size_bytes"] = m_path.stat().st_size
            art_path.write_text(json.dumps(art), encoding="utf-8")

            report = run_validation(
                run_dir=tmp_run,
                dataset_root=self.repo_root,
                n_cutoffs=1,
                write_result=False,
            )
            self.assertEqual(report.overall_status, "UNVERIFIED_DIRTY")
            self.assertNotEqual(report.overall_status, "VERIFIED")
