"""Streamlit AppTest suite for all 7 V3-15 dashboard pages (empty store, populated store, missing artifact)."""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import queries
from autotrade_v3.tests.test_dashboard_queries_and_actions import _build_synthetic_store

PAGES_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "pages"
ALL_PAGE_FILES = [
    "p01_datasets.py",
    "p02_strategies.py",
    "p03_new_run.py",
    "p04_run_detail.py",
    "p05_signal_explorer.py",
    "p06_compare.py",
    "p07_research_log.py",
]


@pytest.mark.parametrize("page_name", ALL_PAGE_FILES)
def test_pages_empty_store_no_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page_name: str) -> None:
    empty_store = tmp_path / "empty_store"
    empty_store.mkdir()
    monkeypatch.setenv("AUTOTRADE_LAB_STORE_DIR", str(empty_store))
    monkeypatch.setenv("AUTOTRADE_LAB_NOTES_DB", str(tmp_path / "notes.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_CATALOG_PATH", str(tmp_path / "missing_catalog.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_QA_PATH", str(tmp_path / "missing_qa.json"))

    at = AppTest.from_file(str(PAGES_DIR / page_name), default_timeout=15)
    at.run()
    assert not at.exception, f"Exception on {page_name} with empty store: {at.exception}"


@pytest.mark.parametrize("page_name", ALL_PAGE_FILES)
def test_pages_populated_store_no_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, page_name: str) -> None:
    store_dir, run_id = _build_synthetic_store(tmp_path)
    # Add a second run so Compare has >= 2 runs
    r2_dir = store_dir / "R_SYNTHETIC_002"
    shutil.copytree(store_dir / run_id, r2_dir)
    monkeypatch.setenv("AUTOTRADE_LAB_STORE_DIR", str(store_dir))
    monkeypatch.setenv("AUTOTRADE_LAB_NOTES_DB", str(tmp_path / "notes.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_CATALOG_PATH", str(tmp_path / "missing_catalog.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_QA_PATH", str(tmp_path / "missing_qa.json"))

    at = AppTest.from_file(str(PAGES_DIR / page_name), default_timeout=15)
    at.run()
    assert not at.exception, f"Exception on {page_name} with populated store: {at.exception}"

    if page_name == "p04_run_detail.py":
        expected = queries.get_run_summary(run_id, store_dir=store_dir)
        metric_values = [m.value for m in at.metric]
        assert any(f"${expected['net_pnl']:,.4f}" in str(v) for v in metric_values)


def test_run_detail_missing_artifact_shows_error_not_numbers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir, run_id = _build_synthetic_store(tmp_path)
    (store_dir / run_id / "legs.parquet").unlink()
    monkeypatch.setenv("AUTOTRADE_LAB_STORE_DIR", str(store_dir))

    at = AppTest.from_file(str(PAGES_DIR / "p04_run_detail.py"), default_timeout=15)
    at.run()
    assert not at.exception
    assert len(at.error) >= 1
    assert len(at.metric) == 0
