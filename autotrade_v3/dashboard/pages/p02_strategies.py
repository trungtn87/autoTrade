"""Screen 2: Strategies — versions, 3 hashes, parent, combo table, clone, edit clone only, diff."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard import actions, queries


def render() -> None:
    st.header("Strategies")
    strategies = queries.list_strategies_info()
    if not strategies:
        st.info("Chưa có strategy nào được đăng ký.")
        return

    st.subheader("Danh sách Strategy & Version")
    st.dataframe(strategies, use_container_width=True, hide_index=True)

    refs = [s["ref"] for s in strategies]
    selected_ref = st.selectbox("Chọn Strategy Version", refs, key="strat_select_ref")
    detail = queries.get_strategy_detail(selected_ref)

    if detail["is_locked"]:
        st.warning(f"🔒 `{selected_ref}` là baseline khóa cứng (`20final_v1.lock`). Không cho phép sửa tham số.")
    else:
        st.success(f"Bản clone `{selected_ref}` (có thể sửa tham số ExitPolicy).")

    h1, h2, h3 = st.columns(3)
    h1.text_input("signal_spec_hash", value=detail["signal_spec_hash"], disabled=True, key="strat_h1")
    h2.text_input("exit_spec_hash", value=detail["exit_spec_hash"], disabled=True, key="strat_h2")
    h3.text_input("spec_hash", value=detail["spec_hash"], disabled=True, key="strat_h3")

    st.subheader("Bảng Combo (Điều kiện vào, Bộ lọc, ExitPolicy)")
    combos_df = detail["combos_df"]
    st.dataframe(combos_df, use_container_width=True, hide_index=True)

    # Clone form
    st.subheader("Clone Strategy")
    with st.form("clone_strategy_form"):
        to_ref = st.text_input("Target ref mới (vd: EXP_01@1)", value="", key="clone_to_ref")
        submitted_clone = st.form_submit_button("Clone từ version đang chọn")
        if submitted_clone:
            try:
                res = actions.run_strategy_clone(from_ref=selected_ref, to_ref=to_ref)
                if res.returncode == 0:
                    st.success(f"Đã clone thành công sang `{to_ref}`.")
                    st.code(res.stdout)
                else:
                    st.error(f"Clone thất bại: {res.stderr or res.stdout}")
            except Exception as exc:
                st.error(f"Lỗi clone: {exc}")

    # Edit form — ONLY rendered for non-locked clones (never for 20FINAL@1)
    if not detail["is_locked"] and not combos_df.empty:
        st.subheader("Sửa tham số trên bản Clone")
        combo_keys = combos_df["combo_key"].tolist()
        sel_combo = st.selectbox("Chọn Combo", combo_keys, key="edit_combo_key")
        crow = combos_df[combos_df["combo_key"] == sel_combo].iloc[0]
        with st.form("edit_clone_params_form"):
            new_tp = st.number_input("tp_pct", min_value=0.0001, max_value=1.0, value=float(crow["tp_pct"]), step=0.001, format="%.4f")
            new_sl = st.number_input("sl_pct", min_value=0.0001, max_value=1.0, value=float(crow["sl_pct"]), step=0.001, format="%.4f")
            new_act = st.number_input("trailing_act_pct", min_value=0.0, max_value=1.0, value=float(crow["trailing_act_pct"]), step=0.001, format="%.4f")
            new_cb = st.number_input("trailing_cb_pct", min_value=0.0, max_value=1.0, value=float(crow["trailing_cb_pct"]), step=0.001, format="%.4f")
            mb_default = int(crow["max_bars_held"]) if crow["max_bars_held"] is not None and str(crow["max_bars_held"]) != "nan" else 0
            new_mb = st.number_input("max_bars_held (0 = unlimited)", min_value=0, max_value=100000, value=mb_default, step=1)
            submitted_edit = st.form_submit_button("Lưu tham số bản Clone")
            if submitted_edit:
                try:
                    res = actions.run_edit_clone_params(
                        strategy_ref=selected_ref,
                        combo_key=sel_combo,
                        updates={
                            "tp_pct": new_tp,
                            "sl_pct": new_sl,
                            "trailing_act_pct": new_act,
                            "trailing_cb_pct": new_cb,
                            "max_bars_held": int(new_mb) if new_mb > 0 else None,
                        },
                    )
                    if res.returncode == 0:
                        st.success("Đã cập nhật tham số bản clone.")
                        st.code(res.stdout)
                    else:
                        st.error(f"Lỗi cập nhật: {res.stderr or res.stdout}")
                except Exception as exc:
                    st.error(f"Lỗi: {exc}")

    # Diff vs parent
    diff_obj = queries.get_strategy_param_diff(selected_ref)
    if diff_obj is not None:
        st.subheader("Diff tham số so với Parent")
        st.json(diff_obj)


if __name__ == "__main__":
    render()
