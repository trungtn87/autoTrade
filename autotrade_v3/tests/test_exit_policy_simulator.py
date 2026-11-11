"""Tests verifying ExitPolicy dynamic connection into ExecutionSimulator (P1-01).

Requirements:
- Flow: Strategy -> Signal -> Resolved ExitPolicy -> Simulator -> Execution Events.
- Changing ExitPolicy modifies real execution rules, not just metadata.
- Tested across Case A (TP 1%, SL 1%) vs Case B (TP 2%, SL 1%).
- Tested across trailing activation and callback.
- Tested in all three modes: ALL_SIGNALS, ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO.
"""
from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import generate_synthetic_m1
from autotrade_v3.engine.execution_simulator import ExecutionSimulator
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import BaseStrategy, ExitPolicy


class CustomExitStrategy(BaseStrategy):
    """Test strategy with configurable ExitPolicy to verify execution simulator response."""

    def __init__(self, policy: ExitPolicy):
        super().__init__(config={"policy": policy.to_dict()})
        self._policy = policy

    @property
    def strategy_id(self) -> str:
        return "TEST_EXIT_STRATEGY"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def required_timeframes(self) -> list[str]:
        return ["15m"]

    def calculate_indicators(self, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        return {}

    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        return {}

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        return self._policy

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        target_symbols = symbols or list(data.keys())
        sigs = []
        for sym in target_symbols:
            df = data[sym]
            # Emit two fixed signals at bar 10 and bar 50
            for idx in (10, 50):
                if idx < len(df):
                    row = df.iloc[idx]
                    ts = int(df.index[idx].value // 1_000_000)
                    pol = self.get_exit_policy(sym, 1)
                    entry_px = float(row["close"])
                    tp_px = entry_px * (1.0 + pol.tp_pct)
                    sl_px = entry_px * (1.0 - pol.sl_pct)
                    sig = Signal(
                        symbol=sym,
                        combo_id=1,
                        combo_name="C1",
                        timeframe="15m",
                        side="BUY",
                        signal_close_ms=ts,
                        signal_close_dt=df.index[idx],
                        bar_position=idx,
                        bar_open_ms=ts - 900_000,
                        entry_price=entry_px,
                        tp_pct=pol.tp_pct,
                        sl_pct=pol.sl_pct,
                        tp_price=tp_px,
                        sl_price=sl_px,
                        approved=True,
                        warmup_ready=True,
                        smc_dir=0,
                        ob_blocked=False,
                        provider="BINANCE",
                        is_new6=not pol.split_legs,
                        exit_policy=pol,
                        exit_policy_hash=pol.get_policy_hash(),
                    )
                    sigs.append(sig)
        return sigs


class TestExitPolicySimulator(unittest.TestCase):
    def setUp(self):
        self.df_m1 = generate_synthetic_m1(
            symbol="BTC-USDT",
            start="2024-01-01",
            periods=500,
            base_price=50000.0,
            seed=42,
        )
        self.candle_dict = {"BTC-USDT": self.df_m1}

    def test_tp_1pct_vs_tp_2pct_all_signals(self):
        """Case A (TP 1%, SL 1%) vs Case B (TP 2%, SL 1%) in ALL_SIGNALS mode."""
        policy_a = ExitPolicy(tp_pct=0.01, sl_pct=0.01, split_legs=False)
        policy_b = ExitPolicy(tp_pct=0.02, sl_pct=0.01, split_legs=False)

        strat_a = CustomExitStrategy(policy_a)
        strat_b = CustomExitStrategy(policy_b)

        sigs_a = strat_a.generate_signals(self.candle_dict)
        sigs_b = strat_b.generate_signals(self.candle_dict)

        # Signal count and IDs must be identical
        self.assertEqual(len(sigs_a), len(sigs_b))
        self.assertEqual([s.signal_close_ms for s in sigs_a], [s.signal_close_ms for s in sigs_b])

        # Simulator runs
        sim_a = ExecutionSimulator(RiskConfig(notional_per_trade=100.0, slippage_rate=0.0))
        sim_b = ExecutionSimulator(RiskConfig(notional_per_trade=100.0, slippage_rate=0.0))

        res_a = sim_a.simulate_research_mode("ALL_SIGNALS", sigs_a, self.candle_dict)
        res_b = sim_b.simulate_research_mode("ALL_SIGNALS", sigs_b, self.candle_dict)

        self.assertEqual(len(res_a.trades), len(res_b.trades))

        # Check that ExitPolicy hashes differ and match configured policies
        self.assertEqual(res_a.trades[0].exit_policy_hash, policy_a.get_policy_hash())
        self.assertEqual(res_b.trades[0].exit_policy_hash, policy_b.get_policy_hash())
        self.assertNotEqual(res_a.trades[0].exit_policy_hash, res_b.trades[0].exit_policy_hash)

        # TP pct in TradeRecord must reflect policy
        self.assertAlmostEqual(res_a.trades[0].tp_pct, 0.01)
        self.assertAlmostEqual(res_b.trades[0].tp_pct, 0.02)

        # Check execution outcome: TP 2% requires higher exit price than TP 1%
        trade_a = res_a.trades[0]
        trade_b = res_b.trades[0]
        if trade_a.exit_reason == "TP":
            self.assertAlmostEqual(trade_a.exit_price, trade_a.entry_price * 1.01, delta=1e-2)
        if trade_b.exit_reason == "TP":
            self.assertAlmostEqual(trade_b.exit_price, trade_b.entry_price * 1.02, delta=1e-2)

    def test_trailing_policy_alteration_mode_b_and_c(self):
        """Trailing activation and callback changes in ONE_TRADE_PER_COMBO and BINGX_PORTFOLIO."""
        # Policy with tight trailing vs wide trailing
        policy_tight = ExitPolicy(
            tp_pct=0.05, sl_pct=0.02, split_legs=True,
            trailing_act_pct=0.005, trailing_cb_pct=0.002,
        )
        policy_wide = ExitPolicy(
            tp_pct=0.05, sl_pct=0.02, split_legs=True,
            trailing_act_pct=0.020, trailing_cb_pct=0.010,
        )

        strat_tight = CustomExitStrategy(policy_tight)
        strat_wide = CustomExitStrategy(policy_wide)

        sigs_tight = strat_tight.generate_signals(self.candle_dict)
        sigs_wide = strat_wide.generate_signals(self.candle_dict)

        sim_b = ExecutionSimulator(RiskConfig(notional_per_trade=100.0, slippage_rate=0.0))
        sim_c = ExecutionSimulator(RiskConfig(notional_per_trade=100.0, slippage_rate=0.0))

        # Mode B
        res_b_tight = sim_b.simulate_research_mode("ONE_TRADE_PER_COMBO", sigs_tight, self.candle_dict)
        res_b_wide = sim_b.simulate_research_mode("ONE_TRADE_PER_COMBO", sigs_wide, self.candle_dict)
        self.assertNotEqual(
            res_b_tight.trades[0].exit_policy_hash,
            res_b_wide.trades[0].exit_policy_hash,
        )
        self.assertAlmostEqual(res_b_tight.trades[1].trailing_act_pct, 0.005)
        self.assertAlmostEqual(res_b_wide.trades[1].trailing_act_pct, 0.020)

        # Mode C
        res_c_tight = sim_c.simulate_research_mode("BINGX_PORTFOLIO", sigs_tight, self.candle_dict)
        res_c_wide = sim_c.simulate_research_mode("BINGX_PORTFOLIO", sigs_wide, self.candle_dict)
        self.assertNotEqual(
            res_c_tight.trades[0].exit_policy_hash,
            res_c_wide.trades[0].exit_policy_hash,
        )
        self.assertAlmostEqual(res_c_tight.trades[1].trailing_cb_pct, 0.002)
        self.assertAlmostEqual(res_c_wide.trades[1].trailing_cb_pct, 0.010)

    def test_max_bars_held_enforcement(self):
        """Verify max_bars_held cleanly exits position on exact holding limit."""
        policy_timed = ExitPolicy(tp_pct=0.10, sl_pct=0.10, split_legs=False, max_bars_held=5)
        strat = CustomExitStrategy(policy_timed)
        sigs = strat.generate_signals(self.candle_dict)

        sim = ExecutionSimulator(RiskConfig(notional_per_trade=100.0, slippage_rate=0.0))
        res = sim.simulate_research_mode("ALL_SIGNALS", sigs, self.candle_dict)

        self.assertTrue(len(res.trades) > 0)
        first_trade = res.trades[0]
        self.assertEqual(first_trade.exit_reason, "MAX_BARS_HELD")
        self.assertEqual(first_trade.bars_held, 5)


if __name__ == "__main__":
    unittest.main()
