"""Positive and negative tests for Filter-Aware Validation (Lab experiments)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.lab.filter_aware_validation import EXPECTED_DATASET_HASH, validate_filtered_run


def _make_valid_run_dir(tmp_path: Path) -> tuple[Path, pd.DataFrame]:
    rdir = tmp_path / "run_ok"
    rdir.mkdir(parents=True, exist_ok=True)

    base_sigs = pd.DataFrame(
        [
            {"signal_id": "S1", "symbol": "BTC-USDT", "combo_id": 104, "signal_close_ms": 1000},
            {"signal_id": "S2", "symbol": "BTC-USDT", "combo_id": 104, "signal_close_ms": 2000},
            {"signal_id": "S3", "symbol": "ETH-USDT", "combo_id": 1, "signal_close_ms": 3000},
        ]
    )
    # S1 dropped by filter, S2 accepted, S3 accepted
    decs = pd.DataFrame(
        [
            {"signal_id": "S1", "symbol": "BTC-USDT", "combo_id": 104, "status": "FILTERED", "reason": "H2b_THIN_KUMO"},
            {"signal_id": "S2", "symbol": "BTC-USDT", "combo_id": 104, "status": "ACCEPTED", "reason": ""},
            {"signal_id": "S3", "symbol": "ETH-USDT", "combo_id": 1, "status": "ACCEPTED", "reason": ""},
        ]
    )
    trades = pd.DataFrame(
        [
            {
                "signal_id": "S2",
                "symbol": "BTC-USDT",
                "combo_id": 104,
                "entry_time_ms": 1001 + 1000,
                "exit_time_ms": 2500,
                "exit_reason": "TP",
                "net_pnl": 1.9,
                "fee_total": 0.1,
                "pnl_own_entry": 1.9,
                "pnl_vwap": 1.5,
            },
            {
                "signal_id": "S3",
                "symbol": "ETH-USDT",
                "combo_id": 1,
                "entry_time_ms": 3001,
                "exit_time_ms": 3500,
                "exit_reason": "SL",
                "net_pnl": -0.7,
                "fee_total": 0.1,
                "pnl_own_entry": -0.7,
                "pnl_vwap": -0.3,
            },
        ]
    )
    manifest = {
        "run_id": "RUN_TEST_OK",
        "dataset_hash": EXPECTED_DATASET_HASH,
    }
    analysis = {
        "pnl": {"net_pnl": 1.2, "total_fees": 0.2},
        "risk": {"closed_trade_max_drawdown_dollars": 0.7},
        "symbols": {
            "BTC-USDT": {"net_pnl": 1.9},
            "ETH-USDT": {"net_pnl": -0.7},
        },
        "combos": [
            {"combo_id": 104, "net_pnl": 1.9},
            {"combo_id": 1, "net_pnl": -0.7},
        ],
    }
    # Simulate expected D2 filter-reconciliation mismatch (only allowed D2 violations)
    val_json = {
        "gates": {
            "D2": {
                "status": "FAIL",
                "violations": [
                    {"path": "analysis.symbols.BTC-USDT.signals", "kind": "VALUE_MISMATCH", "expected": 2, "actual": 1},
                    {"line": "reconciliation_parity", "kind": "RECONCILIATION_MISMATCH", "expected": True, "actual": False},
                ],
            }
        }
    }

    base_sigs.to_parquet(rdir / "signals.parquet", index=False)
    decs.to_parquet(rdir / "signal_decisions.parquet", index=False)
    trades.to_parquet(rdir / "trades.parquet", index=False)
    (rdir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rdir / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (rdir / "validation.json").write_text(json.dumps(val_json), encoding="utf-8")
    return rdir, base_sigs


def test_filter_aware_validation_positive(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    rep = validate_filtered_run(
        rdir,
        variant="H2b",
        baseline_signals_df=base_sigs,
        expected_drop_fn=lambda r: (r["signal_id"] == "S1", "H2b_THIN_KUMO"),
    )
    assert rep.overall_status == "PASS"
    assert all(g["status"] == "PASS" for g in rep.gates.values())


def test_negative_missing_signal_without_drop(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    decs = pd.read_parquet(rdir / "signal_decisions.parquet")
    decs = decs[decs["signal_id"] != "S1"]  # drop S1 silently without FILTERED decision
    decs.to_parquet(rdir / "signal_decisions.parquet", index=False)
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA1"]["status"] == "FAIL"


def test_negative_duplicate_signal(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    sigs = pd.read_parquet(rdir / "signals.parquet")
    sigs = pd.concat([sigs, sigs.iloc[[0]]], ignore_index=True)
    sigs.to_parquet(rdir / "signals.parquet", index=False)
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA1"]["status"] == "FAIL"


def test_negative_trade_mismatch_admission(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    trades = pd.read_parquet(rdir / "trades.parquet")
    trades.loc[0, "signal_id"] = "S1"  # S1 was FILTERED, not ACCEPTED
    trades.to_parquet(rdir / "trades.parquet", index=False)
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA3"]["status"] == "FAIL"


def test_negative_pnl_mismatch(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    an = json.loads((rdir / "analysis.json").read_text(encoding="utf-8"))
    an["pnl"]["net_pnl"] = 9.99  # corrupt total PnL
    (rdir / "analysis.json").write_text(json.dumps(an), encoding="utf-8")
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA4"]["status"] == "FAIL"


def test_negative_fee_mismatch(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    an = json.loads((rdir / "analysis.json").read_text(encoding="utf-8"))
    an["pnl"]["total_fees"] = 0.05  # corrupt fees
    (rdir / "analysis.json").write_text(json.dumps(an), encoding="utf-8")
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA4"]["status"] == "FAIL"


def test_negative_wrong_entry_exit_timestamp(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    trades = pd.read_parquet(rdir / "trades.parquet")
    trades.loc[0, "entry_time_ms"] = 1999  # before S2 signal_close_ms (2000) -> look-ahead!
    trades.to_parquet(rdir / "trades.parquet", index=False)
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA3"]["status"] == "FAIL"


def test_negative_vwap_not_conserved(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    trades = pd.read_parquet(rdir / "trades.parquet")
    trades.loc[0, "pnl_vwap"] = 5.0  # break sum(pnl_own_entry) == sum(pnl_vwap)
    trades.to_parquet(rdir / "trades.parquet", index=False)
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA4"]["status"] == "FAIL"


def test_negative_non_target_combo_filtered(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    decs = pd.read_parquet(rdir / "signal_decisions.parquet")
    decs.loc[decs["signal_id"] == "S3", "status"] = "FILTERED"
    decs.loc[decs["signal_id"] == "S3", "reason"] = "BAD_FILTER"
    decs.to_parquet(rdir / "signal_decisions.parquet", index=False)
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA2"]["status"] == "FAIL"


def test_negative_unexplained_d2_violation(tmp_path: Path) -> None:
    rdir, base_sigs = _make_valid_run_dir(tmp_path)
    vj = json.loads((rdir / "validation.json").read_text(encoding="utf-8"))
    vj["gates"]["D2"]["violations"].append(
        {"path": "analysis.pnl.net_pnl", "kind": "VALUE_MISMATCH", "expected": 1.2, "actual": 1.5}
    )
    (rdir / "validation.json").write_text(json.dumps(vj), encoding="utf-8")
    rep = validate_filtered_run(rdir, variant="H2b", baseline_signals_df=base_sigs)
    assert rep.overall_status == "FAIL"
    assert rep.gates["FA5"]["status"] == "FAIL"
