"""Tests for Validation Gates (P1-03).

Ensures:
- Gate A (DATA): Catalog, partitions, coverage, hash check.
- Gate B (STRATEGY): Identity, no-lookahead, signal parity vs frozen reference.
- Gate C (EXECUTION): Entry causality, frozen ExitPolicy, math reconciliation.
- Gate D (RESULT): Deliverables, ledger-derived metrics, continuous M2M distinction.
- Overall status: SYNTHETIC_PASS for smoke tests, REAL_DATA_PASS for real backtests, FAIL on error.
"""
from __future__ import annotations

import unittest
import pandas as pd

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import generate_synthetic_m1
from autotrade_v3.engine.execution_simulator import ExecutionSimulator, SimulationResult, TradeRecord
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.validation_gates import ValidationGates
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.registry import StrategyRegistry


class TestValidationGates(unittest.TestCase):
    def setUp(self):
        self.strategy = Baseline20FinalStrategy()

    def test_smoke_test_cannot_be_real_data_pass(self):
        """Synthetic smoke test must be marked SYNTHETIC_PASS, never REAL_DATA_PASS."""
        runner = ExperimentRunner()
        res = runner.run_experiment(
            strategy_id="20FINAL_BASELINE",
            mode="ALL_SIGNALS",
            symbols=["BTC-USDT"],
            start="2024-01-01",
            end="2024-02-01",
            smoke_test=True,
        )
        self.assertEqual(res.manifest.validation_status, "SYNTHETIC_PASS")
        self.assertFalse(res.validation_report.is_real_data)
        self.assertEqual(res.validation_report.gates["GATE_A_DATA"].status, "SYNTHETIC_PASS")

    def test_accounting_mismatch_fails_gate_c(self):
        """Simulate an accounting discrepancy and verify Gate C trips to FAIL."""
        # Create an artificial trade with corrupted net_pnl
        bad_trade = TradeRecord(
            trade_id="T_BAD", leg_id="T_BAD_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=50000.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=51000.0, exit_reason="TP", qty=0.002, notional=100.0, margin=2.0,
            gross_pnl=2.0, fee_total=0.1, net_pnl=999.0,  # Corrupted net PnL!
            return_pct=9.99, intrabar_ambiguous=False, bars_held=60, provider="BINANCE",
            exit_policy_hash="dummy_hash",
        )
        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[bad_trade], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = {
            "accounting": {"total_signals": 1, "accepted_entries": 1, "total_legs": 1, "skipped_by_concurrency": 0, "rejected_by_margin": 0},
            "pnl": {"gross_pnl": 2.0, "total_fees": 0.1, "net_pnl": 999.0, "profit_factor": 1.0, "net_profit_factor": 1.0, "gross_profit_factor": 1.0},
            "symbols": {"BTC-USDT": {"net_pnl": 999.0}},
            "risk": {"closed_trade_max_drawdown_dollars": 0.0, "m2m_max_drawdown_dollars": None, "m2m_status": "NOT_APPLICABLE"},
        }
        report = ValidationGates.audit_run(
            strategy=self.strategy,
            mode="ALL_SIGNALS",
            symbols=["BTC-USDT"],
            start="2024-01-01",
            end="2024-02-01",
            sim_result=sim_res,
            analysis=analysis,
            signals=[],
            smoke_test=True,
        )
        self.assertEqual(report.gates["GATE_C_EXECUTION"].status, "FAIL")
        self.assertEqual(report.overall_status, "FAIL")


if __name__ == "__main__":
    unittest.main()
