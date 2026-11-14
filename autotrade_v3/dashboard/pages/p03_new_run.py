"""Screen 3: New Run — launch backtest/uncertainty with CONFIRMED cost profile guard."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard import actions, queries
from autotrade_v3.dashboard.ui_shared import get_catalog_path


def render() -> None:
    st.header("New Run")
    catalog_path = get_catalog_path()
    opts = queries.get_new_run_options(catalog_path=catalog_path)

    if not opts["catalog_exists"]:
        st.warning("Chưa tìm thấy catalog/research.duckdb cho dữ liệu M1 thật.")

    confirmed_profiles = [p["name"] for p in opts["cost_profiles"] if p["selectable_for_real_data"]]
    blocked_profiles = [f"{p['name']} ({p['status']})" for p in opts["cost_profiles"] if not p["selectable_for_real_data"]]

    if blocked_profiles:
        st.caption(f"Hồ sơ chi phí bị khóa trên dữ liệu thật: {', '.join(blocked_profiles)}")

    with st.form("new_run_form"):
        c1, c2 = st.columns(2)
        strategy = c1.selectbox("Strategy Version", opts["strategies"], key="nr_strategy")
        mode = c2.selectbox("Execution Mode", opts["modes"], key="nr_mode")

        c3, c4 = st.columns(2)
        symbols_sel = c3.multiselect("Symbols", opts["symbols"], default=opts["symbols"], key="nr_symbols")
        cost_profile = c4.selectbox(
            "Cost Profile (chỉ CONFIRMED cho dữ liệu thật)",
            confirmed_profiles if confirmed_profiles else ["IDEAL"],
            key="nr_cost",
        )

        c5, c6, c7 = st.columns(3)
        start_dt = c5.text_input("Start Date (UTC)", value="2023-11-01", key="nr_start")
        end_dt = c6.text_input("End Date (UTC)", value="2026-11-01", key="nr_end")
        ambiguity = c7.selectbox("Ambiguity Policy", opts["ambiguity_policies"], key="nr_ambig")

        ann_opts = ["(Không dùng bộ lọc ngoài)"] + [a["source"] for a in opts["annotation_sources"]]
        ann_sel = st.selectbox("External Annotation Filter", ann_opts, key="nr_ann")
        run_unc = st.checkbox("Chạy thêm Uncertainty Interval (CONSERVATIVE vs OPTIMISTIC)", value=False, key="nr_unc")

        submitted = st.form_submit_button("Chạy Backtest")
        if submitted:
            if not symbols_sel:
                st.error("Phải chọn ít nhất 1 symbol.")
            else:
                ann_src = None if (ann_sel is None or str(ann_sel).startswith("(")) else str(ann_sel)
                with st.spinner("Đang thực thi backtest trong subprocess..."):
                    try:
                        res = actions.run_backtest(
                            strategy=strategy,
                            mode=mode,
                            symbols=",".join(symbols_sel),
                            start=start_dt,
                            end=end_dt,
                            cost_profile=cost_profile,
                            ambiguity_policy=ambiguity,
                            catalog_path=catalog_path,
                            annotation_source=ann_src,
                            smoke_test=False,
                        )
                        if res.returncode == 0:
                            st.success(f"Backtest hoàn tất trong {res.duration_s:.2f}s.")
                            st.code(res.stdout)
                        else:
                            st.error(f"Backtest lỗi (exit={res.returncode}).")
                            st.code(res.stderr or res.stdout)

                        if run_unc and res.returncode == 0:
                            u_res = actions.run_uncertainty(
                                strategy=strategy,
                                mode=mode,
                                symbols=",".join(symbols_sel),
                                start=start_dt,
                                end=end_dt,
                                cost_profile=cost_profile,
                                catalog_path=catalog_path,
                            )
                            if u_res.returncode == 0:
                                st.info("Uncertainty Interval hoàn tất:")
                                st.code(u_res.stdout)
                            else:
                                st.warning(f"Uncertainty lỗi: {u_res.stderr}")
                    except Exception as exc:
                        st.error(f"Lỗi thực thi: {exc}")

    st.subheader("Hàng đợi & Nhật ký Lệnh đã chạy")
    logs = queries.get_action_log_entries()
    if logs:
        st.dataframe(logs, use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có lệnh nào được chạy từ Dashboard.")


if __name__ == "__main__":
    render()
