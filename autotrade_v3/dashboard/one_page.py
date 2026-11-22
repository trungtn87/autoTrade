"""NinLab Terminal One-Page Lab Dashboard (read-only: Backtest V3-16 + Live Reconcile 17a)."""
from __future__ import annotations

from datetime import datetime, timezone
import html
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from autotrade_v3.dashboard import all_signals
from autotrade_v3.dashboard.ui_shared import inject_research_theme, style_plotly

DEFAULT_BINGX_MANIFEST = Path("data/bingx/manifests/latest_manifest.json")
DEFAULT_RECONCILE_DIR = Path("autotrade_v3/reconcile_v3")
LIVE_SAMPLE_THRESHOLD = 60

DRIFT_CAUSES: tuple[str, ...] = (
    "DỮ_LIỆU",
    "CHIẾN_LƯỢC",
    "THỰC_THI",
    "HỆ_THỐNG",
    "VPS_TẮT",
    "CHƯA_GIẢI_THÍCH",
)


def compute_breakeven_bps(cost_rows: list[dict[str, Any]]) -> float | None:
    """Linearly interpolate break-even slippage bps where net_pnl crosses zero."""
    pts: list[tuple[float, float]] = []
    for row in cost_rows:
        bps = row.get("slippage_bps")
        net = (row.get("net_pnl") or {}).get("value")
        if isinstance(bps, (int, float)) and isinstance(net, (int, float)):
            pts.append((float(bps), float(net)))
    pts.sort(key=lambda x: x[0])
    if not pts:
        return None
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if y0 >= 0.0 >= y1 and y0 != y1:
            return x0 + (0.0 - y0) * (x1 - x0) / (y1 - y0)
    return None


@st.cache_data(show_spinner=False)
def load_freshness_info(manifest_path: str = str(DEFAULT_BINGX_MANIFEST)) -> dict[str, Any]:
    """Read BingX collector latest_manifest.json strictly read-only."""
    p = Path(manifest_path)
    if not p.is_file():
        return {
            "status": "RED",
            "label": "CHƯA CÓ KHO DỮ LIỆU",
            "collected_at": "—",
            "overall_status": "MISSING",
            "run_id": "—",
            "sources": {},
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "RED",
            "label": "LỖI ĐỌC MANIFEST",
            "collected_at": "—",
            "overall_status": "ERROR",
            "run_id": "—",
            "sources": {},
        }
    overall = str(raw.get("overall_status") or "UNKNOWN").upper()
    collected_at = str(raw.get("collected_at") or "—")
    dot_status = "GREEN" if overall == "COMPLETE" else ("AMBER" if overall == "GAP" else "RED")
    return {
        "status": dot_status,
        "label": f"{overall} · {collected_at[:16].replace('T', ' ')}Z",
        "collected_at": collected_at,
        "overall_status": overall,
        "run_id": str(raw.get("run_id") or "—"),
        "sources": raw.get("sources") or {},
    }


@st.cache_data(show_spinner=False)
def load_reconcile_17a(reconcile_dir: str = str(DEFAULT_RECONCILE_DIR)) -> dict[str, Any]:
    """Read 17a reconciliation summary if available; otherwise return CHƯA CÓ ĐỐI SOÁT."""
    rdir = Path(reconcile_dir)
    cand = rdir / "reconcile_summary.json"
    if cand.is_file():
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {"available": True, **data}
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "available": False,
        "live_trades_count": 0,
        "threshold": LIVE_SAMPLE_THRESHOLD,
        "status": "CHƯA CÓ ĐỐI SOÁT",
        "live_equity": [],
        "mirror_equity": [],
        "combo_live": {},
        "drift_causes": {k: 0.0 for k in DRIFT_CAUSES},
        "vps_off_windows": [],
        "exit_change_markers": [],
    }


@st.cache_data(show_spinner=False)
def load_combo_sparklines(run_dir_str: str) -> dict[str, list[float]]:
    """Compute compact cumulative PnL sparklines per combo_key from trades.parquet."""
    tpath = Path(run_dir_str) / "trades.parquet"
    if not tpath.is_file():
        return {}
    df = pd.read_parquet(tpath, columns=["symbol", "combo_name", "exit_time_ms", "net_pnl"])
    if df.empty:
        return {}
    df = df.sort_values("exit_time_ms")
    df["combo_key"] = df["symbol"].astype(str) + "/" + df["combo_name"].astype(str)
    sparks: dict[str, list[float]] = {}
    for ck, grp in df.groupby("combo_key", sort=False):
        vals = grp["net_pnl"].to_numpy(dtype=float).cumsum()
        if len(vals) > 28:
            idx = np.linspace(0, len(vals) - 1, 28, dtype=int)
            vals = vals[idx]
        sparks[str(ck)] = [round(float(v), 2) for v in vals]
    return sparks


def _svg_sparkline(vals: list[float], width: int = 96, height: int = 24) -> str:
    if len(vals) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    mn, mx = min(vals), max(vals)
    span = (mx - mn) if mx != mn else 1.0
    pts = []
    for i, v in enumerate(vals):
        x = 2.0 + (width - 4.0) * i / (len(vals) - 1)
        y = (height - 3.0) - ((v - mn) / span) * (height - 6.0)
        pts.append(f"{x:.1f},{y:.1f}")
    color = "#36e394" if vals[-1] >= 0 else "#ff6673"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="display:block">'
        f'<polyline fill="none" stroke="{color}" stroke-width="1.8" points="{" ".join(pts)}" />'
        f"</svg>"
    )


def _render_css() -> None:
    st.markdown(
        """
        <style>
        .nl-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:8px 0 14px}
        @media(max-width:1100px){.nl-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
        @media(max-width:640px){.nl-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
        .nl-tile{background:linear-gradient(145deg,rgba(20,30,46,.97),rgba(11,17,28,.97));border:1px solid #35445b;border-radius:12px;padding:12px 14px;min-height:92px}
        .nl-tile-lbl{color:#9fb2c9;font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
        .nl-tile-val{font-size:1.45rem;font-weight:800;margin:4px 0 2px;font-variant-numeric:tabular-nums}
        .nl-tile-sub{color:#b8c5d6;font-size:.76rem;font-variant-numeric:tabular-nums}
        .nl-good{color:#36e394!important}.nl-bad{color:#ff6673!important}.nl-warn{color:#ffbd59!important}.nl-mute{color:#94a3b8!important}.nl-cyan{color:#35d9ff!important}
        .nl-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
        .nl-dot-GREEN{background:#36e394}.nl-dot-AMBER{background:#ffbd59}.nl-dot-RED{background:#ff6673}
        .nl-badge{display:inline-block;padding:3px 8px;border-radius:999px;font-size:.71rem;font-weight:700;white-space:nowrap}
        .nl-b-sync{background:#123524;color:#36e394;border:1px solid #246846}
        .nl-b-drift{background:#3a161c;color:#ff6673;border:1px solid #7d2836}
        .nl-b-wait{background:#2d2412;color:#ffbd59;border:1px solid #745821}
        .nl-b-off{background:#1e293b;color:#cbd5e1;border:1px solid #475569}
        .nl-table-wrap{overflow-x:auto;border:1px solid #35445b;border-radius:12px;background:#0f172a;margin:8px 0 14px}
        .nl-table{width:100%;border-collapse:collapse;font-size:.86rem;white-space:nowrap}
        .nl-table th{background:#182336;color:#cbd5e1;padding:10px 12px;text-align:left;font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #35445b}
        .nl-table td{padding:8px 12px;border-bottom:1px solid #1e293b;color:#f1f5f9;vertical-align:middle;font-variant-numeric:tabular-nums}
        .nl-table tr:last-child td{border-bottom:0}
        .nl-bar-bg{width:110px;height:8px;background:#1e293b;border-radius:4px;overflow:hidden;display:inline-block;vertical-align:middle}
        .nl-bar-pos{height:100%;background:#36e394;border-radius:4px}
        .nl-bar-neg{height:100%;background:#ff6673;border-radius:4px}
        .nl-drawer{background:#0f172a;border:1px solid #35445b;border-radius:12px;padding:12px;max-width:100%;overflow:hidden}
        .nl-drawer .nl-table-wrap{overflow-x:auto;max-width:100%}
        .nl-drawer .nl-table th,.nl-drawer .nl-table td{padding:6px 8px;font-size:.78rem}
        @media(max-width:640px){.nl-drawer .nl-table{white-space:normal}.nl-drawer .nl-table th,.nl-drawer .nl-table td{padding:5px 6px;font-size:.72rem}}
        .nl-est{background:rgba(255,189,89,0.12)!important;color:#ffbd59!important}
        .nl-footer{border-top:1px solid #263244;margin-top:18px;padding-top:10px;color:#94a3b8;font-size:.76rem}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _live_status_badge(live_n: int, threshold: int, has_reconcile: bool, drifted: bool = False) -> tuple[str, str]:
    if not has_reconcile:
        return all_signals.vi("ninlab_status_no_reconcile"), "nl-warn"
    if live_n < threshold:
        return all_signals.vi("ninlab_status_insufficient"), "nl-warn"
    if drifted:
        return all_signals.vi("ninlab_status_drift"), "nl-bad"
    return all_signals.vi("ninlab_status_synced"), "nl-good"


def _build_main_equity_chart(payload: dict[str, Any], rec: dict[str, Any]) -> go.Figure:
    eq = payload.get("series", {}).get("equity_mtm") or []
    tw = payload.get("run_info", {}).get("time_window_utc", {})
    start_s = tw.get("start", "2023-11-01")
    end_s = tw.get("end", "2026-11-01")
    x_dates = pd.date_range(start_s, end_s, periods=max(len(eq), 2), tz="UTC") if eq else []

    fig = go.Figure()
    if eq:
        fig.add_trace(
            go.Scatter(
                x=x_dates,
                y=eq,
                mode="lines",
                name=all_signals.vi("ninlab_trace_backtest"),
                line=dict(color="#35d9ff", width=2.4),
                hovertemplate="%{x|%Y-%m-%d}<br>Vốn: $%{y:,.2f}<extra></extra>",
            )
        )
    if rec.get("available") and rec.get("live_equity"):
        lx = [pd.to_datetime(p["ts"], utc=True) for p in rec["live_equity"]]
        ly = [float(p["equity"]) for p in rec["live_equity"]]
        fig.add_trace(
            go.Scatter(
                x=lx,
                y=ly,
                mode="lines",
                name=all_signals.vi("ninlab_trace_live"),
                line=dict(color="#36e394", width=2.4),
            )
        )
    if rec.get("available") and rec.get("mirror_equity"):
        mx = [pd.to_datetime(p["ts"], utc=True) for p in rec["mirror_equity"]]
        my = [float(p["equity"]) for p in rec["mirror_equity"]]
        fig.add_trace(
            go.Scatter(
                x=mx,
                y=my,
                mode="lines",
                name=all_signals.vi("ninlab_trace_mirror"),
                line=dict(color="#ffbd59", width=2.0, dash="dash"),
            )
        )
    for dt_str in rec.get("exit_change_markers", []):
        fig.add_vline(x=pd.to_datetime(dt_str, utc=True), line_width=1, line_dash="dot", line_color="rgba(148,163,184,0.45)")
    for w in rec.get("vps_off_windows", []):
        fig.add_vrect(
            x0=pd.to_datetime(w["start"], utc=True),
            x1=pd.to_datetime(w["end"], utc=True),
            fillcolor="rgba(255,102,115,0.12)",
            line_width=0,
        )

    fig.update_layout(title=all_signals.vi("ninlab_chart_main_title"))
    return style_plotly(fig, height=400)


def _build_slippage_chart(cost_rows: list[dict[str, Any]], breakeven_bps: float | None) -> go.Figure:
    pts = sorted(
        [
            (float(r["slippage_bps"]), float(r["net_pnl"]["value"]))
            for r in cost_rows
            if isinstance(r.get("slippage_bps"), (int, float))
            and isinstance((r.get("net_pnl") or {}).get("value"), (int, float))
        ],
        key=lambda x: x[0],
    )
    fig = go.Figure()
    if pts:
        fig.add_trace(
            go.Scatter(
                x=[p[0] for p in pts],
                y=[p[1] for p in pts],
                mode="lines+markers",
                line=dict(color="#35d9ff", width=2.4),
                marker=dict(size=7, color="#35d9ff"),
                hovertemplate="%{x:.1f} bps: $%{y:,.2f}<extra></extra>",
            )
        )
    fig.add_hline(y=0, line_dash="dash", line_color="#64748b", line_width=1)
    if breakeven_bps is not None:
        fig.add_vline(x=breakeven_bps, line_dash="dot", line_color="#ffbd59", line_width=2)
        fig.add_annotation(
            x=breakeven_bps,
            y=0,
            text=f"Hòa vốn {breakeven_bps:.2f} bps",
            showarrow=True,
            arrowhead=1,
            ax=40,
            ay=-28,
            font=dict(color="#ffbd59", size=11),
        )
    fig.update_layout(title=all_signals.vi("ninlab_slip_title"), showlegend=False)
    fig.update_xaxes(title_text="bps", range=[0, 5.2])
    fig.update_yaxes(title_text="USD")
    return style_plotly(fig, height=250)


def _build_drift_bar_chart(drift_causes: dict[str, float]) -> go.Figure:
    fig = go.Figure()
    colors = ["#35d9ff", "#a87cff", "#ffbd59", "#ff6673", "#94a3b8", "#64748b"]
    total_abs = sum(abs(float(drift_causes.get(c, 0.0))) for c in DRIFT_CAUSES)
    for cause, col in zip(DRIFT_CAUSES, colors):
        val = float(drift_causes.get(cause, 0.0))
        fig.add_trace(
            go.Bar(
                y=[""],
                x=[val],
                name=cause,
                orientation="h",
                marker_color=col,
                hovertemplate=f"{cause}: $%{{x:,.2f}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="stack", title=all_signals.vi("ninlab_drift_title"))
    if total_abs == 0.0:
        fig.add_annotation(
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            text=all_signals.vi("ninlab_no_live_yet"),
            showarrow=False,
            font=dict(color="#94a3b8", size=12),
        )
    return style_plotly(fig, height=250)


def _render_drawer(combo_row: dict[str, Any], rec: dict[str, Any]) -> None:
    ck = str(combo_row.get("combo_key", ""))
    live_info = (rec.get("combo_live") or {}).get(ck)
    own_pnl = float((combo_row.get("own_entry_pnl") or {}).get("value") or 0.0)
    vwap_pnl = float((combo_row.get("portfolio_net_pnl") or {}).get("value") or 0.0)
    fees = float((combo_row.get("fees") or {}).get("value") or 0.0)
    entries = int((combo_row.get("entries") or {}).get("value") or 0)
    wr = float((combo_row.get("leg_win_rate_pct") or {}).get("value") or 0.0)
    pf = (combo_row.get("net_pf_leg") or {}).get("value")
    pf_s = f"{float(pf):.2f}" if isinstance(pf, (int, float)) else "—"

    items = [
        ("Số lệnh khớp", f"{live_info['trades']}" if live_info else "0", f"{entries}", "—" if not live_info else f"{live_info['trades']-entries:+d}", "EXACT" if live_info else "UNRESOLVED"),
        ("Tỷ lệ thắng", f"{live_info['wr']:.1f}%" if live_info else "—", f"{wr:.1f}%", "—", "RECONCILED" if live_info else "UNRESOLVED"),
        ("Hệ số lợi nhuận", "—", pf_s, "—", "RECONCILED" if live_info else "UNRESOLVED"),
        ("PnL entry riêng", f"${live_info['pnl']:,.2f}" if live_info else "—", f"${own_pnl:,.2f}", "—", "EXACT" if live_info else "UNRESOLVED"),
        ("PnL danh mục (VWAP)", "—", f"${vwap_pnl:,.2f}", "—", "RECONCILED"),
        ("Phí giao dịch", f"${live_info['fees']:,.2f}" if live_info else "—", f"${fees:,.2f}", "—", "EXACT" if live_info else "UNRESOLVED"),
        ("Trượt giá thực", "—", "Theo BASE", "—", "ESTIMATED"),
        ("Funding thực", "—", "Trong mô phỏng", "—", "ESTIMATED"),
        ("Trạng thái đối soát", all_signals.vi("ninlab_status_no_reconcile") if not live_info else live_info["status"], "Sẵn sàng", "—", "EXACT" if live_info else "UNRESOLVED"),
    ]
    rows_html = ""
    for name, lv, sm, df_s, ev in items[:9]:
        cls = " class='nl-est'" if ev == "ESTIMATED" else ""
        rows_html += (
            f"<tr{cls}><td>{html.escape(name)}</td>"
            f"<td style='text-align:right'>{html.escape(lv)}</td>"
            f"<td style='text-align:right'>{html.escape(sm)}</td>"
            f"<td style='text-align:right'>{html.escape(df_s)}</td>"
            f"<td><span class='nl-badge nl-b-off'>{html.escape(ev)}</span></td></tr>"
        )
    st.markdown(
        f'<div class="nl-drawer" data-testid="ninlab-drawer">'
        f'<div style="font-weight:700;margin-bottom:8px;color:#f8fafc">{html.escape(all_signals.vi("ninlab_drawer_title"))} · <code>{html.escape(ck)}</code></div>'
        f'<div class="nl-table-wrap"><table class="nl-table"><thead><tr>'
        f"<th>{html.escape(all_signals.vi('ninlab_drawer_item'))}</th>"
        f"<th>{html.escape(all_signals.vi('ninlab_drawer_live'))}</th>"
        f"<th>{html.escape(all_signals.vi('ninlab_drawer_sim'))}</th>"
        f"<th>{html.escape(all_signals.vi('ninlab_drawer_diff'))}</th>"
        f"<th>{html.escape(all_signals.vi('ninlab_drawer_evidence'))}</th>"
        f"</tr></thead><tbody>{rows_html}</tbody></table></div>"
        f"<div style='color:#ffbd59;font-size:.76rem'>{html.escape(all_signals.vi('ninlab_estimated_note'))}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_one_page_terminal() -> None:
    """Render the single-page NinLab Terminal Lab Dashboard."""
    inject_research_theme()
    _render_css()

    runs = all_signals.list_all_signals_runs()
    base_runs = [r for r in runs if r.cost_profile == "BASE"]
    if not base_runs:
        base_runs = runs

    if not base_runs:
        st.error(all_signals.vi("no_runs"))
        return

    # Header + Run selector + Link to full MT5 report
    h_col1, h_col2, h_col3 = st.columns([3.2, 1.5, 1.3])
    with h_col1:
        st.markdown(f'<div class="v3-brand">AUTOTRADE V3 · NINLAB TERMINAL</div>', unsafe_allow_html=True)
        st.subheader(all_signals.vi("ninlab_title"))
    with h_col2:
        run_ids = [r.run_id for r in base_runs]
        default_idx = run_ids.index("ALL_SIGNALS_BASE") if "ALL_SIGNALS_BASE" in run_ids else 0
        sel_run_id = st.selectbox(
            all_signals.vi("ninlab_kpi_run_view"),
            run_ids,
            index=default_idx,
            label_visibility="collapsed",
        )
    with h_col3:
        if st.button(all_signals.vi("ninlab_btn_full_report"), use_container_width=True):
            st.session_state["v3_16_selected_run"] = sel_run_id
            st.switch_page("pages/p10_mt5_report.py")

    selected = next(r for r in base_runs if r.run_id == sel_run_id)
    st.session_state["v3_16_selected_run"] = selected.run_id

    # Mandatory fail-closed verification on open
    verification = all_signals.verify_open_report(selected)
    payload = all_signals.load_report_json(selected) if verification.passed else None
    if not verification.passed or payload is None:
        st.error(all_signals.vi("ninlab_hash_fail_banner"))
        st.error(f"{all_signals.vi('verify_fail')}: {verification.detail}")
        return

    freshness = load_freshness_info()
    rec = load_reconcile_17a()

    # Extract top-level metrics
    acc = payload.get("kpi", {}).get("account", {})
    risk = payload.get("kpi", {}).get("risk", {})
    init_cap = float((acc.get("initial_capital") or {}).get("value") or 1000.0)
    final_eq = float((acc.get("final_equity_mtm") or {}).get("value") or init_cap)
    net_3y = final_eq - init_cap
    roi_3y = (net_3y / init_cap * 100.0) if init_cap > 0 else 0.0
    m2m_dd_usd = float((risk.get("m2m_max_dd_dollars") or {}).get("value") or 0.0)
    m2m_dd_pct = float((risk.get("m2m_max_dd_pct") or {}).get("value") or 0.0)
    cost_rows = payload.get("breakdowns", {}).get("cost_sensitivity", [])
    be_bps = compute_breakeven_bps(cost_rows)
    be_txt = f"{be_bps:.2f} bps" if be_bps is not None else "—"

    live_n = int(rec.get("live_trades_count") or 0)
    thresh = int(rec.get("threshold") or LIVE_SAMPLE_THRESHOLD)
    live_lbl, live_cls = _live_status_badge(live_n, thresh, bool(rec.get("available")), bool(rec.get("drifted", False)))
    dot_cls = f"nl-dot-{freshness['status']}"

    # 1. Top Status Strip (max 6 tiles)
    st.markdown(
        f'<div class="nl-grid" data-testid="ninlab-top-strip">'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_net_3y"))}</div>'
        f'<div class="nl-tile-val {"nl-good" if net_3y>=0 else "nl-bad"}">{"+" if net_3y>=0 else "-"}${abs(net_3y):,.2f}</div>'
        f'<div class="nl-tile-sub">ROI {roi_3y:+.1f}% · {html.escape(selected.run_id)}</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_dd_m2m"))}</div>'
        f'<div class="nl-tile-val nl-warn">${m2m_dd_usd:,.2f}</div>'
        f'<div class="nl-tile-sub">{m2m_dd_pct:.2f}% vốn đỉnh</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_breakeven"))}</div>'
        f'<div class="nl-tile-val nl-cyan">{html.escape(be_txt)}</div>'
        f'<div class="nl-tile-sub">mỗi chiều (BASE = 2,0 bps)</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_live_vs_lab"))}</div>'
        f'<div class="nl-tile-val {live_cls}" style="font-size:1.08rem">{html.escape(live_lbl)}</div>'
        f'<div class="nl-tile-sub">{live_n} / {thresh} lệnh</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_freshness"))}</div>'
        f'<div class="nl-tile-val" style="font-size:1.02rem"><span class="nl-dot {dot_cls}"></span>{html.escape(freshness["overall_status"])}</div>'
        f'<div class="nl-tile-sub">{html.escape(freshness["collected_at"][:19].replace("T"," "))}Z</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("verification_status"))}</div>'
        f'<div class="nl-tile-val {"nl-good" if selected.validation_status=="VERIFIED" else "nl-warn"}" style="font-size:1.08rem">● {html.escape(all_signals.validation_status_vi(selected.validation_status))}</div>'
        f'<div class="nl-tile-sub">Commit {html.escape(selected.git_commit[:8])}</div></div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    with st.expander(all_signals.vi("ninlab_freshness_detail"), expanded=False):
        src_rows = [
            {"Nguồn": k, "Trạng thái": v.get("status"), "Bản ghi": v.get("total_records"), "SHA-256": (v.get("parquet_files") or [{}])[0].get("sha256", "")[:16]}
            for k, v in (freshness.get("sources") or {}).items()
        ]
        if src_rows:
            st.dataframe(pd.DataFrame(src_rows), use_container_width=True, hide_index=True)

    # 2. Main Equity Chart (half screen)
    st.plotly_chart(_build_main_equity_chart(payload, rec), use_container_width=True, config={"displayModeBar": False})

    # 3. NinLab Combo Table + Optional Right Detail Drawer
    combos_raw = payload.get("breakdowns", {}).get("combos", [])
    sparks = load_combo_sparklines(str(selected.run_dir))

    st.markdown(f"**{all_signals.vi('ninlab_table_title')}**")
    f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.3, 1.6])
    with f1:
        pair_f = st.selectbox(all_signals.vi("ninlab_filter_pair"), [all_signals.vi("ninlab_filter_all"), "BTC-USDT", "ETH-USDT"])
    with f2:
        status_f = st.selectbox(
            all_signals.vi("ninlab_filter_status"),
            [all_signals.vi("ninlab_filter_all"), all_signals.vi("ninlab_status_low_trades"), all_signals.vi("ninlab_status_synced"), all_signals.vi("ninlab_status_drift")],
        )
    with f3:
        sort_f = st.selectbox(
            all_signals.vi("ninlab_sort_label"),
            [all_signals.vi("ninlab_sort_pnl_desc"), all_signals.vi("ninlab_sort_pnl_asc"), all_signals.vi("ninlab_sort_name")],
        )
    with f4:
        combo_keys_all = [str(c.get("combo_key", "")) for c in combos_raw]
        if "ninlab_drawer_sel" not in st.session_state or st.session_state["ninlab_drawer_sel"] not in (["—"] + combo_keys_all):
            st.session_state["ninlab_drawer_sel"] = "—"
        drawer_sel = st.selectbox(all_signals.vi("ninlab_drawer_select"), ["—"] + combo_keys_all, key="ninlab_drawer_sel")

    # Filter & Sort combos
    filtered: list[dict[str, Any]] = []
    for c in combos_raw:
        sym = str(c.get("symbol", ""))
        ck = str(c.get("combo_key", ""))
        if pair_f != all_signals.vi("ninlab_filter_all") and sym != pair_f:
            continue
        live_info = (rec.get("combo_live") or {}).get(ck)
        st_lbl = all_signals.vi("ninlab_status_low_trades") if not live_info else live_info.get("status", all_signals.vi("ninlab_status_low_trades"))
        if status_f != all_signals.vi("ninlab_filter_all") and st_lbl != status_f:
            continue
        filtered.append(c)

    if sort_f == all_signals.vi("ninlab_sort_pnl_desc"):
        filtered.sort(key=lambda x: float((x.get("portfolio_net_pnl") or {}).get("value") or 0.0), reverse=True)
    elif sort_f == all_signals.vi("ninlab_sort_pnl_asc"):
        filtered.sort(key=lambda x: float((x.get("portfolio_net_pnl") or {}).get("value") or 0.0))
    else:
        filtered.sort(key=lambda x: str(x.get("combo_key", "")))

    max_abs_pnl = max([abs(float((c.get("portfolio_net_pnl") or {}).get("value") or 0.0)) for c in combos_raw] or [1.0])

    def _render_combo_table() -> None:
        rows_html = ""
        for c in filtered:
            ck = str(c.get("combo_key", ""))
            pnl = float((c.get("portfolio_net_pnl") or {}).get("value") or 0.0)
            w_pct = min(100.0, abs(pnl) / max_abs_pnl * 100.0)
            bar_cls = "nl-bar-pos" if pnl >= 0 else "nl-bar-neg"
            sp_svg = _svg_sparkline(sparks.get(ck, []))
            live_info = (rec.get("combo_live") or {}).get(ck)
            live_cnt = str(live_info["trades"]) if live_info else "0"
            st_lbl = all_signals.vi("ninlab_status_low_trades") if not live_info else live_info["status"]
            b_cls = "nl-b-wait" if st_lbl == all_signals.vi("ninlab_status_low_trades") else ("nl-b-sync" if st_lbl == all_signals.vi("ninlab_status_synced") else "nl-b-drift")
            drift_s = f"{live_info['drift_pct']:+.1f}%" if live_info else "—"
            rows_html += (
                f"<tr>"
                f"<td><strong>{html.escape(ck)}</strong></td>"
                f"<td><div class='nl-bar-bg'><div class='{bar_cls}' style='width:{w_pct:.1f}%'></div></div></td>"
                f"<td>{sp_svg}</td>"
                f"<td style='text-align:right'>{html.escape(live_cnt)}</td>"
                f"<td><span class='nl-badge {b_cls}'>{html.escape(st_lbl)}</span></td>"
                f"<td style='text-align:right'>{html.escape(drift_s)}</td>"
                f"</tr>"
            )
        st.markdown(
            f'<div class="nl-table-wrap" data-testid="ninlab-combo-table"><table class="nl-table"><thead><tr>'
            f"<th>{html.escape(all_signals.vi('ninlab_col_combo'))}</th>"
            f"<th>{html.escape(all_signals.vi('ninlab_col_bar'))}</th>"
            f"<th>{html.escape(all_signals.vi('ninlab_col_spark'))}</th>"
            f"<th style='text-align:right'>{html.escape(all_signals.vi('ninlab_col_live_n'))}</th>"
            f"<th>{html.escape(all_signals.vi('ninlab_col_status'))}</th>"
            f"<th style='text-align:right'>{html.escape(all_signals.vi('ninlab_col_drift'))}</th>"
            f"</tr></thead><tbody>{rows_html}</tbody></table></div>",
            unsafe_allow_html=True,
        )

    if drawer_sel != "—":
        t_col, d_col = st.columns([1.65, 1.15])
        with t_col:
            _render_combo_table()
        with d_col:
            sel_row = next((c for c in combos_raw if str(c.get("combo_key")) == drawer_sel), None)
            if sel_row is not None:
                _render_drawer(sel_row, rec)
                if st.button(all_signals.vi("ninlab_drawer_close"), use_container_width=True):
                    st.session_state["ninlab_drawer_sel"] = "—"
                    st.rerun()
    else:
        _render_combo_table()

    # 4. Two compact side-by-side panels below the table
    c_left, c_right = st.columns(2)
    with c_left:
        st.plotly_chart(_build_slippage_chart(cost_rows, be_bps), use_container_width=True, config={"displayModeBar": False})
    with c_right:
        st.plotly_chart(_build_drift_bar_chart(rec.get("drift_causes") or {}), use_container_width=True, config={"displayModeBar": False})

    # 6. Compact Footer
    cur_commit = all_signals.current_git_commit()
    if selected.code_dirty:
        st.error(all_signals.vi("dirty_run_warning"))
    if cur_commit and selected.git_commit and cur_commit != selected.git_commit:
        st.warning(f"{all_signals.vi('commit_mismatch_warning')} ({selected.git_commit[:8]} ≠ {cur_commit[:8]})")

    f_info, f_btn1, f_btn2 = st.columns([3.5, 0.8, 1.2])
    with f_info:
        st.markdown(
            f'<div class="nl-footer">'
            f"run_id: <code>{html.escape(selected.run_id)}</code> · "
            f"commit: <code>{html.escape(selected.git_commit[:12])}</code> · "
            f"sinh: <code>{html.escape(selected.generated_at_utc[:19])}</code> · "
            f"băm chi phí: <code>{html.escape(selected.cost_profile_hash[:12])}</code>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with f_btn1:
        if st.button(all_signals.vi("reload"), use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with f_btn2:
        json_bytes = all_signals.report_json_bytes(selected)
        if json_bytes is not None:
            st.download_button(
                all_signals.vi("export_ai_json"),
                data=json_bytes,
                file_name=f"{selected.run_id}_report_v1.json",
                mime="application/json",
                use_container_width=True,
            )
