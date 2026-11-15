"""Live / Paper Risk & News Guard with explicit telemetry provenance."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.ui_shared import get_store_dir, kpi_card, page_heading, style_plotly


def _execution_slippage_pct(legs: pd.DataFrame) -> pd.Series:
    parts: list[pd.Series] = []
    if {"entry_px_raw", "entry_px_fill"}.issubset(legs.columns):
        raw = legs["entry_px_raw"].astype(float).replace(0, np.nan)
        parts.append((legs["entry_px_fill"].astype(float) - raw).abs() / raw * 100.0)
    if {"exit_px_trigger", "exit_px_fill"}.issubset(legs.columns):
        trigger = legs["exit_px_trigger"].astype(float).replace(0, np.nan)
        parts.append((legs["exit_px_fill"].astype(float) - trigger).abs() / trigger * 100.0)
    return pd.concat(parts, ignore_index=True).dropna() if parts else pd.Series(dtype=float)


def render() -> None:
    page_heading("05", "Live / Paper Risk & News Guard", "Watchdog vận hành đọc trực tiếp telemetry có sẵn; nguồn nào chưa nối sẽ được ghi rõ, không dựng số giả.")
    store_dir = get_store_dir()
    runs = queries.list_store_runs(store_dir=store_dir)
    if not runs:
        st.info("Chưa có run để thiết lập ngưỡng backtest.")
        return

    selected_run = st.selectbox("Threshold source run", [str(run["run_id"]) for run in runs], key="guard-run")
    summary = queries.get_run_summary(selected_run, store_dir=store_dir)
    legs = queries.get_run_legs_ledger(selected_run, store_dir=store_dir)
    decisions = queries.get_run_decisions_drilldown(selected_run, store_dir=store_dir)
    slippage = _execution_slippage_pct(legs)
    avg_slippage = float(slippage.mean()) if not slippage.empty else 0.0
    threshold = float(summary["break_even_slippage_pct"])
    healthy = summary["validation_status"] in {"VERIFIED", "PARTIALLY_VERIFIED"} and avg_slippage <= max(threshold, 0.0)

    cards = st.columns(5)
    values = [
        ("AI Risk Check", "ACTIVE" if healthy else "HALTED", f"Validation: {summary['validation_status']}", "good" if healthy else "bad", "risk-check-status"),
        ("Daily News Guard", "NOT CONNECTED", "No macro calendar DB in V3", "warn", "news-guard-status"),
        ("Avg Execution Slippage", f"{avg_slippage:.4f}%", f"BE threshold {threshold:.4f}%", "good" if avg_slippage <= threshold else "bad", "slippage-watchdog"),
        ("System Mode", "OFFLINE REPLAY", summary["execution_mode"], "info", "system-mode"),
        ("Global Cooldown", "INACTIVE", "No live risk event stream", "good", "global-cooldown"),
    ]
    for column, value in zip(cards, values):
        with column:
            kpi_card(*value)

    st.subheader("Macro News Guard Timeline")
    st.warning("Daily News Database chưa được cấu hình trong V3. Timeline được giữ ở trạng thái rỗng để tránh hiển thị sự kiện giả.")
    timeline = go.Figure()
    timeline.add_trace(go.Scatter(x=[0, 24], y=["News Guard", "News Guard"], mode="lines", line=dict(color="#334155", width=10), hoverinfo="skip"))
    timeline.add_annotation(x=12, y="News Guard", text="NO MACRO EVENT SOURCE", showarrow=False, font=dict(color="#ffb547", size=14))
    timeline.update_xaxes(range=[0, 24], dtick=2, title="Hour (exchange-local)")
    st.plotly_chart(style_plotly(timeline, 220), use_container_width=True, key="macro-news-timeline")

    drift_col, log_col = st.columns([1.15, 1])
    with drift_col:
        st.subheader("Execution Drift Monitor")
        ordered = legs.sort_values(["exit_time_ms", "leg_id"], kind="mergesort").tail(300).copy()
        ordered["timestamp"] = pd.to_datetime(ordered["exit_time_ms"].astype("int64"), unit="ms", utc=True)
        if {"exit_px_trigger", "exit_px_fill"}.issubset(ordered.columns):
            ordered["signal_price"] = ordered["exit_px_trigger"].astype(float)
            ordered["fill_price"] = ordered["exit_px_fill"].astype(float)
            drift = go.Figure([
                go.Scatter(x=ordered["timestamp"], y=ordered["signal_price"], name="Theoretical trigger", line=dict(color="#35d9ff", dash="dot")),
                go.Scatter(x=ordered["timestamp"], y=ordered["fill_price"], name="Execution fill", line=dict(color="#ff7b32")),
            ])
            st.plotly_chart(style_plotly(drift, 430), use_container_width=True, key="execution-drift-monitor")
        else:
            st.info("Ledger không có cặp trigger/fill để tính drift.")

    with log_col:
        st.subheader("Risk / Decision Log")
        if decisions.empty:
            st.info("Không có decision event.")
        else:
            view = decisions.sort_values(["signal_close_ms", "signal_id"], ascending=False, kind="mergesort").head(250).copy()
            view["risk_score"] = view["status"].astype(str).str.upper().map({"ACCEPTED": 20, "SKIPPED": 70, "REJECTED": 90}).fillna(50)
            columns = [column for column in ["signal_close_dt", "symbol", "combo_name", "risk_score", "status", "reason", "blocking_trade_id"] if column in view.columns]
            st.dataframe(view[columns], use_container_width=True, hide_index=True, height=430)
            st.caption("Risk score là mapping trình bày theo decision status; status/reason là dữ liệu RunStore nguyên bản.")


if __name__ == "__main__":
    render()
