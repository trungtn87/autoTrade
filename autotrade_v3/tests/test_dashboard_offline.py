"""Offline isolation test (B9 / AC6): block all non-loopback sockets and traverse all 7 pages."""
from __future__ import annotations

from pathlib import Path
import shutil
import socket

import pytest
from streamlit.testing.v1 import AppTest

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

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


def test_offline_all_7_pages_no_non_loopback_sockets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_dir, run_id = _build_synthetic_store(tmp_path)
    shutil.copytree(store_dir / run_id, store_dir / "R_SYNTHETIC_002")
    monkeypatch.setenv("AUTOTRADE_LAB_STORE_DIR", str(store_dir))
    monkeypatch.setenv("AUTOTRADE_LAB_NOTES_DB", str(tmp_path / "notes.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_CATALOG_PATH", str(tmp_path / "missing_catalog.duckdb"))
    monkeypatch.setenv("AUTOTRADE_LAB_QA_PATH", str(tmp_path / "missing_qa.json"))

    attempted_sockets: list[tuple[str, object]] = []
    orig_connect = socket.socket.connect
    orig_create = socket.create_connection

    def _guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else str(address)
        attempted_sockets.append(("socket.connect", address))
        if str(host) not in LOOPBACK_HOSTS:
            raise PermissionError(f"Blocked non-loopback socket.connect to {address}")
        return orig_connect(self, address)

    def _guarded_create(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else str(address)
        attempted_sockets.append(("socket.create_connection", address))
        if str(host) not in LOOPBACK_HOSTS:
            raise PermissionError(f"Blocked non-loopback socket.create_connection to {address}")
        return orig_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket, "create_connection", _guarded_create)

    for page_name in ALL_PAGE_FILES:
        at = AppTest.from_file(str(PAGES_DIR / page_name), default_timeout=15)
        at.run()
        assert not at.exception, f"Offline failure on {page_name}: {at.exception}"

    non_loopback = [
        item for item in attempted_sockets
        if (item[1][0] if isinstance(item[1], tuple) and item[1] else str(item[1])) not in LOOPBACK_HOSTS
    ]
    assert non_loopback == [], f"Unexpected non-loopback socket attempts: {non_loopback}"
