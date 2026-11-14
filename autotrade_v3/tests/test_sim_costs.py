"""Hand-calculated unit tests for CostModel and cost_profiles.json (V3-13)."""
from __future__ import annotations

import random

import pytest

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.sim.costs import (
    CostModel,
    load_cost_profile,
    validate_profile_for_real_data,
)


def test_cost_model_buy_leg_hand_calculated():
    cm = CostModel.from_profile_name("TEST_ONLY", is_real_data=False)
    # TEST_ONLY: half_spread=1.5bps, entry_market=2.0bps, tp=0.5bps, fee_taker=0.0005, fee_maker=0.0002 (tp is maker)
    # BUY entry raw = 100.0 -> +3.5 bps = 100.035
    # BUY TP trigger = 102.0 -> -2.0 bps = 102.0 * (1 - 0.0002) = 101.9796
    cb = cm.evaluate_leg(
        symbol="BTC-USDT",
        side="BUY",
        notional=100.0,
        entry_px_raw=100.0,
        exit_px_trigger=102.0,
        exit_reason="TP",
    )
    exp_entry_fill = 100.0 * (1.0 + 3.5 / 10000.0)
    exp_exit_fill = 102.0 * (1.0 - 2.0 / 10000.0)
    exp_qty = 100.0 / exp_entry_fill
    exp_spread = (100.0 * 1.5 / 10000.0 + 102.0 * 1.5 / 10000.0) * exp_qty
    exp_slip = (100.0 * 2.0 / 10000.0 + 102.0 * 0.5 / 10000.0) * exp_qty
    exp_fee_entry = 100.0 * 0.0005
    exp_fee_exit = exp_qty * exp_exit_fill * 0.0002
    exp_gross_raw = (102.0 - 100.0) * exp_qty
    exp_net = exp_gross_raw - exp_fee_entry - exp_fee_exit - exp_spread - exp_slip

    assert cb.entry_px_raw == pytest.approx(100.0)
    assert cb.entry_px_fill == pytest.approx(exp_entry_fill, abs=1e-12)
    assert cb.exit_px_trigger == pytest.approx(102.0)
    assert cb.exit_px_fill == pytest.approx(exp_exit_fill, abs=1e-12)
    assert cb.entry_px_fill > cb.entry_px_raw  # adverse BUY entry
    assert cb.exit_px_fill < cb.exit_px_trigger  # adverse BUY exit
    assert cb.spread_cost == pytest.approx(exp_spread, abs=1e-12)
    assert cb.slippage_cost == pytest.approx(exp_slip, abs=1e-12)
    assert cb.fee_entry == pytest.approx(exp_fee_entry, abs=1e-12)
    assert cb.fee_exit == pytest.approx(exp_fee_exit, abs=1e-12)
    assert cb.funding_cost == 0.0
    assert cb.gross_pnl_raw == pytest.approx(exp_gross_raw, abs=1e-12)
    assert cb.net_pnl == pytest.approx(exp_net, abs=1e-12)


def test_cost_model_sell_leg_adverse_direction_and_stop_slippage():
    cm = CostModel.from_profile_name("TEST_ONLY", is_real_data=False)
    # SELL entry raw = 200.0 -> -3.5 bps = 199.93
    # SELL SL trigger = 204.0 -> +(1.5 + 4.0) bps = +5.5 bps = 204.1122
    cb = cm.evaluate_leg(
        symbol="ETH-USDT",
        side="SELL",
        notional=100.0,
        entry_px_raw=200.0,
        exit_px_trigger=204.0,
        exit_reason="SL",
    )
    exp_entry_fill = 200.0 * (1.0 - 3.5 / 10000.0)
    exp_exit_fill = 204.0 * (1.0 + 5.5 / 10000.0)
    exp_qty = 100.0 / exp_entry_fill
    assert cb.entry_px_fill == pytest.approx(exp_entry_fill, abs=1e-12)
    assert cb.exit_px_fill == pytest.approx(exp_exit_fill, abs=1e-12)
    assert cb.entry_px_fill < cb.entry_px_raw  # adverse SELL entry
    assert cb.exit_px_fill > cb.exit_px_trigger  # adverse SELL exit
    assert cb.fee_exit == pytest.approx(exp_qty * exp_exit_fill * 0.0005, abs=1e-12)  # stop is taker


def test_cost_model_different_slippage_by_order_type():
    cm = CostModel.from_profile_name("TEST_ONLY", is_real_data=False)
    p = cm.params_for("BTC-USDT")
    assert p.slippage_bps_for("entry_market") == 2.0
    assert p.slippage_bps_for("tp") == 0.5
    assert p.slippage_bps_for("stop") == 4.0
    assert p.slippage_bps_for("trailing_stop") == 3.0


def test_cost_model_zero_params_matches_legacy_risk_config():
    risk = RiskConfig(taker_fee_rate=0.0005, slippage_rate=0.0)
    cm = CostModel.from_profile_name("IDEAL", is_real_data=True)
    for side in ("BUY", "SELL"):
        cb = cm.evaluate_leg(
            symbol="BTC-USDT",
            side=side,
            notional=50.0,
            entry_px_raw=50000.0,
            exit_px_trigger=50500.0,
            exit_reason="TP",
        )
        qty = 50.0 / 50000.0
        legacy_fee = risk.calc_fee(50.0, True) + risk.calc_fee(qty * 50500.0, True)
        legacy_gross = (50500.0 - 50000.0) * qty if side == "BUY" else (50000.0 - 50500.0) * qty
        assert cb.entry_px_fill == 50000.0
        assert cb.exit_px_fill == 50500.0
        assert cb.spread_cost == 0.0
        assert cb.slippage_cost == 0.0
        assert cb.funding_cost == 0.0
        assert cb.fee_total == pytest.approx(legacy_fee, abs=1e-12)
        assert cb.net_pnl == pytest.approx(legacy_gross - legacy_fee, abs=1e-12)


def test_cost_model_net_pnl_invariant_seeded_random():
    rng = random.Random(1337)
    cm = CostModel.from_uniform_params(
        fee_taker=0.0005,
        fee_maker=0.0002,
        maker_order_types=("tp",),
        half_spread_bps=1.2,
        slippage_bps=2.5,
        funding_bps_per_8h=1.0,
    )
    reasons = ["TP", "SL", "SL_INTRABAR_AMBIGUOUS", "TRAIL", "MAX_BARS_HELD", "FORCE_CLOSE"]
    for _ in range(200):
        side = rng.choice(["BUY", "SELL"])
        entry_raw = rng.uniform(100.0, 70000.0)
        exit_trig = entry_raw * rng.uniform(0.95, 1.05)
        notional = rng.uniform(25.0, 200.0)
        reason = rng.choice(reasons)
        t0 = 1700000000000
        t1 = t0 + rng.randint(60_000, 86_400_000)
        cb = cm.evaluate_leg(
            symbol=rng.choice(["BTC-USDT", "ETH-USDT"]),
            side=side,
            notional=notional,
            entry_px_raw=entry_raw,
            exit_px_trigger=exit_trig,
            exit_reason=reason,
            entry_time_ms=t0,
            exit_time_ms=t1,
        )
        recomputed = (
            cb.gross_pnl_raw
            - cb.fee_entry
            - cb.fee_exit
            - cb.spread_cost
            - cb.slippage_cost
            - cb.funding_cost
        )
        assert abs(cb.net_pnl - recomputed) < 1e-9


def test_unconfirmed_and_test_only_profiles_rejected_on_real_data():
    for name in ("BASE", "STRESS"):
        prof = load_cost_profile(name)
        assert prof.status == "UNCONFIRMED"
        assert prof.values == {}
        with pytest.raises(ValueError, match="BLOCKED_ON_OWNER"):
            validate_profile_for_real_data(prof)

    test_prof = load_cost_profile("TEST_ONLY")
    assert test_prof.status == "TEST_ONLY"
    with pytest.raises(ValueError, match="TEST_ONLY"):
        validate_profile_for_real_data(test_prof)

    ideal_prof = load_cost_profile("IDEAL")
    assert ideal_prof.status == "CONFIRMED"
    validate_profile_for_real_data(ideal_prof)
