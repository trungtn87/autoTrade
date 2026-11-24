"""AutoTrade V3 Local Research Lab Dashboard (One-Page NinLab Terminal + Full MT5 Report)."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard import all_signals
from autotrade_v3.dashboard.one_page import render_one_page_terminal

st.set_page_config(
    page_title=all_signals.vi("ninlab_title"),
    layout="wide",
    initial_sidebar_state="collapsed",
)


def _page_one() -> None:
    render_one_page_terminal()


def main() -> None:
    main_page = st.Page(_page_one, title=all_signals.vi("ninlab_title"), icon="⚡", default=True)
    st.session_state["ninlab_main_page"] = main_page
    pages = [
        main_page,
        st.Page("pages/p10_mt5_report.py", title=all_signals.vi("dashboard_report_nav"), icon="📊"),
    ]
    pages.append(st.Page("pages/p11_v3_plus31.py", title="V3_LAB_11 + TV31", icon="🧪"))
    nav = st.navigation(pages, position="sidebar")
    nav.run()


if __name__ == "__main__":
    main()
