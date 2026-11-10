"""Adversarial and Mutation Tests (M1-M4) for V3-08 Golden Baseline."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import pandas as pd

from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.tools.artifact_hash import compute_artifact_hashes, compute_content_hash
from autotrade_v3.tools.compare_ledgers import compare_ledgers


class TestAdversarialMutationV308(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp_dir)

        # Generate a synthetic golden baseline bundle in temp
        rm = RunManager(runs_dir=self.tmp_path)
        runner = ExperimentRunner(run_manager=rm)
        with unittest.mock.patch("autotrade_v3.lab.experiment.get_git_state", return_value=("mock_sha", False, "")):
            self.res = runner.run_experiment(
                strategy_id="20FINAL_BASELINE",
                mode="ALL_SIGNALS",
                symbols=["BTC-USDT"],
                start="2023-11-01",
                end="2026-11-01",
                smoke_test=True,
                is_golden=True,
            )
        self.run_dir = self.res.run_dir

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_m1_mutate_net_pnl_detected_by_content_hash_and_compare(self):
        """M1: Sửa net_pnl của một leg: content hash đổi, compare_ledgers báo đúng 1 dòng khác."""
        trades_file = self.run_dir / "trades.parquet"
        df_orig = pd.read_parquet(trades_file)
        self.assertGreater(len(df_orig), 0)

        # Compute original content hash
        hash_orig = compute_content_hash(df_orig)

        # Mutate single leg's net_pnl
        copy_file = self.tmp_path / "trades_mutated.parquet"
        df_mut = df_orig.copy()
        df_mut.loc[0, "net_pnl"] += 1.0
        df_mut.to_parquet(copy_file)

        hash_mut = compute_content_hash(df_mut)
        self.assertNotEqual(hash_orig, hash_mut, "Content hash must change upon net_pnl mutation")

        # Compare using compare_ledgers tool
        cmp_res = compare_ledgers(trades_file, copy_file)
        self.assertEqual(cmp_res["rows_only_in_a"], 0)
        self.assertEqual(cmp_res["rows_only_in_b"], 0)
        self.assertEqual(cmp_res["col_diffs"]["net_pnl"]["diff_count"], 1, "Exactly 1 row must differ")
        self.assertAlmostEqual(cmp_res["col_diffs"]["net_pnl"]["max_abs_diff"], 1.0, places=5)

    def test_m2_mutate_signal_symbol_detected_by_signal_id_set_comparison(self):
        """M2: Đổi symbol của một tín hiệu trong signals.parquet: báo 1 thiếu và 1 thừa."""
        sig_file = self.run_dir / "signals.parquet"
        df_sigs = pd.read_parquet(sig_file)
        self.assertGreater(len(df_sigs), 0)

        orig_ids = set(df_sigs["signal_id"])

        # Create mutated copy
        df_mut = df_sigs.copy()
        old_id = df_mut.loc[0, "signal_id"]
        new_id = old_id.replace("BTCUSDT", "ETHUSDT")
        df_mut.loc[0, "signal_id"] = new_id
        df_mut.loc[0, "symbol"] = "ETH-USDT"

        mut_ids = set(df_mut["signal_id"])

        missing_in_mut = orig_ids - mut_ids
        extra_in_mut = mut_ids - orig_ids

        self.assertEqual(len(missing_in_mut), 1, "Must report exactly 1 missing signal")
        self.assertEqual(len(extra_in_mut), 1, "Must report exactly 1 extra signal")
        self.assertIn(old_id, missing_in_mut)
        self.assertIn(new_id, extra_in_mut)

    def test_m3_missing_artifact_detected_by_artifacts_json(self):
        """M3: Xóa một file artifact: phép kiểm ARTIFACTS.json báo thiếu."""
        art_json_path = self.run_dir / "ARTIFACTS.json"
        with open(art_json_path, "r", encoding="utf-8") as f:
            art_data = json.load(f)

        # Delete skipped_signals.parquet
        target_to_delete = self.run_dir / "skipped_signals.parquet"
        self.assertTrue(target_to_delete.exists())
        target_to_delete.unlink()

        # Audit artifacts against index
        missing = []
        for name, meta in art_data["artifacts"].items():
            if not (self.run_dir / name).exists():
                missing.append(name)

        self.assertEqual(missing, ["skipped_signals.parquet"])

    def test_m4_dirty_working_tree_rejects_golden_run(self):
        """M4: Làm bẩn cây làm việc: --golden bị từ chối."""
        with tempfile.TemporaryDirectory() as git_tmp:
            gpath = Path(git_tmp)
            subprocess.run(["git", "init"], cwd=gpath, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=gpath, check=True)
            subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=gpath, check=True)

            f = gpath / "code.py"
            f.write_text("print('clean')", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=gpath, check=True)
            subprocess.run(["git", "commit", "-m", "clean"], cwd=gpath, check=True)

            # Clean check
            _, dirty, _ = get_git_state(gpath)
            self.assertFalse(dirty)

            # Make tree dirty
            f.write_text("print('dirty')", encoding="utf-8")
            _, dirty_after, _ = get_git_state(gpath)
            self.assertTrue(dirty_after)

            # Verify that ExperimentRunner enforces clean tree when is_golden=True
            rm = RunManager(runs_dir=gpath / "runs")
            runner = ExperimentRunner(run_manager=rm)
            with unittest.mock.patch("autotrade_v3.lab.experiment.get_git_state", return_value=("mock_sha", True, "diff_hash")):
                with self.assertRaises(RuntimeError) as ctx:
                    runner.run_experiment(
                        strategy_id="20FINAL_BASELINE",
                        mode="ALL_SIGNALS",
                        smoke_test=True,
                        is_golden=True,
                    )
                self.assertIn("working tree is dirty", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
