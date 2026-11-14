"""Screen 7: Research Log — tags, notes, pinned baseline (in notes.duckdb), export report & strategy_core."""
from __future__ import annotations

from pathlib import Path
import streamlit as st

from autotrade_v3.dashboard import actions, notes, queries
from autotrade_v3.dashboard.ui_shared import get_notes_db_path, get_store_dir


def render() -> None:
    st.header("Research Log")
    store_dir = get_store_dir()
    notes_db = get_notes_db_path()

    log_rows = queries.list_research_log(store_dir=store_dir, notes_db_path=notes_db)
    if not log_rows:
        st.info("Run Store đang trống. Chưa có nhật ký nghiên cứu.")
    else:
        st.subheader("Danh sách Run & Trạng thái Kiểm chứng")
        display_cols = [
            "run_id",
            "strategy_id",
            "strategy_version",
            "execution_mode",
            "cost_profile",
            "ambiguity_policy",
            "validation_status",
            "is_synthetic",
            "is_pinned_baseline",
            "tags",
            "note",
        ]
        table_data = [{k: r.get(k) for k in display_cols} for r in log_rows]
        st.dataframe(table_data, use_container_width=True, hide_index=True)

        run_ids = [str(r["run_id"]) for r in log_rows]
        sel_run = st.selectbox("Chọn Run ID để ghi chú / ghim / xuất báo cáo", run_ids, key="rlog_select_run")
        cur_row = next(r for r in log_rows if str(r["run_id"]) == sel_run)

        with st.form("research_note_form"):
            tags_val = st.text_input("Nhãn (tags, cách nhau bằng dấu phẩy)", value=str(cur_row.get("tags", "")), key="rlog_tags")
            note_val = st.text_area("Ghi chú nghiên cứu", value=str(cur_row.get("note", "")), key="rlog_note")
            c1, c2 = st.columns(2)
            save_btn = c1.form_submit_button("Lưu nhãn & ghi chú (notes.duckdb)")
            pin_btn = c2.form_submit_button("Ghim làm Baseline")
            if save_btn:
                notes.set_run_note(sel_run, tags=tags_val, note=note_val, notes_db_path=notes_db)
                st.success(f"Đã lưu ghi chú cho `{sel_run}` vào `notes.duckdb`.")
            if pin_btn:
                notes.set_pinned_baseline(sel_run, pinned=True, notes_db_path=notes_db)
                st.success(f"Đã ghim `{sel_run}` làm Baseline.")

        try:
            report_md = queries.get_run_report_text(sel_run, store_dir=store_dir)
            st.download_button(
                label="Xuất Báo cáo Markdown",
                data=report_md,
                file_name=f"{sel_run}_report.md",
                mime="text/markdown",
                key="rlog_dl_report",
            )
        except Exception as exc:
            st.error(f"Không thể tạo báo cáo: {exc}")

    st.subheader("Xuất gói strategy_core")
    strategies = queries.list_strategies_info()
    if strategies:
        refs = [s["ref"] for s in strategies]
        sel_strat = st.selectbox("Chọn Strategy Version để xuất", refs, key="rlog_export_strat")
        default_out = f"runs/lab/exports/{sel_strat.replace('@', '_v')}_bundle.zip"
        out_path = st.text_input("Đường dẫn file xuất (.zip)", value=default_out, key="rlog_export_path")
        if st.button("Xuất gói strategy_core", key="rlog_btn_export"):
            try:
                res = actions.run_export_strategy_core(strategy_ref=sel_strat, out_path=Path(out_path))
                if res.returncode == 0:
                    st.success(f"Đã xuất gói strategy_core: `{out_path}`")
                    st.code(res.stdout)
                else:
                    st.error(f"Xuất thất bại: {res.stderr or res.stdout}")
            except Exception as exc:
                st.error(f"Lỗi xuất gói: {exc}")


if __name__ == "__main__":
    render()
