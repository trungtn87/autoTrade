"""Kiểm thử dashboard lab V3-16 chỉ đọc cho ALL_SIGNALS_PORTFOLIO."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import all_signals
from autotrade_v3.dashboard.__main__ import main as dashboard_main
from autotrade_v3.reports.labels_vi import FORBIDDEN_ENGLISH_LABELS


PAGES = Path(__file__).resolve().parents[1] / "dashboard" / "pages"


def _field(value: float | int) -> dict[str, object]:
    return {"value": value, "unit": "test", "source": "test", "status": "AVAILABLE", "reason": None}


def _build_store(tmp_path: Path) -> tuple[Path, Path]:
    store = tmp_path / "store"
    reports = tmp_path / "reports"
    for index, profile in enumerate(("IDEAL", "BASE", "STRESS")):
        run_id = f"ALL_SIGNALS_{profile}"
        run_dir = store / profile
        report_dir = reports / run_id
        run_dir.mkdir(parents=True)
        report_dir.mkdir(parents=True)
        manifest = {
            "run_id": run_id,
            "execution_mode": "ALL_SIGNALS_PORTFOLIO",
            "cost_profile": profile,
            "cost_profile_hash": f"hash-{profile.lower()}",
            "git_sha": f"commit-{profile.lower()}",
            "code_dirty": profile == "STRESS",
            "validation_status": "FAILED",
        }
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # Chứng minh dashboard không tin validation_status=FAILED trong manifest.
        (run_dir / "validation.json").write_text(
            json.dumps({"overall_status": "VERIFIED" if profile != "STRESS" else "PARTIALLY_VERIFIED"}),
            encoding="utf-8",
        )
        payload = {
            "generated_at_utc": f"2026-11-15T0{index}:00:00+00:00",
            "run_info": {"run_id": run_id},
            "kpi": {
                "mt5_results": {"total_net_profit": _field(100.0 - index * 50)},
                "risk": {"m2m_max_dd_pct": _field(5.0 + index), "net_pf_leg": _field(1.2 - index / 10)},
                "cost_reality": {"break_even_slippage_per_side_bps": _field(3.0 - index)},
                "volume": {"accepted_entries": _field(1000 + index)},
            },
            "breakdowns": {
                "cost_sensitivity": [{"label": "3,4 bps (Hòa vốn tham chiếu)", "slippage_bps": 3.4}]
            },
        }
        (report_dir / "report_v1.json").write_text(json.dumps(payload), encoding="utf-8")
        (report_dir / "report_mt5.html").write_text("<html><body>Báo cáo thử nghiệm</body></html>", encoding="utf-8")
    return store, reports


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_run_list_order_validation_truth_and_base_default(tmp_path: Path) -> None:
    store, reports = _build_store(tmp_path)
    runs = all_signals.list_all_signals_runs(store, reports)
    assert [run.cost_profile for run in runs] == ["STRESS", "BASE", "IDEAL"]
    assert [run.validation_status for run in runs] == ["PARTIALLY_VERIFIED", "VERIFIED", "VERIFIED"]
    default_run = all_signals.choose_default_run(runs)
    assert default_run is not None
    assert default_run.cost_profile == "BASE"
    assert runs[0].entry_count == 1002
    base_payload = all_signals.load_report_json(default_run)
    assert base_payload is not None
    assert all_signals.comparison_row(base_payload)["break_even"] == 2.0


def test_only_all_signals_portfolio_is_listed(tmp_path: Path) -> None:
    store, reports = _build_store(tmp_path)
    other = store / "OTHER"
    other.mkdir()
    (other / "run_manifest.json").write_text(
        json.dumps({"run_id": "OTHER", "execution_mode": "ALL_SIGNALS", "cost_profile": "BASE"}),
        encoding="utf-8",
    )
    assert len(all_signals.list_all_signals_runs(store, reports)) == 3


def test_all_three_reports_open_and_read_path_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, reports = _build_store(tmp_path)
    monkeypatch.setattr(all_signals, "verify_mt5_report", lambda *args, **kwargs: True)
    before = _digest_tree(tmp_path)
    for run in all_signals.list_all_signals_runs(store, reports):
        assert all_signals.verify_open_report(run).passed
        assert all_signals.load_report_json(run) is not None
        assert all_signals.load_report_html(run) is not None
    assert _digest_tree(tmp_path) == before


def test_hash_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, reports = _build_store(tmp_path)
    run = all_signals.choose_default_run(all_signals.list_all_signals_runs(store, reports))
    assert run is not None
    (run.run_dir / "ARTIFACTS.json").write_text(
        json.dumps({"artifacts": {"report_v1.json": {"sha256": "0" * 64}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(all_signals, "verify_mt5_report", lambda *args, **kwargs: True)
    result = all_signals.verify_open_report(run)
    assert not result.passed
    assert "HASH_MISMATCH" in result.detail


def test_detail_warns_dirty_and_commit_mismatch_and_hides_numbers_on_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, reports = _build_store(tmp_path)
    monkeypatch.setenv("AUTOTRADE_V3_16_RUN_STORE_DIR", str(store))
    monkeypatch.setenv("AUTOTRADE_V3_16_REPORT_DIR", str(reports))
    monkeypatch.setattr(all_signals, "current_git_commit", lambda: "different-commit")
    monkeypatch.setattr(
        all_signals,
        "verify_open_report",
        lambda run: all_signals.VerificationResult(False, "TEST_FAIL"),
    )
    at = AppTest.from_file(str(PAGES / "p10_mt5_report.py"), default_timeout=15)
    at.session_state["v3_16_selected_run"] = "ALL_SIGNALS_STRESS"
    at.run()
    assert not at.exception
    errors = " ".join(error.value for error in at.error)
    assert all_signals.vi("dirty_run_warning") in errors
    assert all_signals.vi("commit_mismatch_warning") in errors
    assert all_signals.vi("verification_hidden") in errors
    assert all_signals.vi("comparison_title") not in [item.value for item in at.subheader]


def test_active_pages_have_no_forbidden_english_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, reports = _build_store(tmp_path)
    monkeypatch.setenv("AUTOTRADE_V3_16_RUN_STORE_DIR", str(store))
    monkeypatch.setenv("AUTOTRADE_V3_16_REPORT_DIR", str(reports))
    at = AppTest.from_file(str(PAGES / "p09_all_signals_runs.py"), default_timeout=15).run()
    assert not at.exception
    visible = " ".join(
        element.value
        for element_type in (at.title, at.caption, at.info, at.warning, at.button, at.selectbox)
        for element in element_type
        if isinstance(element.value, str)
    )
    assert not [label for label in FORBIDDEN_ENGLISH_LABELS if label.lower() in visible.lower()]


def test_serve_command_forces_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[list[str]] = []

    def fake_main() -> int:
        called.append(list(sys.argv))
        return 0

    from streamlit.web import cli as stcli

    monkeypatch.setattr(stcli, "main", fake_main)
    assert dashboard_main(["serve"]) == 0
    args = called[0]
    assert "--server.address=127.0.0.1" in args
    assert "--server.headless=true" in args
    assert not any("0.0.0.0" in arg for arg in args)
