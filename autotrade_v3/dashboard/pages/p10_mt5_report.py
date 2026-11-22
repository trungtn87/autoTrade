"""Trang chi tiết báo cáo MT5 ALL_SIGNALS V3-16."""
from __future__ import annotations

import html

import streamlit as st
import streamlit.components.v1 as components

from autotrade_v3.dashboard import all_signals
from autotrade_v3.dashboard.ui_shared import inject_research_theme


def _warn_stale(run: all_signals.RunReport, current_commit: str) -> None:
    if run.code_dirty:
        st.error(all_signals.vi("dirty_run_warning"))
    if current_commit and run.git_commit and current_commit != run.git_commit:
        st.error(
            f'{all_signals.vi("commit_mismatch_warning")} '
            f'{all_signals.vi("report_commit")}: {run.git_commit[:12]} · '
            f'{all_signals.vi("current_commit")}: {current_commit[:12]}'
        )


def _numeric_text(value: object, format_spec: str, *, prefix: str = "", suffix: str = "") -> str:
    if not isinstance(value, (int, float)):
        return all_signals.vi("table_empty_value")
    return f"{prefix}{format(value, format_spec)}{suffix}"


def _comparison(runs: list[all_signals.RunReport]) -> bool:
    rows: list[dict[str, object]] = []
    all_passed = True
    for profile in all_signals.SUPPORTED_PROFILES:
        run = all_signals.runs_by_profile(runs).get(profile)
        if run is None:
            all_passed = False
            continue
        result = all_signals.verify_open_report(run)
        payload = all_signals.load_report_json(run) if result.passed else None
        if payload is None:
            all_passed = False
            continue
        metric_values = all_signals.comparison_row(payload)
        rows.append(
            {
                all_signals.vi("cost_profile"): profile,
                all_signals.vi("net_profit"): metric_values["net"],
                all_signals.vi("profit_factor"): metric_values["pf"],
                all_signals.vi("max_drawdown"): metric_values["drawdown"],
                all_signals.vi("break_even_slippage"): metric_values["break_even"],
            }
        )
    if rows:
        st.subheader(all_signals.vi("comparison_title"))
        headers = ("cost_profile", "net_profit", "profit_factor", "max_drawdown", "break_even_slippage")
        head = "".join(f"<th>{html.escape(all_signals.vi(key))}</th>" for key in headers)
        body = ""
        for row in rows:
            display_values = (
                row[all_signals.vi("cost_profile")],
                _numeric_text(row[all_signals.vi("net_profit")], ",.2f", prefix="$ "),
                _numeric_text(row[all_signals.vi("profit_factor")], ".3f"),
                _numeric_text(row[all_signals.vi("max_drawdown")], ".2f", suffix="%"),
                _numeric_text(row[all_signals.vi("break_even_slippage")], ".1f", suffix=" bps"),
            )
            body += "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in display_values) + "</tr>"
        st.markdown(
            "<style>.v3-compare{width:100%;border-collapse:collapse;background:#111827;border:1px solid #35445b}"
            ".v3-compare th,.v3-compare td{padding:11px 13px;border-bottom:1px solid #35445b;color:#f8fafc;text-align:right;font-variant-numeric:tabular-nums}"
            ".v3-compare th:first-child,.v3-compare td:first-child{text-align:left}</style>"
            f'<div style="overflow-x:auto"><table class="v3-compare" data-testid="cost-profile-comparison"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    return all_passed


def render() -> None:
    inject_research_theme()
    st.title(all_signals.vi("report_title"))
    st.caption(all_signals.vi("report_subtitle"))
    if st.button(all_signals.vi("back_to_runs"), key="back-to-runs"):
        main_page = st.session_state.get("ninlab_main_page")
        st.switch_page(main_page if main_page is not None else "pages/p09_all_signals_runs.py")

    runs = all_signals.list_all_signals_runs()
    if not runs:
        st.warning(all_signals.vi("no_runs"))
        return
    profiles = all_signals.runs_by_profile(runs)
    requested = st.session_state.get("v3_16_selected_run")
    selected = all_signals.choose_default_run(runs, str(requested) if requested else None)
    assert selected is not None
    available_profiles = [profile for profile in all_signals.SUPPORTED_PROFILES if profile in profiles]
    profile = st.radio(
        all_signals.vi("choose_cost_profile"),
        available_profiles,
        index=available_profiles.index(selected.cost_profile),
        horizontal=True,
        key="profile-quick-switch",
    )
    selected = profiles[profile]
    st.session_state["v3_16_selected_run"] = selected.run_id

    current_commit = all_signals.current_git_commit()
    _warn_stale(selected, current_commit)
    if selected.validation_status != "VERIFIED":
        st.error(
            f'{all_signals.vi("validation_not_verified_warning")} '
            f'{all_signals.vi("validation_file_status")}: '
            f'{all_signals.validation_status_vi(selected.validation_status)}'
        )
    st.markdown(
        f'<div class="v3-panel" data-testid="run-metadata">'
        f'<strong>{html.escape(all_signals.vi("run_id"))}:</strong> {html.escape(selected.run_id)}<br>'
        f'<strong>{html.escape(all_signals.vi("commit"))}:</strong> {html.escape(selected.git_commit)}<br>'
        f'<strong>{html.escape(all_signals.vi("generated_at"))}:</strong> {html.escape(selected.generated_at_utc)}<br>'
        f'<strong>{html.escape(all_signals.vi("validation_file_status"))}:</strong> '
        f'{html.escape(all_signals.validation_status_vi(selected.validation_status))}<br>'
        f'<strong>{html.escape(all_signals.vi("cost_profile_hash"))}:</strong> {html.escape(selected.cost_profile_hash)}'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.spinner(all_signals.vi("verification_running")):
        verification = all_signals.verify_open_report(selected)
    if not verification.passed:
        st.error(f'{all_signals.vi("verify_fail")}: {verification.detail}')
        st.error(all_signals.vi("verification_hidden"))
        return
    st.success(all_signals.vi("verify_pass"))

    if not _comparison(runs):
        st.warning(all_signals.vi("profile_unavailable"))

    json_bytes = all_signals.report_json_bytes(selected)
    if json_bytes is not None:
        st.download_button(
            all_signals.vi("export_ai_json"),
            data=json_bytes,
            file_name=f"{selected.run_id}_report_v1.json",
            mime="application/json",
            key="export-ai-json",
            type="primary",
        )

    report_html = all_signals.load_report_html(selected)
    if report_html is None:
        st.error(all_signals.vi("missing_report"))
        return
    st.subheader(all_signals.vi("report_content"))
    components.html(report_html, height=2400, scrolling=True)


render()
