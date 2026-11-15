"""Integration tests for V3-13 cost profiles, ambiguity policies, CLI uncertainty, and C2/C3/C4."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autotrade_v3.cli import run_uncertainty_analysis
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, generate_synthetic_m1
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager
from autotrade_v3.validation.runner import run_validation


@pytest.mark.parametrize("policy", ["CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH"])
def test_synthetic_pipeline_with_test_only_and_verify_c2_c3_c4(tmp_path, policy):
    rm = RunManager(runs_dir=tmp_path / "runs")
    runner = ExperimentRunner(run_manager=rm)
    res = runner.run_experiment(
        strategy_id="20FINAL_BASELINE",
        mode="ALL_SIGNALS",
        symbols=["ETH-USDT"],
        start="2023-11-01",
        end="2026-11-01",
        smoke_test=True,
        cost_profile="TEST_ONLY",
        ambiguity_policy=policy,
    )
    assert res.manifest.cost_profile == "TEST_ONLY"
    assert res.manifest.cost_profile_hash
    assert res.manifest.ambiguity_policy == policy

    # Stage matching synthetic M1 parquet + dataset_manifest.json so C3 replays independently
    ds_root = tmp_path / "ds"
    ds_root.mkdir(parents=True, exist_ok=True)
    df_m1 = generate_synthetic_m1("ETH-USDT", start="2023-11-01", periods=75000, base_price=3500.0, seed=42)
    df_out = df_m1.copy()
    df_out["open_time"] = datetime_index_to_ms(df_m1.index)
    df_out.to_parquet(ds_root / "eth_m1.parquet", index=False)
    dm = {
        "partitions": [
            {"symbol": "ETH-USDT", "relative_path": "eth_m1.parquet", "row_count": len(df_out)}
        ]
    }
    (Path(res.run_dir) / "dataset_manifest.json").write_text(json.dumps(dm), encoding="utf-8")

    report = run_validation(res.run_dir, dataset_root=ds_root)
    for gid in ("C2", "C3", "C4"):
        assert report.gates[gid]["status"] == "PASS", f"{gid} failed under {policy}: {report.gates[gid].get('violations')}"


def test_real_data_rejects_missing_or_unconfirmed_cost_profile(tmp_path):
    from autotrade_v3.sim.costs import CostModel

    rm = RunManager(runs_dir=tmp_path)
    runner = ExperimentRunner(run_manager=rm)
    with pytest.raises(ValueError, match="cost-profile"):
        runner.run_experiment(smoke_test=False, cost_profile=None)
    for confirmed in ("BASE", "STRESS", "IDEAL"):
        cm = CostModel.from_profile_name(confirmed, is_real_data=True)
        assert cm.profile.status == "CONFIRMED"
    for bad in ("TEST_ONLY",):
        with pytest.raises(ValueError):
            runner.run_experiment(smoke_test=False, cost_profile=bad)


def test_synthetic_uncertainty_analysis():
    out = run_uncertainty_analysis(
        strategy_id="20FINAL_BASELINE",
        mode="ALL_SIGNALS",
        symbols=["ETH-USDT"],
        start="2023-11-01",
        end="2026-11-01",
        cost_profile="TEST_ONLY",
        smoke_test=True,
    )
    assert out["cost_profile"] == "TEST_ONLY"
    assert out["net_pnl_optimistic"] >= out["net_pnl_conservative"] - 1e-9
    assert isinstance(out["changed_signal_ids"], list)
