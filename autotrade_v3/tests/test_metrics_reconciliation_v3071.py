"""Tests for Profit Factor, accounting reconciliation, and 24/6/6 subwindow attribution (P1-02, P2-01)."""
from __future__ import annotations

import unittest
import pandas as pd

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.engine.execution_simulator import SimulationResult, TradeRecord


class TestMetricsReconciliationV3071(unittest.TestCase):
    def test_net_vs_gross_profit_factor_and_zero_denominator(self):
        """Verify distinct Net vs Gross Profit Factor and zero denominator handling."""
        # Trade 1: Gross PnL +$10, Fee $2 -> Net PnL +$8
        # Trade 2: Gross PnL +$5, Fee $2 -> Net PnL +$3
        # Trade 3: Gross PnL -$6, Fee $2 -> Net PnL -$8
        t1 = TradeRecord(
            trade_id="T1", leg_id="T1_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=110.0, exit_reason="TP", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=10.0, fee_total=2.0, net_pnl=8.0, return_pct=0.08, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE", exit_policy_hash="h1",
        )
        t2 = TradeRecord(
            trade_id="T2", leg_id="T2_L1", symbol="BTC-USDT", combo_id=2, combo_name="C2",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=3000,
            entry_time_dt=pd.Timestamp("2024-01-02", tz="UTC"), entry_price=100.0,
            exit_time_ms=4000, exit_time_dt=pd.Timestamp("2024-01-02 01:00", tz="UTC"),
            exit_price=105.0, exit_reason="TP", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=5.0, fee_total=2.0, net_pnl=3.0, return_pct=0.03, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE", exit_policy_hash="h2",
        )
        t3 = TradeRecord(
            trade_id="T3", leg_id="T3_L1", symbol="ETH-USDT", combo_id=3, combo_name="C3",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=5000,
            entry_time_dt=pd.Timestamp("2024-01-03", tz="UTC"), entry_price=100.0,
            exit_time_ms=6000, exit_time_dt=pd.Timestamp("2024-01-03 01:00", tz="UTC"),
            exit_price=94.0, exit_reason="SL", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=-6.0, fee_total=2.0, net_pnl=-8.0, return_pct=-0.08, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE", exit_policy_hash="h3",
        )

        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[t1, t2, t3], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        pnl = analysis["pnl"]

        # Math checks
        # Gross wins = 10 + 5 = 15; Gross loss = 6 -> Gross PF = 15 / 6 = 2.5
        # Net wins = 8 + 3 = 11; Net loss = 8 -> Net PF = 11 / 8 = 1.375 (rounded to 1.38)
        self.assertAlmostEqual(pnl["gross_profit_factor"], 2.50, places=2)
        self.assertAlmostEqual(pnl["net_profit_factor"], 1.38, places=2)
        self.assertEqual(pnl["profit_factor"], pnl["net_profit_factor"])  # Net is default

        # Accounting balance
        self.assertAlmostEqual(pnl["gross_pnl"], 9.0)
        self.assertAlmostEqual(pnl["total_fees"], 6.0)
        self.assertAlmostEqual(pnl["net_pnl"], 3.0)
        self.assertTrue(pnl["accounting_verified"])

        # Symbol reconciliation: BTC net + ETH net == Total Net
        btc_net = analysis["symbols"]["BTC-USDT"]["net_pnl"]
        eth_net = analysis["symbols"]["ETH-USDT"]["net_pnl"]
        self.assertAlmostEqual(btc_net + eth_net, pnl["net_pnl"])

        # Test zero denominator (only winning trades)
        sim_res_no_loss = SimulationResult(
            mode="ALL_SIGNALS", trades=[t1, t2], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis_no_loss = ResearchAnalyzer.analyze_simulation(sim_res_no_loss)
        self.assertEqual(analysis_no_loss["pnl"]["net_profit_factor"], "UNDEFINED_NO_LOSS")
        self.assertEqual(analysis_no_loss["pnl"]["gross_profit_factor"], "UNDEFINED_NO_LOSS")

    def test_subwindows_24_6_6_attribution(self):
        """Verify standard 24/6/6 subwindows: Dev 24M, Val 6M, Holdout 6M."""
        # Window bounds:
        # Dev: 2023-11-01 to 2025-11-01
        # Val: 2025-11-01 to 2026-05-01
        # Holdout: 2026-05-01 to 2026-11-01

        t_dev = TradeRecord(
            trade_id="T_DEV", leg_id="T_DEV_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE",
            entry_time_ms=int(pd.Timestamp("2024-06-01", tz="UTC").value // 1_000_000),
            entry_time_dt=pd.Timestamp("2024-06-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=int(pd.Timestamp("2024-06-01 01:00", tz="UTC").value // 1_000_000),
            exit_time_dt=pd.Timestamp("2024-06-01 01:00", tz="UTC"), exit_price=105.0,
            exit_reason="TP", qty=1.0, notional=100.0, margin=2.0, gross_pnl=5.0, fee_total=0.1,
            net_pnl=4.9, return_pct=0.049, intrabar_ambiguous=False, bars_held=60, provider="BINANCE",
            exit_policy_hash="h_dev",
        )
        t_val = TradeRecord(
            trade_id="T_VAL", leg_id="T_VAL_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE",
            entry_time_ms=int(pd.Timestamp("2025-12-01", tz="UTC").value // 1_000_000),
            entry_time_dt=pd.Timestamp("2025-12-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=int(pd.Timestamp("2025-12-01 01:00", tz="UTC").value // 1_000_000),
            exit_time_dt=pd.Timestamp("2025-12-01 01:00", tz="UTC"), exit_price=105.0,
            exit_reason="TP", qty=1.0, notional=100.0, margin=2.0, gross_pnl=5.0, fee_total=0.1,
            net_pnl=4.9, return_pct=0.049, intrabar_ambiguous=False, bars_held=60, provider="BINANCE",
            exit_policy_hash="h_val",
        )
        t_hold = TradeRecord(
            trade_id="T_HOLD", leg_id="T_HOLD_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE",
            entry_time_ms=int(pd.Timestamp("2026-07-01", tz="UTC").value // 1_000_000),
            entry_time_dt=pd.Timestamp("2026-07-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=int(pd.Timestamp("2026-07-01 01:00", tz="UTC").value // 1_000_000),
            exit_time_dt=pd.Timestamp("2026-07-01 01:00", tz="UTC"), exit_price=105.0,
            exit_reason="TP", qty=1.0, notional=100.0, margin=2.0, gross_pnl=5.0, fee_total=0.1,
            net_pnl=4.9, return_pct=0.049, intrabar_ambiguous=False, bars_held=60, provider="BINANCE",
            exit_policy_hash="h_hold",
        )

        sim_res = SimulationResult(
            mode="ALL_SIGNALS", trades=[t_dev, t_val, t_hold], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis = ResearchAnalyzer.analyze_simulation(sim_res)
        subw = analysis["subwindows"]

        self.assertEqual(len(subw), 3)
        self.assertEqual(subw[0]["subwindow"], "Development (24M)")
        self.assertEqual(subw[0]["legs"], 1)
        self.assertEqual(subw[1]["subwindow"], "Validation (6M)")
        self.assertEqual(subw[1]["legs"], 1)
        self.assertEqual(subw[2]["subwindow"], "Holdout (6M)")
        self.assertEqual(subw[2]["legs"], 1)

    def test_drawdown_labeling_mode_a_vs_c(self):
        """Mode A has m2m_max_drawdown_dollars=None; Mode C has valid continuous M2M value."""
        t1 = TradeRecord(
            trade_id="T1", leg_id="T1_L1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
            timeframe="15m", side="BUY", leg_type="SINGLE", entry_time_ms=1000,
            entry_time_dt=pd.Timestamp("2024-01-01", tz="UTC"), entry_price=100.0,
            exit_time_ms=2000, exit_time_dt=pd.Timestamp("2024-01-01 01:00", tz="UTC"),
            exit_price=105.0, exit_reason="TP", qty=1.0, notional=100.0, margin=2.0,
            gross_pnl=5.0, fee_total=0.1, net_pnl=4.9, return_pct=0.049, intrabar_ambiguous=False,
            bars_held=60, provider="BINANCE", exit_policy_hash="h1",
        )
        sim_res_a = SimulationResult(
            mode="ALL_SIGNALS", trades=[t1], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
        )
        analysis_a = ResearchAnalyzer.analyze_simulation(sim_res_a)
        self.assertIsNone(analysis_a["risk"]["m2m_max_drawdown_dollars"])
        self.assertEqual(analysis_a["risk"]["m2m_status"], "NOT_APPLICABLE")

        sim_res_c = SimulationResult(
            mode="BINGX_PORTFOLIO", trades=[t1], events=[],
            accepted_signals=[], skipped_signals=[], rejected_signals=[],
            snapshots=pd.DataFrame(), ambiguity_stats={},
            summary_dict={"m2m_max_drawdown_dollars": 12.5, "m2m_max_drawdown_pct": 1.25},
        )
        analysis_c = ResearchAnalyzer.analyze_simulation(sim_res_c)
        self.assertEqual(analysis_c["risk"]["m2m_max_drawdown_dollars"], 12.5)
        self.assertEqual(analysis_c["risk"]["m2m_status"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
