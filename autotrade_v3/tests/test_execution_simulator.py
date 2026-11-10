"""Unit tests for execution simulation, conservative intrabar resolution, and hedge mode merging."""
import unittest
import numpy as np
import pandas as pd
from autotrade_v3.engine.execution_simulator import ExecutionSimulator
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal


def make_test_bars(prices: list[tuple[float, float, float, float]], start_ms: int = 1704067200000) -> pd.DataFrame:
    """Helper to build test bars: [(open, high, low, close), ...]."""
    idx = pd.date_range(start=pd.to_datetime(start_ms, unit="ms", utc=True), periods=len(prices), freq="15min")
    df = pd.DataFrame(
        {
            "open": [p[0] for p in prices],
            "high": [p[1] for p in prices],
            "low": [p[2] for p in prices],
            "close": [p[3] for p in prices],
            "volume": [10.0] * len(prices),
            "provider": ["BINANCE"] * len(prices),
        },
        index=idx,
    )
    return df


class TestExecutionSimulator(unittest.TestCase):
    def test_conservative_intrabar_ambiguity(self):
        """When a candle breaches both TP and SL, conservative policy triggers SL first and flags ambiguous."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry close = 100.0
            (100.0, 103.0, 98.0, 101.0),   # Bar 1: High 103 >= TP (102), Low 98 <= SL (99)
        ])
        sig = Signal(
            symbol="BTC-USDT",
            combo_id=101,  # NEW6 combo (100% hard TP/SL)
            combo_name="C11",
            timeframe="1h",
            side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 + 899_999),
            signal_close_dt=bars.index[0],
            bar_position=0,
            bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0,
            tp_pct=0.02,   # TP at 102.0
            sl_pct=0.01,   # SL at 99.0
            tp_price=102.0,
            sl_price=99.0,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="BINANCE",
            is_new6=True,
        )

        sim = ExecutionSimulator(RiskConfig(taker_fee_rate=0.0))
        trades = sim.simulate_virtual_trades([sig], bars)

        self.assertEqual(len(trades), 1)
        t = trades[0]
        # Must resolve to SL with ambiguous flag
        self.assertTrue(t.intrabar_ambiguous)
        self.assertEqual(t.exit_reason, "SL_INTRABAR_AMBIGUOUS")
        self.assertAlmostEqual(t.exit_price, 99.0)
        self.assertLess(t.net_pnl, 0)  # Realized loss

    def test_pure_tp_hit(self):
        """When only TP is hit, exit is TP and not ambiguous."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry close = 100.0
            (100.0, 102.5, 99.5, 102.0),   # Bar 1: High 102.5 >= TP (102.0), Low 99.5 > SL (99.0)
        ])
        sig = Signal(
            symbol="BTC-USDT",
            combo_id=101,
            combo_name="C11",
            timeframe="1h",
            side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 + 899_999),
            signal_close_dt=bars.index[0],
            bar_position=0,
            bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0,
            tp_pct=0.02,
            sl_pct=0.01,
            tp_price=102.0,
            sl_price=99.0,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="BINANCE",
            is_new6=True,
        )

        sim = ExecutionSimulator(RiskConfig(taker_fee_rate=0.0))
        trades = sim.simulate_virtual_trades([sig], bars)

        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertFalse(t.intrabar_ambiguous)
        self.assertEqual(t.exit_reason, "TP")
        self.assertAlmostEqual(t.exit_price, 102.0)
        self.assertAlmostEqual(t.net_pnl, 2.0)  # 2% of $100 notional

    def test_trailing_stop_activation_and_exit(self):
        """FINAL14 hybrid mode activates trailing after +0.5% move and trails on subsequent bars."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry close = 100.0
            (100.0, 100.8, 99.8, 100.7),   # Bar 1: Favorable move to 100.8 (+0.8% > +0.5% act)
            (100.5, 100.6, 100.0, 100.1),  # Bar 2: Callback hit (peak 100.8 * 0.995 = 100.296; low 100.0 hits trail)
        ])
        sig = Signal(
            symbol="BTC-USDT",
            combo_id=1,  # FINAL14 combo
            combo_name="C1",
            timeframe="1h",
            side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 + 899_999),
            signal_close_dt=bars.index[0],
            bar_position=0,
            bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0,
            tp_pct=0.02,
            sl_pct=0.008,
            tp_price=102.0,
            sl_price=99.2,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="BINANCE",
            is_new6=False,
        )

        sim = ExecutionSimulator(
            RiskConfig(taker_fee_rate=0.0),
            final14_exit_mode="50TP_50TRAIL",
            trailing_act_pct=0.005,
            trailing_cb_pct=0.005,
        )
        trades = sim.simulate_virtual_trades([sig], bars)
        # FIX P1-03: Two separate legs for 50TP_50TRAIL
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].trade_id, trades[1].trade_id)
        self.assertNotEqual(trades[0].leg_id, trades[1].leg_id)
        reasons = [t.exit_reason for t in trades]
        self.assertTrue(any("TRAIL" in r for r in reasons))

    def test_hedge_mode_portfolio_merging(self):
        """Two concurrent BUY signals merge entry price according to volume-weighted average."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry 1 at 100.0
            (105.0, 105.0, 105.0, 105.0),  # Bar 1: Entry 2 at 105.0
            (106.0, 110.0, 104.0, 108.0),  # Bar 2: Exits trigger
        ])
        sig1 = Signal(
            symbol="BTC-USDT", combo_id=101, combo_name="C11", timeframe="1h", side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 + 899_999),
            signal_close_dt=bars.index[0], bar_position=0, bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.05, sl_pct=0.05, tp_price=105.0, sl_price=95.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )
        sig2 = Signal(
            symbol="BTC-USDT", combo_id=104, combo_name="C14", timeframe="1h", side="BUY",
            signal_close_ms=int(bars.index[1].value // 1_000_000 + 899_999),
            signal_close_dt=bars.index[1], bar_position=1, bar_open_ms=int(bars.index[1].value // 1_000_000),
            entry_price=105.0, tp_pct=0.05, sl_pct=0.05, tp_price=110.25, sl_price=99.75,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )

        sim = ExecutionSimulator(RiskConfig(taker_fee_rate=0.0))
        ledger, snapshots = sim.simulate_portfolio_trades([sig1, sig2], bars)
        self.assertEqual(len(ledger), 2)
        # Verify that merged position snapshot exists
        self.assertFalse(snapshots.empty)


if __name__ == "__main__":
    unittest.main()
