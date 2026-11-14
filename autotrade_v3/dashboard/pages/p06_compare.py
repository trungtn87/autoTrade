"""Screen 6: Compare — 2..6 runs side-by-side, overlaid equity, combo delta, per-signal diff."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.ui_shared import get_store_dir


def render() -> None:
    st.header("Compare Runs")
    store_dir = get_store_dir()
    runs = queries.list_store_runs(store_dir=store_dir)
    if len(runs) < 2:
        st.info("Cần ít nhất 2 run trong Run Store để so sánh.")
        return

    run_ids = [str(r["run_id"]) for r in runs]
    selected = st.multiselect(
        "Chọn 2 đến 6 Run ID để so sánh",
        run_ids,
        default=run_ids[:2],
        max_selections=6,
        key="compare_select_runs",
    )
    if len(selected) < 2:
        st.warning("Hãy chọn ít nhất 2 run.")
        return

    try:
        comp = queries.compare_runs_summary(selected, store_dir=store_dir)
        eq_all = queries.get_compare_equity_curves(selected, store_dir=store_dir)
        combo_deltas = queries.get_compare_combo_deltas(selected, store_dir=store_dir)
    except queries.MissingArtifactError as exc:
        st.error(f"Lỗi thiếu artifact trong RunStore: {exc}")
        return
    except Exception as exc:
        st.error(f"Không thể so sánh các run đã chọn: {exc}")
        return

    for w in comp["warnings"]:
        st.warning(f"Cảnh báo so sánh: {w}")

    st.subheader("Bảng Số liệu Cạnh nhau")
    st.dataframe(comp["table_df"], use_container_width=True, hide_index=True)

    st.subheader("Đồ thị Equity Chồng nhau")
    if not eq_all.empty:
        fig = go.Figure()
        for rid, grp in eq_all.groupby("run_id", sort=False):
            fig.add_trace(go.Scatter(x=grp["timestamp_dt"], y=grp["equity"], mode="lines", name=str(rid)))
        fig.update_layout(height=360, margin=dict(l=10, r=10, t=25, b=10), yaxis_title="Equity ($)")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Chênh lệch theo (Symbol, Combo)")
    st.dataframe(combo_deltas, use_container_width=True, hide_index=True)

    st.subheader("Per-Signal Diff (Run A vs Run B)")
    c1, c2 = st.columns(2)
    run_a = c1.selectbox("Run A (Baseline)", selected, index=0, key="compare_diff_a")
    run_b = c2.selectbox("Run B (Target)", selected, index=1, key="compare_diff_b")
    if run_a != run_b:
        diff_res = queries.get_compare_signal_diff(run_a, run_b, store_dir=store_dir)
        s = diff_res["summary"]
        st.write(
            f"Total Signals: **{s['total_signals']}** | Net(A)=`${s['net_a']:.4f}` -> Net(B)=`${s['net_b']:.4f}` | "
            f"Delta=`${s['expected_delta_net']:+.4f}` | InvariantDiff=`{s['invariant_diff']:.2e}`"
        )
        cat_rows = [
            {"category": cat, "count": st_c["count"], "delta_net": st_c["delta_net"]}
            for cat, st_c in s["by_category"].items()
        ]
        st.dataframe(cat_rows, use_container_width=True, hide_index=True)
        diff_df = diff_res["diff_df"]
        non_id = diff_df[diff_df["category"] != "IDENTICAL"] if not diff_df.empty else diff_df
        st.caption(f"Các tín hiệu khác nhau ({len(non_id)} dòng):")
        st.dataframe(non_id.head(300), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render()
