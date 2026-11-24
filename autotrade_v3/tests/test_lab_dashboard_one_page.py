"""Tests for the NinLab Terminal One-Page Lab Dashboard (PLUS44 ALL_SIGNALS_PORTFOLIO)."""
from __future__ import annotations

from pathlib import Path
import shutil

import pandas as pd
import pytest

from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import all_signals, one_page, plus44_source
from autotrade_v3.reports.labels_vi import find_english_label_leaks


def test_live_status_badge_threshold_60() -> None:
    # Never synced when N < 60, even if drifted=False
    lbl_0, _ = one_page._live_status_badge(0, 60, has_reconcile=False, drifted=False)
    assert lbl_0 == all_signals.vi("ninlab_status_no_reconcile")

    lbl_22, _ = one_page._live_status_badge(22, 60, has_reconcile=True, drifted=False)
    assert lbl_22 == all_signals.vi("ninlab_status_insufficient")
    assert lbl_22 != all_signals.vi("ninlab_status_synced")

    lbl_59, _ = one_page._live_status_badge(59, 60, has_reconcile=True, drifted=False)
    assert lbl_59 == all_signals.vi("ninlab_status_insufficient")

    # N >= 60
    lbl_60_ok, _ = one_page._live_status_badge(60, 60, has_reconcile=True, drifted=False)
    assert lbl_60_ok == all_signals.vi("ninlab_status_synced")

    lbl_60_drift, _ = one_page._live_status_badge(60, 60, has_reconcile=True, drifted=True)
    assert lbl_60_drift == all_signals.vi("ninlab_status_drift")


def test_main_dashboard_renders_plus44_at_root() -> None:
    app_path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=20)
    at.run()
    assert not at.exception
    all_md = "\n".join(markdown.value for markdown in at.markdown)
    assert 'data-testid="ninlab-top-strip"' in all_md
    assert 'data-testid="ninlab-splits-table"' in all_md
    assert 'data-testid="ninlab-yearly-table"' in all_md
    assert 'data-testid="ninlab-reconciliation-box"' in all_md
    assert 'data-testid="ninlab-combo-table"' in all_md
    assert "V3PLUS43_ALLSIG" in all_md
    assert "+$1,525.84" in all_md
    assert "+$1,168.42" not in all_md
    assert "pnl_own_entry" in all_md
    assert "DEVELOPMENT" in all_md
    assert "VALIDATION" in all_md
    assert "HOLDOUT" in all_md
    assert "Y1" in all_md
    assert "Y2" in all_md
    assert "Y3" in all_md


def test_plus44_list_runs_includes_base_and_stress() -> None:
    runs = plus44_source.list_runs()
    assert [r.cost_profile for r in runs] == ["IDEAL", "BASE", "STRESS"]
    ideal_run, base_run, stress_run = runs
    assert ideal_run.net_profit == pytest.approx(1773.1034, abs=1e-3)
    assert base_run.net_profit == pytest.approx(1525.8433, abs=1e-3)
    assert stress_run.net_profit == pytest.approx(1077.4334, abs=1e-3)
    assert ideal_run.entry_count == 6472
    assert base_run.entry_count == 6472
    assert stress_run.entry_count == 6472

    base_payload = plus44_source.load_payload(base_run)
    stress_payload = plus44_source.load_payload(stress_run)
    assert base_payload is not None and stress_payload is not None
    base_rec = base_payload["breakdowns"]["reconciliation"]
    stress_rec = stress_payload["breakdowns"]["reconciliation"]
    assert base_rec["prune23_summary_net"] == pytest.approx(924.0799919104808)
    assert stress_rec["prune23_summary_net"] == pytest.approx(616.9395952240635)
    assert base_rec["plus44_delta_vs_summary"] == pytest.approx(0.0, abs=1e-6)
    assert stress_rec["plus44_delta_vs_summary"] == pytest.approx(0.0, abs=1e-6)


def test_plus44_combo_table_uses_own_entry_only() -> None:
    runs = {r.cost_profile: r for r in plus44_source.list_runs()}
    expected_totals = {
        "IDEAL": float(runs["IDEAL"].net_profit or 0.0),
        "BASE": float(runs["BASE"].net_profit or 0.0),
        "STRESS": float(runs["STRESS"].net_profit or 0.0),
    }
    for profile, expected_net in expected_totals.items():
        payload = plus44_source.load_payload(runs[profile])
        assert payload is not None
        assert payload["run_info"]["attribution"] == "OWN_ENTRY_ONLY"
        assert payload["run_info"]["mode"] == "ALL_SIGNALS_PORTFOLIO"
        combos = {row["combo_key"]: row for row in payload["breakdowns"]["combos"]}
        assert len(combos) == 43
        assert "TV13" not in combos
        assert all("portfolio_net_pnl" not in row for row in combos.values())
        assert sum(row["own_entry_pnl"]["value"] for row in combos.values()) == pytest.approx(expected_net)
        assert sum(s["net_pnl"] for s in payload["breakdowns"]["splits"]) == pytest.approx(expected_net)
        assert sum(y["net_pnl"] for y in payload["breakdowns"]["yearly"]) == pytest.approx(expected_net)


def test_mutation_fails_verify_and_hides_numbers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src_dir = plus44_source.DEFAULT_OUTPUT
    dst_dir = tmp_path / "plus43_all_signals"
    shutil.copytree(src_dir, dst_dir)

    summary_path = dst_dir / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv"
    df = pd.read_csv(summary_path)
    df.loc[df["study_group"].eq("PRUNE23_PLUS_TV20") & df["profile"].eq("BASE"), "net_pnl"] += 123.45
    df.to_csv(summary_path, index=False)

    monkeypatch.setenv("AUTOTRADE_PLUS43_ALL_SIGNALS_DIR", str(dst_dir))

    app_path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=20)
    at.run()
    assert not at.exception
    all_md = "\n".join(m.value for m in at.markdown)
    assert 'data-testid="ninlab-top-strip"' not in all_md
    errors = "\n".join(e.value for e in at.error)
    assert all_signals.vi("ninlab_hash_fail_banner") in errors


def test_serve_command_dark_and_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from autotrade_v3.dashboard import __main__ as dmain
    from streamlit.web import cli as stcli

    captured: list[str] = []

    def _fake_stcli_main() -> int:
        import sys
        captured.extend(sys.argv)
        return 0

    monkeypatch.setattr(stcli, "main", _fake_stcli_main)
    rc = dmain.main(["serve"])
    assert rc == 0
    assert "--server.address=127.0.0.1" in captured
    assert "--theme.base=dark" in captured
