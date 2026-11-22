"""Tests for the NinLab Terminal One-Page Lab Dashboard (Step B)."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import all_signals, one_page
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


def test_one_page_real_data_renders_and_is_readonly() -> None:
    app_path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=20)
    at.run()
    assert not at.exception

    all_md = "\n".join(m.value for m in at.markdown)
    assert 'data-testid="ninlab-top-strip"' in all_md
    assert 'data-testid="ninlab-combo-table"' in all_md
    # Default run is locked live V3_LAB_11_PRUNE23_BASE with explicit cost label
    assert "V3_LAB_11_PRUNE23_BASE" in all_md
    assert all_signals.vi("ninlab_cost_base_desc") in all_md
    # Top strip has at most 6 KPI tiles
    assert all_md.count('class="nl-tile"') <= 6
    # Combo table rows == exact 23 live combos from registry
    live_keys = one_page.get_live_23_combo_keys()
    assert len(live_keys) == 23
    runs = all_signals.list_all_signals_runs()
    sel = one_page.select_run_for_family_and_profile(runs, "V3_LAB_11_PRUNE23", "BASE")
    assert sel is not None
    payload = all_signals.load_report_json(sel)
    assert payload is not None
    ok, err = one_page.verify_one_page_totals(sel, payload)
    assert ok, err
    combos_in_payload = [str(c.get("combo_key", "")) for c in (payload["breakdowns"]["combos"] or [])]
    assert len(combos_in_payload) == len(live_keys) == 23
    assert set(combos_in_payload) == live_keys
    # 1 main chart + 2 small charts = 3 plotly charts
    assert len(at.get("plotly_chart")) == 3
    # Zero forbidden English labels
    assert find_english_label_leaks(all_md) == []


def test_cost_profile_toggle_ideal_base_stress_and_non_live_split() -> None:
    app_path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=20)
    at.run()
    assert not at.exception
    assert at.radio[0].value == "BASE"

    at.radio[0].set_value("STRESS").run()
    assert not at.exception
    all_md = "\n".join(m.value for m in at.markdown)
    assert "V3_LAB_11_PRUNE23_STRESS" in all_md
    assert all_signals.vi("ninlab_cost_stress_desc") in all_md
    assert find_english_label_leaks(all_md) == []

    # Non-live combos are partitioned away from main 23-combo set
    live_keys = one_page.get_live_23_combo_keys()
    sample = [{"combo_key": k} for k in sorted(live_keys)[:2]] + [{"combo_key": "BTC-USDT/C12"}, {"combo_key": "ETH-USDT/C16"}]
    in_live, out_live = one_page.split_live_and_non_live_combos(sample, live_keys)
    assert [c["combo_key"] for c in out_live] == ["BTC-USDT/C12", "ETH-USDT/C16"]
    assert len(in_live) == 2


def test_mutation_fails_verify_and_hides_numbers(tmp_path: Path, monkeypatch) -> None:
    src_run = Path("runs/v3_16_all_signals/BASE")
    src_rep = Path("evidence/v3-16/reports/ALL_SIGNALS_BASE")
    if not (src_run.is_dir() and src_rep.is_dir()):
        return

    store = tmp_path / "runs"
    rep_store = tmp_path / "reports"
    dst_run = store / "BASE"
    dst_rep = rep_store / "ALL_SIGNALS_BASE"
    shutil.copytree(src_run, dst_run)
    shutil.copytree(src_rep, dst_rep)

    # Mutate a single number in report_v1.json
    rjson_path = dst_rep / "report_v1.json"
    data = json.loads(rjson_path.read_text(encoding="utf-8"))
    data["kpi"]["account"]["final_equity_mtm"]["value"] += 123.45
    rjson_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setenv("AUTOTRADE_V3_16_RUN_STORE_DIR", str(store))
    monkeypatch.setenv("AUTOTRADE_V3_16_REPORT_DIR", str(rep_store))

    app_path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=20)
    at.run()
    assert not at.exception
    all_md = "\n".join(m.value for m in at.markdown)
    assert 'data-testid="ninlab-top-strip"' not in all_md
    errors = "\n".join(e.value for e in at.error)
    assert all_signals.vi("ninlab_hash_fail_banner") in errors


def test_serve_command_dark_and_loopback(monkeypatch) -> None:
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


def test_total_and_run_id_mutation_fail_closed_and_hide_numbers(tmp_path: Path, monkeypatch) -> None:
    src_run = Path("runs/v3_16_all_signals/V3_LAB_11_PRUNE23_BASE")
    if not src_run.is_dir():
        src_run = Path("runs/v3_16_all_signals/BASE")
    if not src_run.is_dir():
        return

    store = tmp_path / "runs"
    rep_store = tmp_path / "reports"
    rep_store.mkdir(parents=True, exist_ok=True)
    dst_run = store / "V3_LAB_11_PRUNE23_BASE"
    shutil.copytree(src_run, dst_run)
    rjson_path = dst_run / "report_v1.json"

    runs = all_signals.list_all_signals_runs()
    real_sel = next((r for r in runs if r.run_id == "V3_LAB_11_PRUNE23_BASE"), runs[0])
    real_payload = all_signals.load_report_json(real_sel)
    assert real_payload is not None
    ok, _ = one_page.verify_one_page_totals(real_sel, real_payload)
    assert ok

    # Direct mutation 1: run_id mismatch
    bad_id = json.loads(rjson_path.read_text(encoding="utf-8"))
    bad_id["run_info"]["run_id"] = "OTHER_RUN"
    ok_id, msg_id = one_page.verify_one_page_totals(real_sel, bad_id)
    assert not ok_id and msg_id.startswith("RUN_ID_MISMATCH")

    # Direct mutation 2: single combo PnL mismatch
    bad_sum = json.loads(rjson_path.read_text(encoding="utf-8"))
    bad_sum["breakdowns"]["combos"][0]["portfolio_net_pnl"]["value"] += 50.0
    ok_sum, msg_sum = one_page.verify_one_page_totals(real_sel, bad_sum)
    assert not ok_sum and msg_sum.startswith("TOTAL_MISMATCH")

    # End-to-end AppTest with mutated combo total (bypassing hash check to exercise total reconciliation gate)
    rjson_path.write_text(json.dumps(bad_sum), encoding="utf-8")
    monkeypatch.setenv("AUTOTRADE_V3_16_RUN_STORE_DIR", str(store))
    monkeypatch.setenv("AUTOTRADE_V3_16_REPORT_DIR", str(rep_store))
    monkeypatch.setattr(all_signals, "verify_open_report", lambda run: all_signals.VerificationResult(True, "ok"))

    app_path = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=20)
    at.run()
    assert not at.exception
    all_md = "\n".join(m.value for m in at.markdown)
    assert 'data-testid="ninlab-top-strip"' not in all_md
    assert 'data-testid="ninlab-combo-table"' not in all_md
    errors = "\n".join(e.value for e in at.error)
    assert all_signals.vi("ninlab_total_verify_fail") in errors
