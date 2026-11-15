"""Differential test for runs/lab/scratch/run_entry_mode_suite.py (AT2-004).

Verifies that P0_ALL_SIGNALS and P1_ONE_TRADE_PER_COMBO from ``run_portfolio_variant``
match the public ``ExecutionSimulator.simulate_research_mode`` engine on a fixed data slice
(net PnL, accepted entries, total legs) with explicit leverage BTC=50, ETH=50.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Literal

import pandas as pd
import pytest

from autotrade_v3.engine.candle_loader import datetime_index_to_ms, generate_synthetic_m1
from autotrade_v3.engine.execution_simulator import ExecutionSimulator
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.leg import extract_m1_arrays


def _load_entry_mode_suite():
    script_path = Path(__file__).resolve().parents[2] / "runs" / "lab" / "scratch" / "run_entry_mode_suite.py"
    spec = importlib.util.spec_from_file_location("run_entry_mode_suite", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_entry_mode_suite"] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_signal(
    *,
    signal_id: str,
    symbol: str,
    combo_id: int,
    combo_name: str,
    side: Literal["BUY", "SELL"],
    bar_idx: int,
    ts_arr,
    df_m1: pd.DataFrame,
    is_new6: bool = False,
) -> Signal:
    entry_px = float(df_m1["close"].iloc[bar_idx])
    tp_pct = 0.008
    sl_pct = 0.006
    tp_px = entry_px * (1.0 + tp_pct) if side == "BUY" else entry_px * (1.0 - tp_pct)
    sl_px = entry_px * (1.0 - sl_pct) if side == "BUY" else entry_px * (1.0 + sl_pct)
    close_ms = int(ts_arr[bar_idx])
    return Signal(
        signal_id=signal_id,
        symbol=symbol,
        combo_id=combo_id,
        combo_name=combo_name,
        timeframe="15m",
        side=side,
        signal_close_ms=close_ms,
        signal_close_dt=pd.Timestamp(df_m1.index[bar_idx]),
        bar_position=bar_idx,
        bar_open_ms=close_ms - 60_000,
        entry_price=entry_px,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=tp_px,
        sl_price=sl_px,
        approved=True,
        warmup_ready=True,
        smc_dir=1 if side == "BUY" else -1,
        ob_blocked=False,
        provider="FINAL14" if not is_new6 else "NEW6",
        is_new6=is_new6,
    )


def _build_fixed_slice():
    btc_m1 = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=1440, base_price=42000.0, seed=101)
    eth_m1 = generate_synthetic_m1("ETH-USDT", start="2024-01-01", periods=1440, base_price=2300.0, seed=202)
    candles = {"BTC-USDT": btc_m1, "ETH-USDT": eth_m1}
    btc_ts = datetime_index_to_ms(btc_m1.index)
    eth_ts = datetime_index_to_ms(eth_m1.index)

    # Non-overlapping same-side signals across BTC and ETH, plus a second signal on
    # BTC C1 while the first is still open (so P0 accepts it and P1 skips it).
    signals = [
        _make_signal(
            signal_id="SIG_BTC_C1_1",
            symbol="BTC-USDT",
            combo_id=1,
            combo_name="C1",
            side="BUY",
            bar_idx=60,
            ts_arr=btc_ts,
            df_m1=btc_m1,
        ),
        _make_signal(
            signal_id="SIG_BTC_C1_2",
            symbol="BTC-USDT",
            combo_id=1,
            combo_name="C1",
            side="SELL",
            bar_idx=62,
            ts_arr=btc_ts,
            df_m1=btc_m1,
        ),
        _make_signal(
            signal_id="SIG_ETH_C2_1",
            symbol="ETH-USDT",
            combo_id=2,
            combo_name="C2",
            side="BUY",
            bar_idx=300,
            ts_arr=eth_ts,
            df_m1=eth_m1,
        ),
    ]
    return candles, signals


def test_differential_p0_and_p1_match_public_research_engine() -> None:
    suite = _load_entry_mode_suite()
    candles, signals = _build_fixed_slice()
    arrays_by_sym = {sym: extract_m1_arrays(df) for sym, df in candles.items()}

    cost_model = CostModel.from_profile_name("BASE", is_real_data=False)
    risk_cfg = RiskConfig(
        initial_equity=1000.0,
        notional_per_trade=100.0,
        leverage_btc=50.0,
        leverage_eth=50.0,
        cost_profile=cost_model.profile.name,
        cost_model=cost_model,
    )

    variants_by_id = {v.variant_id: v for v in suite.VARIANTS}

    # 1) P1_ONE_TRADE_PER_COMBO vs public BINGX_PORTFOLIO
    p1_res, _ = suite.run_portfolio_variant(
        spec=variants_by_id["P1_ONE_TRADE_PER_COMBO"],
        signals=signals,
        arrays_by_sym=arrays_by_sym,
        risk_config=risk_cfg,
        cost_model=cost_model,
        ambiguity_policy="CONSERVATIVE",
    )
    pub_p1 = ExecutionSimulator(
        risk_config=risk_cfg, cost_model=cost_model, ambiguity_policy="CONSERVATIVE"
    ).simulate_research_mode(mode="BINGX_PORTFOLIO", signals=signals, candle_dict=candles)

    assert p1_res.summary_dict["accepted_entries"] == pub_p1.summary_dict["accepted_entries"]
    assert len(p1_res.trades) == len(pub_p1.trades)
    assert sum(t.net_pnl for t in p1_res.trades) == pytest.approx(
        sum(t.net_pnl for t in pub_p1.trades), abs=1e-9
    )

    # 2) P0_ALL_SIGNALS vs public ALL_SIGNALS (on non-overlapping same-side signals)
    p0_res, _ = suite.run_portfolio_variant(
        spec=variants_by_id["P0_ALL_SIGNALS"],
        signals=signals,
        arrays_by_sym=arrays_by_sym,
        risk_config=risk_cfg,
        cost_model=cost_model,
        ambiguity_policy="CONSERVATIVE",
    )
    pub_p0 = ExecutionSimulator(
        risk_config=risk_cfg, cost_model=cost_model, ambiguity_policy="CONSERVATIVE"
    ).simulate_research_mode(mode="ALL_SIGNALS", signals=signals, candle_dict=candles)

    assert p0_res.summary_dict["accepted_entries"] == pub_p0.summary_dict["accepted_entries"]
    assert len(p0_res.trades) == len(pub_p0.trades)
    assert sum(t.net_pnl for t in p0_res.trades) == pytest.approx(
        sum(t.net_pnl for t in pub_p0.trades), abs=1e-9
    )
    assert p0_res.summary_dict["accepted_entries"] > p1_res.summary_dict["accepted_entries"]
