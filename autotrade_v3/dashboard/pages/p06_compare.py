"""Run Comparison & Walk-Forward Lab using canonical V3 RunStore metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.metrics_engine import cost_sensitivity_grid, oos_degradation_ratio
from autotrade_v3.dashboard.ui_shared import PLOTLY_COLORS, get_store_dir, kpi_card, page_heading, style_plotly


def _metric_matrix(summaries: list[dict]) -> pd.DataFrame:
    specs = [
        ("Net PnL", "net_pnl", lambda v: f"${v:,.2f}"),
        ("Weighted PF", "weighted_pf", lambda v: str(v) if isinstance(v, str) else f"{v:.2f}"),
        ("Max DD %", "m2m_max_drawdown_pct", lambda v: "N/A" if v is None else f"{v:.2f}%"),
        ("Total Entries", "accepted_entries", lambda v: f"{int(v):,}"),
        ("Break-Even Slippage %", "break_even_slippage_pct", lambda v: f"{v:.4f}%"),
    ]
    base = summaries[0]
    rows: list[dict] = []
    for label, key, formatter in specs:
        row: dict[str, str] = {"Metric": label}
        base_value = base.get(key)
        for index, summary in enumerate(summaries):
            value = summary.get(key)
            cell = formatter(value)
            if index and isinstance(value, (int, float)) and isinstance(base_value, (int, float)):
                cell += f"  ({value - base_value:+.2f})"
            row[str(summary["run_id"])] = cell
        rows.append(row)
    return pd.DataFrame(rows)


def render() -> None:
    page_heading("02", "Run Comparison & Walk-Forward Lab", "So sánh chéo run, kiểm tra ngoài mẫu và stress-test sức chịu chi phí.")
    store_dir = get_store_dir()
    runs = queries.list_store_runs(store_dir=store_dir)
    if len(runs) < 2:
        st.info("Cần ít nhất hai run trong RunStore để mở phòng lab so sánh.")
        return

    run_ids = [str(run["run_id"]) for run in runs]
    selected = st.multiselect("Runs", run_ids, default=run_ids[: min(4, len(run_ids))], max_selections=6)
    if len(selected) < 2:
        st.warning("Chọn ít nhất hai run.")
        return

    comparison = queries.compare_runs_summary(selected, store_dir=store_dir)
    for warning in comparison["warnings"]:
        st.warning(warning)

    st.subheader("Multi-Run Matrix")
    st.dataframe(_metric_matrix(comparison["summaries"]), use_container_width=True, hide_index=True, height=260)

    min_year = 2024
    max_year = 2026
    split_year = st.slider("Mốc bắt đầu Out-of-Sample", min_year, max_year, 2026)
    split = pd.Timestamp(f"{split_year}-01-01", tz="UTC")
    equity = queries.get_compare_equity_curves(selected, store_dir=store_dir)
    chart_col, kpi_col = st.columns([4.2, 1])
    with chart_col:
        st.subheader("In-Sample vs Out-of-Sample Equity")
        fig = go.Figure()
        for index, (run_id, frame) in enumerate(equity.groupby("run_id", sort=False)):
            fig.add_trace(go.Scatter(x=frame["timestamp_dt"], y=frame["equity"], name=str(run_id), line=dict(color=PLOTLY_COLORS[index % len(PLOTLY_COLORS)], width=2)))
        fig.add_vline(x=split.timestamp() * 1000, line_dash="dash", line_color="#eef3f9")
        if not equity.empty:
            fig.add_vrect(x0=split, x1=equity["timestamp_dt"].max(), fillcolor="#8193aa", opacity=0.10, line_width=0, annotation_text="OUT-OF-SAMPLE")
        st.plotly_chart(style_plotly(fig, 410), use_container_width=True, key="walk-forward-equity")
    with kpi_col:
        base_equity = equity[equity["run_id"] == selected[0]].copy()
        degradation = oos_degradation_ratio(base_equity, split)
        if degradation is None:
            kpi_card("OOS Degradation Ratio", "N/A", "Không đủ dữ liệu hai phía mốc chia", "warn", "oos-degradation")
        else:
            tone = "good" if degradation >= 0.75 else "warn" if degradation >= 0.35 else "bad"
            kpi_card("OOS Degradation Ratio", f"{degradation:.1%}", "OOS annualized / IS annualized", tone, "oos-degradation")
        base_summary = comparison["summaries"][0]
        kpi_card("Baseline PF", str(base_summary["weighted_pf"]) if isinstance(base_summary["weighted_pf"], str) else f"{base_summary['weighted_pf']:.2f}", "Weighted by realized dollars", "info", "baseline-pf")

    st.subheader("Cost Sensitivity Grid")
    baseline_legs = queries.get_run_legs_ledger(selected[0], store_dir=store_dir)
    levels = [0.00, 0.02, 0.035, 0.05]
    modes = {"IDEAL": 0.00, "REALISTIC TAKER + FUNDING": 0.075}
    grid = cost_sensitivity_grid(baseline_legs, levels, modes)
    pivot = grid.pivot(index="fee_mode", columns="slippage_pct", values="net_pnl").reindex(index=list(modes), columns=levels)
    max_abs = max(float(np.abs(pivot.to_numpy()).max()), 1.0)
    heat = go.Figure(go.Heatmap(
        z=pivot.to_numpy(),
        x=[f"{value:.3f}%" for value in levels],
        y=pivot.index.tolist(),
        zmid=0,
        zmin=-max_abs,
        zmax=max_abs,
        colorscale=[[0, "#8f1737"], [0.5, "#ffb547"], [1, "#167d56"]],
        text=np.round(pivot.to_numpy(), 2),
        texttemplate="$%{text:,.2f}",
        colorbar_title="Net PnL",
    ))
    st.plotly_chart(style_plotly(heat, 310), use_container_width=True, key="cost-sensitivity-grid")

    with st.expander("Per-signal diff", expanded=False):
        left, right = st.columns(2)
        run_a = left.selectbox("Baseline", selected, index=0)
        run_b = right.selectbox("Target", selected, index=1)
        if run_a != run_b:
            diff = queries.get_compare_signal_diff(run_a, run_b, store_dir=store_dir)
            st.json(diff["summary"])
            changed = diff["diff_df"]
            if not changed.empty:
                changed = changed[changed["category"] != "IDENTICAL"]
            st.dataframe(changed.head(300), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render()
