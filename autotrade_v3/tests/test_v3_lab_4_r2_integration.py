"""Unit and regression tests for V3_LAB_4@4.1.0 Round 2 integration (A-R2-01 & A-R2-05)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.lab import (
    V3_LAB_4_BASELINE_VERSION,
    V3_LAB_4_STRATEGY_ID,
    load_v3_lab_4_config,
    load_v3_lab_4_strategy,
)
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import RiskConfig
from autotrade_v3.sim.leg import extract_m1_arrays, simulate_leg
from autotrade_v3.sim.types import ExitRules, LegSpec
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy


def test_registry_and_config_v4_1_1_and_all_rollbacks():
    assert V3_LAB_4_STRATEGY_ID == "V3_LAB_4@4.1.1"
    assert V3_LAB_4_BASELINE_VERSION == "4.0.0"

    cfg411 = load_v3_lab_4_config()
    cfg410 = load_v3_lab_4_config(version="4.1.0")
    cfg401 = load_v3_lab_4_config(version="4.0.1")
    cfg400 = load_v3_lab_4_config(version="4.0.0")
    assert cfg411["version"] == "4.1.1"
    assert cfg410["version"] == "4.1.0"
    assert cfg401["version"] == "4.0.1"
    assert cfg400["version"] == "4.0.0"
    assert cfg400["config_hash"] == "f4ef7b0d3991bcf980eed3304ea2128289a383a647209a64de03a4d1c145a8de"
    assert cfg411["live_eligible"] is False
    assert cfg411["top20_live_modified"] is False
    assert len(cfg411["combos"]) == 30

    keys411 = {(c["symbol"], int(c["combo_id"])) for c in cfg411["combos"]}
    keys401 = {(c["symbol"], int(c["combo_id"])) for c in cfg401["combos"]}
    keys400 = {(c["symbol"], int(c["combo_id"])) for c in cfg400["combos"]}
    assert len(keys411) == 30
    assert keys411 == keys401 == keys400

    assert cfg411["upgraded_combos"]["BTC-USDT/C13"] == "H4_SELL_ONLY_4H_TREND"
    assert cfg411["upgraded_combos"]["ETH-USDT/C14"] == "A-R2-05_ATR_SL_ADJUSTMENT"
    assert cfg411["overrides"]["ETH-USDT/C14"]["audit_status"] == "AUDIT_VERIFIED_BUT_FRAGILE"
    assert cfg411["overrides"]["ETH-USDT/C14"]["live_eligible"] is False
    assert cfg411["btc_c10_policy"] == cfg401["btc_c10_policy"] == cfg400["btc_c10_policy"]

    # 29 combos in 4.1.1 (all except ETH-USDT/C14) must match 4.0.1
    for k in keys401 - {("ETH-USDT", 104)}:
        c411 = next(c for c in cfg411["combos"] if (c["symbol"], int(c["combo_id"])) == k)
        c401 = next(c for c in cfg401["combos"] if (c["symbol"], int(c["combo_id"])) == k)
        assert c411 == c401

    strat411 = load_v3_lab_4_strategy()
    strat410 = load_v3_lab_4_strategy(version="4.1.0")
    strat401 = load_v3_lab_4_strategy(version="4.0.1")
    strat400 = load_v3_lab_4_strategy(version="4.0.0")
    assert strat411.version == "4.1.1"
    assert strat410.version == "4.1.0"
    assert strat401.version == "4.0.1"
    assert strat400.version == "4.0.0"
    for ver in ("4.0.0", "4.0.1", "4.1.0", "4.1.1"):
        assert StrategyRegistry.get("V3_LAB_4", ver).version == ver

    for sym, cid in keys401 - {("ETH-USDT", 104)}:
        assert strat411.get_exit_policy(sym, cid).get_policy_hash() == strat401.get_exit_policy(sym, cid).get_policy_hash()


def test_exit_policy_hash_backward_compatibility():
    ep_plain = ExitPolicy(tp_pct=0.02, sl_pct=0.008, split_legs=False, trailing_act_pct=0.0, trailing_cb_pct=0.0, leg_weights=(1.0,))
    ep_explicit_none = ExitPolicy(
        tp_pct=0.02, sl_pct=0.008, split_legs=False, trailing_act_pct=0.0, trailing_cb_pct=0.0, leg_weights=(1.0,),
        be_trigger_pct=None, be_offset_pct=None,
    )
    ep_be = ExitPolicy(
        tp_pct=0.02, sl_pct=0.008, split_legs=False, trailing_act_pct=0.0, trailing_cb_pct=0.0, leg_weights=(1.0,),
        be_trigger_pct=0.012, be_offset_pct=-0.0014,
    )
    assert ep_plain.get_policy_hash() == ep_explicit_none.get_policy_hash()
    assert ep_plain.get_policy_hash() != ep_be.get_policy_hash()


def test_native_simulate_leg_breakeven_lock_conservative_ambiguity():
    idx = pd.date_range("2025-01-01", periods=3, freq="1min", tz="UTC")
    # SELL at 100.0; BE trigger = 98.8 (-1.2%), BE stop = 99.86 (-0.14%), TP = 98.0 (-2.0%), SL = 100.8 (+0.8%)
    # Bar 0 hits trigger (low=98.7) AND bounces above BE stop (high=99.9) -> conservative BREAKEVEN_STOP on bar 0
    df = pd.DataFrame(
        {"open": [100.0, 99.0, 99.0], "high": [100.1, 99.9, 99.0], "low": [98.7, 98.9, 98.0], "close": [99.5, 99.1, 98.0]},
        index=idx,
    )
    arr = extract_m1_arrays(df)
    out = simulate_leg(
        bars_m1=arr,
        entry_idx=0,
        entry_price=100.0,
        side="SELL",
        leg_spec=LegSpec(leg_id="L1", trade_id="T1", leg_type="SINGLE", notional=100.0, is_trailing=False),
        exit_rules=ExitRules(tp_pct=0.02, sl_pct=0.008, be_trigger_pct=0.012, be_offset_pct=-0.0014),
        ambiguity_policy="CONSERVATIVE",
    )
    assert out.exit_reason == "BREAKEVEN_STOP"
    assert out.exit_bar_idx == 0
    assert out.intrabar_ambiguous is True
    assert out.ambiguity_type == "BE_TRIGGER_AND_STOP_SAME_BAR"
    assert out.exit_price == pytest.approx(99.86)


def test_eth_c15_controller_state_isolation_regression():
    """Regression test for eth_c15_ctrl state leakage across sequential runs."""
    from scripts.run_v3_lab_2_eth_c15_h3_asymmetric import H3AsymmetricController
    from scripts.round2_engine import Round2ResearchSimulator
    from autotrade_v3.strategy.base import Signal

    idx = pd.date_range("2025-01-01", periods=10, freq="1min", tz="UTC")
    df_m1 = pd.DataFrame(
        {"open": np.full(10, 100.0), "high": np.full(10, 100.5), "low": np.full(10, 97.5), "close": np.full(10, 98.0)},
        index=idx,
    )
    arr = extract_m1_arrays(df_m1)
    ctrl = H3AsymmetricController(feat_map={})
    ctrl.last_sell_tp_exit_ms = int(idx[-1].timestamp() * 1000) + 10_000_000  # Simulate dirty state from a prior run

    cm = CostModel.from_profile_name("BASE", is_real_data=True)
    risk = RiskConfig(initial_equity=1000.0, notional_per_trade=100.0, cost_profile="BASE", cost_model=cm)
    sim = Round2ResearchSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE", c15_controller=ctrl)

    sig_t0 = int(idx[0].timestamp() * 1000) - 1
    sig = Signal(
        symbol="ETH-USDT", combo_id=105, combo_name="C15", timeframe="1h", side="SELL",
        bar_position=100, bar_open_ms=sig_t0 - 3_600_000 + 1,
        signal_close_ms=sig_t0, signal_close_dt=str(idx[0]), entry_price=100.0,
        tp_price=98.0, sl_price=100.8, tp_pct=0.02, sl_pct=0.008,
        approved=True, warmup_ready=True, smc_dir=-1, ob_blocked=False, provider="BINANCE",
        is_new6=True,
    )
    res = sim.run_with_arrays(mode="BINGX_PORTFOLIO", signals=[sig], arrays_by_sym={"ETH-USDT": arr})
    # Must reset dirty state so the signal is NOT blocked by a stale prior-run cooldown
    assert len(res.trades) == 1
