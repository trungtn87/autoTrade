"""Screen 4: Run Detail — KPI cards, equity/drawdown, BTC/ETH tabs, combo, monthly, subwindows, costs, drill-down."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import queries
from autotrade_v3.dashboard.ui_shared import get_store_dir, render_status_banner


def render() -> None:
    st.header("Run Detail")
    store_dir = get_store_dir()
    runs = queries.list_store_runs(store_dir=store_dir)
    if not runs:
        st.info("Run Store đang trống. Hãy chạy một backtest ở màn hình New Run hoặc nạp dữ liệu vào RunStore.")
        return

    run_ids = [str(r["run_id"]) for r in runs]
    selected_run = st.selectbox("Chọn Run ID", run_ids, key="run_detail_select")

    try:
        summary = queries.get_run_summary(selected_run, store_dir=store_dir)
        eq_df = queries.get_run_equity_curve(selected_run, store_dir=store_dir)
        sym_df = queries.get_run_symbol_breakdown(selected_run, store_dir=store_dir)
        combo_df = queries.get_run_combo_breakdown(selected_run, store_dir=store_dir)
        month_df = queries.get_run_monthly_breakdown(selected_run, store_dir=store_dir)
        subw_df = queries.get_run_subwindows_breakdown(selected_run, store_dir=store_dir)
        exit_df = queries.get_run_exit_reasons(selected_run, store_dir=store_dir)
        cost_info = queries.get_run_cost_breakdown(selected_run, store_dir=store_dir)
        unc_info = queries.get_run_uncertainty(selected_run, store_dir=store_dir)
    except queries.MissingArtifactError as exc:
        st.error(f"Lỗi thiếu artifact trong RunStore: {exc}")
        return
    except Exception as exc:
        st.error(f"Không thể đọc dữ liệu run '{selected_run}': {exc}")
        return

    render_status_banner(summary)
    st.caption(
        f"Strategy: `{summary['strategy_id']}@{summary['strategy_version']}` | "
        f"Window: `{summary['time_window'].get('start', '')}` -> `{summary['time_window'].get('end', '')}` | "
        f"Dataset: `{summary['dataset_hash'][:12]}`"
    )

    # KPI Cards
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net PnL (Simulation)", f"${summary['net_pnl']:,.4f}", help=f"Gross: ${summary['gross_pnl']:,.4f} | Fees: ${summary['total_fees']:,.4f}")
    c2.metric(summary["roi_label"], f"{summary['roi_pct']:+.2f}%", help=summary["roi_note"])
    c3.metric("PF (Trade / Leg)", f"{summary['net_pf_trade']} / {summary['net_pf_leg']}", help=f"Gross PF: {summary['gross_pf_trade']} / {summary['gross_pf_leg']}")
    c4.metric(
        "Win Rate (Trade / Leg)",
        f"{summary['trade_win_rate_pct']:.2f}% / {summary['leg_win_rate_pct']:.2f}%",
        help=f"Trades: {summary['trade_wins']}W/{summary['trade_losses']}L | Legs: {summary['leg_wins']}W/{summary['leg_losses']}L",
    )

    d1, d2, d3, d4 = st.columns(4)
    if summary["m2m_status"] == "AVAILABLE" and summary["m2m_max_drawdown_dollars"] is not None:
        d1.metric("Continuous M2M Max DD", f"${summary['m2m_max_drawdown_dollars']:,.2f} ({summary['m2m_max_drawdown_pct']:.2f}%)")
    else:
        d1.metric("Closed-Trade Max DD", f"${summary['closed_trade_max_drawdown_dollars']:,.2f} ({summary['closed_trade_max_drawdown_pct']:.2f}%)")
    d2.metric("Total Signals", f"{summary['total_signals']:,}")
    d3.metric("Accepted Entries / Legs", f"{summary['accepted_entries']:,} / {summary['total_legs']:,}")
    dec_str = ", ".join(f"{k}: {v}" for k, v in sorted(summary["decision_counts"].items())) or "N/A"
    d4.metric("Decision Breakdown", dec_str)

    # Equity & Drawdown Charts
    st.subheader("Đồ thị Equity & Drawdown")
    if not eq_df.empty:
        eq_kind = str(eq_df["equity_kind"].iloc[0]) if "equity_kind" in eq_df.columns else ""
        st.caption(f"Loại đường equity: `{eq_kind}`")
        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(x=eq_df["timestamp_dt"], y=eq_df["equity"], mode="lines", name="Equity"))
        fig_eq.update_layout(height=320, margin=dict(l=10, r=10, t=25, b=10), yaxis_title="Equity ($)")
        st.plotly_chart(fig_eq, use_container_width=True)

        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(x=eq_df["timestamp_dt"], y=-eq_df["drawdown_dollars"], mode="lines", name="Drawdown ($)", fill="tozeroy"))
        fig_dd.update_layout(height=220, margin=dict(l=10, r=10, t=25, b=10), yaxis_title="Drawdown ($)")
        st.plotly_chart(fig_dd, use_container_width=True)
    else:
        st.info("Không có dữ liệu equity curve.")

    # Symbol Tabs (BTC / ETH)
    st.subheader("Phân tích theo Symbol (BTC / ETH)")
    if not sym_df.empty:
        sym_list = sym_df["symbol"].tolist()
        tabs = st.tabs(sym_list)
        for tab, sym in zip(tabs, sym_list):
            with tab:
                srow = sym_df[sym_df["symbol"] == sym].iloc[0]
                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Signals / Entries / Legs", f"{int(srow['signals']):,} / {int(srow['entries']):,} / {int(srow['legs']):,}")
                sc2.metric("Net PnL", f"${srow['net_pnl']:,.4f}")
                sc3.metric("PF (Trade / Leg)", f"{srow['net_pf_trade']} / {srow['net_pf_leg']}")
                sc4.metric("Win Rate (Trade / Leg)", f"{srow['trade_win_rate_pct']:.2f}% / {srow['leg_win_rate_pct']:.2f}%")
                sym_combos = combo_df[combo_df["symbol"] == sym] if not combo_df.empty else combo_df
                st.dataframe(sym_combos, use_container_width=True, hide_index=True)

    # Full Combo Table keyed by (symbol, combo_name)
    st.subheader("Bảng Combo theo (Symbol, Combo)")
    st.dataframe(combo_df, use_container_width=True, hide_index=True)

    # Monthly & Subwindows
    col_m, col_s = st.columns(2)
    with col_m:
        st.subheader("Bảng theo Tháng")
        st.dataframe(month_df, use_container_width=True, hide_index=True)
    with col_s:
        st.subheader("Bảng Cửa sổ con")
        st.dataframe(subw_df, use_container_width=True, hide_index=True)

    # Exit Reasons & Execution Costs
    col_e, col_c = st.columns(2)
    with col_e:
        st.subheader("Phân bố Lý do Thoát")
        st.dataframe(exit_df, use_container_width=True, hide_index=True)
    with col_c:
        st.subheader("Thành phần Chi phí Thực thi")
        st.json(cost_info)

    # Uncertainty Interval
    if unc_info is not None:
        st.subheader("Khoảng Bất định Ambiguity (CONSERVATIVE vs OPTIMISTIC)")
        st.json(unc_info)

    # Interactive Drill-down to Legs
    st.subheader("Truy ngược về từng Leg (Drill-down)")
    dd_type = st.selectbox(
        "Chọn lát cắt truy ngược",
        ["Tất cả legs", "Theo Symbol", "Theo (Symbol, Combo)", "Theo Tháng", "Theo Lý do Thoát", "Theo Trạng thái Quyết định"],
        key="run_detail_dd_type",
    )
    if dd_type == "Theo Trạng thái Quyết định":
        statuses = sorted(summary["decision_counts"].keys()) or ["ACCEPTED"]
        sel_st = st.selectbox("Trạng thái quyết định", statuses, key="run_detail_dd_status")
        dec_dd = queries.get_run_decisions_drilldown(selected_run, store_dir=store_dir, status=sel_st)
        st.write(f"Số tín hiệu `{sel_st}`: **{len(dec_dd)}**")
        st.dataframe(dec_dd, use_container_width=True, hide_index=True)
    else:
        kwargs: dict[str, str | None] = {}
        if dd_type == "Theo Symbol" and not sym_df.empty:
            kwargs["symbol"] = st.selectbox("Symbol", sym_df["symbol"].tolist(), key="dd_sym")
        elif dd_type == "Theo (Symbol, Combo)" and not combo_df.empty:
            kwargs["combo_key"] = st.selectbox("Combo", combo_df["combo_key"].tolist(), key="dd_combo")
        elif dd_type == "Theo Tháng" and not month_df.empty:
            kwargs["year_month"] = st.selectbox("Tháng", month_df["year_month"].tolist(), key="dd_month")
        elif dd_type == "Theo Lý do Thoát" and not exit_df.empty:
            kwargs["exit_reason"] = st.selectbox("Lý do thoát", exit_df["exit_reason"].tolist(), key="dd_exit")

        dd_res = queries.get_run_legs_drilldown(selected_run, store_dir=store_dir, **kwargs)
        st.write(
            f"Legs: **{dd_res['leg_count']:,}** | Entries: **{dd_res['entry_count']:,}** | "
            f"Tổng Net PnL: **${dd_res['sum_net_pnl']:,.6f}** | Tổng Gross: **${dd_res['sum_gross_pnl']:,.6f}** | Tổng Fees: **${dd_res['sum_fees']:,.6f}**"
        )
        st.dataframe(dd_res["legs_df"], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render()
