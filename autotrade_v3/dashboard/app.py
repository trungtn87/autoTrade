"""AutoTrade V3 Local Research Lab Dashboard (offline, read-only RunStore)."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard.all_signals import vi
from autotrade_v3.dashboard.ui_shared import inject_research_theme

st.set_page_config(
    page_title=vi("dashboard_page_title"),
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    inject_research_theme()
    st.sidebar.title(vi("dashboard_sidebar_title"))
    st.sidebar.caption(vi("dashboard_sidebar_caption"))

    pages = [
        st.Page("pages/p09_all_signals_runs.py", title=vi("dashboard_runs_nav"), icon="📋", default=True),
        st.Page("pages/p11_run_catalog.py", title=vi("catalog_nav"), icon="🗂️"),
        st.Page("pages/p10_mt5_report.py", title=vi("dashboard_report_nav"), icon="📊"),
    ]
    nav = st.navigation(pages)
    nav.run()


if __name__ == "__main__":
    main()
