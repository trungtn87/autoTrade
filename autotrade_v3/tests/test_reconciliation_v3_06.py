"""Unit tests for AutoTrade V3-06 Cross-Backtest Reconciliation."""
import json
from pathlib import Path
import unittest
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestCrossBacktestReconciliationV306(unittest.TestCase):
    """Regression test suite for V3-06 reconciliation artifacts and mathematical parity."""

    def setUp(self):
        self.out_dir = REPO_ROOT / "runs" / "v3_crossrun_reconciliation"

    def test_all_11_deliverable_artifacts_exist(self):
        expected_files = [
            "signal_reconciliation.csv",
            "entry_reconciliation.csv",
            "trade_reconciliation.csv",
            "leg_reconciliation.csv",
            "exit_reconciliation.csv",
            "ambiguity_reconciliation.csv",
            "pnl_reconciliation.json",
            "slippage_verification.json",
            "run_manifest.json",
            "comparison_report.html",
            "validation_report.md",
        ]
        for f in expected_files:
            file_path = self.out_dir / f
            self.assertTrue(file_path.exists(), f"Missing required deliverable: {f}")
            self.assertGreater(file_path.stat().st_size, 0, f"File {f} is empty")

    def test_signal_count_is_exactly_5984(self):
        sig_csv = self.out_dir / "signal_reconciliation.csv"
        df = pd.read_csv(sig_csv)
        self.assertEqual(len(df), 5984, f"Expected exactly 5,984 signals, got {len(df)}")
        self.assertEqual(df["signal_id"].nunique(), 5984, "Signal IDs must be uniquely identifiable")
        self.assertEqual((df["symbol"] == "BTC-USDT").sum(), 2575)
        self.assertEqual((df["symbol"] == "ETH-USDT").sum(), 3409)

    def test_pnl_parity_holds_strictly(self):
        pnl_json = self.out_dir / "pnl_reconciliation.json"
        with open(pnl_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Virtual mode parity
        v = data["virtual_mode"]
        self.assertEqual(v["btc_legs"] + v["eth_legs"], v["total_legs"])
        self.assertAlmostEqual(v["btc_net_pnl"] + v["eth_net_pnl"], v["total_net_pnl"], places=3)
        self.assertEqual(v["total_legs"], 6273)
        self.assertAlmostEqual(v["total_net_pnl"], 284.19, places=2)

        # Portfolio mode parity
        p = data["portfolio_mode"]
        self.assertEqual(p["btc_legs"] + p["eth_legs"], p["total_legs"])
        self.assertAlmostEqual(p["btc_net_pnl"] + p["eth_net_pnl"], p["total_net_pnl"], places=3)
        self.assertEqual(p["total_legs"], 6265)
        self.assertAlmostEqual(p["total_net_pnl"], 282.19, places=2)

    def test_leg_multipliers_new6_and_final14(self):
        pnl_json = self.out_dir / "pnl_reconciliation.json"
        with open(pnl_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        combos = data["breakdown_by_combo"]
        # NEW6: 1 leg per trade
        self.assertEqual(combos["NEW6"]["virtual_legs"], combos["NEW6"]["virtual_trades"] * 1)
        self.assertEqual(combos["NEW6"]["portfolio_legs"], combos["NEW6"]["portfolio_trades"] * 1)

        # FINAL14: 2 legs per trade
        self.assertEqual(combos["FINAL14"]["virtual_legs"], combos["FINAL14"]["virtual_trades"] * 2)
        self.assertEqual(combos["FINAL14"]["portfolio_legs"], combos["FINAL14"]["portfolio_trades"] * 2)

    def test_slippage_breakeven_within_range(self):
        slip_json = self.out_dir / "slippage_verification.json"
        with open(slip_json, "r", encoding="utf-8") as f:
            data = json.load(f)

        be_slip = data["simulated_breakeven_slippage_per_side_pct"]
        self.assertGreater(be_slip, 0.030, "Breakeven slippage must be greater than 0.030%")
        self.assertLess(be_slip, 0.035, "Breakeven slippage must be less than 0.035%")
        self.assertAlmostEqual(data["theoretical_flat_volume_breakeven_v3_04_pct"], 0.0274, places=3)


if __name__ == "__main__":
    unittest.main()
