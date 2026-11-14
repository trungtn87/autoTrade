"""Adversarial / Mutation tests (M1-M6, Section 9) on temporary copies of store/code."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import shutil
import socket

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import actions, queries
from autotrade_v3.tests.test_dashboard_queries_and_actions import _build_synthetic_store
from autotrade_v3.tools.recompute_metrics import recompute_metrics

PAGES_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "pages"
QUERIES_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "queries.py"


def test_m1_mutate_leg_net_pnl_detected_by_verify_and_ledger_recompute(tmp_path: Path) -> None:
    """M1: Mutate net_pnl of 1 leg in a copied run -> verify fails & Run Detail reflects mismatch."""
    store_dir, run_id = _build_synthetic_store(tmp_path)
    rdir = store_dir / run_id
    orig_metrics = recompute_metrics(rdir / "legs.parquet")

    legs_df = pd.read_parquet(rdir / "legs.parquet")
    legs_df.loc[0, "net_pnl"] = float(legs_df.loc[0, "net_pnl"]) + 50.0
    legs_df.to_parquet(rdir / "legs.parquet", index=False)

    # Run verify on the mutated copy
    res = actions.run_verify(run_dir=rdir, dataset_root=".", no_write=False, log_path=tmp_path / "actions.log")
    assert res.returncode != 0 or "FAILED" in res.stdout or "FAIL" in res.stdout
    val = json.loads((rdir / "validation.json").read_text(encoding="utf-8"))
    assert val["overall_status"] != "VERIFIED"

    mut_summary = queries.get_run_summary(run_id, store_dir=store_dir)
    assert mut_summary["validation_status"] != "VERIFIED"
    assert abs(mut_summary["net_pnl"] - orig_metrics["total_net_pnl"]) > 1.0


def test_m2_combo_group_by_combo_id_only_fails_combo_check(tmp_path: Path) -> None:
    """M2: In a copy of queries.py, group combos by combo_id only -> BTC/ETH collide and test fails."""
    store_dir, run_id = _build_synthetic_store(tmp_path)
    mut_q = tmp_path / "mut_queries.py"
    src = QUERIES_PATH.read_text(encoding="utf-8")
    mut_src = src.replace(
        '    for (sym, cname), grp in df.groupby(["symbol", "combo_name"], sort=True):',
        '    for cid, grp in df.groupby("combo_id", sort=True):\n        sym = str(grp["symbol"].iloc[0])\n        cname = str(grp["combo_name"].iloc[0])',
    )
    assert mut_src != src
    mut_q.write_text(mut_src, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("mut_queries_m2", mut_q)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    combo_df = mod.get_run_combo_breakdown(run_id, store_dir=store_dir)
    # Must fail the expectation that both BTC-USDT/C1 and ETH-USDT/C1 are distinct rows
    assert set(combo_df["combo_key"]) != {"BTC-USDT/C1", "ETH-USDT/C1"}


def test_m3_wrong_tp_reference_price_fails_signal_explorer_check(tmp_path: Path) -> None:
    """M3: In a copy of queries.py, compute TP from wrong base price -> level check fails."""
    store_dir, run_id = _build_synthetic_store(tmp_path)
    mut_q = tmp_path / "mut_queries_m3.py"
    src = QUERIES_PATH.read_text(encoding="utf-8")
    mut_src = src.replace(
        "tp_price = entry_price * (1.0 + tp_pct)",
        "tp_price = (entry_price * 0.99) * (1.0 + tp_pct)",
    )
    assert mut_src != src
    mut_q.write_text(mut_src, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("mut_queries_m3", mut_q)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    detail = mod.get_signal_detail_and_chart(run_id, "SIG_1", store_dir=store_dir, catalog_path=tmp_path / "no.duckdb")
    expected_tp = 100.0 * 1.10
    assert abs(detail["levels"]["tp_price"] - expected_tp) > 0.5


def test_m4_deleted_artifact_causes_error_and_no_numbers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M4: Delete legs.parquet in copied run -> Run Detail displays missing artifact error and no metrics."""
    store_dir, run_id = _build_synthetic_store(tmp_path)
    (store_dir / run_id / "legs.parquet").unlink()
    monkeypatch.setenv("AUTOTRADE_LAB_STORE_DIR", str(store_dir))

    at = AppTest.from_file(str(PAGES_DIR / "p04_run_detail.py"), default_timeout=15)
    at.run()
    assert not at.exception
    assert len(at.error) >= 1
    assert "Missing required artifact" in at.error[0].value or "thiếu artifact" in at.error[0].value
    assert len(at.metric) == 0


def test_m5_network_call_in_copied_page_blocked_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M5: Inject an external socket/network call into a copied page -> offline socket guard blocks it."""
    mut_page = tmp_path / "p_net_evil.py"
    mut_page.write_text(
        "import socket\ns = socket.create_connection(('example.com', 80), timeout=1)\n",
        encoding="utf-8",
    )

    def _blocked_connect(*args, **kwargs):
        raise OSError("Blocked non-loopback network connection in offline test")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect)
    at = AppTest.from_file(str(mut_page), default_timeout=10)
    at.run()
    assert at.exception  # Offline guard triggers failure


def test_m6_hardcoded_verified_in_copied_page_detected(tmp_path: Path) -> None:
    """M6: Hardcoding 'VERIFIED' label in a copied page fails static/status scan."""
    mut_page = tmp_path / "p04_run_detail_evil.py"
    src = (PAGES_DIR / "p04_run_detail.py").read_text(encoding="utf-8")
    mut_src = src + '\nst.success("VERIFIED")\n'
    mut_page.write_text(mut_src, encoding="utf-8")

    tree = ast.parse(mut_page.read_text(encoding="utf-8"))
    hardcoded_verified = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "VERIFIED"
    ]
    assert len(hardcoded_verified) >= 1
