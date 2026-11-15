"""Three-stage Signal Explorer with real V3 artifacts and candle drill-down."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.ui_shared import get_catalog_path, get_store_dir, page_heading, render_status_banner, style_plotly


def render() -> None:
    page_heading("04", "Signal Explorer", "Theo dấu trigger thô qua filter gate, position lock và execution ledger.")
    store_dir, catalog_path = get_store_dir(), get_catalog_path()
    runs = queries.list_store_runs(store_dir=store_dir)
    if not runs:
        st.info("RunStore chưa có tín hiệu.")
        return

    selected_run = st.selectbox("Run", [str(run["run_id"]) for run in runs], key="signal-run")
    summary = queries.get_run_summary(selected_run, store_dir=store_dir)
    render_status_banner(summary)
    all_signals = queries.list_run_signals(selected_run, store_dir=store_dir)
    if all_signals.empty:
        st.info("Run này không có signal.")
        return

    combo_options = sorted(all_signals["combo_key"].unique().tolist())
    selected_combo = st.selectbox("Combo", combo_options, key="signal-combo")
    funnel_data = queries.get_signal_funnel(selected_run, store_dir=store_dir, combo_key=selected_combo)
    funnel = go.Figure(go.Funnel(
        y=["Raw Indicator Triggers", "Passed Filter Signals", "Executed Entries"],
        x=[funnel_data["raw_indicator_triggers"], funnel_data["passed_filter_signals"], funnel_data["executed_entries"]],
        textinfo="value+percent initial",
        marker=dict(color=["#35d9ff", "#ffb547", "#36e394"]),
        connector=dict(line=dict(color="#334155")),
    ))
    st.plotly_chart(style_plotly(funnel, 300), use_container_width=True, key="signal-funnel")

    table_col, chart_col = st.columns([1.05, 1.35])
    with table_col:
        st.subheader("Signal Log & Rejection Tags")
        f1, f2 = st.columns(2)
        status_options = ["ALL"] + sorted(all_signals["status"].astype(str).unique().tolist())
        status = f1.selectbox("Status", status_options)
        reasons = ["ALL"] + sorted(all_signals["reason"].astype(str).unique().tolist())
        reason = f2.selectbox("Reason", reasons)
        filtered = queries.list_run_signals(
            selected_run,
            store_dir=store_dir,
            combo_key=selected_combo,
            status=None if status == "ALL" else status,
        )
        if reason != "ALL":
            filtered = filtered[filtered["reason"].astype(str) == reason]
        display_columns = ["signal_id", "signal_close_dt", "entry_price", "status", "reason", "leg_count", "net_pnl"]
        event = st.dataframe(
            filtered[display_columns].head(500),
            use_container_width=True,
            hide_index=True,
            height=540,
            on_select="rerun",
            selection_mode="single-row",
            key="signal-log-table",
        )

    if filtered.empty:
        chart_col.info("Không có signal phù hợp bộ lọc.")
        return
    selected_index = event.selection.rows[0] if event.selection.rows else 0
    selected_signal = str(filtered.iloc[selected_index]["signal_id"])
    detail = queries.get_signal_detail_and_chart(selected_run, selected_signal, store_dir=store_dir, catalog_path=catalog_path)
    signal, decision, levels = detail["signal"], detail["decision"], detail["levels"]

    with chart_col:
        st.subheader("Interactive Candlestick Drill-down")
        st.caption(f"`{selected_signal}` · {signal['symbol']} · {signal['combo_name']} · Decision `{decision.get('status', 'UNKNOWN')}`")
        if detail["ambiguity_flags"]:
            st.warning("Ambiguity: " + ", ".join(detail["ambiguity_flags"]))
        fig = go.Figure()
        candles = detail["m15_df"]
        if not candles.empty:
            times = pd.to_datetime(candles["time_ms"].astype("int64"), unit="ms", utc=True)
            fig.add_trace(go.Candlestick(
                x=times, open=candles["open"], high=candles["high"], low=candles["low"], close=candles["close"],
                name="M15", increasing_line_color="#36e394", decreasing_line_color="#ff5c68",
            ))
        m1 = detail["m1_path_df"]
        if not m1.empty:
            fig.add_trace(go.Scatter(x=pd.to_datetime(m1["time_ms"].astype("int64"), unit="ms", utc=True), y=m1["close"], name="M1 path", line=dict(color="#35d9ff", width=1)))
        trailing_source = detail["trailing_series_df"]
        trailing = trailing_source.dropna(subset=["trailing_stop"]) if "trailing_stop" in trailing_source.columns else pd.DataFrame()
        if not trailing.empty:
            fig.add_trace(go.Scatter(x=pd.to_datetime(trailing["time_ms"].astype("int64"), unit="ms", utc=True), y=trailing["trailing_stop"], name="Trailing", line=dict(color="#ffb547", dash="dot")))
        fig.add_hline(y=levels["entry_price"], line_color="#35d9ff", annotation_text="ENTRY")
        if levels["tp_price"] is not None:
            fig.add_hline(y=levels["tp_price"], line_dash="dash", line_color="#36e394", annotation_text="TP")
        if levels["sl_price"] is not None:
            fig.add_hline(y=levels["sl_price"], line_dash="dash", line_color="#ff5c68", annotation_text="SL")
        fig.update_layout(xaxis_rangeslider_visible=False)
        st.plotly_chart(style_plotly(fig, 570), use_container_width=True, key="signal-candlestick")

    with st.expander("Legs & execution events", expanded=False):
        st.dataframe(detail["legs_df"], use_container_width=True, hide_index=True)
        st.dataframe(detail["events_df"], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render()
