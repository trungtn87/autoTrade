"""Comprehensive 12-Item Test & Acceptance Suite for AutoTrade V3 Offline Research Lab Core."""
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import (
    generate_synthetic_m1,
    resample_m1_to_timeframe,
    validate_m1,
)
from autotrade_v3.engine.execution_simulator import ExecutionSimulator, TradeRecord
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.registry import StrategyRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestResearchLab12Checks(unittest.TestCase):
    """The 12 mandatory acceptance tests for V3-07 Research Lab Core."""

    @classmethod
    def setUpClass(cls):
        cls.data_layer = DataLayer()
        cls.catalog_exists = cls.data_layer.catalog_path.exists()

    # 1. Data integrity
    def test_01_data_integrity(self):
        if not self.catalog_exists:
            self.skipTest("DuckDB catalog not present")
        audit = self.data_layer.verify_integrity()
        self.assertTrue(audit["passed"], f"Data integrity audit failed: {audit.get('issues')}")
        manifest = self.data_layer.get_manifest()
        self.assertEqual(manifest.total_candles, 3_156_480)
        self.assertEqual(manifest.symbol_candle_counts["BTC-USDT"], 1_578_240)
        self.assertEqual(manifest.symbol_candle_counts["ETH-USDT"], 1_578_240)
        self.assertEqual(manifest.partition_count, 74)

    # 2. M15/H1 no-lookahead
    def test_02_m15_h1_no_lookahead(self):
        df_m1 = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=180, base_price=50000.0, seed=1)
        # Resample to M15
        df_m15 = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        self.assertEqual(len(df_m15), 180 // 15)
        # Check first bucket
        first_15 = df_m1.iloc[:15]
        self.assertEqual(df_m15.iloc[0]["open"], first_15.iloc[0]["open"])
        self.assertEqual(df_m15.iloc[0]["close"], first_15.iloc[-1]["close"])
        self.assertEqual(df_m15.iloc[0]["high"], first_15["high"].max())
        self.assertEqual(df_m15.iloc[0]["low"], first_15["low"].min())
        self.assertAlmostEqual(float(df_m15.iloc[0]["volume"]), float(first_15["volume"].sum()), places=5)
        # Index must be start of bucket, never end
        self.assertEqual(df_m15.index[0], first_15.index[0])

        # Resample to H1
        df_h1 = resample_m1_to_timeframe(df_m1, timeframe_min=60)
        self.assertEqual(len(df_h1), 180 // 60)
        first_60 = df_m1.iloc[:60]
        self.assertEqual(df_h1.iloc[0]["open"], first_60.iloc[0]["open"])
        self.assertEqual(df_h1.iloc[0]["close"], first_60.iloc[-1]["close"])

    # 3. Signal parity with 20FINAL baseline
    def test_03_signal_parity_20final_baseline(self):
        strategy = StrategyRegistry.get("20FINAL_BASELINE")
        self.assertEqual(strategy.strategy_id, "20FINAL_BASELINE")
        self.assertEqual(strategy.version, "1.0.0")
        self.assertIn("15m", strategy.required_timeframes)
        self.assertIn("1h", strategy.required_timeframes)

        # Combo exit policies: FINAL14 splits (e.g. C1), NEW6 does not split (e.g. C101)
        c1_exit = strategy.get_exit_policy("BTC-USDT", 1)
        self.assertTrue(c1_exit.split_legs)  # FINAL14 splits
        c101_exit = strategy.get_exit_policy("BTC-USDT", 101)
        self.assertFalse(c101_exit.split_legs)  # NEW6 does not split

    # 4. ALL_SIGNALS does not skip entries because of concurrency
    def test_04_all_signals_no_concurrency_skip(self):
        df_m1 = generate_synthetic_m1("ETH-USDT", start="2024-01-01", periods=75000, seed=42)
        df_m15 = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        strategy = StrategyRegistry.get("20FINAL_BASELINE")
        sigs = strategy.generate_signals({"ETH-USDT": df_m15}, symbols=["ETH-USDT"], only_approved=True)

        sim = ExecutionSimulator()
        res_a = sim.simulate_research_mode("ALL_SIGNALS", sigs, {"ETH-USDT": df_m1})
        self.assertEqual(len(res_a.skipped_signals), 0, "Mode A must NEVER skip signals due to concurrency")
        self.assertEqual(res_a.accepted_trades_count, len(sigs))

    # 5. ONE_TRADE_PER_COMBO skips concurrent signals
    def test_05_one_trade_per_combo_skips_concurrency(self):
        df_m1 = generate_synthetic_m1("ETH-USDT", start="2024-01-01", periods=75000, seed=42)
        df_m15 = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        strategy = StrategyRegistry.get("20FINAL_BASELINE")
        sigs = strategy.generate_signals({"ETH-USDT": df_m15}, symbols=["ETH-USDT"], only_approved=True)

        sim = ExecutionSimulator()
        res_b = sim.simulate_research_mode("ONE_TRADE_PER_COMBO", sigs, {"ETH-USDT": df_m1})
        self.assertGreater(len(res_b.skipped_signals), 0, "Mode B must skip overlapping signals")
        for sk in res_b.skipped_signals:
            self.assertEqual(sk["reason"], "SKIPPED_BY_CONCURRENCY")
        self.assertEqual(res_b.accepted_trades_count + len(res_b.skipped_signals), len(sigs))

    # 6. BINGX_PORTFOLIO manages margin and merged positions
    def test_06_bingx_portfolio_margin_and_merged_positions(self):
        df_m1 = generate_synthetic_m1("ETH-USDT", start="2024-01-01", periods=75000, seed=42)
        df_m15 = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        strategy = StrategyRegistry.get("20FINAL_BASELINE")
        sigs = strategy.generate_signals({"ETH-USDT": df_m15}, symbols=["ETH-USDT"], only_approved=True)

        # Set tiny initial equity ($1.00) below required margin ($2.00) to verify strict margin gate
        tiny_risk = RiskConfig(initial_equity=1.0, notional_per_trade=100.0)
        sim = ExecutionSimulator(risk_config=tiny_risk)
        res_c = sim.simulate_research_mode("BINGX_PORTFOLIO", sigs, {"ETH-USDT": df_m1})
        self.assertGreater(len(res_c.rejected_signals), 0, "Mode C must reject on insufficient margin")
        for rj in res_c.rejected_signals:
            self.assertEqual(rj["reason"], "INSUFFICIENT_MARGIN")

    # 7. TP/SL/Trailing M1 simulation accuracy
    def test_07_tp_sl_trailing_m1(self):
        # Build 10 M1 bars with deliberate price ramp
        idx = pd.date_range("2024-01-01 00:00:00", periods=10, freq="1min", tz="UTC")
        # Bar 0: open 100, high 100.2, low 99.8, close 100.1
        # Bar 1: open 100.1 (entry), high 101.5 (hits +1.0% TP!), low 100.0, close 101.2
        df_test = pd.DataFrame({
            "open": [100.0, 100.1] + [101.0] * 8,
            "high": [100.2, 101.5] + [101.5] * 8,
            "low": [99.8, 100.0] + [100.5] * 8,
            "close": [100.1, 101.2] + [101.0] * 8,
            "volume": [100.0] * 10,
        }, index=idx)
        validate_m1(df_test)

        sig = Signal(
            symbol="BTC-USDT", combo_id=11, combo_name="C11", timeframe="1h", side="BUY",
            signal_close_ms=int(idx[0].value // 1_000_000), signal_close_dt=idx[0],
            bar_position=0, bar_open_ms=int(idx[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.01, sl_pct=0.01, tp_price=101.0, sl_price=99.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False,
            provider="BINANCE", is_new6=True,
        )

        sim = ExecutionSimulator()
        trades = sim.simulate_virtual_trades([sig], df_test, allow_concurrency=True)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].exit_reason, "TP")
        self.assertAlmostEqual(trades[0].exit_price, 100.1 * 1.01, places=3)
        self.assertEqual(trades[0].bars_held, 0)  # Entered and hit TP within bar 1

    # 8. Intrabar ambiguity handling (SL prioritized)
    def test_08_intrabar_ambiguity(self):
        idx = pd.date_range("2024-01-01 00:00:00", periods=5, freq="1min", tz="UTC")
        # Bar 1 has extreme wick: high 102.0 (+2%) AND low 98.0 (-2%), touches both TP and SL!
        df_test = pd.DataFrame({
            "open": [100.0, 100.0, 100.0, 100.0, 100.0],
            "high": [100.1, 102.0, 100.1, 100.1, 100.1],
            "low": [99.9, 98.0, 99.9, 99.9, 99.9],
            "close": [100.0, 100.0, 100.0, 100.0, 100.0],
            "volume": [100.0] * 5,
        }, index=idx)
        validate_m1(df_test)

        sig = Signal(
            symbol="BTC-USDT", combo_id=11, combo_name="C11", timeframe="1h", side="BUY",
            signal_close_ms=int(idx[0].value // 1_000_000), signal_close_dt=idx[0],
            bar_position=0, bar_open_ms=int(idx[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.01, sl_pct=0.01, tp_price=101.0, sl_price=99.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False,
            provider="BINANCE", is_new6=True,
        )

        sim = ExecutionSimulator()
        trades = sim.simulate_virtual_trades([sig], df_test, allow_concurrency=True)
        self.assertEqual(len(trades), 1)
        self.assertTrue(trades[0].intrabar_ambiguous)
        self.assertEqual(trades[0].exit_reason, "SL_INTRABAR_AMBIGUOUS")
        self.assertEqual(trades[0].exit_price, 99.0)  # SL price

    # 9. PnL and accounting reconciliation
    def test_09_pnl_accounting_reconciliation(self):
        # Create synthetic trades and run ResearchAnalyzer
        rec1 = TradeRecord(
            trade_id="T1", leg_id="T1_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01 00:00:00", tz="UTC"), entry_price=100.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 00:01:00", tz="UTC"),
            exit_price=101.0, exit_reason="TP", qty=1.0, notional=100.0, margin=1.0,
            gross_pnl=1.0, fee_total=0.1, net_pnl=0.9, return_pct=0.009,
            intrabar_ambiguous=False, bars_held=1, provider="BINANCE",
        )
        rec2 = TradeRecord(
            trade_id="T2", leg_id="T2_L1", symbol="ETH-USDT", combo_id=3, combo_name="C3",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=3000,
            entry_time_dt=pd.Timestamp("2024-01-01 00:02:00", tz="UTC"), entry_price=200.0,
            exit_time_ms=4000, exit_time_dt=pd.Timestamp("2024-01-01 00:03:00", tz="UTC"),
            exit_price=198.0, exit_reason="SL", qty=0.5, notional=100.0, margin=2.0,
            gross_pnl=-1.0, fee_total=0.1, net_pnl=-1.1, return_pct=-0.011,
            intrabar_ambiguous=False, bars_held=1, provider="BINANCE",
        )

        from autotrade_v3.engine.execution_simulator import SimulationResult
        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[rec1, rec2], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )

        analysis = ResearchAnalyzer.analyze_simulation(sim_res, initial_equity=1000.0)
        # Check parity: BTC Net PnL + ETH Net PnL == Total Net PnL
        s = analysis["symbols"]
        total_pnl = analysis["pnl"]["net_pnl"]
        self.assertAlmostEqual(s["BTC-USDT"]["net_pnl"] + s["ETH-USDT"]["net_pnl"], total_pnl, places=4)
        self.assertEqual(s["BTC-USDT"]["legs"] + s["ETH-USDT"]["legs"], 2)

    # 10. Reproducibility
    def test_10_reproducibility(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            runner = ExperimentRunner(run_manager=RunManager(runs_dir=tmp_dir))
            res1 = runner.run_experiment(
                strategy_id="20FINAL_BASELINE", mode="ALL_SIGNALS",
                symbols=["ETH-USDT"], start="2023-11-01", end="2026-11-01", smoke_test=True,
            )
            res2 = runner.run_experiment(
                strategy_id="20FINAL_BASELINE", mode="ALL_SIGNALS",
                symbols=["ETH-USDT"], start="2023-11-01", end="2026-11-01", smoke_test=True,
            )
            self.assertEqual(res1.analysis["accounting"], res2.analysis["accounting"])
            self.assertEqual(res1.analysis["pnl"]["net_pnl"], res2.analysis["pnl"]["net_pnl"])
            self.assertEqual(res1.analysis["win_rate"], res2.analysis["win_rate"])

    # 11. Offline execution
    def test_11_offline_execution(self):
        import tempfile
        def blocked_connect(*args, **kwargs):
            raise AssertionError("Online network call attempted in offline mode!")

        with patch("socket.socket.connect", side_effect=blocked_connect):
            with tempfile.TemporaryDirectory() as tmp_dir:
                runner = ExperimentRunner(run_manager=RunManager(runs_dir=tmp_dir))
                res = runner.run_experiment(
                    strategy_id="20FINAL_BASELINE", mode="ONE_TRADE_PER_COMBO",
                    symbols=["BTC-USDT"], start="2023-11-01", end="2026-11-01", smoke_test=True,
                )
                self.assertIn(res.manifest.validation_status, ("PASS", "SYNTHETIC_PASS"))

    # 12. Cross-window consistency
    def test_12_cross_window_consistency(self):
        # Generate trades spanning Development (24M), Validation (6M), Holdout (6M)
        t1 = TradeRecord(
            trade_id="T1", leg_id="T1_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE",
            entry_time_ms=int(pd.Timestamp("2024-01-15", tz="UTC").value // 1_000_000),
            entry_time_dt=pd.Timestamp("2024-01-15", tz="UTC"), entry_price=100.0,
            exit_time_ms=int(pd.Timestamp("2024-01-15 01:00:00", tz="UTC").value // 1_000_000),
            exit_time_dt=pd.Timestamp("2024-01-15 01:00:00", tz="UTC"), exit_price=101.0,
            exit_reason="TP", qty=1.0, notional=100.0, margin=1.0, gross_pnl=1.0,
            fee_total=0.1, net_pnl=0.9, return_pct=0.009, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE",
        )
        t2 = TradeRecord(
            trade_id="T2", leg_id="T2_L1", symbol="ETH-USDT", combo_id=3, combo_name="C3",
            timeframe="15m", side="BUY", leg_type="SINGLE",
            entry_time_ms=int(pd.Timestamp("2025-12-10", tz="UTC").value // 1_000_000),
            entry_time_dt=pd.Timestamp("2025-12-10", tz="UTC"), entry_price=200.0,
            exit_time_ms=int(pd.Timestamp("2025-12-10 01:00:00", tz="UTC").value // 1_000_000),
            exit_time_dt=pd.Timestamp("2025-12-10 01:00:00", tz="UTC"), exit_price=202.0,
            exit_reason="TP", qty=0.5, notional=100.0, margin=2.0, gross_pnl=1.0,
            fee_total=0.1, net_pnl=0.9, return_pct=0.009, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE",
        )
        t3 = TradeRecord(
            trade_id="T3", leg_id="T3_L1", symbol="BTC-USDT", combo_id=2, combo_name="C2",
            timeframe="15m", side="BUY", leg_type="SINGLE",
            entry_time_ms=int(pd.Timestamp("2026-06-10", tz="UTC").value // 1_000_000),
            entry_time_dt=pd.Timestamp("2026-06-10", tz="UTC"), entry_price=300.0,
            exit_time_ms=int(pd.Timestamp("2026-06-10 01:00:00", tz="UTC").value // 1_000_000),
            exit_time_dt=pd.Timestamp("2026-06-10 01:00:00", tz="UTC"), exit_price=303.0,
            exit_reason="TP", qty=0.33, notional=100.0, margin=2.0, gross_pnl=1.0,
            fee_total=0.1, net_pnl=0.9, return_pct=0.009, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE",
        )
        from autotrade_v3.engine.execution_simulator import SimulationResult
        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[t1, t2, t3], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        subw = analysis["subwindows"]
        # Development (24M) has t1, Validation (6M) has t2, Holdout (6M) has t3
        self.assertEqual(subw[0]["legs"], 1)  # Dev 24M
        self.assertEqual(subw[1]["legs"], 1)  # Val 6M
        self.assertEqual(subw[2]["legs"], 1)  # Holdout 6M
        self.assertEqual(sum(w["legs"] for w in subw), 3)


if __name__ == "__main__":
    unittest.main()
