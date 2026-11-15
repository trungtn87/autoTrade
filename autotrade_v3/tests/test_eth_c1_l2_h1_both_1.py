"""Verification tests for ETH-USDT/C1 L2 H1_BOTH_1 integration.

Covers all 5 minimum pre-merge checks:
1. 3-year ETH/C1 signal_id replay parity against P3 H1_BOTH_1.
2. All 19 other combos' signal_id list unchanged vs 20FINAL_BASELINE.
3. Zero look-ahead and strict 1H/4H candle boundary alignment.
4. Exact H1 thresholds (BUY <= 1.5%, SELL >= -6.0%), L2 rejection reasons,
   missing-indicator error handling, and rollback flag (`enable_eth_c1_h1_l2=False`).
5. Fixed TP 2% / SL 0.666667% (no trailing) and ExecutionSimulator parity.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.execution_simulator import ExecutionSimulator, RiskConfig
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.eth_c1_h1_both_1 import (
    ETH_C1_STRATEGY_ID,
    REASON_H1_BUY_EMA_SLOPE,
    REASON_H1_SELL_EMA_DISTANCE,
    REASON_SMC_VETO,
    EthC1H1Both1Strategy,
    compute_eth_c1_l2_features,
    evaluate_eth_c1_l2,
)
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
P3_H1_BOTH_1_SIGNALS_PARQUET = (
    REPO_ROOT
    / "runs"
    / "c1_p3_ab"
    / "RUN_20261115_081733_880144_P3_H1_BOTH_1_ALL_SIGNALS_IDEAL_20231101_20261101"
    / "signals.parquet"
)


def _make_synthetic_m15(n_bars: int = 900, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01 00:00:00+00:00", periods=n_bars, freq="15min")
    rets = rng.normal(0.0001, 0.003, size=n_bars)
    close = 2500.0 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0005, 0.003, size=n_bars))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0005, 0.003, size=n_bars))
    vol = rng.uniform(50.0, 500.0, size=n_bars)
    return pd.DataFrame(
        {
            "open_time": (idx.asi8 // 1_000_000).astype(np.int64),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "provider": "SYNTH",
        },
        index=idx,
    )


def test_01_registry_and_20final_baseline_lock_untouched() -> None:
    strat = StrategyRegistry.get(ETH_C1_STRATEGY_ID)
    assert isinstance(strat, EthC1H1Both1Strategy)
    assert strat.strategy_id == ETH_C1_STRATEGY_ID
    assert strat.enable_eth_c1_h1_l2 is True

    # Locked 20FINAL@1 baseline must load cleanly with unchanged lock hashes
    core = StrategyCoreRegistry.get("20FINAL", "1")
    assert core.spec().strategy_id == "20FINAL"
    assert len(core.spec().combos) == 20


def test_02_thresholds_l2_reasons_missing_data_and_rollback() -> None:
    # BUY boundary: <= 1.5 passes, > 1.5 rejects with H1_BUY_EMA_SLOPE
    assert evaluate_eth_c1_l2("BUY", smc_dir=1, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=1.5).approved is True
    assert evaluate_eth_c1_l2("BUY", smc_dir=0, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=-0.5).approved is True

    dec_buy_fail = evaluate_eth_c1_l2(
        "BUY", smc_dir=1, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=1.500001
    )
    assert dec_buy_fail.approved is False
    assert dec_buy_fail.reason == REASON_H1_BUY_EMA_SLOPE

    # SELL boundary: >= -6.0 passes, < -6.0 rejects with H1_SELL_EMA_DISTANCE
    assert (
        evaluate_eth_c1_l2("SELL", smc_dir=-1, ob_blocked=False, layer2="SMC_VETO", dist_ema150_4h_pct=-6.0).approved
        is True
    )
    dec_sell_fail = evaluate_eth_c1_l2(
        "SELL", smc_dir=-1, ob_blocked=False, layer2="SMC_VETO", dist_ema150_4h_pct=-6.000001
    )
    assert dec_sell_fail.approved is False
    assert dec_sell_fail.reason == REASON_H1_SELL_EMA_DISTANCE

    # Existing SMC_VETO still blocks even when H1 passes
    dec_smc_buy = evaluate_eth_c1_l2(
        "BUY", smc_dir=-1, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=0.5
    )
    assert dec_smc_buy.approved is False
    assert dec_smc_buy.reason == REASON_SMC_VETO

    dec_smc_sell = evaluate_eth_c1_l2(
        "SELL", smc_dir=1, ob_blocked=False, layer2="SMC_VETO", dist_ema150_4h_pct=-1.0
    )
    assert dec_smc_sell.approved is False
    assert dec_smc_sell.reason == REASON_SMC_VETO

    # Rollback mode (enable_h1=False) bypasses H1 while keeping SMC_VETO
    assert (
        evaluate_eth_c1_l2(
            "BUY", smc_dir=1, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=5.0, enable_h1=False
        ).approved
        is True
    )
    assert (
        evaluate_eth_c1_l2(
            "SELL", smc_dir=-1, ob_blocked=False, layer2="SMC_VETO", dist_ema150_4h_pct=-12.0, enable_h1=False
        ).approved
        is True
    )

    # Missing or non-finite indicator data must raise a clear ValueError
    with pytest.raises(ValueError, match="ema150_4h_slope_10bar_pct"):
        evaluate_eth_c1_l2("BUY", smc_dir=1, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=None)
    with pytest.raises(ValueError, match="ema150_4h_slope_10bar_pct"):
        evaluate_eth_c1_l2("BUY", smc_dir=1, ob_blocked=False, layer2="SMC_VETO", slope_10bar_pct=float("nan"))
    with pytest.raises(ValueError, match="dist_ema150_4h_pct"):
        evaluate_eth_c1_l2("SELL", smc_dir=-1, ob_blocked=False, layer2="SMC_VETO", dist_ema150_4h_pct=None)
    with pytest.raises(ValueError, match="dist_ema150_4h_pct"):
        evaluate_eth_c1_l2("SELL", smc_dir=-1, ob_blocked=False, layer2="SMC_VETO", dist_ema150_4h_pct=float("inf"))

    # Empty / malformed M15 input raises ValueError
    with pytest.raises(ValueError, match="missing or empty"):
        compute_eth_c1_l2_features(pd.DataFrame())
    with pytest.raises(ValueError, match="missing required OHLCV"):
        compute_eth_c1_l2_features(pd.DataFrame({"close": [1.0, 2.0]}))


def test_03_zero_lookahead_and_1h_4h_candle_boundary() -> None:
    df_full = _make_synthetic_m15(n_bars=2800, seed=7)
    full_feat = compute_eth_c1_l2_features(df_full)

    # Cut at 03:00 (mid-way through forming 4H bar 00:00..04:00) after >160 4H bars and mutate 03:15..end
    cutoff_1h = pd.Timestamp("2024-01-29 03:00:00+00:00")
    df_mut = df_full.copy()
    mut_mask = df_mut.index >= cutoff_1h
    df_mut.loc[mut_mask, "close"] *= 1.35
    df_mut.loc[mut_mask, "high"] *= 1.40
    df_mut.loc[mut_mask, "low"] *= 0.70
    mut_feat = compute_eth_c1_l2_features(df_mut)

    # All 1H bars strictly before cutoff_1h must have identical 4H EMA150, slope, and dist
    prior_1h = full_feat["d1"].index[full_feat["d1"].index < cutoff_1h]
    for key in ("ema150_4h_1h", "slope_10bar_1h", "dist_ema150_4h_pct"):
        s0 = full_feat[key].loc[prior_1h].dropna()
        assert len(s0) > 0, f"Expected non-empty finite values for {key} before {cutoff_1h}"
        s1 = mut_feat[key].loc[s0.index]
        assert np.allclose(s0.to_numpy(float), s1.to_numpy(float), atol=1e-12, rtol=0.0), (
            f"Look-ahead detected on {key} before {cutoff_1h}"
        )

    # Explicit 4H boundary check:
    # At 1H bar ot=03:00 (closes 04:00), confirmed 4H must be 20:00 (closed 00:00), NOT 00:00 (closes 04:00).
    # At 1H bar ot=04:00 (closes 05:00), confirmed 4H updates to 00:00 (closed 04:00).
    ema_1h = full_feat["ema150_4h_1h"]
    ts_00 = pd.Timestamp("2024-01-29 00:00:00+00:00")
    ts_03 = pd.Timestamp("2024-01-29 03:00:00+00:00")
    ts_04 = pd.Timestamp("2024-01-29 04:00:00+00:00")
    assert math.isfinite(float(ema_1h.loc[ts_00]))
    assert math.isclose(float(ema_1h.loc[ts_00]), float(ema_1h.loc[ts_03]), abs_tol=1e-12)
    assert not math.isclose(float(ema_1h.loc[ts_03]), float(ema_1h.loc[ts_04]), abs_tol=1e-12)


def test_04_exit_policy_fixed_tpsl_and_simulator_execution() -> None:
    strat = EthC1H1Both1Strategy()
    ep_c1 = strat.get_exit_policy("ETH-USDT", 1)
    assert math.isclose(ep_c1.tp_pct, 0.02, abs_tol=1e-12)
    assert math.isclose(ep_c1.sl_pct, 0.00666667, abs_tol=1e-12)
    assert ep_c1.split_legs is False
    assert ep_c1.trailing_act_pct == 0.0
    assert ep_c1.trailing_cb_pct == 0.0
    assert ep_c1.leg_weights == (1.0,)

    # Other combos keep their exact 20FINAL@1 exit policies
    base = Baseline20FinalStrategy()
    for c in base.spec().combos:
        if c.symbol == "ETH-USDT" and c.combo_id == 1:
            continue
        assert strat.get_exit_policy(c.symbol, c.combo_id) == base.get_exit_policy(c.symbol, c.combo_id)

    # Verify ExecutionSimulator executes ETH/C1 signal as 1-leg fixed TP/SL (no trailing)
    dl = DataLayer()
    eth_m15 = dl.query_timeframe("ETH-USDT", "15m", "2023-11-01", "2024-03-01", include_warmup=True)
    eth_m1 = dl.query_timeframe("ETH-USDT", "1m", "2023-11-01", "2024-03-01", include_warmup=False)
    sigs = strat.generate_signals({"ETH-USDT": eth_m15}, symbols=["ETH-USDT"])
    c1_sigs = [s for s in sigs if s.symbol == "ETH-USDT" and s.combo_id == 1]
    assert len(c1_sigs) > 0
    for s in c1_sigs:
        assert math.isclose(s.tp_pct, 0.02, abs_tol=1e-12)
        assert math.isclose(s.sl_pct, 0.00666667, abs_tol=1e-12)

    sim = ExecutionSimulator(risk_config=RiskConfig(initial_equity=1000.0, notional_per_trade=100.0))
    res = sim.simulate_research_mode(mode="ALL_SIGNALS", signals=c1_sigs[:2], candle_dict={"ETH-USDT": eth_m1})
    assert len(res.trades) == len(c1_sigs[:2])
    assert all(t.exit_reason in ("TP", "SL", "SL_INTRABAR_AMBIGUOUS", "WINDOW_END") for t in res.trades)


def test_05_three_year_replay_parity_with_p3_h1_both_1_and_19_combos_unchanged() -> None:
    assert P3_H1_BOTH_1_SIGNALS_PARQUET.exists(), f"Missing P3 reference parquet: {P3_H1_BOTH_1_SIGNALS_PARQUET}"

    dl = DataLayer()
    btc_m15 = dl.query_timeframe("BTC-USDT", "15m", "2023-11-01", "2026-11-01", include_warmup=True)
    eth_m15 = dl.query_timeframe("ETH-USDT", "15m", "2023-11-01", "2026-11-01", include_warmup=True)
    data = {"BTC-USDT": btc_m15, "ETH-USDT": eth_m15}

    strat_pre = EthC1H1Both1Strategy(pre_edge_h1=True)
    strat_post = EthC1H1Both1Strategy(pre_edge_h1=False)
    base = Baseline20FinalStrategy()

    sigs_new = strat_pre.generate_signals(data, symbols=["BTC-USDT", "ETH-USDT"])
    sigs_post = strat_post.generate_signals(data, symbols=["BTC-USDT", "ETH-USDT"])
    sigs_base = base.generate_signals(data, symbols=["BTC-USDT", "ETH-USDT"])

    # 1. Compare full signal_id list for ETH-USDT/C1 against P3 H1_BOTH_1
    p3_df = pd.read_parquet(P3_H1_BOTH_1_SIGNALS_PARQUET)
    p3_c1_ids = p3_df[(p3_df["symbol"] == "ETH-USDT") & (p3_df["combo_id"] == 1)]["signal_id"].tolist()
    new_c1_ids = [s.signal_id for s in sigs_new if s.symbol == "ETH-USDT" and s.combo_id == 1]
    post_c1_ids = [s.signal_id for s in sigs_post if s.symbol == "ETH-USDT" and s.combo_id == 1]

    assert new_c1_ids == p3_c1_ids
    assert post_c1_ids == p3_c1_ids

    # 2. Verify all 19 other combos have identical signal_id list to 20FINAL_BASELINE
    new_other_ids = [s.signal_id for s in sigs_new if not (s.symbol == "ETH-USDT" and s.combo_id == 1)]
    base_other_ids = [s.signal_id for s in sigs_base if not (s.symbol == "ETH-USDT" and s.combo_id == 1)]
    assert new_other_ids == base_other_ids

    # 3. Verify L2 rejection reasons were recorded for H1_BUY_EMA_SLOPE and H1_SELL_EMA_DISTANCE
    reasons = {r["reason"] for r in strat_pre.last_l2_rejections}
    assert REASON_H1_BUY_EMA_SLOPE in reasons
    assert REASON_H1_SELL_EMA_DISTANCE in reasons
