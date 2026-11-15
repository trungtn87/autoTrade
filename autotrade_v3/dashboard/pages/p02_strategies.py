"""Strategy Config & Clone Lab backed by the V3 strategy registry."""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from autotrade_v3.dashboard import actions, queries
from autotrade_v3.dashboard.ui_shared import page_heading


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    rows: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            rows.update(_flatten(child, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        rows[prefix] = value
    else:
        rows[prefix] = value
    return rows


def _diff_table(source: dict[str, Any], target: dict[str, Any]) -> pd.DataFrame:
    left, right = _flatten(source), _flatten(target)
    rows = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            rows.append({"Parameter": key, "Baseline": left.get(key), "Variant": right.get(key)})
    return pd.DataFrame(rows)


def render() -> None:
    page_heading("01", "Strategy Config & Clone Lab", "Quản lý version, xem đúng phần thay đổi và chỉnh từng ExitPolicy trên bản clone.")
    strategies = queries.list_strategies_info()
    if not strategies:
        st.info("Chưa có strategy nào trong StrategyCoreRegistry.")
        return

    refs = [str(item["ref"]) for item in strategies]
    source_col, arrow_col, target_col, action_col = st.columns([3, 0.25, 3, 1.15])
    source_ref = source_col.selectbox("Phiên bản gốc", refs, index=0, key="config_source")
    arrow_col.markdown("<div style='padding-top:37px;text-align:center'>→</div>", unsafe_allow_html=True)
    target_options = refs if len(refs) > 1 else refs
    target_ref = target_col.selectbox("Biến thể so sánh", target_options, index=min(1, len(target_options) - 1), key="config_target")
    action_col.write("")
    action_col.write("")
    clone_open = action_col.button("Clone & Mutate", use_container_width=True, type="primary")

    source = queries.get_strategy_detail(source_ref)
    target = queries.get_strategy_detail(target_ref)
    if source["is_locked"]:
        st.warning(f"🔒 `{source_ref}` là golden strategy khóa cứng; chỉ bản clone mới được phép mutate.")

    st.subheader("Config Diff Viewer")
    diff = _diff_table(source["raw_dict"], target["raw_dict"])
    if diff.empty:
        st.info("Hai selector đang trỏ tới cùng version. Clone baseline để tạo một variant có thể chỉnh sửa.")
    else:
        st.dataframe(diff, use_container_width=True, hide_index=True, height=240)

    combos = target["combos_df"].copy()
    first = combos.iloc[0] if not combos.empty else {}
    entry, gate, sizing = st.columns(3)
    with entry:
        st.markdown("### Entry Logic")
        st.markdown(f"**Timeframes:** `{', '.join(target['timeframes'])}`")
        st.markdown(f"**Warm-up:** `{target['warmup_bars']}` bars")
        st.markdown(f"**Variants:** `{combos['entry_variant'].nunique() if not combos.empty else 0}`")
        st.caption("Signal hash")
        st.code(target["signal_spec_hash"][:24], language=None)
    with gate:
        st.markdown("### Filter Gate")
        filters = sorted({str(v) for v in combos.get("filters", pd.Series(dtype=str)).tolist()})
        st.markdown(f"**Profiles:** `{len(filters)}`")
        st.markdown("\n".join(f"- `{value}`" for value in filters[:5]) or "No filters")
        st.caption("Các gate được đọc trực tiếp từ StrategySpec.")
    with sizing:
        st.markdown("### Exit & Sizing")
        if not combos.empty:
            st.markdown(f"**TP / SL:** `{float(first['tp_pct']):.2%}` / `{float(first['sl_pct']):.2%}`")
            st.markdown(f"**Trailing:** `{float(first['trailing_act_pct']):.2%}` act · `{float(first['trailing_cb_pct']):.2%}` callback")
            st.markdown(f"**Leg weights:** `{first['leg_weights']}`")
        st.caption("Exit hash")
        st.code(target["exit_spec_hash"][:24], language=None)

    if clone_open:
        st.session_state["show_clone_form"] = True
    if st.session_state.get("show_clone_form", False):
        with st.form("clone_strategy_form"):
            st.markdown("### Tạo version mới")
            to_ref = st.text_input("Target ref", placeholder="OPT_1@1")
            submitted = st.form_submit_button("Clone version đang chọn", type="primary")
            if submitted:
                try:
                    result = actions.run_strategy_clone(from_ref=source_ref, to_ref=to_ref)
                    if result.returncode == 0:
                        st.success(f"Đã clone `{source_ref}` thành `{to_ref}`. Tải lại trang để chọn version mới.")
                    else:
                        st.error(result.stderr or result.stdout)
                except Exception as exc:
                    st.error(f"Không thể clone: {exc}")

    if not target["is_locked"] and not combos.empty:
        with st.expander("Mutate một combo trên bản clone", expanded=False):
            combo_key = st.selectbox("Combo", combos["combo_key"].tolist(), key="mutate_combo")
            row = combos[combos["combo_key"] == combo_key].iloc[0]
            with st.form("edit_clone_params_form"):
                c1, c2, c3, c4 = st.columns(4)
                tp = c1.number_input("TP %", 0.0001, 1.0, float(row["tp_pct"]), 0.001, format="%.4f")
                sl = c2.number_input("SL %", 0.0001, 1.0, float(row["sl_pct"]), 0.001, format="%.4f")
                activation = c3.number_input("Trail activation %", 0.0, 1.0, float(row["trailing_act_pct"]), 0.001, format="%.4f")
                callback = c4.number_input("Trail callback %", 0.0, 1.0, float(row["trailing_cb_pct"]), 0.001, format="%.4f")
                if st.form_submit_button("Lưu mutation"):
                    result = actions.run_edit_clone_params(
                        strategy_ref=target_ref,
                        combo_key=combo_key,
                        updates={"tp_pct": tp, "sl_pct": sl, "trailing_act_pct": activation, "trailing_cb_pct": callback},
                    )
                    if result.returncode == 0:
                        st.success("Đã cập nhật bản clone.")
                    else:
                        st.error(result.stderr or result.stdout)

    st.subheader("Combo Registry")
    registry = combos.copy()
    if not registry.empty:
        registry.insert(0, "enabled", True)
        registry["legs"] = registry["leg_weights"].map(lambda value: "2-Leg" if "," in str(value) else "1-Leg")
        registry["allocation"] = registry["leg_weights"]
        display = registry[["enabled", "combo_key", "symbol", "timeframe", "legs", "allocation", "entry_variant", "filters", "tp_pct", "sl_pct"]]
        st.data_editor(
            display,
            disabled=list(display.columns),
            column_config={"enabled": st.column_config.CheckboxColumn("Active")},
            use_container_width=True,
            hide_index=True,
            height=540,
            key="strategy-combo-registry",
        )
        st.caption(f"{len(display)} combo · Trạng thái Active phản ánh StrategySpec hiện tại; golden baseline không thể sửa trực tiếp.")


if __name__ == "__main__":
    render()
