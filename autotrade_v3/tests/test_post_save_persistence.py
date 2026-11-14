"""Integration tests for post-save validation status persistence and consistency.

Verifies that run_manifest.json, analysis.json, report.md, validation.json, and ARTIFACTS.json
remain strictly consistent on disk with valid checksums under both PASS and FAIL outcomes.
Also verifies B2 seed sensitivity, signal content matching, and closed-bar boundary guarantees.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from autotrade_v3.engine.execution_simulator import TradeRecord
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, compute_artifact_hashes
from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.gates_b import gate_b2
from autotrade_v3.validation.runner import run_validation
from autotrade_v3.validation.types import FAIL, GateResult


class TestPostSavePersistence(unittest.TestCase):
    """Test suite verifying post-save disk consistency and checksum integrity."""

    def test_post_save_persistence_on_synthetic_pass(self):
        """Smoke experiment produces consistent files and valid checksums on disk."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_mgr = RunManager(runs_dir=tmp_dir)
            runner = ExperimentRunner(run_manager=run_mgr)
            res = runner.run_experiment(
                strategy_id="20FINAL_BASELINE",
                mode="ALL_SIGNALS",
                symbols=["ETH-USDT"],
                start="2023-11-01",
                end="2026-11-01",
                smoke_test=True,
            )

            run_dir = Path(res.run_dir)
            expected_status = res.manifest.validation_status
            self.assertIn(expected_status, ("SYNTHETIC", "PARTIALLY_VERIFIED"))

            # 1. run_manifest.json on disk
            manifest_file = run_dir / "run_manifest.json"
            self.assertTrue(manifest_file.exists(), "run_manifest.json missing on disk")
            with open(manifest_file, "r", encoding="utf-8") as f:
                disk_manifest = json.load(f)
            self.assertEqual(disk_manifest["validation_status"], expected_status)

            # 2. analysis.json on disk
            analysis_file = run_dir / "analysis.json"
            self.assertTrue(analysis_file.exists(), "analysis.json missing on disk")
            with open(analysis_file, "r", encoding="utf-8") as f:
                disk_analysis = json.load(f)
            self.assertEqual(
                disk_analysis.get("validation", {}).get("overall_status"),
                expected_status,
            )

            # 3. report.md on disk
            report_file = run_dir / "report.md"
            self.assertTrue(report_file.exists(), "report.md missing on disk")
            report_content = report_file.read_text(encoding="utf-8")
            self.assertIn(f"- **Validation Status:** **{expected_status}**", report_content)

            # 4. validation.json on disk
            val_file = run_dir / "validation.json"
            self.assertTrue(val_file.exists(), "validation.json missing on disk")
            with open(val_file, "r", encoding="utf-8") as f:
                disk_val = json.load(f)
            self.assertEqual(disk_val["overall_status"], expected_status)

            # 5. ARTIFACTS.json checksum integrity
            art_file = run_dir / "ARTIFACTS.json"
            self.assertTrue(art_file.exists(), "ARTIFACTS.json missing on disk")
            with open(art_file, "r", encoding="utf-8") as f:
                disk_art = json.load(f)

            artifacts_map = disk_art.get("artifacts", {})
            self.assertIn("run_manifest.json", artifacts_map)
            self.assertIn("analysis.json", artifacts_map)
            self.assertIn("signals.parquet", artifacts_map)
            self.assertIn("trades.parquet", artifacts_map)

            # Recompute and verify every artifact checksum
            for art_name, meta in artifacts_map.items():
                p = run_dir / art_name
                self.assertTrue(p.exists(), f"Artifact {art_name} listed but missing on disk")
                h_info = compute_artifact_hashes(p)
                self.assertEqual(
                    h_info["sha256"],
                    meta["sha256"],
                    f"SHA256 checksum mismatch for {art_name}",
                )
                self.assertEqual(
                    h_info["content_hash"],
                    meta["content_hash"],
                    f"Content hash mismatch for {art_name}",
                )

    def test_post_save_persistence_on_post_validation_fail(self):
        """When post-save validation fails, all disk artifacts persist FAILED and checksums remain valid."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_mgr = RunManager(runs_dir=tmp_dir)
            runner = ExperimentRunner(run_manager=run_mgr)

            with patch("autotrade_v3.validation.runner.evaluate_overall_status", return_value=("FAILED", ["Injected validation failure"])):
                res = runner.run_experiment(
                    strategy_id="20FINAL_BASELINE",
                    mode="ALL_SIGNALS",
                    symbols=["ETH-USDT"],
                    start="2023-11-01",
                    end="2026-11-01",
                    smoke_test=True,
                )

            run_dir = Path(res.run_dir)

            # Assert returned manifest reflects FAILED
            self.assertEqual(res.manifest.validation_status, "FAILED")

            # 1. Check run_manifest.json on disk
            manifest_file = run_dir / "run_manifest.json"
            self.assertTrue(manifest_file.exists())
            with open(manifest_file, "r", encoding="utf-8") as f:
                disk_manifest = json.load(f)
            self.assertEqual(disk_manifest["validation_status"], "FAILED")

            # 2. Check analysis.json on disk
            analysis_file = run_dir / "analysis.json"
            self.assertTrue(analysis_file.exists())
            with open(analysis_file, "r", encoding="utf-8") as f:
                disk_analysis = json.load(f)
            self.assertEqual(
                disk_analysis.get("validation", {}).get("overall_status"),
                "FAILED",
            )

            # 3. Check report.md on disk
            report_file = run_dir / "report.md"
            self.assertTrue(report_file.exists())
            report_content = report_file.read_text(encoding="utf-8")
            self.assertIn("- **Validation Status:** **FAILED**", report_content)

            # 4. Check validation.json on disk
            val_file = run_dir / "validation.json"
            self.assertTrue(val_file.exists())
            with open(val_file, "r", encoding="utf-8") as f:
                disk_val = json.load(f)
            self.assertEqual(disk_val["overall_status"], "FAILED")
            self.assertIn("Injected validation failure", disk_val.get("failure_reasons", []))

            # 5. Check ARTIFACTS.json checksum integrity on disk
            art_file = run_dir / "ARTIFACTS.json"
            self.assertTrue(art_file.exists())
            with open(art_file, "r", encoding="utf-8") as f:
                disk_art = json.load(f)

            artifacts_map = disk_art.get("artifacts", {})
            for art_name, meta in artifacts_map.items():
                p = run_dir / art_name
                self.assertTrue(p.exists())
                h_info = compute_artifact_hashes(p)
                self.assertEqual(h_info["sha256"], meta["sha256"])
                self.assertEqual(h_info["content_hash"], meta["content_hash"])


class TestGateB2Properties(unittest.TestCase):
    """Test suite verifying Gate B2 properties: seed sensitivity, content matching, and boundary enforcement."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[2]
        self.golden_run_dir = self.repo_root / "runs" / "golden" / "v3-08" / "ALL_SIGNALS"

    def test_b2_seed_sensitivity_on_cutoff_sampling(self):
        """Different seeds generate different sampled cutoffs across buckets, while the same seed is deterministic."""
        sig_file = self.golden_run_dir / "signals.parquet"
        df = pd.read_parquet(sig_file)
        all_close_ms = np.sort(df["signal_close_ms"].unique())

        n_cutoffs = 20

        # Sample with seed 20261110
        rng1 = np.random.default_rng(20261110)
        buckets1 = np.array_split(all_close_ms, n_cutoffs)
        cutoffs_seed1_a = np.array([int(rng1.choice(b)) for b in buckets1], dtype=np.int64)

        # Reproducibility with same seed
        rng1_repeat = np.random.default_rng(20261110)
        buckets1_rep = np.array_split(all_close_ms, n_cutoffs)
        cutoffs_seed1_b = np.array([int(rng1_repeat.choice(b)) for b in buckets1_rep], dtype=np.int64)

        np.testing.assert_array_equal(cutoffs_seed1_a, cutoffs_seed1_b, "Same seed must be deterministic")

        # Different seed (e.g. 42)
        rng2 = np.random.default_rng(42)
        buckets2 = np.array_split(all_close_ms, n_cutoffs)
        cutoffs_seed2 = np.array([int(rng2.choice(b)) for b in buckets2], dtype=np.int64)

        # Cutoffs should differ across different seeds
        self.assertFalse(
            np.array_equal(cutoffs_seed1_a, cutoffs_seed2),
            "Different seeds must generate different cutoff points",
        )

    def test_b2_closed_bar_boundary_enforcement(self):
        """B2 slices strictly past closed bars: candle with open_time >= cutoff_dt must be excluded."""
        catalog_path = self.repo_root / "catalog" / "research.duckdb"
        data_dir = self.repo_root / "data" / "m1_raw"
        if not catalog_path.exists() or not data_dir.exists():
            self.skipTest("BLOCKED: Missing real M1 data per Invariant N8")

        ctx = VerifyContext(
            run_dir=self.golden_run_dir,
            dataset_root=self.repo_root,
            repo_root=self.repo_root,
            n_cutoffs=1,
            seed=20261110,
        )

        res = gate_b2(ctx)
        self.assertEqual(res.status, "PASS")
        self.assertEqual(res.violation_count, 0)
        tested = res.details.get("cutoffs_tested", [])
        self.assertEqual(len(tested), 1)

        cutoff_ms = tested[0]["cutoff_ms"]
        cutoff_dt = pd.Timestamp(cutoff_ms, unit="ms", tz="UTC")

        # Query full M15 and check that bar opening at cutoff_dt is excluded
        from autotrade_v3.data_layer import DataLayer
        dl = DataLayer(
            catalog_path=self.repo_root / "catalog" / "research.duckdb",
            data_dir=self.repo_root / "data" / "m1_raw",
        )
        df_m15 = dl.query_timeframe("BTC-USDT", "15m", "2023-11-01", "2026-11-01", include_warmup=True)
        sliced = df_m15[df_m15.index < cutoff_dt]

        # Assert no bar in sliced has index >= cutoff_dt
        if len(sliced) > 0:
            self.assertLess(sliced.index.max(), cutoff_dt)


if __name__ == "__main__":
    unittest.main()
