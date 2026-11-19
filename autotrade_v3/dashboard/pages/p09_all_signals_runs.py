"""Trang danh sách run ALL_SIGNALS V3-16."""
from __future__ import annotations

import html
import streamlit as st

from autotrade_v3.dashboard import all_signals
from autotrade_v3.dashboard.ui_shared import inject_research_theme


def _money(value: float | None) -> str:
    return all_signals.vi("table_empty_value") if value is None else f"$ {value:,.2f}"


def _number(value: float | None, suffix: str = "") -> str:
    return all_signals.vi("table_empty_value") if value is None else f"{value:,.2f}{suffix}"


def _run_table(runs: list[all_signals.RunReport]) -> str:
    headers = (
        "run_id", "generated_at", "cost_profile", "cost_profile_hash", "commit",
        "verification_status", "net_profit", "max_drawdown", "entry_count",
    )
    head = "".join(f"<th>{html.escape(all_signals.vi(key))}</th>" for key in headers)
    body = ""
    for run in runs:
        values = (
            run.run_id,
            run.generated_at_utc,
            run.cost_profile,
            run.cost_profile_hash[:12],
            run.git_commit[:12],
            all_signals.validation_status_vi(run.validation_status),
            _money(run.net_profit),
            _number(run.max_drawdown_m2m_pct, "%"),
            all_signals.vi("table_empty_value") if run.entry_count is None else f"{run.entry_count:,}",
        )
        body += "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in values) + "</tr>"
    return (
        "<style>.v3-table-wrap{overflow-x:auto;margin:1rem 0 1.5rem;border:1px solid #35445b;border-radius:12px}"
        ".v3-table{width:100%;border-collapse:collapse;white-space:nowrap;background:#111827}"
        ".v3-table th{padding:12px;text-align:left;background:#192437;color:#f8fafc;border-bottom:1px solid #35445b}"
        ".v3-table td{padding:11px 12px;color:#e5edf7;border-bottom:1px solid #263244;font-variant-numeric:tabular-nums}"
        ".v3-table tr:last-child td{border-bottom:0}</style>"
        f'<div class="v3-table-wrap" data-testid="all-signals-runs-table"><table class="v3-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def render() -> None:
    inject_research_theme()
    st.title(all_signals.vi("runs_title"))
    st.caption(all_signals.vi("runs_subtitle"))
    st.info(all_signals.vi("all_signals_only"))
    if st.button(all_signals.vi("reload"), key="reload-runs", type="primary"):
        st.rerun()

    runs = all_signals.list_all_signals_runs()
    if not runs:
        st.warning(all_signals.vi("no_runs"))
        return

    st.markdown(_run_table(runs), unsafe_allow_html=True)

    default = all_signals.choose_default_run(runs)
    selected_id = st.selectbox(
        all_signals.vi("open_report"),
        [run.run_id for run in runs],
        index=runs.index(default) if default else 0,
        key="run-list-selection",
    )
    if st.button(all_signals.vi("open_report"), key="open-selected-report", type="primary"):
        st.session_state["v3_16_selected_run"] = selected_id
        st.switch_page("pages/p10_mt5_report.py")


render()
