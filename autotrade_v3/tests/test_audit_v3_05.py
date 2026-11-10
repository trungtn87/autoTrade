"""Unit tests for V3-05 independent portfolio audit, margin rejection, and M2M drawdown."""
import unittest
import numpy as np
import pandas as pd

from autotrade_v3.analysis.metrics import calculate_metrics
from autotrade_v3.engine.execution_simulator import ExecutionSimulator
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal


def make_test_bars(prices: list[tuple[float, float, float, float]], start_ms: int = 1704067200000) -> pd.DataFrame:
    """Helper to build test M1 bars: [(open, high, low, close), ...]."""
    idx = pd.date_range(start=pd.to_datetime(start_ms, unit="ms", utc=True), periods=len(prices), freq="1min")
    return pd.DataFrame(
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


class TestAuditV305(unittest.TestCase):
    def test_independent_portfolio_margin_rejection(self):
        """When account available margin is exhausted, incoming signals MUST be rejected with INSUFFICIENT_MARGIN."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Signal 1 enters
            (100.0, 100.0, 100.0, 100.0),  # Bar 1: Signal 2 attempts entry
            (100.0, 100.0, 100.0, 100.0),  # Bar 2
        ])
        sig1 = Signal(
            symbol="BTC-USDT", combo_id=1, combo_name="C1", timeframe="15m", side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 - 1),
            signal_close_dt=bars.index[0], bar_position=0, bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.05, sl_pct=0.05, tp_price=105.0, sl_price=95.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )
        sig2 = Signal(
            symbol="BTC-USDT", combo_id=2, combo_name="C2", timeframe="15m", side="BUY",
            signal_close_ms=int(bars.index[1].value // 1_000_000 - 1),
            signal_close_dt=bars.index[1], bar_position=1, bar_open_ms=int(bars.index[1].value // 1_000_000),
            entry_price=100.0, tp_pct=0.05, sl_pct=0.05, tp_price=105.0, sl_price=95.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )

        # Capital = $150, notional = $100, 1x leverage: trade 1 requires $100 margin, leaving only $50 free margin.
        # Trade 2 requires $100 margin, so trade 2 MUST be rejected!
        risk = RiskConfig(initial_equity=150.0, notional_per_trade=100.0, leverage_btc=1.0, leverage_eth=1.0)
        sim = ExecutionSimulator(risk)
        trades, snaps, rejected, summary = sim.simulate_portfolio_independent([sig1, sig2], bars)

        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "INSUFFICIENT_MARGIN")
        self.assertEqual(rejected[0]["combo_id"], 2)
        self.assertEqual(len(trades), 1)  # Only trade 1 opened and closed

    def test_same_bar_trailing_activation_and_callback(self):
        """When trailing activates and calls back in the SAME M1 bar, it flags intrabar_trailing_ambiguous and exits conservatively."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry at 100.0
            (100.0, 101.0, 100.0, 100.2),  # Bar 1: Spikes to 101.0 (+1.0% > +0.5% act) then drops to 100.0 <= trail stop (101 * 0.995 = 100.495)
            (100.0, 100.0, 100.0, 100.0),  # Bar 2
        ])
        sig = Signal(
            symbol="BTC-USDT", combo_id=1, combo_name="C1", timeframe="1h", side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 - 1),
            signal_close_dt=bars.index[0], bar_position=0, bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.05, sl_pct=0.02, tp_price=105.0, sl_price=98.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=False,
        )

        sim = ExecutionSimulator(RiskConfig(taker_fee_rate=0.0), final14_exit_mode="50TP_50TRAIL")
        trades = sim.simulate_virtual_trades([sig], bars)

        self.assertEqual(len(trades), 2)
        trailing_leg = next(t for t in trades if t.leg_type == "TRAIL_HALF")
        self.assertTrue(trailing_leg.intrabar_trailing_ambiguous)
        self.assertEqual(trailing_leg.exit_reason, "TRAIL")

    def test_m2m_drawdown_vs_closed_trade_drawdown(self):
        """Mark-to-market continuous equity captures intra-trade adverse excursion that closed-trade equity misses."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry at 100.0
            (100.0, 100.0, 92.0, 93.0),    # Bar 1: Drops to 92.0 (unrealized loss -$8.0)
            (93.0, 105.0, 93.0, 105.0),    # Bar 2: Recovers and hits TP at 105.0 (+5.0 profit)
        ])
        sig = Signal(
            symbol="BTC-USDT", combo_id=1, combo_name="C1", timeframe="1h", side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 - 1),
            signal_close_dt=bars.index[0], bar_position=0, bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.05, sl_pct=0.10, tp_price=105.0, sl_price=90.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )

        sim = ExecutionSimulator(RiskConfig(initial_equity=1000.0, notional_per_trade=100.0, taker_fee_rate=0.0))
        trades, snaps, rejected, summary = sim.simulate_portfolio_independent([sig], bars)

        metrics = calculate_metrics(trades, initial_equity=1000.0, m2m_snapshots=snaps)
        # Closed-trade drawdown is 0 because the trade ended in profit (+5)
        self.assertAlmostEqual(metrics.max_drawdown_dollars, 0.0)
        # True M2M drawdown is > 0 because of Bar 1 drop to 92.0!
        self.assertGreater(metrics.m2m_max_drawdown_dollars, 5.0)

    def test_multi_combo_position_merging_vwap(self):
        """Two concurrent BUY combos merge entry price into volume-weighted average."""
        bars = make_test_bars([
            (100.0, 100.0, 100.0, 100.0),  # Bar 0: Entry 1 at 100.0 (qty = 100/100 = 1.0)
            (110.0, 110.0, 110.0, 110.0),  # Bar 1: Entry 2 at 110.0 (qty = 100/110 = 0.90909)
            (115.0, 125.0, 114.0, 120.0),  # Bar 2: Exits trigger
        ])
        sig1 = Signal(
            symbol="BTC-USDT", combo_id=101, combo_name="C11", timeframe="1h", side="BUY",
            signal_close_ms=int(bars.index[0].value // 1_000_000 - 1),
            signal_close_dt=bars.index[0], bar_position=0, bar_open_ms=int(bars.index[0].value // 1_000_000),
            entry_price=100.0, tp_pct=0.20, sl_pct=0.10, tp_price=120.0, sl_price=90.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )
        sig2 = Signal(
            symbol="BTC-USDT", combo_id=102, combo_name="C12", timeframe="1h", side="BUY",
            signal_close_ms=int(bars.index[1].value // 1_000_000 - 1),
            signal_close_dt=bars.index[1], bar_position=1, bar_open_ms=int(bars.index[1].value // 1_000_000),
            entry_price=110.0, tp_pct=0.10, sl_pct=0.10, tp_price=121.0, sl_price=99.0,
            approved=True, warmup_ready=True, smc_dir=1, ob_blocked=False, provider="BINANCE", is_new6=True,
        )

        sim = ExecutionSimulator(RiskConfig(taker_fee_rate=0.0))
        trades, snaps, rejected, summary = sim.simulate_portfolio_independent([sig1, sig2], bars)

        self.assertEqual(len(trades), 2)
        # Entry price for both reconciled trades must reflect the merged VWAP ~ 104.76
        for t in trades:
            self.assertGreater(t.entry_price, 100.0)
            self.assertLess(t.entry_price, 110.0)


if __name__ == "__main__":
    unittest.main()
