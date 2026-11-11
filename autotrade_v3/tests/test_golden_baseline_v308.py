"""Unit and Integration Tests for V3-08 Golden Baseline & Reproducibility."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import numpy as np
import pandas as pd

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import (
    datetime_index_to_ms,
    generate_synthetic_m1,
    resample_m1_to_timeframe,
    to_utc_ms,
)
from autotrade_v3.engine.execution_simulator import (
    ExecutionEvent,
    ExecutionSimulator,
    TradeRecord,
)
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal, SignalEngine
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.tools.artifact_hash import compute_artifact_hashes, compute_content_hash
from autotrade_v3.tools.compare_ledgers import compare_ledgers
from autotrade_v3.tools.dataset_manifest import (
    compute_file_sha256,
    generate_dataset_manifest,
    verify_dataset_integrity,
)
from autotrade_v3.tools.recompute_metrics import recompute_metrics
from autotrade_v3.tools.verify_snapshot import verify_snapshot


class TestSignalIdAndProvenance(unittest.TestCase):
    def test_signal_id_format_and_properties(self):
        sig = Signal(
            symbol="BTC-USDT",
            combo_id=1,
            combo_name="C1",
            timeframe="15m",
            side="BUY",
            signal_close_ms=1701856799999,
            signal_close_dt=pd.to_datetime(1701856799999, unit="ms", utc=True),
            bar_position=10,
            bar_open_ms=1701855900000,
            entry_price=42000.0,
            tp_pct=0.05,
            sl_pct=0.03,
            tp_price=44100.0,
            sl_price=40740.0,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="BINANCE",
            is_new6=False,
        )
        expected = "SIG_BTCUSDT_C1_15m_BUY_1701856799999"
        self.assertEqual(sig.signal_id, expected)
        # Regex check: SIG_{SYMBOL}_{combo_name}_{timeframe}_{side}_{signal_close_ms}
        pattern = r"^SIG_[A-Z0-9]+_[A-Z0-9]+_[0-9]+[mh]_(BUY|SELL)_[0-9]+$"
        self.assertRegex(sig.signal_id, pattern)

    def test_signal_id_in_trades_and_events(self):
        trade = TradeRecord(
            trade_id="T_BTCUSDT_1_00001",
            leg_id="T_BTCUSDT_1_00001_L1",
            symbol="BTC-USDT",
            combo_id=1,
            combo_name="C1",
            timeframe="15m",
            side="BUY",
            leg_type="SINGLE",
            entry_time_ms=1701856800000,
            entry_time_dt=pd.to_datetime(1701856800000, unit="ms", utc=True),
            entry_price=42000.0,
            exit_time_ms=1701860400000,
            exit_time_dt=pd.to_datetime(1701860400000, unit="ms", utc=True),
            exit_price=44100.0,
            exit_reason="TP",
            qty=0.002,
            notional=100.0,
            margin=100.0,
            gross_pnl=5.0,
            fee_total=0.1,
            net_pnl=4.9,
            return_pct=0.049,
            intrabar_ambiguous=False,
            bars_held=60,
            provider="BINANCE",
            signal_id="SIG_BTCUSDT_C1_15m_BUY_1701856799999",
        )
        self.assertEqual(trade.signal_id, "SIG_BTCUSDT_C1_15m_BUY_1701856799999")
        d = trade.to_dict()
        self.assertIn("signal_id", d)
        self.assertEqual(d["signal_id"], "SIG_BTCUSDT_C1_15m_BUY_1701856799999")

        ev = ExecutionEvent(
            event_id="EV_L1_OPEN",
            timestamp_ms=1701856800000,
            timestamp_dt="2023-12-06 10:00:00+00:00",
            trade_id="T_BTCUSDT_1_00001",
            leg_id="T_BTCUSDT_1_00001_L1",
            symbol="BTC-USDT",
            combo_id=1,
            combo_name="C1",
            timeframe="15m",
            event_type="ENTRY",
            side="BUY",
            order_action="OPEN",
            price=42000.0,
            qty=0.002,
            notional=100.0,
            fee=0.05,
            realized_pnl=0.0,
            exit_reason="",
            signal_id="SIG_BTCUSDT_C1_15m_BUY_1701856799999",
        )
        self.assertEqual(ev.signal_id, "SIG_BTCUSDT_C1_15m_BUY_1701856799999")
        self.assertEqual(ev.to_dict()["signal_id"], "SIG_BTCUSDT_C1_15m_BUY_1701856799999")


class TestContentHashAndDatasetManifest(unittest.TestCase):
    def test_content_hash_permutations_and_crlf_invariance(self):
        data = {
            "signal_id": ["SIG_BTC_1", "SIG_ETH_2", "SIG_BTC_3"],
            "leg_id": ["L1", "L1", "L2"],
            "net_pnl": [10.500000000000001, -2.1234567890123456, 0.0],
            "exit_reason": ["TP", "SL", "TRAIL"],
        }
        df1 = pd.DataFrame(data)
        # Permute rows and columns
        df2 = df1.sample(frac=1.0, random_state=42)[["exit_reason", "net_pnl", "leg_id", "signal_id"]]

        h1 = compute_content_hash(df1)
        h2 = compute_content_hash(df2)
        self.assertEqual(h1, h2, "Content hash must be invariant to row and column permutations")

        # Mutate single value
        df_mutated = df1.copy()
        df_mutated.loc[0, "net_pnl"] = 11.5
        h_mutated = compute_content_hash(df_mutated)
        self.assertNotEqual(h1, h_mutated, "Content hash must change when value changes")

    def test_dataset_hash_sensitivity_on_byte_mutation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            data_sub = tmp_path / "data" / "m1_raw" / "symbol=TEST"
            data_sub.mkdir(parents=True)
            pf = data_sub / "test.parquet"

            df = pd.DataFrame({"open": [1.0, 2.0], "close": [1.5, 2.5]})
            df.to_parquet(pf)

            dm1 = generate_dataset_manifest(
                catalog_path=tmp_path / "nonexistent.duckdb",
                data_dir=tmp_path / "data" / "m1_raw",
                base_dir=tmp_path,
            )
            h1 = dm1["dataset_hash"]

            # Mutate one byte in file
            raw = pf.read_bytes()
            mutated = bytearray(raw)
            mutated[10] = (mutated[10] + 1) % 256
            pf.write_bytes(bytes(mutated))

            dm2 = generate_dataset_manifest(
                catalog_path=tmp_path / "nonexistent.duckdb",
                data_dir=tmp_path / "data" / "m1_raw",
                base_dir=tmp_path,
            )
            h2 = dm2["dataset_hash"]

            self.assertNotEqual(h1, h2, "dataset_hash must change when any file byte is mutated")


class TestGitStateAndGoldenProtection(unittest.TestCase):
    def test_dirty_working_tree_detection(self):
        with tempfile.TemporaryDirectory() as tmp_git:
            tpath = Path(tmp_git)
            # Init git repo
            subprocess.run(["git", "init"], cwd=tpath, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tpath, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tpath, check=True)

            f = tpath / "file.txt"
            f.write_text("initial", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=tpath, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tpath, check=True)

            sha, dirty, diff_hash = get_git_state(tpath)
            self.assertFalse(dirty)
            self.assertEqual(diff_hash, "")

            # Modify file
            f.write_text("modified", encoding="utf-8")
            sha_m, dirty_m, diff_hash_m = get_git_state(tpath)
            self.assertTrue(dirty_m)
            self.assertNotEqual(diff_hash_m, "")

    def test_git_error_or_unknown_commit_blocks_golden(self):
        with tempfile.TemporaryDirectory() as tmp_out:
            rm = RunManager(runs_dir=tmp_out)
            runner = ExperimentRunner(run_manager=rm)
            with unittest.mock.patch("autotrade_v3.lab.experiment.get_git_state", return_value=("UNKNOWN_COMMIT", True, "")):
                with self.assertRaises(RuntimeError) as ctx:
                    runner.run_experiment(smoke_test=True, is_golden=True)
                self.assertIn("Golden run rejected", str(ctx.exception))

            with unittest.mock.patch("autotrade_v3.lab.experiment.get_git_state", return_value=("", True, "")):
                with self.assertRaises(RuntimeError) as ctx:
                    runner.run_experiment(smoke_test=True, is_golden=True)
                self.assertIn("Golden run rejected", str(ctx.exception))


class TestRobustnessAndAuditTools(unittest.TestCase):
    def test_to_utc_ms_and_datetime_index_robustness(self):
        from datetime import datetime, timezone, timedelta
        # Test timezone-naive datetime
        dt_naive = datetime(2024, 1, 1, 0, 0, 0)
        self.assertEqual(to_utc_ms(dt_naive), 1704067200000)

        # Test timezone-aware UTC datetime
        dt_utc = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(to_utc_ms(dt_utc), 1704067200000)

        # Test timezone-aware with offset (+07:00 at 07:00 == 00:00 UTC)
        dt_bkk = datetime(2024, 1, 1, 7, 0, 0, tzinfo=timezone(timedelta(hours=7)))
        self.assertEqual(to_utc_ms(dt_bkk), 1704067200000)

        # Test pd.Timestamp across all resolutions (s, ms, us, ns)
        for unit in ["s", "ms", "us", "ns"]:
            ts_naive = pd.Timestamp("2024-01-01 00:00:00", unit=unit)
            self.assertEqual(to_utc_ms(ts_naive), 1704067200000, f"Failed for naive unit {unit}")
            ts_aware = pd.Timestamp("2024-01-01 00:00:00+00:00", unit=unit)
            self.assertEqual(to_utc_ms(ts_aware), 1704067200000, f"Failed for aware unit {unit}")

        # Test datetime_index_to_ms across all resolutions
        for unit in ["s", "ms", "us", "ns"]:
            dr = pd.date_range("2024-01-01", periods=3, freq="1min", tz="UTC").astype(f"datetime64[{unit}, UTC]")
            res = datetime_index_to_ms(dr)
            expected = np.array([1704067200000, 1704067260000, 1704067320000], dtype=np.int64)
            np.testing.assert_array_equal(res, expected)

    def test_compare_ledgers_duplicate_keys_and_null_divergence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir)
            fa = p / "table_a.parquet"
            fb = p / "table_b.parquet"

            # Case 1: Duplicate keys
            df_a = pd.DataFrame({
                "signal_id": ["S1", "S1"],  # Duplicate S1!
                "leg_type": ["ENTRY", "ENTRY"],
                "net_pnl": [10.0, 10.0],
            })
            df_b = pd.DataFrame({
                "signal_id": ["S1"],
                "leg_type": ["ENTRY"],
                "net_pnl": [10.0],
            })
            df_a.to_parquet(fa)
            df_b.to_parquet(fb)

            res = compare_ledgers(fa, fb, key_cols=["signal_id", "leg_type"])
            self.assertFalse(res["key_uniqueness_pass"])
            self.assertEqual(res["duplicates_in_a"], 1)

            # Case 2: Null vs numeric mismatch
            df_a2 = pd.DataFrame({
                "signal_id": ["S1", "S2"],
                "leg_type": ["ENTRY", "ENTRY"],
                "net_pnl": [10.0, None],  # S2 is null in A
            })
            df_b2 = pd.DataFrame({
                "signal_id": ["S1", "S2"],
                "leg_type": ["ENTRY", "ENTRY"],
                "net_pnl": [10.0, 15.0],  # S2 is non-null in B
            })
            df_a2.to_parquet(fa)
            df_b2.to_parquet(fb)

            res2 = compare_ledgers(fa, fb, key_cols=["signal_id", "leg_type"])
            self.assertTrue(res2["key_uniqueness_pass"])
            self.assertFalse(res2["col_diffs"]["net_pnl"]["identical"])
            self.assertEqual(res2["col_diffs"]["net_pnl"]["diff_count"], 1)
            self.assertEqual(res2["col_diffs"]["net_pnl"]["null_mismatches"], 1)

    def test_verify_snapshot_tool_and_dataset_integrity(self):
        from autotrade_v3.tools.verify_snapshot import DEFAULT_SNAPSHOT_PATH

        # 1. Check default snapshot path behavior (environment-aware)
        snap_res = verify_snapshot()
        if DEFAULT_SNAPSHOT_PATH.exists():
            self.assertTrue(snap_res["verified"])
            self.assertTrue(snap_res["sha_match"])
            self.assertTrue(snap_res["rows_match"])
        else:
            self.assertFalse(snap_res["verified"])
            self.assertIn("reproduction_command", snap_res)

        # 2. Synthetic verification of verify_snapshot logic
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            fake_snap = tmp_p / "snapshots.parquet"
            df_snap = pd.DataFrame({"open_ms": [1, 2], "portfolio_equity": [1000.0, 1010.0]})
            df_snap.to_parquet(fake_snap)
            fake_sha = compute_file_sha256(fake_snap)

            fake_art = tmp_p / "ARTIFACTS.json"
            fake_art.write_text(json.dumps({
                "artifacts": {
                    "snapshots.parquet": {
                        "sha256": fake_sha,
                        "rows": 2
                    }
                }
            }), encoding="utf-8")

            # Matching case
            res_match = verify_snapshot(fake_snap, fake_art)
            self.assertTrue(res_match["verified"])
            self.assertTrue(res_match["sha_match"])
            self.assertEqual(res_match["actual_rows"], 2)

            # Mismatched SHA case
            fake_art_bad = tmp_p / "ARTIFACTS_bad.json"
            fake_art_bad.write_text(json.dumps({
                "artifacts": {
                    "snapshots.parquet": {
                        "sha256": "0" * 64,
                        "rows": 2
                    }
                }
            }), encoding="utf-8")
            res_bad = verify_snapshot(fake_snap, fake_art_bad)
            self.assertFalse(res_bad["verified"])
            self.assertFalse(res_bad["sha_match"])

        # 3. Test verify_dataset_integrity logic with synthetic partitions
        with tempfile.TemporaryDirectory() as tmp_data_dir:
            tpath = Path(tmp_data_dir)
            p_data = tpath / "data" / "m1_raw" / "symbol=BTC-USDT"
            p_data.mkdir(parents=True)
            pf = p_data / "part.parquet"
            pd.DataFrame({"open": [1.0]}).to_parquet(pf)

            man = generate_dataset_manifest(
                catalog_path=tpath / "fake.duckdb",
                data_dir=tpath / "data" / "m1_raw",
                base_dir=tpath,
            )
            man_file = tpath / "manifest.json"
            man_file.write_text(json.dumps(man), encoding="utf-8")

            is_valid, l_hash, err = verify_dataset_integrity(
                manifest_path=man_file,
                catalog_path=tpath / "fake.duckdb",
                data_dir=tpath / "data" / "m1_raw",
                base_dir=tpath,
            )
            self.assertTrue(is_valid, f"Dataset integrity check failed: {err}")
            self.assertEqual(l_hash, man["dataset_hash"])

            # Tamper with file to assert failure detection
            pf.write_bytes(pf.read_bytes() + b"extra")
            is_valid_tamp, _, _ = verify_dataset_integrity(
                manifest_path=man_file,
                catalog_path=tpath / "fake.duckdb",
                data_dir=tpath / "data" / "m1_raw",
                base_dir=tpath,
            )
            self.assertFalse(is_valid_tamp)


class TestEndToEndSyntheticGoldenArtifacts(unittest.TestCase):
    def test_synthetic_three_modes_artifact_completeness(self):
        with tempfile.TemporaryDirectory() as tmp_out:
            rm = RunManager(runs_dir=tmp_out)
            runner = ExperimentRunner(run_manager=rm)

            modes = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
            with unittest.mock.patch("autotrade_v3.lab.experiment.get_git_state", return_value=("mock_sha", False, "")):
                for m in modes:
                    res = runner.run_experiment(
                        strategy_id="20FINAL_BASELINE",
                        mode=m,
                        symbols=["BTC-USDT"],
                        start="2023-11-01",
                        end="2026-11-01",
                        smoke_test=True,
                        is_golden=True,
                    )

                self.assertTrue(res.run_dir.exists())
                # Verify required artifacts
                required_files = [
                    "run_manifest.json",
                    "signals.parquet",
                    "trades.parquet",
                    "execution_events.parquet",
                    "skipped_signals.parquet",
                    "rejected_signals.parquet",
                    "analysis.json",
                    "ARTIFACTS.json",
                ]
                for rf in required_files:
                    p = res.run_dir / rf
                    self.assertTrue(p.exists(), f"Missing required artifact: {rf} in {m}")

                # Check ARTIFACTS.json contents
                with open(res.run_dir / "ARTIFACTS.json", "r", encoding="utf-8") as f:
                    art_index = json.load(f)
                self.assertIn("artifacts", art_index)
                for rf in [f for f in required_files if f != "ARTIFACTS.json"]:
                    self.assertIn(rf, art_index["artifacts"])
                    meta = art_index["artifacts"][rf]
                    self.assertIn("sha256", meta)
                    self.assertIn("size_bytes", meta)

                # Check manifest provenance
                with open(res.run_dir / "run_manifest.json", "r", encoding="utf-8") as f:
                    man = json.load(f)
                self.assertTrue(man.get("is_golden"))
                self.assertIn("code_sha", man)
                self.assertIn("dataset_hash", man)
                self.assertIn("python_version", man)


if __name__ == "__main__":
    unittest.main()
