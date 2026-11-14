"""Tests for V3-15b Codex audit fixes: Gate C4 Mode C dual-basis mutations & validation.json single source of truth."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from autotrade_v3.dashboard.queries import get_run_summary, list_store_runs
from autotrade_v3.runstore.index import StoreIndex
from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.gates_c import gate_c4

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_V313_PORTFOLIO = REPO_ROOT / "runs" / "golden" / "v3-13" / "BINGX_PORTFOLIO"


def _make_synthetic_mode_c_run(tmp_path: Path) -> Path:
    """Create a minimal synthetic Mode C run passing C4 subchecks."""
    rdir = tmp_path / "R_MODEC_MUT"
    rdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": "R_MODEC_MUT",
        "execution_mode": "BINGX_PORTFOLIO",
        "cost_profile": "IDEAL",
        "ambiguity_policy": "CONSERVATIVE",
        "initial_equity": 1000.0,
        "total_legs": 2,
        "total_signals": 1,
        "accepted_entries": 1,
        "smoke_test": True,
    }
    (rdir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rdir / "analysis.json").write_text(
        json.dumps({"pnl": {"net_pnl": 1.0, "roi_pct": 0.1}}),
        encoding="utf-8",
    )
    (rdir / "validation.json").write_text(
        json.dumps({"overall_status": "VERIFIED", "gates": []}),
        encoding="utf-8",
    )

    # Two overlapping legs on BTC-USDT BUY sharing a VWAP position:
    # Leg 1: entry=100, exit=102, qty=1, notional=100 -> own=2.0, vwap=1.5 (basis_shift=-0.5), fee=0.1 -> net=1.4?
    # Keep IDEAL zero-fee-like or exact taker 0.0005:
    # Let's use IDEAL (fee_taker = 0.0005):
    # fee_entry = 100 * 0.0005 = 0.05; fee_exit = 1 * 102 * 0.0005 = 0.051; fee_total = 0.101
    # gross_pnl_raw = (102 - 100) * 1 = 2.0
    # pnl_own_entry = 2.0 - 0.101 = 1.899
    # Leg 1 vwap: entry_price=100.5, qty=1, notional=100.5? Wait: C4 checks notional*0.0005 + qty*x*0.0005 and gross=(x-e)*qty.
    # So use entry_price=100.5, notional=100.0, qty=1.0 -> gross_vwap=(102-100.5)*1=1.5, net_vwap=1.5-0.101=1.399, basis_shift=-0.5
    # Leg 2: entry_price=100.5, notional=100.0, qty=1.0, exit=102 -> gross_vwap=1.5, net_vwap=1.399, own=0.899 (gross_raw=1.0, basis_shift=+0.5)
    t1_dt = pd.Timestamp("2026-01-01T00:00:00Z")
    t2_dt = pd.Timestamp("2026-01-01T01:00:00Z")
    trades_df = pd.DataFrame(
        [
            {
                "trade_id": "T1",
                "leg_id": "L1",
                "symbol": "BTC-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "BUY",
                "leg_type": "TP",
                "entry_time_ms": 1767225600000,
                "entry_time_dt": t1_dt,
                "entry_price": 100.5,
                "exit_time_ms": 1767229200000,
                "exit_time_dt": t2_dt,
                "exit_price": 102.0,
                "exit_reason": "TP",
                "qty": 1.0,
                "notional": 100.0,
                "margin": 10.0,
                "gross_pnl": 1.5,
                "fee_total": 0.101,
                "net_pnl": 1.399,
                "return_pct": 0.01399,
                "intrabar_ambiguous": False,
                "bars_held": 4,
                "provider": "BINANCE",
                "signal_id": "S1",
                "fee_entry": 0.05,
                "fee_exit": 0.051,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "funding_cost": 0.0,
                "gross_pnl_raw": 2.0,
                "pnl_own_entry": 1.899,
                "pnl_vwap": 1.399,
            },
            {
                "trade_id": "T2",
                "leg_id": "L2",
                "symbol": "BTC-USDT",
                "combo_id": 2,
                "combo_name": "C2",
                "timeframe": "1h",
                "side": "BUY",
                "leg_type": "TP",
                "entry_time_ms": 1767225600000,
                "entry_time_dt": t1_dt,
                "entry_price": 100.5,
                "exit_time_ms": 1767229200000,
                "exit_time_dt": t2_dt,
                "exit_price": 102.0,
                "exit_reason": "TP",
                "qty": 1.0,
                "notional": 100.0,
                "margin": 10.0,
                "gross_pnl": 1.5,
                "fee_total": 0.101,
                "net_pnl": 1.399,
                "return_pct": 0.01399,
                "intrabar_ambiguous": False,
                "bars_held": 4,
                "provider": "BINANCE",
                "signal_id": "S2",
                "fee_entry": 0.05,
                "fee_exit": 0.051,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "funding_cost": 0.0,
                "gross_pnl_raw": 1.0,
                "pnl_own_entry": 0.899,
                "pnl_vwap": 1.399,
            },
        ]
    )
    trades_df.to_parquet(rdir / "trades.parquet", index=False)
    pd.DataFrame({"timestamp_ms": [1767225600000, 1767229200000], "equity": [1000.0, 1002.798]}).to_parquet(
        rdir / "snapshots.parquet", index=False
    )
    pd.DataFrame({"signal_id": ["S1", "S2"]}).to_parquet(rdir / "signals.parquet", index=False)
    return rdir


def _run_c4(rdir: Path):
    ctx = VerifyContext(run_dir=rdir, dataset_root=rdir, repo_root=REPO_ROOT)
    return gate_c4(ctx)


def test_c4_mode_c_clean_baseline_passes(tmp_path: Path) -> None:
    rdir = _make_synthetic_mode_c_run(tmp_path)
    res = _run_c4(rdir)
    assert res.status == "PASS", res.details
    sub = res.details["c4_subchecks"]
    assert sub == {
        "NET_COST_INVARIANT_OWN": "PASS",
        "NET_IS_VWAP_BASIS": "PASS",
        "BASIS_SHIFT_CONSERVATION": "PASS",
    }


def test_c4_mutation_a_pnl_own_entry_plus_001_fails(tmp_path: Path) -> None:
    """Mutation (a): perturb 1 leg pnl_own_entry +0.01 -> Gate C4 must FAIL."""
    rdir = _make_synthetic_mode_c_run(tmp_path)
    df = pd.read_parquet(rdir / "trades.parquet")
    df.loc[0, "pnl_own_entry"] = float(df.loc[0, "pnl_own_entry"]) + 0.01
    df.to_parquet(rdir / "trades.parquet", index=False)

    res = _run_c4(rdir)
    assert res.status == "FAIL"
    assert res.details["c4_subchecks"]["NET_COST_INVARIANT_OWN"] == "FAIL"


def test_c4_mutation_b_net_pnl_diverges_from_pnl_vwap_fails(tmp_path: Path) -> None:
    """Mutation (b): perturb 1 leg net_pnl while keeping pnl_vwap -> Gate C4 must FAIL."""
    rdir = _make_synthetic_mode_c_run(tmp_path)
    df = pd.read_parquet(rdir / "trades.parquet")
    df.loc[0, "net_pnl"] = float(df.loc[0, "net_pnl"]) + 0.01
    df.to_parquet(rdir / "trades.parquet", index=False)

    res = _run_c4(rdir)
    assert res.status == "FAIL"
    assert res.details["c4_subchecks"]["NET_IS_VWAP_BASIS"] == "FAIL"


def test_c4_mutation_c_basis_shift_sum_nonzero_fails(tmp_path: Path) -> None:
    """Mutation (c): shift total basis_shift by 0.5 -> Gate C4 must FAIL on BASIS_SHIFT_CONSERVATION."""
    rdir = _make_synthetic_mode_c_run(tmp_path)
    df = pd.read_parquet(rdir / "trades.parquet")
    df.loc[0, "pnl_vwap"] = float(df.loc[0, "pnl_vwap"]) + 0.5
    df.loc[0, "net_pnl"] = float(df.loc[0, "net_pnl"]) + 0.5
    df.to_parquet(rdir / "trades.parquet", index=False)

    res = _run_c4(rdir)
    assert res.status == "FAIL"
    assert res.details["c4_subchecks"]["BASIS_SHIFT_CONSERVATION"] == "FAIL"


def test_c4_mutation_d_missing_pnl_own_entry_mode_c_fails(tmp_path: Path) -> None:
    """Mutation (d): drop pnl_own_entry in Mode C -> Gate C4 must FAIL without silent fallback."""
    rdir = _make_synthetic_mode_c_run(tmp_path)
    df = pd.read_parquet(rdir / "trades.parquet").drop(columns=["pnl_own_entry"])
    df.to_parquet(rdir / "trades.parquet", index=False)

    res = _run_c4(rdir)
    assert res.status == "FAIL"
    assert res.details["c4_subchecks"]["NET_COST_INVARIANT_OWN"] == "FAIL"


def test_validation_json_is_single_source_of_truth(tmp_path: Path) -> None:
    """Even if run_manifest.json says FAILED, validation.json=VERIFIED wins in StoreIndex and Dashboard."""
    store_dir = tmp_path / "store"
    rdir = store_dir / "R_TRUTH_TEST"
    rdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(_make_synthetic_mode_c_run(tmp_path / "src"), rdir, dirs_exist_ok=True)
    shutil.copy2(rdir / "trades.parquet", rdir / "legs.parquet")
    shutil.copy2(rdir / "snapshots.parquet", rdir / "equity.parquet")
    pd.DataFrame({"signal_id": ["S1", "S2"], "status": ["ACCEPTED", "ACCEPTED"]}).to_parquet(
        rdir / "signal_decisions.parquet", index=False
    )

    # Set manifest validation_status to FAILED while validation.json has VERIFIED
    man = json.loads((rdir / "run_manifest.json").read_text(encoding="utf-8"))
    man["run_id"] = "R_TRUTH_TEST"
    man["validation_status"] = "FAILED"
    man["smoke_test"] = False
    (rdir / "run_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    (rdir / "validation.json").write_text(json.dumps({"overall_status": "VERIFIED"}), encoding="utf-8")

    idx = StoreIndex(store_dir)
    idx.upsert_run(rdir)
    listed = idx.list_runs()
    assert len(listed) == 1
    assert listed[0]["validation_status"] == "VERIFIED"

    dash_runs = list_store_runs(store_dir)
    assert len(dash_runs) == 1
    assert dash_runs[0]["validation_status"] == "VERIFIED"

    summary = get_run_summary("R_TRUTH_TEST", store_dir)
    assert summary["validation_status"] == "VERIFIED"

    # If validation.json is removed, manifest's stale status must NOT be used
    (rdir / "validation.json").unlink()
    idx.upsert_run(rdir)
    assert idx.list_runs()[0]["validation_status"] == "UNVERIFIED"
    assert list_store_runs(store_dir)[0]["validation_status"] == "UNVERIFIED"
