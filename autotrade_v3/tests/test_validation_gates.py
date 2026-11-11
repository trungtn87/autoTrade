"""Tests for V3-10 Independent Validation Gates and legacy adapter."""
from __future__ import annotations

import unittest
from pathlib import Path
import pandas as pd

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import generate_synthetic_m1, resample_m1_to_timeframe
from autotrade_v3.engine.execution_simulator import ExecutionSimulator, SimulationResult
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab.validation_gates import ValidationGates
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation import gates_a, gates_b, gates_c, gates_d


class TestValidationGatesV310(unittest.TestCase):
    """Verify validation gates unit behavior on small fixtures."""

    def test_legacy_adapter_synthetic_smoke(self):
        """Smoke test without persisted artifacts returns SYNTHETIC status."""
        strategy = Baseline20FinalStrategy()
        sim_res = SimulationResult(
            mode="ALL_SIGNALS",
            trades=[],
            events=[],
            accepted_signals=[],
            skipped_signals=[],
            rejected_signals=[],
            snapshots=pd.DataFrame(),
            ambiguity_stats={"intrabar_tp_sl_ambiguous_count": 0, "intrabar_trailing_ambiguous_count": 0},
            summary_dict={},
        )

        report = ValidationGates.audit_run(
            strategy=strategy,
            mode="ALL_SIGNALS",
            symbols=["BTC-USDT"],
            start="2024-01-01",
            end="2024-01-02",
            sim_result=sim_res,
            analysis={},
            signals=[],
            smoke_test=True,
        )

        self.assertEqual(report.overall_status, "SYNTHETIC")
        self.assertFalse(report.is_real_data)
        self.assertIn("GATE_A_DATA", report.gates)

    def test_gate_b3_is_inconclusive(self):
        """Gate B3 must always return INCONCLUSIVE in V3-10."""
        ctx = VerifyContext(Path("runs/golden/v3-08/ALL_SIGNALS"), Path("."), Path("."))
        res = gates_b.gate_b3(ctx)
        self.assertEqual(res.status, "INCONCLUSIVE")
        self.assertIn("chưa tồn tại", res.reason)

    def test_gate_d1_golden(self):
        """Gate D1 verifies file SHA256 and content hashes."""
        ctx = VerifyContext(Path("runs/golden/v3-08/ALL_SIGNALS"), Path("."), Path("."))
        res = gates_d.gate_d1(ctx)
        self.assertEqual(res.status, "PASS")
        self.assertEqual(res.violation_count, 0)

    def test_golden_run_immutability_under_validation(self):
        """Proof that running validation on golden runs does NOT mutate runs/golden/v3-08/."""
        import hashlib
        import tempfile
        from autotrade_v3.validation.runner import run_validation

        golden_dir = Path("runs/golden/v3-08")
        before_hashes: dict[str, str] = {}
        for p in golden_dir.rglob("*"):
            if p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                before_hashes[str(p.relative_to(golden_dir))] = h

        # Run validation on golden run using an isolated TemporaryDirectory
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_out = Path(tmp_dir)
            rep = run_validation(
                run_dir=golden_dir / "ALL_SIGNALS",
                dataset_root=Path("."),
                skip_gates=["B2"],
                write_result=True,
                output_dir=tmp_out,
            )

            after_hashes: dict[str, str] = {}
            for p in golden_dir.rglob("*"):
                if p.is_file():
                    h = hashlib.sha256(p.read_bytes()).hexdigest()
                    after_hashes[str(p.relative_to(golden_dir))] = h

            # Check no file added or removed
            self.assertEqual(set(before_hashes.keys()), set(after_hashes.keys()))
            # Check all file contents unchanged
            for f, h_before in before_hashes.items():
                self.assertEqual(h_before, after_hashes[f], f"File {f} was modified during validation!")

            # Check validation output was written to the isolated temp directory
            val_file = tmp_out / "validation.json"
            self.assertTrue(val_file.exists(), "validation.json was not written to output_dir")

    def test_b2_replay_cache_isolation(self):
        """B2 replay cache must isolate by strat_id, config_hash, dataset_hash, symbols, start, end, cutoff."""
        from unittest.mock import MagicMock, patch
        from autotrade_v3.validation.gates_b import _REPLAY_CACHE, clear_replay_cache, gate_b2

        clear_replay_cache()
        self.assertEqual(len(_REPLAY_CACHE), 0)

        mock_strat = MagicMock()
        dummy_sig = MagicMock()
        dummy_sig.signal_id = "test_sig"
        dummy_sig.combo_id = 1
        dummy_sig.side = "LONG"
        dummy_sig.signal_close_ms = 1700000000000
        dummy_sig.entry_price = 100.0
        dummy_sig.tp_price = 110.0
        dummy_sig.sl_price = 95.0
        mock_strat.generate_signals.return_value = [dummy_sig]

        manifest1 = {
            "strategy_id": "20FINAL_BASELINE",
            "config_hash": "hash_config_A",
            "dataset_hash": "hash_dataset_A",
            "symbols": ["BTC-USDT"],
            "time_window": {"start": "2023-11-01", "end": "2026-11-01"},
        }
        manifest2 = {
            "strategy_id": "20FINAL_BASELINE",
            "config_hash": "hash_config_B",
            "dataset_hash": "hash_dataset_A",
            "symbols": ["BTC-USDT"],
            "time_window": {"start": "2023-11-01", "end": "2026-11-01"},
        }
        manifest3 = {
            "strategy_id": "20FINAL_BASELINE",
            "config_hash": "hash_config_A",
            "dataset_hash": "hash_dataset_C",
            "symbols": ["BTC-USDT"],
            "time_window": {"start": "2023-11-01", "end": "2026-11-01"},
        }

        ctx1 = VerifyContext(Path("runs/golden/v3-08/ALL_SIGNALS"), Path("."), Path("."))
        ctx1.run._cache["run_manifest.json"] = manifest1
        ctx1.run._cache["dataset_manifest"] = {"partitions": []}
        ctx1.n_cutoffs = 1
        ctx1.seed = 42

        dummy_df = pd.DataFrame(
            {"close": [100.0]},
            index=pd.date_range("2024-01-01", periods=1, freq="15min", tz="UTC"),
        )

        with patch("autotrade_v3.strategy.registry.StrategyRegistry.get", return_value=mock_strat), \
             patch("autotrade_v3.data_layer.DataLayer.query_timeframe", return_value=dummy_df), \
             patch.object(ctx1, "m1_store", return_value=MagicMock()):
            # Run 1 on ctx1: cache miss -> generate_signals called
            gate_b2(ctx1)
            self.assertEqual(mock_strat.generate_signals.call_count, 1)

            # Run 2 on ctx1 (same process, same config): cache hit -> no new call
            gate_b2(ctx1)
            self.assertEqual(mock_strat.generate_signals.call_count, 1)

            # Run on ctx2 (different config_hash in same process): cache miss -> generate_signals called again
            ctx2 = VerifyContext(Path("runs/golden/v3-08/ALL_SIGNALS"), Path("."), Path("."))
            ctx2.run._cache["run_manifest.json"] = manifest2
            ctx2.run._cache["dataset_manifest"] = {"partitions": []}
            ctx2.n_cutoffs = 1
            ctx2.seed = 42
            with patch.object(ctx2, "m1_store", return_value=MagicMock()):
                gate_b2(ctx2)
                self.assertEqual(mock_strat.generate_signals.call_count, 2)

            # Run on ctx3 (different dataset_hash in same process): cache miss -> generate_signals called again
            ctx3 = VerifyContext(Path("runs/golden/v3-08/ALL_SIGNALS"), Path("."), Path("."))
            ctx3.run._cache["run_manifest.json"] = manifest3
            ctx3.run._cache["dataset_manifest"] = {"partitions": []}
            ctx3.n_cutoffs = 1
            ctx3.seed = 42
            with patch.object(ctx3, "m1_store", return_value=MagicMock()):
                gate_b2(ctx3)
                self.assertEqual(mock_strat.generate_signals.call_count, 3)

        # Calling clear_replay_cache() must empty the cache
        clear_replay_cache()
        self.assertEqual(len(_REPLAY_CACHE), 0)
