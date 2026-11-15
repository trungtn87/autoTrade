from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from autotrade_v3.core.report_engine import build_ai_analysis_bundle, build_run_report, simulate_cost_sensitivity
from autotrade_v3.dashboard import queries


def _fixture() -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary = {
        "run_id": "R_TEST",
        "strategy_id": "TEST",
        "strategy_version": "1",
        "time_window": {"start": "2025-01-01", "end": "2026-12-31"},
        "execution_mode": "BINGX_PORTFOLIO",
        "cost_profile": "IDEAL",
        "validation_status": "PARTIALLY_VERIFIED",
        "dataset_hash": "abc",
        "initial_equity": 1000.0,
        "gross_pnl": 53.0,
        "leg_win_rate_pct": 50.0,
        "m2m_max_drawdown_dollars": 20.0,
        "m2m_max_drawdown_pct": 2.0,
    }
    legs = pd.DataFrame([
        {"trade_id": "T1", "signal_id": "S1", "symbol": "BTC-USDT", "combo_name": "C1", "timeframe": "1h", "side": "BUY", "net_pnl": 40.0, "gross_pnl": 42.0, "pnl_own_entry": 35.0, "fee_total": 2.0, "notional": 1000.0},
        {"trade_id": "T2", "signal_id": "S2", "symbol": "ETH-USDT", "combo_name": "C2", "timeframe": "15m", "side": "SELL", "net_pnl": -10.0, "gross_pnl": -9.0, "pnl_own_entry": -8.0, "fee_total": 1.0, "notional": 1000.0},
    ])
    decisions = pd.DataFrame([
        {"signal_id": "S1", "symbol": "BTC-USDT", "combo_name": "C1", "status": "ACCEPTED"},
        {"signal_id": "S2", "symbol": "ETH-USDT", "combo_name": "C2", "status": "ACCEPTED"},
        {"signal_id": "S3", "symbol": "ETH-USDT", "combo_name": "C2", "status": "SKIPPED"},
    ])
    equity = pd.DataFrame({
        "timestamp_dt": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"], utc=True),
        "equity": [1000.0, 1010.0, 1030.0],
    })
    config = pd.DataFrame([
        {"combo_key": "BTC-USDT/C1", "symbol": "BTC-USDT", "combo_name": "C1", "timeframe": "1h", "entry_variant": "EMA", "entry": "{}", "filters": "SMC", "tp_pct": .02, "sl_pct": .01, "trailing_act_pct": 0.0, "trailing_cb_pct": 0.0, "leg_weights": "[1.0]"},
        {"combo_key": "ETH-USDT/C2", "symbol": "ETH-USDT", "combo_name": "C2", "timeframe": "15m", "entry_variant": "RSI", "entry": "{}", "filters": "OB", "tp_pct": .03, "sl_pct": .01, "trailing_act_pct": .01, "trailing_cb_pct": .005, "leg_weights": "[0.5, 0.5]"},
    ])
    return summary, legs, decisions, equity, config


def test_report_engine_joins_config_and_performance_without_nulls() -> None:
    report = build_run_report(*_fixture())
    combo = report["combo_table"]
    assert len(combo) == 2
    assert not combo.isna().any().any()
    assert combo.loc[combo["combo_key"] == "BTC-USDT/C1", "rr_ratio"].iloc[0] == pytest.approx(2.0)
    assert combo["attribution_delta"].sum() == pytest.approx(report["portfolio"]["net_pnl"] - report["portfolio"]["pnl_own_entry"])
    assert report["portfolio"]["weighted_pf"] == pytest.approx(4.0)


def test_cost_sensitivity_and_bundle_have_five_clean_sections() -> None:
    report = build_run_report(*_fixture())
    sensitivity = simulate_cost_sensitivity(report, extra_fee_pct=.075)
    ideal = sensitivity["portfolio"].query("fee_mode == 'IDEAL'").sort_values("slippage_pct")
    assert ideal["net_pnl"].is_monotonic_decreasing
    bundle = build_ai_analysis_bundle(report, sensitivity)
    assert list(bundle.payload) == [
        "1_RUN_METADATA_AND_ALERTS",
        "2_PORTFOLIO_AND_GROUP_BREAKDOWN",
        "3_COST_SENSITIVITY_MATRIX",
        "4_UNIFIED_COMBO_TABLE",
        "5_ANALYTICAL_GUARDRAILS",
    ]
    json.loads(bundle.json_text)
    assert "NaN" not in bundle.json_text
    assert ": null" not in bundle.json_text
    for section in bundle.payload:
        assert f"## {section}" in bundle.markdown


@pytest.mark.skipif(not Path("runs/lab/store/R_86b3e8b4c96cd4b6").exists(), reason="Local acceptance RunStore is unavailable")
def test_baseline_acceptance_numbers_from_real_runstore() -> None:
    run_id = "R_86b3e8b4c96cd4b6"
    store = Path("runs/lab/store")
    summary = queries.get_run_summary(run_id, store)
    report = build_run_report(
        summary,
        queries.get_run_legs_ledger(run_id, store),
        queries.get_run_decisions_drilldown(run_id, store),
        queries.get_run_equity_curve(run_id, store),
        queries.get_strategy_detail("20FINAL@1")["combos_df"],
        queries.get_run_cost_breakdown(run_id, store),
    )
    symbols = {row["group"]: row for row in report["breakdowns"]["Symbol"]}
    legs = {row["group"]: row for row in report["breakdowns"]["Leg Structure"]}
    assert symbols["BTC-USDT"]["entries"] == 1861
    assert symbols["BTC-USDT"]["net_pnl"] == pytest.approx(138.530866, abs=1e-6)
    assert symbols["ETH-USDT"]["entries"] == 2288
    assert symbols["ETH-USDT"]["net_pnl"] == pytest.approx(143.662618, abs=1e-6)
    assert legs["1-Leg"]["pnl_own_entry"] == pytest.approx(193.178867, abs=1e-6)
    assert legs["2-Leg"]["pnl_own_entry"] == pytest.approx(89.014617, abs=1e-6)
    assert report["portfolio"]["break_even_slippage_per_side_pct"] == pytest.approx(0.0340074095)
