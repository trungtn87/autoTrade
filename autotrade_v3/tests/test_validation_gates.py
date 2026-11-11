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
        from autotrade_v3.validation.runner import run_validation

        golden_dir = Path("runs/golden/v3-08")
        before_hashes: dict[str, str] = {}
        for p in golden_dir.rglob("*"):
            if p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                before_hashes[str(p.relative_to(golden_dir))] = h

        # Run validation on golden run
        rep = run_validation(
            run_dir=golden_dir / "ALL_SIGNALS",
            dataset_root=Path("."),
            skip_gates=["B2"],
            write_result=True,
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

        # Check evidence was written to evidence directory instead
        ev_file = Path("evidence/v3-10/golden_validation/ALL_SIGNALS/validation.json")
        self.assertTrue(ev_file.exists(), "validation.json was not written to evidence directory")
