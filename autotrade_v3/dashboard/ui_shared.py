"""Shared UI formatting helpers for AutoTrade V3 Research Lab Dashboard (no metric math)."""
from __future__ import annotations

import os
from pathlib import Path
import streamlit as st

from autotrade_v3.dashboard.queries import DEFAULT_CATALOG_PATH, DEFAULT_NOTES_DB, DEFAULT_QA_PATH, DEFAULT_STORE_DIR


def get_store_dir() -> Path:
    return Path(os.environ.get("AUTOTRADE_LAB_STORE_DIR", str(DEFAULT_STORE_DIR)))


def get_catalog_path() -> Path:
    return Path(os.environ.get("AUTOTRADE_LAB_CATALOG_PATH", str(DEFAULT_CATALOG_PATH)))


def get_qa_path() -> Path:
    return Path(os.environ.get("AUTOTRADE_LAB_QA_PATH", str(DEFAULT_QA_PATH)))


def get_notes_db_path() -> Path:
    return Path(os.environ.get("AUTOTRADE_LAB_NOTES_DB", str(DEFAULT_NOTES_DB)))


def render_status_banner(summary: dict) -> None:
    """Render mandatory metadata & validation status banner (never hardcodes VERIFIED)."""
    status = str(summary.get("validation_status", "UNVERIFIED"))
    is_syn = bool(summary.get("is_synthetic", False))
    mode = str(summary.get("execution_mode", ""))
    cost = str(summary.get("cost_profile", ""))
    amb = str(summary.get("ambiguity_policy", ""))

    badges = [f"Mode: **{mode}**", f"Cost: **{cost}**", f"Ambiguity: **{amb}**", f"Validation: **{status}**"]
    if is_syn:
        badges.append("**[SYNTHETIC]**")

    line = " | ".join(badges)
    if status == "VERIFIED" and not is_syn:
        st.success(line)
    elif status in ("PARTIALLY_VERIFIED",):
        st.warning(f"{line} — Kết quả chưa đạt toàn bộ cổng kiểm chứng; luôn đọc kèm nhãn trạng thái.")
    else:
        st.error(f"{line} — Chú ý: dữ liệu/run không ở trạng thái VERIFIED.")
