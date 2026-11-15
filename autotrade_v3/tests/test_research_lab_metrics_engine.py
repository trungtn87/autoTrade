from __future__ import annotations

import pandas as pd
import pytest

from autotrade_v3.dashboard.metrics_engine import (
    apply_cost_scenario,
    attribution_breakdown,
    cost_reality_metrics,
    weighted_profit_factor,
)


def _legs() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trade_id": "T1", "signal_id": "S1", "symbol": "BTC-USDT", "combo_name": "C1", "net_pnl": 100.0, "pnl_own_entry": 90.0, "gross_pnl": 105.0, "fee_total": 5.0, "notional": 1000.0},
            {"trade_id": "T2", "signal_id": "S2", "symbol": "BTC-USDT", "combo_name": "C1", "net_pnl": -50.0, "pnl_own_entry": -48.0, "gross_pnl": -48.0, "fee_total": 2.0, "notional": 800.0},
            {"trade_id": "T3", "signal_id": "S3", "symbol": "ETH-USDT", "combo_name": "C2", "net_pnl": 10.0, "pnl_own_entry": 11.0, "gross_pnl": 11.0, "fee_total": 1.0, "notional": 500.0},
            {"trade_id": "T4", "signal_id": "S4", "symbol": "ETH-USDT", "combo_name": "C2", "net_pnl": -5.0, "pnl_own_entry": -4.0, "gross_pnl": -4.0, "fee_total": 1.0, "notional": 500.0},
        ]
    )


def test_weighted_pf_is_dollar_weighted_not_mean_of_children() -> None:
    assert weighted_profit_factor(_legs()["net_pnl"]) == pytest.approx(2.0)


def test_cost_reality_formulas() -> None:
    metrics = cost_reality_metrics(_legs(), run_id="R1")
    assert metrics.net_pnl == pytest.approx(55.0)
    assert metrics.weighted_pf == pytest.approx(2.0)
    assert metrics.break_even_slippage_pct == pytest.approx(55.0 / (2 * 2800.0) * 100)
    assert metrics.fee_drag_pct == pytest.approx(9.0 / 64.0 * 100)
    assert metrics.net_pnl_per_entry == pytest.approx(13.75)


def test_attribution_identity_and_reconciliation() -> None:
    decisions = pd.DataFrame([{"symbol": "BTC-USDT", "combo_name": "C1"}] * 4 + [{"symbol": "ETH-USDT", "combo_name": "C2"}] * 2)
    result = attribution_breakdown(_legs(), decisions, group_columns=["symbol", "combo_name"])
    assert (result["attribution_delta"] - (result["net_pnl"] - result["pnl_own_entry"])).abs().max() < 1e-12
    assert result["net_pnl"].sum() == pytest.approx(_legs()["net_pnl"].sum())
    assert result["fees"].sum() == pytest.approx(_legs()["fee_total"].sum())


def test_cost_scenario_is_monotonic_and_non_mutating() -> None:
    legs = _legs()
    original = legs["net_pnl"].sum()
    stressed = apply_cost_scenario(legs, slippage_pct_per_side=0.05, extra_fee_pct=0.10)
    assert stressed["net_pnl"].sum() < original
    assert legs["net_pnl"].sum() == pytest.approx(original)
