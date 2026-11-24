"""AutoTrade V3 Local Research Lab Dashboard."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard import all_signals
st.set_page_config(
    page_title="V3_LAB_44 · PLUS44",
    layout="wide",
    initial_sidebar_state="collapsed",
)

def main() -> None:
    # PLUS44 is the dashboard itself. Keeping it as the default page maps it to
    # `/` and removes the former standalone `/p11_v3_plus31` navigation branch.
    main_page = st.Page(
        "pages/p11_v3_plus31.py",
        title="V3_LAB_44 · PLUS44",
        icon="⚡",
        default=True,
    )
    st.session_state["ninlab_main_page"] = main_page
    pages = [
        main_page,
        st.Page("pages/p10_mt5_report.py", title=all_signals.vi("dashboard_report_nav"), icon="📊"),
    ]
    nav = st.navigation(pages, position="sidebar")
    nav.run()


if __name__ == "__main__":
    main()
