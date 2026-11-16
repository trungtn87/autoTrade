"""Read-only, multi-mode local run catalog.

This does not treat research/legacy runs as V3-16 MT5 reports.
"""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard import run_catalog
from autotrade_v3.dashboard.all_signals import vi
from autotrade_v3.dashboard.ui_shared import inject_research_theme


def _money(value: float | None) -> str:
    return "—" if value is None else "$" + format(value, ",.2f")


def render() -> None:
    inject_research_theme()
    st.title(vi("catalog_title"))
    st.caption(vi("catalog_subtitle"))
    if st.button(vi("reload"), key="catalog-refresh", type="primary"):
        st.rerun()

    runs = run_catalog.discover_runs()
    if not runs:
        st.warning(vi("catalog_no_runs"))
        st.code("runs/**/run_manifest.json\nAUTOTRADE_BACKTEST_RUN_ROOTS", language="text")
        return

    modes = sorted({run.execution_mode for run in runs})
    profiles = sorted({run.cost_profile for run in runs})
    mode = st.selectbox(vi("catalog_mode"), ["Tất cả", *modes], key="catalog-mode")
    profile = st.selectbox(vi("cost_profile"), ["Tất cả", *profiles], key="catalog-profile")
    search = st.text_input(vi("catalog_search"), key="catalog-query").strip().casefold()

    filtered = [
        run for run in runs
        if (mode == "Tất cả" or run.execution_mode == mode)
        and (profile == "Tất cả" or run.cost_profile == profile)
        and (
            not search or search in " ".join((
                run.run_id, run.strategy_ref, run.execution_mode,
                " ".join(run.symbols), str(run.run_dir),
            )).casefold()
        )
    ]
    st.caption(f"{vi('catalog_count')}: {len(filtered)} / {len(runs)}")
    if not filtered:
        st.info(vi("catalog_no_match"))
        return

    st.dataframe(
        [{
            vi("run_id"): run.run_id,
            vi("generated_at"): run.generated_at or "—",
            vi("catalog_mode"): run.execution_mode,
            vi("catalog_strategy"): run.strategy_ref,
            vi("cost_profile"): run.cost_profile,
            vi("verification_status"): run.validation_status,
            vi("net_profit"): _money(run.net_pnl),
            vi("catalog_entries"): run.accepted_entries,
            vi("catalog_path"): str(run.run_dir),
        } for run in filtered],
        hide_index=True,
        use_container_width=True,
        height=min(600, 45 + 38 * len(filtered)),
    )

    by_identity = {run.identity: run for run in filtered}
    selected = st.selectbox(
        vi("catalog_open"),
        list(by_identity),
        format_func=lambda identity: (
            f"{by_identity[identity].run_id} · {by_identity[identity].execution_mode}"
            f" · {by_identity[identity].cost_profile} · {by_identity[identity].run_dir}"
        ),
        key="catalog-selected-run",
    )
    run = by_identity[selected]
    st.subheader(vi("report_metadata"))
    st.json({
        "run_id": run.run_id,
        "run_directory": str(run.run_dir),
        "execution_mode": run.execution_mode,
        "strategy_ref": run.strategy_ref,
        "cost_profile": run.cost_profile,
        "created_at_utc": run.generated_at,
        "git_sha": run.git_sha,
        "code_dirty": run.code_dirty,
        "symbols": list(run.symbols),
        "validation_from_file": run.validation_status,
        "validation_json_present": run.validation_present,
        "analysis_note": run.metrics_note,
    }, expanded=False)

    if run.code_dirty:
        st.error(vi("dirty_run_warning"))
    if run.validation_status != "VERIFIED":
        st.warning(f"{vi('catalog_unverified')}: {run.validation_status}")
    st.caption(run.metrics_note)
    if run.net_pnl is not None or run.roi_pct is not None or run.accepted_entries is not None:
        a, b, c = st.columns(3)
        a.metric(vi("net_profit"), _money(run.net_pnl))
        b.metric(vi("catalog_roi"), "—" if run.roi_pct is None else f"{run.roi_pct:,.2f}%")
        c.metric(vi("catalog_entries"), "—" if run.accepted_entries is None else f"{run.accepted_entries:,}")

    if run.combo_rows:
        st.subheader(vi("catalog_combos"))
        st.caption(vi("catalog_combo_note"))
        allowed = ("symbol", "combo_id", "combo_name", "entries", "net_pnl", "profit_factor")
        st.dataframe(
            [{key: row.get(key) for key in allowed} for row in run.combo_rows],
            hide_index=True,
            use_container_width=True,
        )

    if run.execution_mode == "ALL_SIGNALS_PORTFOLIO":
        st.info(vi("catalog_mt5_hint"))
    if run.report_path is not None:
        with st.expander(vi("catalog_original_report")):
            try:
                st.markdown(run.report_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                st.error(vi("catalog_report_error"))


render()
