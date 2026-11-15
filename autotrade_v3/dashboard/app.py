"""AutoTrade V3 Local Research Lab Dashboard (offline, read-only RunStore)."""
from __future__ import annotations

import streamlit as st

from autotrade_v3.dashboard.ui_shared import inject_research_theme

st.set_page_config(
    page_title="AutoTrade V3 Research Lab",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    inject_research_theme()
    st.sidebar.title("◈ AutoTrade V3")
    st.sidebar.caption("Research Lab · Offline-first · Read-only RunStore")

    pages = [
        st.Page("pages/p02_strategies.py", title="1. Strategy Config", icon="🧬"),
        st.Page("pages/p06_compare.py", title="2. Walk-Forward Lab", icon="↗️"),
        st.Page("pages/p04_run_detail.py", title="3. Run Detail", icon="📊", default=True),
        st.Page("pages/p05_signal_explorer.py", title="4. Signal Explorer", icon="🔎"),
        st.Page("pages/p08_live_guard.py", title="5. Live / Paper Guard", icon="🛡️"),
        st.Page("pages/p01_datasets.py", title="Admin · Datasets", icon="🗄️"),
        st.Page("pages/p03_new_run.py", title="Admin · New Run", icon="▶️"),
        st.Page("pages/p07_research_log.py", title="Admin · Research Log", icon="📝"),
    ]
    nav = st.navigation(pages)
    nav.run()


if __name__ == "__main__":
    main()
