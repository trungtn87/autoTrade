"""AutoTrade V3 Local Research Lab Dashboard (offline, read-only RunStore)."""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="AutoTrade V3 Research Lab",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    st.sidebar.title("AutoTrade V3 Research Lab")
    st.sidebar.caption("Local Offline Dashboard (127.0.0.1)")

    pages = [
        st.Page("pages/p01_datasets.py", title="1. Datasets"),
        st.Page("pages/p02_strategies.py", title="2. Strategies"),
        st.Page("pages/p03_new_run.py", title="3. New Run"),
        st.Page("pages/p04_run_detail.py", title="4. Run Detail", default=True),
        st.Page("pages/p05_signal_explorer.py", title="5. Signal Explorer"),
        st.Page("pages/p06_compare.py", title="6. Compare"),
        st.Page("pages/p07_research_log.py", title="7. Research Log"),
    ]
    nav = st.navigation(pages)
    nav.run()


if __name__ == "__main__":
    main()
