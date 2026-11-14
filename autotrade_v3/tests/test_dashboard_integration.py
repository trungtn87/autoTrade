"""Integration tests (Section 7) for V3-15 Dashboard against real RunStore and real M1 catalog."""
from __future__ import annotations

from pathlib import Path
import random
import shutil

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import actions, queries
from autotrade_v3.tools.recompute_metrics import recompute_metrics

STORE_DIR = Path("runs/lab/store")
CATALOG_PATH = Path("catalog/research.duckdb")
PAGES_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "pages"


def _require_real_store() -> list[dict]:
    if not STORE_DIR.exists() or not CATALOG_PATH.exists():
        pytest.skip("Real RunStore or catalog/research.duckdb not present")
    runs = queries.list_store_runs(STORE_DIR)
    if not runs:
        pytest.skip("Real RunStore has no runs")
    return runs


def test_real_store_run_detail_matches_recompute_metrics() -> None:
    runs = _require_real_store()
    for r in runs:
        rid = str(r["run_id"])
        rdir = STORE_DIR / rid
        indep = recompute_metrics(rdir / "legs.parquet")
        summary = queries.get_run_summary(rid, store_dir=STORE_DIR)

        assert abs(summary["net_pnl"] - indep["total_net_pnl"]) < 1e-9
        assert abs(summary["gross_pnl"] - indep["total_gross_pnl"]) < 1e-9
        assert abs(summary["total_fees"] - indep["total_fees"]) < 1e-9
        assert summary["accepted_entries"] == indep["total_entries"]
        assert summary["total_legs"] == indep["total_legs"]
        assert abs(summary["trade_win_rate_pct"] - indep["trade_win_rate_pct"]) < 1e-9
        assert abs(summary["leg_win_rate_pct"] - indep["leg_win_rate_pct"]) < 1e-9

        # Drill-down sum invariant
        dd_all = queries.get_run_legs_drilldown(rid, store_dir=STORE_DIR)
        assert abs(dd_all["sum_net_pnl"] - indep["total_net_pnl"]) < 1e-9

        # Symbol breakdown vs recompute_metrics
        sym_df = queries.get_run_symbol_breakdown(rid, store_dir=STORE_DIR)
        indep_by_sym = {row["symbol"]: row for row in indep["by_symbol"]}
        for _, srow in sym_df.iterrows():
            ir = indep_by_sym[srow["symbol"]]
            assert abs(srow["net_pnl"] - ir["net_pnl"]) < 1e-9
            assert srow["entries"] == ir["entries"]
            assert srow["legs"] == ir["legs"]

        # Exit reason breakdown vs recompute_metrics
        exit_df = queries.get_run_exit_reasons(rid, store_dir=STORE_DIR)
        for _, erow in exit_df.iterrows():
            assert erow["count"] == indep["exit_reason_dist"][erow["exit_reason"]]["count"]


def test_real_store_signal_explorer_30_signals() -> None:
    runs = _require_real_store()
    all_sig_runs = [r for r in runs if r.get("execution_mode") == "ALL_SIGNALS"]
    if not all_sig_runs:
        pytest.skip("No ALL_SIGNALS run in store")
    rid = str(all_sig_runs[0]["run_id"])
    rdir = STORE_DIR / rid

    legs_df = pd.read_parquet(rdir / "legs.parquet")
    events_df = pd.read_parquet(rdir / "events.parquet")

    rng = random.Random(20261114)
    tp_sids = sorted(set(legs_df[legs_df["exit_reason"] == "TP"]["signal_id"].astype(str)))
    sl_sids = sorted(set(legs_df[legs_df["exit_reason"] == "SL"]["signal_id"].astype(str)))
    trail_sids = sorted(set(legs_df[legs_df["exit_reason"] == "TRAIL"]["signal_id"].astype(str)))
    ambig_mask = legs_df["intrabar_ambiguous"].fillna(False).astype(bool) | legs_df["intrabar_trailing_ambiguous"].fillna(False).astype(bool)
    ambig_sids = sorted(set(legs_df[ambig_mask]["signal_id"].astype(str)))

    picked: list[str] = []
    for pool, k in ((ambig_sids, 6), (tp_sids, 8), (sl_sids, 8), (trail_sids, 8)):
        avail = [s for s in pool if s not in picked]
        picked.extend(rng.sample(avail, min(k, len(avail))))
    assert len(picked) == 30

    con = duckdb.connect(str(CATALOG_PATH), read_only=True)
    try:
        for sid in picked:
            detail = queries.get_signal_detail_and_chart(rid, sid, store_dir=STORE_DIR, catalog_path=CATALOG_PATH)
            s_legs = legs_df[legs_df["signal_id"].astype(str) == sid].reset_index(drop=True)
            s_evts = events_df[events_df["signal_id"].astype(str) == sid].reset_index(drop=True)

            assert len(detail["legs_df"]) == len(s_legs)
            assert len(detail["events_df"]) == len(s_evts)

            lv = detail["levels"]
            expected_entry = float(s_legs["entry_px_raw"].iloc[0])
            assert abs(lv["entry_price"] - expected_entry) < 1e-9

            # Verify M1 path matches catalog M1 close values
            m1_df = detail["m1_path_df"]
            assert not m1_df.empty
            t0 = int(m1_df["time_ms"].iloc[0])
            t1 = int(m1_df["time_ms"].iloc[-1])
            cat_m1 = con.execute(
                "SELECT timestamp_ms AS time_ms, close FROM candles WHERE symbol = ? AND timestamp_ms >= ? AND timestamp_ms <= ? ORDER BY timestamp_ms ASC",
                [str(s_legs["symbol"].iloc[0]), t0, t1],
            ).df()
            assert len(m1_df) == len(cat_m1)
            assert (m1_df["close"].to_numpy() == cat_m1["close"].to_numpy()).all()
    finally:
        con.close()


def test_real_store_compare_diff_invariant() -> None:
    runs = _require_real_store()
    if len(runs) < 2:
        pytest.skip("Need >= 2 runs in store for compare test")
    ra = str(runs[0]["run_id"])
    rb = str(runs[1]["run_id"])
    res = queries.get_compare_signal_diff(ra, rb, store_dir=STORE_DIR)
    assert res["summary"]["invariant_diff"] <= 1e-9
    assert res["summary"]["unexplained_count"] == 0


@pytest.mark.parametrize(
    "page_name",
    [
        "p01_datasets.py",
        "p02_strategies.py",
        "p03_new_run.py",
        "p04_run_detail.py",
        "p05_signal_explorer.py",
        "p06_compare.py",
        "p07_research_log.py",
    ],
)
def test_real_store_apptest_all_pages(page_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _require_real_store()
    monkeypatch.setenv("AUTOTRADE_LAB_STORE_DIR", str(STORE_DIR))
    monkeypatch.setenv("AUTOTRADE_LAB_NOTES_DB", str(tmp_path / "notes.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_CATALOG_PATH", str(CATALOG_PATH))
    at = AppTest.from_file(str(PAGES_DIR / page_name), default_timeout=30)
    at.run()
    assert not at.exception, f"Real-store AppTest failed on {page_name}: {at.exception}"


def test_e2e_clone_edit_run_compare_real_data(tmp_path: Path) -> None:
    if not CATALOG_PATH.exists():
        pytest.skip("Real catalog/research.duckdb not present")
    params_dir = tmp_path / "params"
    params_dir.mkdir(parents=True, exist_ok=True)
    src_json = Path("autotrade_v3/strategy_core/strategies/final20/params/20final_v1.json")
    src_lock = Path("autotrade_v3/strategy_core/strategies/final20/params/20final_v1.lock")
    shutil.copy2(src_json, params_dir / "20final_v1.json")
    shutil.copy2(src_lock, params_dir / "20final_v1.lock")

    log_path = tmp_path / "actions.log"
    c_res = actions.run_strategy_clone(from_ref="20FINAL@1", to_ref="EXP_E2E@1", params_dir=params_dir, log_path=log_path)
    assert c_res.returncode == 0

    e_res = actions.run_edit_clone_params(
        strategy_ref="EXP_E2E@1",
        combo_key="BTC-USDT/C1",
        updates={"tp_pct": 0.035},
        params_dir=params_dir,
        log_path=log_path,
    )
    assert e_res.returncode == 0

    out_dir = tmp_path / "lab_out"
    # Run baseline and cloned strategy on real M1 data (1-month window for fast E2E)
    r1 = actions.run_backtest(
        strategy="20FINAL@1",
        mode="ALL_SIGNALS",
        symbols="BTC-USDT",
        start="2026-09-01",
        end="2026-10-01",
        cost_profile="IDEAL",
        ambiguity_policy="CONSERVATIVE",
        catalog_path=CATALOG_PATH,
        out_dir=out_dir,
        smoke_test=False,
        log_path=log_path,
    )
    assert r1.returncode == 0

    # Copy cloned strategy into StrategyCoreRegistry params dir only? No, run_backtest uses default params_dir,
    # so let's verify via StrategyCoreRegistry + ExperimentRunner or run directly with StrategyRegistry:
    from autotrade_v3.data_layer import DataLayer
    from autotrade_v3.lab.experiment import ExperimentRunner
    from autotrade_v3.lab.run_manager import RunManager
    from autotrade_v3.strategy.registry import StrategyRegistry

    cloned_strat = StrategyRegistry.get("EXP_E2E@1", params_dir=params_dir)
    StrategyRegistry.register(cloned_strat, version="1")
    try:
        runner = ExperimentRunner(data_layer=DataLayer(catalog_path=CATALOG_PATH), run_manager=RunManager(runs_dir=out_dir))
        res_clone = runner.run_experiment(
            strategy_id="EXP_E2E@1",
            mode="ALL_SIGNALS",
            symbols=["BTC-USDT"],
            start="2026-09-01",
            end="2026-10-01",
            cost_profile="IDEAL",
            ambiguity_policy="CONSERVATIVE",
            use_runstore=True,
        )
    finally:
        StrategyRegistry._strategies.pop(("EXP_E2E", "1"), None)

    store_runs = queries.list_store_runs(store_dir=out_dir / "store")
    if not store_runs:
        store_runs = queries.list_store_runs(store_dir=out_dir / "scratch")
        active_store = out_dir / "scratch"
    else:
        active_store = out_dir / "store"
    assert len(store_runs) >= 2
    comp = queries.compare_runs_summary([str(r["run_id"]) for r in store_runs[:2]], store_dir=active_store)
    assert len(comp["summaries"]) == 2
    assert res_clone.run_id in [str(r["run_id"]) for r in store_runs]
