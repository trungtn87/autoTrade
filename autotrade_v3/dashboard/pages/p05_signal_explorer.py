"""Screen 5: Signal Explorer — signal table, search by signal_id, M15/M1/trailing/exit chart."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.ui_shared import get_catalog_path, get_store_dir, render_status_banner


def render() -> None:
    st.header("Signal Explorer")
    store_dir = get_store_dir()
    catalog_path = get_catalog_path()
    runs = queries.list_store_runs(store_dir=store_dir)
    if not runs:
        st.info("Run Store đang trống. Chưa có tín hiệu để tra cứu.")
        return

    run_ids = [str(r["run_id"]) for r in runs]
    selected_run = st.selectbox("Chọn Run ID", run_ids, key="sig_exp_run")

    try:
        summary = queries.get_run_summary(selected_run, store_dir=store_dir)
        render_status_banner(summary)
        all_sigs = queries.list_run_signals(selected_run, store_dir=store_dir)
    except queries.MissingArtifactError as exc:
        st.error(f"Lỗi thiếu artifact trong RunStore: {exc}")
        return
    except Exception as exc:
        st.error(f"Không thể đọc tín hiệu của run '{selected_run}': {exc}")
        return

    if all_sigs.empty:
        st.info("Run này không có tín hiệu nào.")
        return

    f1, f2, f3, f4 = st.columns(4)
    sig_query = f1.text_input("Tìm theo signal_id", value="", key="sig_exp_query")
    sym_opts = ["ALL"] + sorted(all_sigs["symbol"].unique().tolist())
    sel_sym = f2.selectbox("Symbol", sym_opts, key="sig_exp_sym")
    st_opts = ["ALL"] + sorted(all_sigs["status"].unique().tolist())
    sel_st = f3.selectbox("Trạng thái", st_opts, key="sig_exp_status")
    ambig_only = f4.checkbox("Chỉ tín hiệu Ambiguous", value=False, key="sig_exp_ambig")

    filtered = queries.list_run_signals(
        selected_run,
        store_dir=store_dir,
        signal_id_query=sig_query or None,
        symbol=None if sel_sym == "ALL" else sel_sym,
        status=None if sel_st == "ALL" else sel_st,
        ambiguous_only=ambig_only,
    )
    st.write(f"Hiển thị **{len(filtered):,}** / {len(all_sigs):,} tín hiệu")
    st.dataframe(filtered.head(500), use_container_width=True, hide_index=True)

    if filtered.empty:
        return

    sig_ids = filtered["signal_id"].tolist()
    selected_sig = st.selectbox("Chọn Signal ID để mở biểu đồ", sig_ids, key="sig_exp_selected_id")

    detail = queries.get_signal_detail_and_chart(
        selected_run,
        selected_sig,
        store_dir=store_dir,
        catalog_path=catalog_path,
    )

    sig = detail["signal"]
    dec = detail["decision"]
    lv = detail["levels"]
    ambig_flags = detail["ambiguity_flags"]

    st.subheader(f"Chi tiết Signal `{selected_sig}` ({sig['symbol']} | {sig['combo_name']} | {sig['side']})")
    if ambig_flags:
        st.warning(f"Cờ Ambiguity: {', '.join(ambig_flags)}")
    st.caption(
        f"Status: `{dec.get('status', 'UNKNOWN')}` ({dec.get('reason', '')}) | "
        f"Entry: `{lv['entry_price']:.4f}` | "
        f"TP: `{lv['tp_price']:.4f}` | " if lv["tp_price"] is not None else f"Entry: `{lv['entry_price']:.4f}` | "
    )

    m15_df = detail["m15_df"]
    m1_df = detail["m1_path_df"]
    trail_df = detail["trailing_series_df"]
    legs_df = detail["legs_df"]

    fig = go.Figure()
    if not m15_df.empty:
        t15 = pd.to_datetime(m15_df["time_ms"].astype("int64"), unit="ms", utc=True)
        fig.add_trace(
            go.Candlestick(
                x=t15,
                open=m15_df["open"],
                high=m15_df["high"],
                low=m15_df["low"],
                close=m15_df["close"],
                name="M15 Candles",
                opacity=0.6,
            )
        )
    if not m1_df.empty:
        t1 = pd.to_datetime(m1_df["time_ms"].astype("int64"), unit="ms", utc=True)
        fig.add_trace(
            go.Scatter(
                x=t1,
                y=m1_df["close"],
                mode="lines",
                name="M1 Close Path",
                line=dict(color="royalblue", width=1.5),
            )
        )
    if not trail_df.empty and trail_df["trailing_stop"].notna().any():
        tr_sub = trail_df.dropna(subset=["trailing_stop"])
        tt = pd.to_datetime(tr_sub["time_ms"].astype("int64"), unit="ms", utc=True)
        fig.add_trace(
            go.Scatter(
                x=tt,
                y=tr_sub["trailing_stop"],
                mode="lines",
                name="Trailing Stop",
                line=dict(color="orange", width=2, dash="dot"),
            )
        )

    fig.add_hline(y=lv["entry_price"], line_dash="solid", line_color="blue", annotation_text="Entry")
    if lv["tp_price"] is not None:
        fig.add_hline(y=lv["tp_price"], line_dash="dash", line_color="green", annotation_text="TP")
    if lv["sl_price"] is not None:
        fig.add_hline(y=lv["sl_price"], line_dash="dash", line_color="red", annotation_text="SL")
    if lv["trailing_act_price"] is not None:
        fig.add_hline(y=lv["trailing_act_price"], line_dash="dot", line_color="orange", annotation_text="Trail Act")

    if not legs_df.empty:
        ex_t = pd.to_datetime(legs_df["exit_time_ms"].astype("int64"), unit="ms", utc=True)
        ex_px = legs_df["exit_px_fill"] if "exit_px_fill" in legs_df.columns else legs_df["exit_price"]
        fig.add_trace(
            go.Scatter(
                x=ex_t,
                y=ex_px,
                mode="markers+text",
                text=legs_df["exit_reason"],
                textposition="top center",
                marker=dict(size=10, color="purple", symbol="diamond"),
                name="Leg Exits",
            )
        )

    fig.update_layout(height=480, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Legs của Signal")
    st.dataframe(legs_df, use_container_width=True, hide_index=True)
    st.subheader("Execution Events của Signal")
    st.dataframe(detail["events_df"], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render()
