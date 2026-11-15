"""Unit and integration tests for 20FINAL_FIXED_V1 and FIXED_TP_SL simulator."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.engine.execution_simulator import ExecutionSimulator
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.precision import (
    BingXContractRules,
    round_price_half_up,
    round_qty_down,
    validate_min_order,
)
from autotrade_v3.strategy.registry import StrategyRegistry


def _make_m1_bars(prices: list[float], start_ms: int = 1_700_000_040_000) -> pd.DataFrame:
    idx = pd.to_datetime([start_ms + i * 60_000 for i in range(len(prices))], unit="ms", utc=True)
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.002 for p in prices],
            "low": [p * 0.998 for p in prices],
            "close": prices,
            "volume": [100.0] * len(prices),
        },
        index=idx,
    )


def _make_signal(
    *,
    signal_id: str,
    symbol: str,
    combo_id: int,
    combo_name: str,
    side: str,
    signal_close_ms: int,
    tp_pct: float = 0.02,
    sl_pct: float = 0.01,
    is_new6: bool = False,
    exit_policy=None,
) -> Signal:
    return Signal(
        symbol=symbol,
        combo_id=combo_id,
        combo_name=combo_name,
        timeframe="1h",
        side=side,
        signal_close_ms=signal_close_ms,
        signal_close_dt=pd.Timestamp(signal_close_ms, unit="ms", tz="UTC"),
        bar_position=0,
        bar_open_ms=signal_close_ms - 3_600_000 + 1,
        entry_price=100.0,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=100.0 * (1 + tp_pct) if side == "BUY" else 100.0 * (1 - tp_pct),
        sl_price=100.0 * (1 - sl_pct) if side == "BUY" else 100.0 * (1 + sl_pct),
        approved=True,
        warmup_ready=True,
        smc_dir=1 if side == "BUY" else -1,
        ob_blocked=False,
        provider="BINANCE",
        is_new6=is_new6,
        exit_policy=exit_policy,
        signal_id=signal_id,
    )


def test_20final_fixed_v1_preserves_20final_entries_and_disables_trailing():
    fixed = StrategyRegistry.get("20FINAL_FIXED_V1")
    base = StrategyRegistry.get("20FINAL_BASELINE")

    assert fixed.strategy_id == "20FINAL_FIXED_V1"
    assert fixed.version == "1"
    assert fixed.get_signal_spec_hash() == base.get_signal_spec_hash()
    assert fixed.get_exit_spec_hash() != base.get_exit_spec_hash()

    fixed_combos = {c.combo_key: c for c in fixed.spec().combos}
    base_combos = {c.combo_key: c for c in base.spec().combos}
    assert len(fixed_combos) == 20
    assert set(fixed_combos.keys()) == set(base_combos.keys())

    for key, fc in fixed_combos.items():
        bc = base_combos[key]
        assert fc.symbol == bc.symbol
        assert fc.combo_id == bc.combo_id
        assert fc.native_timeframe == bc.native_timeframe
        assert fc.entry_variant == bc.entry_variant
        assert fc.layer2 == bc.layer2
        assert fc.exit_policy.tp_pct == pytest.approx(bc.exit_policy.tp_pct)
        assert fc.exit_policy.sl_pct == pytest.approx(bc.exit_policy.sl_pct)
        assert fc.exit_policy.split_legs is False
        assert fc.exit_policy.leg_weights == (1.0,)
        assert fc.exit_policy.trailing_act_pct == 0.0
        assert fc.exit_policy.trailing_cb_pct == 0.0


def test_tp_sl_computed_from_filled_entry_with_slippage():
    fixed = StrategyRegistry.get("20FINAL_FIXED_V1")
    ep = fixed.get_exit_policy("BTC-USDT", 1)  # tp=0.02, sl=0.008, split_legs=False

    # Bar 0: 100.0, Bar 1: 100.0, Bar 2: 103.0 (hits TP)
    bars = _make_m1_bars([100.0, 100.0, 103.0, 103.0])
    sig_close_ms = int(bars.index[0].timestamp() * 1000) - 1  # enters on bar 0 open
    sig = _make_signal(
        signal_id="SIG_TP_TEST",
        symbol="BTC-USDT",
        combo_id=1,
        combo_name="C1",
        side="BUY",
        signal_close_ms=sig_close_ms,
        tp_pct=ep.tp_pct,
        sl_pct=ep.sl_pct,
        exit_policy=ep,
    )

    cost_model = CostModel.from_profile_name("BASE", is_real_data=False)
    sim = ExecutionSimulator(
        risk_config=RiskConfig(initial_equity=1000.0, notional_per_trade=100.0),
        cost_model=cost_model,
        ambiguity_policy="CONSERVATIVE",
    )
    res = sim.simulate_research_mode("ONE_TRADE_PER_COMBO", [sig], {"BTC-USDT": bars})
    assert len(res.trades) == 1
    tr = res.trades[0]
    assert tr.leg_type == "SINGLE"
    assert tr.entry_px_fill > tr.entry_px_raw  # positive slippage on BUY entry
    assert tr.tp_price == pytest.approx(tr.entry_px_fill * (1.0 + ep.tp_pct))
    assert tr.sl_price == pytest.approx(tr.entry_px_fill * (1.0 - ep.sl_pct))
    assert tr.exit_reason == "TP"


def test_bingx_portfolio_merges_same_symbol_side_and_reproduces_deterministically():
    fixed = StrategyRegistry.get("20FINAL_FIXED_V1")
    ep1 = fixed.get_exit_policy("BTC-USDT", 1)
    ep2 = fixed.get_exit_policy("BTC-USDT", 2)

    # Bar 0: 100.0, Bar 1: 101.0, Bar 2: 103.0 (both TP)
    bars = _make_m1_bars([100.0, 101.0, 103.0, 103.0])
    t0 = int(bars.index[0].timestamp() * 1000) - 1
    t1 = int(bars.index[1].timestamp() * 1000) - 1
    s1 = _make_signal(
        signal_id="SIG_C1",
        symbol="BTC-USDT",
        combo_id=1,
        combo_name="C1",
        side="BUY",
        signal_close_ms=t0,
        tp_pct=ep1.tp_pct,
        sl_pct=ep1.sl_pct,
        exit_policy=ep1,
    )
    s2 = _make_signal(
        signal_id="SIG_C2",
        symbol="BTC-USDT",
        combo_id=2,
        combo_name="C2",
        side="BUY",
        signal_close_ms=t1,
        tp_pct=ep2.tp_pct,
        sl_pct=ep2.sl_pct,
        exit_policy=ep2,
    )

    cost_model = CostModel.from_profile_name("IDEAL", is_real_data=False)
    sim1 = ExecutionSimulator(
        risk_config=RiskConfig(initial_equity=1000.0, notional_per_trade=100.0),
        cost_model=cost_model,
        ambiguity_policy="CONSERVATIVE",
    )
    sim2 = ExecutionSimulator(
        risk_config=RiskConfig(initial_equity=1000.0, notional_per_trade=100.0),
        cost_model=cost_model,
        ambiguity_policy="CONSERVATIVE",
    )
    r1 = sim1.simulate_research_mode("BINGX_PORTFOLIO", [s1, s2], {"BTC-USDT": bars})
    r2 = sim2.simulate_research_mode("BINGX_PORTFOLIO", [s1, s2], {"BTC-USDT": bars})

    assert len(r1.trades) == 2
    assert all(t.leg_type == "SINGLE" for t in r1.trades)
    # Merged VWAP entry should be 200.0 / (100/100 + 100/101)
    expected_vwap = 200.0 / (100.0 / 100.0 + 100.0 / 101.0)
    assert r1.trades[0].entry_price == pytest.approx(expected_vwap)
    assert r1.trades[1].entry_price == pytest.approx(expected_vwap)
    assert [t.net_pnl for t in r1.trades] == [t.net_pnl for t in r2.trades]


def test_bingx_contract_precision_helpers():
    rules = BingXContractRules(
        symbol="BTC-USDT",
        quantity_precision=4,
        price_precision=1,
        trade_min_quantity=0.0001,
        trade_min_usdt=2.0,
    )
    assert round_price_half_up(90123.45, rules.price_precision) == 90123.5
    assert round_qty_down(0.001199, rules.quantity_precision) == 0.0011
    ok, reason = validate_min_order(0.0011, 90123.5, rules)
    assert ok is True and reason == "OK"
    ok_small, reason_small = validate_min_order(0.00001, 90123.5, rules)
    assert ok_small is False and reason_small == "BELOW_TRADE_MIN_QUANTITY"


def test_rebind_exit_policies_updates_exit_policy_hash():
    from autotrade_v3.runstore.signal_cache import rebind_exit_policies

    base = StrategyRegistry.get("20FINAL_BASELINE")
    fixed = StrategyRegistry.get("20FINAL_FIXED_V1")
    ep_base = base.get_exit_policy("BTC-USDT", 1)
    ep_fixed = fixed.get_exit_policy("BTC-USDT", 1)
    assert ep_base.get_policy_hash() != ep_fixed.get_policy_hash()

    sig = _make_signal(
        signal_id="SIG_REBIND",
        symbol="BTC-USDT",
        combo_id=1,
        combo_name="C1",
        side="BUY",
        signal_close_ms=1_700_000_000_000,
        tp_pct=ep_base.tp_pct,
        sl_pct=ep_base.sl_pct,
        exit_policy=ep_base,
    )
    object.__setattr__(sig, "exit_policy_hash", ep_base.get_policy_hash())
    rebound = rebind_exit_policies([sig], fixed)
    assert len(rebound) == 1
    assert rebound[0].exit_policy_hash == ep_fixed.get_policy_hash()
    assert rebound[0].exit_policy.split_legs is False
