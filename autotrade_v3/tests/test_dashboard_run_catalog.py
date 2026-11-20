"""Regression tests for the read-only multi-mode local backtest catalog."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import run_catalog


PAGES = Path(__file__).resolve().parents[1] / "dashboard" / "pages"


def _write_run(
    directory: Path,
    *,
    mode: str,
    run_id: str,
    status: str | None = "VERIFIED",
    cost: str = "BASE",
    hash_ok: bool = True,
) -> None:
    directory.mkdir(parents=True)
    manifest = {
        "run_id": run_id,
        "execution_mode": mode,
        "strategy_id": "C1_RESEARCH",
        "strategy_version": "H1_BOTH_1",
        "cost_profile": cost,
        "created_at_utc": "2026-11-17T00:00:00Z",
        "symbols": ["BTC-USDT", "ETH-USDT"],
        "validation_status": "VERIFIED",  # Must NEVER be used as source of truth.
    }
    (directory / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if status is not None:
        (directory / "validation.json").write_text(json.dumps({"overall_status": status}), encoding="utf-8")
    analysis = {
        "pnl": {"net_pnl": 19.25, "roi_pct": 1.925},
        "accounting": {"accepted_entries": 13},
        "combos": [
            {"symbol": "BTC-USDT", "combo_id": 1, "net_pnl": 15.25},
            {"symbol": "ETH-USDT", "combo_id": 1, "net_pnl": 4.00},
        ],
    }
    data = json.dumps(analysis).encode("utf-8")
    (directory / "analysis.json").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (directory / "ARTIFACTS.json").write_text(json.dumps({
        "artifacts": {"analysis.json": {"sha256": digest if hash_ok else "0" * 64}}
    }), encoding="utf-8")
    (directory / "report.md").write_text("# Kết quả chạy thực tế\n\nĐây là dữ liệu mẫu.", encoding="utf-8")


def test_discovers_all_modes_and_a_new_run_without_caching(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    _write_run(root / "v3_16_all_signals" / "a", mode="ALL_SIGNALS_PORTFOLIO", run_id="A")
    _write_run(root / "lab" / "store" / "b", mode="ONE_TRADE_PER_COMBO", run_id="B")
    rows = run_catalog.discover_runs([root, root / "lab"])
    assert len(rows) == 2  # Deduplicate overlapping configured roots.
    assert {row.execution_mode for row in rows} == {"ALL_SIGNALS_PORTFOLIO", "ONE_TRADE_PER_COMBO"}
    _write_run(root / "v3_c1_h1" / "c", mode="BINGX_PORTFOLIO", run_id="C", cost="STRESS")
    updated = run_catalog.discover_runs([root])
    assert len(updated) == 3
    assert any(row.run_id == "C" and row.cost_profile == "STRESS" for row in updated)


def test_same_run_id_in_different_directories_stays_separate(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    _write_run(root / "research_1", mode="ALL_SIGNALS", run_id="SAME")
    _write_run(root / "research_2", mode="ALL_SIGNALS", run_id="SAME")
    rows = run_catalog.discover_runs([root])
    assert len(rows) == 2
    assert len({row.identity for row in rows}) == 2


def test_only_validation_json_may_unlock_read_only_analysis(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    _write_run(root / "valid", mode="ALL_SIGNALS", run_id="valid")
    _write_run(root / "unvalidated", mode="ALL_SIGNALS", run_id="unvalidated", status=None)
    _write_run(root / "mismatch", mode="ALL_SIGNALS", run_id="mismatch", hash_ok=False)
    _write_run(root / "partial", mode="ALL_SIGNALS", run_id="partial", status="PARTIALLY_VERIFIED")
    by_id = {r.run_id: r for r in run_catalog.discover_runs([root])}
    assert by_id["valid"].net_pnl == 19.25
    assert by_id["valid"].accepted_entries == 13
    assert len(by_id["valid"].combo_rows) == 2
    assert {r["symbol"] for r in by_id["valid"].combo_rows} == {"BTC-USDT", "ETH-USDT"}
    assert by_id["unvalidated"].validation_status == "UNVERIFIED"
    assert by_id["unvalidated"].net_pnl is None
    assert not by_id["unvalidated"].combo_rows
    assert by_id["mismatch"].net_pnl is None
    assert "HASH_MISMATCH" in by_id["mismatch"].metrics_note or "Chưa xác minh" in by_id["mismatch"].metrics_note
    assert by_id["partial"].validation_status == "PARTIALLY_VERIFIED"
    assert by_id["partial"].net_pnl == 19.25


def test_malformed_manifest_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    (root / "broken").mkdir(parents=True)
    (root / "broken" / "run_manifest.json").write_text("{broken", encoding="utf-8")
    _write_run(root / "good", mode="ALL_SIGNALS", run_id="good")
    assert [r.run_id for r in run_catalog.discover_runs([root])] == ["good"]


def test_extra_roots_can_be_configured_without_overwriting_main_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extra = tmp_path / "other"
    _write_run(extra / "experiment", mode="ALL_SIGNALS", run_id="extra")
    monkeypatch.setenv(run_catalog.ROOTS_ENV, str(extra))
    assert extra in run_catalog.run_roots()
    assert Path("runs") in run_catalog.run_roots()


def test_catalog_page_renders_and_updates_when_run_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runs"
    _write_run(root / "first", mode="ALL_SIGNALS", run_id="first")
    monkeypatch.setattr(run_catalog, "run_roots", lambda: [root])
    page = str(PAGES / "p11_run_catalog.py")
    at = AppTest.from_file(page, default_timeout=20).run()
    assert not at.exception
    assert len(at.dataframe) >= 1
    _write_run(root / "second", mode="ONE_TRADE_PER_COMBO", run_id="second")
    at = AppTest.from_file(page, default_timeout=20).run()
    assert not at.exception
    assert any("2 / 2" in caption.value for caption in at.caption)
