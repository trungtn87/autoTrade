"""Screen 1: Datasets — catalog overview, Gate A1/A2 status, provider breakdown, verify action."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard import actions, queries
from autotrade_v3.dashboard.ui_shared import get_catalog_path, get_qa_path, get_store_dir


def render() -> None:
    st.header("Datasets")
    catalog_path = get_catalog_path()
    qa_path = get_qa_path()
    store_dir = get_store_dir()

    overview = queries.get_datasets_overview(
        catalog_path=catalog_path,
        qa_path=qa_path,
        store_dir=store_dir,
    )
    if not overview["datasets"]:
        st.info("Chưa tìm thấy dataset manifest hoặc catalog DuckDB.")
        return

    st.subheader("Danh sách Dataset Offline")
    st.dataframe(overview["datasets"], use_container_width=True, hide_index=True)

    ds = overview["datasets"][0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng nến M1", f"{ds['total_candles']:,}")
    c2.metric("Gate A1 (lần kiểm gần nhất)", str(ds["gate_a1_status"]))
    c3.metric("Gate A2 (lần kiểm gần nhất)", str(ds["gate_a2_status"]))
    c4.metric("Partitions", f"{ds['partition_count']}")
    st.caption(f"Dataset SHA256: `{ds['dataset_hash']}` | Nguồn Gate A: `{ds['gate_a_source']}`")

    st.subheader("Tỷ lệ nến theo Provider")
    prov_df = queries.get_dataset_provider_breakdown(catalog_path=catalog_path)
    if prov_df.empty:
        st.info("Không có dữ liệu provider trong catalog.")
    else:
        st.dataframe(prov_df, use_container_width=True, hide_index=True)

    st.subheader("Kiểm tra Toàn vẹn (Gate A)")
    if st.button("Chạy kiểm tra toàn vẹn Dataset", key="btn_dataset_verify"):
        with st.spinner("Đang chạy kiểm tra toàn vẹn Gate A..."):
            res = actions.run_dataset_verify(catalog_path=catalog_path)
        if res.returncode == 0:
            st.success(f"Hoàn tất kiểm tra ({res.duration_s:.2f}s).")
        else:
            st.error(f"Kiểm tra thất bại (exit={res.returncode}).")
        st.code(res.stdout or res.stderr)


if __name__ == "__main__":
    render()
