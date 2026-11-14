"""Tests for Mode C two-column PnL, accounting invariant, margin binding, and liquidation warning."""
from __future__ import annotations

import pandas as pd

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.account import verify_mode_c_accounting_invariant
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import MODE_C_SIMULATION_LIMITS, UnifiedSimulator
from autotrade_v3.strategy.base import ExitPolicy


def _make_bars(prices: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01 00:00", periods=len(prices), freq="1min", tz="UTC")
    return pd.DataFrame(prices, columns=["open", "high", "low", "close"], index=idx)


def _sig(
    df: pd.DataFrame,
    bar_idx: int,
    combo_id: int,
    side: str = "BUY",
    symbol: str = "BTC-USDT",
    tp_pct: float = 0.02,
    sl_pct: float = 0.05,
) -> Signal:
    sc_ms = int(df.index[bar_idx].timestamp() * 1000) - 1
    return Signal(
        symbol=symbol,
        combo_id=combo_id,
        combo_name=f"C{combo_id}",
        timeframe="15m",
        side=side,
        signal_close_ms=sc_ms,
        signal_close_dt=str(df.index[bar_idx]),
        bar_position=bar_idx,
        bar_open_ms=sc_ms,
        entry_price=float(df.iloc[bar_idx]["open"]),
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=float(df.iloc[bar_idx]["open"]) * (1 + tp_pct),
        sl_price=float(df.iloc[bar_idx]["open"]) * (1 - sl_pct),
        approved=True,
        warmup_ready=True,
        smc_dir=1,
        ob_blocked=False,
        provider="SYNTHETIC",
        is_new6=True,
        signal_id=f"SIG_{symbol}_{combo_id}_{bar_idx}",
        exit_policy=ExitPolicy(tp_pct=tp_pct, sl_pct=sl_pct, split_legs=False, leg_weights=(1.0,)),
    )


def test_mode_c_three_overlapping_signals_two_pnl_columns_and_invariant():
    # Three BUY signals enter at bars 0 (px=100), 1 (px=101), 2 (px=102)
    # Bar 3: high=104.5 hits TP for all three (102, 103.02, 104.04)
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.2),
        (101.0, 101.5, 100.5, 101.2),
        (102.0, 102.5, 101.5, 102.2),
        (102.5, 104.5, 102.0, 104.0),
        (104.0, 104.0, 104.0, 104.0),
    ])
    sigs = [_sig(df, 0, 1), _sig(df, 1, 2), _sig(df, 2, 3)]
    cm = CostModel.from_profile_name("TEST_ONLY", is_real_data=False)
    sim = UnifiedSimulator(risk_config=RiskConfig(initial_equity=1000.0), cost_model=cm, ambiguity_policy="CONSERVATIVE")
    res = sim.run("BINGX_PORTFOLIO", sigs, {"BTC-USDT": df})

    assert len(res.trades) == 3
    # Individual own-entry vs VWAP must differ for individual legs due to blended entry
    assert any(abs(t.pnl_own_entry - t.pnl_vwap) > 1e-4 for t in res.trades)
    inv = verify_mode_c_accounting_invariant(
        res.trades,
        initial_equity=1000.0,
        final_equity=res.summary_dict["final_equity"],
    )
    assert inv["passed"] is True
    assert inv["max_group_diff"] < 1e-9
    assert set(MODE_C_SIMULATION_LIMITS.keys()) <= set(res.summary_dict["simulation_limits"].keys())


def test_synthetic_margin_binding_rejects_signal():
    # initial_equity=1.5, notional=100, lev=100 -> 1.0 margin per trade -> 2nd signal rejected!
    df = _make_bars([
        (100.0, 100.2, 99.8, 100.0),
        (100.0, 100.2, 99.8, 100.0),
        (100.0, 103.0, 99.8, 102.5),
    ])
    sigs = [_sig(df, 0, 1), _sig(df, 1, 2)]
    cm = CostModel.from_profile_name("IDEAL", is_real_data=False)
    sim = UnifiedSimulator(
        risk_config=RiskConfig(initial_equity=1.5, notional_per_trade=100.0, leverage_btc=100.0),
        cost_model=cm,
        ambiguity_policy="CONSERVATIVE",
    )
    res = sim.run("BINGX_PORTFOLIO", sigs, {"BTC-USDT": df})
    assert len(res.rejected_signals) == 1
    assert res.rejected_signals[0]["reason"] == "INSUFFICIENT_MARGIN"
    assert any(d.status == "REJECTED" and d.reason == "INSUFFICIENT_MARGIN" for d in res.signal_decisions)


def test_synthetic_maintenance_margin_triggers_liquidation_warning():
    # initial_equity=5.5, notional=100, lev=100 (req_margin=1.0). mmr=0.05 -> mm~5.0.
    # Bar 0 low=99.8 -> adverse_equity ~ 5.25 > 4.99 (safe).
    # Bar 1 low=96.0 -> unrealized = -4.0 -> adverse_equity ~ 1.45 < 4.80 -> LIQUIDATION_WARNING!
    df = _make_bars([
        (100.0, 100.2, 99.8, 100.0),
        (100.0, 100.2, 96.0, 96.5),
        (96.5, 96.5, 96.5, 96.5),
    ])
    sigs = [_sig(df, 0, 1, sl_pct=0.10)]
    cm = CostModel.from_profile_name("IDEAL", is_real_data=False)
    sim = UnifiedSimulator(
        risk_config=RiskConfig(
            initial_equity=5.5,
            notional_per_trade=100.0,
            leverage_btc=100.0,
            mmr_by_symbol={"BTC-USDT": 0.05},
        ),
        cost_model=cm,
        ambiguity_policy="CONSERVATIVE",
    )
    res = sim.run("BINGX_PORTFOLIO", sigs, {"BTC-USDT": df})
    assert res.summary_dict["status"] == "LIQUIDATION_WARNING"
    assert res.summary_dict["liquidation_warning_time_ms"] == int(df.index[1].timestamp() * 1000)
    assert res.summary_dict["liquidation_warning_equity"] < res.summary_dict["liquidation_warning_mm"]
