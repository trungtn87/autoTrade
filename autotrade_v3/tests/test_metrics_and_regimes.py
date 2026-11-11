"""Unit tests for performance metrics calculation and regime segmentation."""
import unittest
import pandas as pd
from autotrade_v3.analysis.metrics import calculate_metrics
from autotrade_v3.analysis.regimes import (
    REGIMES,
    classify_timestamp,
    evaluate_regimes,
    split_trades_by_regime,
)
from autotrade_v3.engine.execution_simulator import TradeRecord


def make_dummy_trade(
    trade_id: str,
    net_pnl: float,
    entry_ms: int = 1700000000000,
    exit_ms: int = 1700000900000,
    ambiguous: bool = False,
) -> TradeRecord:
    dt_entry = pd.to_datetime(entry_ms, unit="ms", utc=True)
    dt_exit = pd.to_datetime(exit_ms, unit="ms", utc=True)
    return TradeRecord(
        trade_id=trade_id,
        leg_id=f"{trade_id}_L1",
        symbol="BTC-USDT",
        combo_id=1,
        combo_name="C1",
        timeframe="1h",
        side="BUY",
        leg_type="SINGLE",
        entry_time_ms=entry_ms,
        entry_time_dt=dt_entry,
        entry_price=100.0,
        exit_time_ms=exit_ms,
        exit_time_dt=dt_exit,
        exit_price=102.0 if net_pnl > 0 else 98.0,
        exit_reason="TP" if net_pnl > 0 else "SL",
        qty=1.0,
        notional=100.0,
        margin=1.0,
        gross_pnl=net_pnl + 0.1,
        fee_total=0.1,
        net_pnl=net_pnl,
        return_pct=net_pnl / 100.0,
        intrabar_ambiguous=ambiguous,
        bars_held=1,
        provider="BINANCE",
    )


class TestMetricsAndRegimes(unittest.TestCase):
    def test_metrics_calculation(self):
        trades = [
            make_dummy_trade("T1", 2.0, entry_ms=100, exit_ms=200),
            make_dummy_trade("T2", -1.0, entry_ms=300, exit_ms=400),
            make_dummy_trade("T3", 3.0, entry_ms=500, exit_ms=600),
            make_dummy_trade("T4", -2.0, entry_ms=700, exit_ms=800, ambiguous=True),
        ]
        m = calculate_metrics(trades, initial_equity=1000.0)
        self.assertEqual(m.total_trades, 4)
        self.assertEqual(m.winning_trades, 2)
        self.assertEqual(m.losing_trades, 2)
        self.assertAlmostEqual(m.win_rate_pct, 50.0)
        self.assertAlmostEqual(m.net_pnl, 2.0)  # 2 - 1 + 3 - 2 = 2
        # Profit factor: wins (2 + 3 = 5) / losses (1 + 2 = 3) = 1.67
        self.assertAlmostEqual(m.profit_factor, 1.67, places=2)
        # Intrabar ambiguous: 1 of 4 = 25%
        self.assertEqual(m.intrabar_ambiguous_trades, 1)
        self.assertAlmostEqual(m.intrabar_ambiguous_pct, 25.0)

    def test_regime_classification(self):
        # Development: 2024-06-01
        dev_ms = int(pd.Timestamp("2024-06-01T00:00:00Z").value // 1_000_000)
        self.assertEqual(classify_timestamp(dev_ms), "DEVELOPMENT")

        # Validation: 2026-01-15
        val_ms = int(pd.Timestamp("2026-01-15T00:00:00Z").value // 1_000_000)
        self.assertEqual(classify_timestamp(val_ms), "VALIDATION")

        # Holdout: 2026-07-01
        hold_ms = int(pd.Timestamp("2026-07-01T00:00:00Z").value // 1_000_000)
        self.assertEqual(classify_timestamp(hold_ms), "HOLDOUT")

        # Out of bounds: 2022-01-01
        old_ms = int(pd.Timestamp("2022-01-01T00:00:00Z").value // 1_000_000)
        self.assertEqual(classify_timestamp(old_ms), "OUT_OF_BOUNDS")

    def test_evaluate_regimes(self):
        dev_ms = int(pd.Timestamp("2024-06-01T00:00:00Z").value // 1_000_000)
        val_ms = int(pd.Timestamp("2026-01-15T00:00:00Z").value // 1_000_000)
        trades = [
            make_dummy_trade("T_DEV", 5.0, entry_ms=dev_ms, exit_ms=dev_ms + 900_000),
            make_dummy_trade("T_VAL", 3.0, entry_ms=val_ms, exit_ms=val_ms + 900_000),
        ]
        reg_results = evaluate_regimes(trades)
        self.assertEqual(reg_results["DEVELOPMENT"]["total_trades"], 1)
        self.assertEqual(reg_results["DEVELOPMENT"]["net_pnl"], 5.0)
        self.assertEqual(reg_results["VALIDATION"]["total_trades"], 1)
        self.assertEqual(reg_results["VALIDATION"]["net_pnl"], 3.0)
        self.assertEqual(reg_results["HOLDOUT"]["total_trades"], 0)


if __name__ == "__main__":
    unittest.main()
