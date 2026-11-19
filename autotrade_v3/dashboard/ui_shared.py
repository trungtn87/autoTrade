"""Shared UI formatting helpers for AutoTrade V3 Research Lab Dashboard (no metric math)."""
from __future__ import annotations

import os
import html
from pathlib import Path
import streamlit as st

PLOTLY_COLORS = ["#35d9ff", "#36e394", "#ffb547", "#a87cff", "#ff5c68", "#5f8cff"]
DEFAULT_STORE_DIR = Path("runs/lab/store")
DEFAULT_CATALOG_PATH = Path("catalog/research.duckdb")
DEFAULT_QA_PATH = Path("catalog/v3_m1_qa.json")
DEFAULT_NOTES_DB = Path("runs/lab/notes.duckdb")


def inject_research_theme() -> None:
    """Apply the shared V3 dark research-lab visual language."""
    st.markdown(
        """
        <style>
        :root{--v3-bg:#080d16;--v3-panel:#111827;--v3-line:#35445b;--v3-muted:#b8c5d6;--v3-text:#f8fafc;--v3-cyan:#35d9ff;--v3-green:#36e394;--v3-amber:#ffbd59;--v3-red:#ff6673}
        .stApp{background:radial-gradient(circle at 82% -15%,#182840 0,#080d16 38%);color:var(--v3-text)!important}
        .stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp h5,.stApp h6,
        .stApp p,.stApp li,.stApp label,.stApp small,
        .stApp [data-testid="stCaptionContainer"],
        .stApp [data-testid="stMarkdownContainer"]{color:var(--v3-text)!important}
        .stApp [data-testid="stWidgetLabel"] p,
        .stApp [data-testid="stSlider"] p,
        .stApp [data-testid="stSelectbox"] label,
        .stApp [data-testid="stMultiSelect"] label,
        .stApp [data-testid="stTextInput"] label,
        .stApp [data-testid="stNumberInput"] label{color:#f1f5f9!important;font-weight:600}
        [data-testid="stSidebar"]{background:linear-gradient(180deg,#151d2c,#0b111c);border-right:1px solid var(--v3-line)}
        [data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,[data-testid="stSidebar"] span,[data-testid="stSidebar"] a{color:#f8fafc!important}
        [data-testid="stSidebarNav"] a{border-radius:10px;margin:3px 8px;color:#eaf1f8!important}
        [data-testid="stSidebarNav"] a span{color:#eaf1f8!important;font-weight:600}
        [data-testid="stSidebarNav"] a[aria-current="page"]{background:#1c2b40;border-left:3px solid var(--v3-cyan)}
        .block-container{max-width:1680px;padding-top:1.7rem;padding-bottom:4rem}
        h1,h2,h3{letter-spacing:-.025em;color:#fff!important}.v3-brand{color:var(--v3-cyan)!important;font-size:.72rem;font-weight:800;letter-spacing:.15em;text-transform:uppercase}
        .v3-subtitle{color:#c6d2e1!important;margin-top:-.5rem;margin-bottom:1.4rem}
        .v3-panel{border:1px solid var(--v3-line);background:linear-gradient(145deg,rgba(20,30,46,.96),rgba(11,17,28,.96));border-radius:15px;padding:17px;box-shadow:0 16px 40px rgba(0,0,0,.16)}
        .v3-kpi{min-height:120px}.v3-kpi-label{color:#c5d2e3!important;font-size:.72rem;letter-spacing:.08em;text-transform:uppercase}.v3-kpi-value{font-size:1.72rem;font-weight:800;margin:.32rem 0}.v3-kpi-note{color:#b8c5d6!important;font-size:.78rem}.tone-good{color:var(--v3-green)!important}.tone-warn{color:var(--v3-amber)!important}.tone-bad{color:var(--v3-red)!important}.tone-info{color:var(--v3-cyan)!important}
        .v3-badge{display:inline-block;padding:4px 9px;border-radius:999px;background:#1c2b40;color:#bfefff;border:1px solid #294a65;font-size:.72rem;font-weight:700}
        [data-testid="stDataFrame"]{border:1px solid var(--v3-line);border-radius:12px;overflow:hidden}
        .stButton>button{border-radius:10px;border-color:#40516b;background:#172235;color:#fff!important;font-weight:700}.stButton>button:hover{border-color:var(--v3-cyan);color:var(--v3-cyan)!important}
        [data-testid="stMetric"]{border:1px solid var(--v3-line);background:#111827;border-radius:13px;padding:14px}
        [data-testid="stMetricLabel"] p{color:#c5d2e3!important}
        [data-testid="stMetricValue"]{color:#f8fafc!important}
        [data-testid="stMetricDelta"]{color:#b8c5d6!important}
        [data-testid="stAlert"]{background:#211b0e!important;border:1px solid #8a6728!important;border-radius:12px}
        [data-testid="stAlert"] p,[data-testid="stAlert"] span,[data-testid="stAlert"] div{color:#ffe7b0!important}
        .stApp code{background:#27364a!important;color:#f8fafc!important;border:1px solid #43536a}
        .stApp [data-baseweb="select"]>div{background:#f8fafc!important;color:#0f172a!important;border-color:#cbd5e1!important}
        .stApp [data-baseweb="select"] span,.stApp [data-baseweb="select"] input{color:#0f172a!important}
        .stApp input{color:#f8fafc}
        .stApp [data-testid="stNumberInput"] input,.stApp [data-testid="stTextInput"] input{background:#121c2b!important;color:#f8fafc!important}
        hr{border-color:var(--v3-line)}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_heading(step: str, title: str, subtitle: str) -> None:
    st.markdown(f'<div class="v3-brand">{html.escape(step)} · AUTOTRADE V3 RESEARCH LAB</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<div class="v3-subtitle">{html.escape(subtitle)}</div>', unsafe_allow_html=True)


def kpi_card(label: str, value: str, note: str = "", tone: str = "info", testid: str = "kpi") -> None:
    st.markdown(
        f'<div class="v3-panel v3-kpi" data-testid="{html.escape(testid)}">'
        f'<div class="v3-kpi-label">{html.escape(label)}</div>'
        f'<div class="v3-kpi-value tone-{html.escape(tone)}">{html.escape(value)}</div>'
        f'<div class="v3-kpi-note">{html.escape(note)}</div></div>',
        unsafe_allow_html=True,
    )


def style_plotly(fig: object, height: int = 400) -> object:
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#dce6f2", family="Inter, sans-serif"),
        margin=dict(l=20, r=20, t=42, b=28),
        hoverlabel=dict(bgcolor="#172235"),
        legend=dict(orientation="h", y=1.12),
    )
    fig.update_xaxes(gridcolor="#263244", zerolinecolor="#263244")
    fig.update_yaxes(gridcolor="#263244", zerolinecolor="#263244")
    return fig


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
