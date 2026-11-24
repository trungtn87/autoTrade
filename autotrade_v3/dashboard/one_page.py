"""NinLab Terminal One-Page Lab Dashboard (read-only: PLUS44 ALL_SIGNALS_PORTFOLIO + Live Reconcile 17a).

NGOẠI LỆ CÓ CHỦ Ý CỦA NGUYÊN TẮC CHỈ ĐỌC:
Nút 'Đồng bộ' ở chân trang gọi `autotrade_v3.dashboard.sync_runner` (tiến trình con không chặn UI),
chỉ ghi vào `data/bingx/`, `evidence/v3-17a/` và cache dashboard; chỉ đọc Supabase qua collector_v3 (`SUPABASE_DB_URL`).
"""
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
from autotrade_v3.dashboard import plus44_source
from autotrade_v3.dashboard.sync_runner import evaluate_sync_freshness, read_sync_state, trigger_manual_sync
from autotrade_v3.dashboard.ui_shared import inject_research_theme, style_plotly
from autotrade_v3.reconcile_v3.labels_vi import RECONCILE_LABELS_VI
from autotrade_v3.reconcile_v3.reconcile_orders import load_tolerances

def _resolve_bingx_data_root(base_dir: Path | None = None) -> Path:
    """Resolve local BingX data store root, falling back to main repo when running in a git worktree."""
    cwd = base_dir or Path.cwd()
    local_root = cwd / "data" / "bingx"
    if (local_root / "manifests" / "latest_manifest.json").is_file() or local_root.is_dir():
        return Path("data/bingx") if base_dir is None else local_root

    dot_git = cwd / ".git"
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8", errors="ignore").strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = (cwd / gitdir).resolve()
                if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                    candidate = gitdir.parent.parent.parent / "data" / "bingx"
                    if candidate.is_dir():
                        return candidate
        except OSError:
            pass

    fallback = Path("D:/AutoTrade/data/bingx")
    if fallback.is_dir():
        return fallback
    return Path("data/bingx") if base_dir is None else local_root


DEFAULT_BINGX_DATA_ROOT = _resolve_bingx_data_root()
DEFAULT_BINGX_MANIFEST = DEFAULT_BINGX_DATA_ROOT / "manifests" / "latest_manifest.json"
DEFAULT_RECONCILE_DIR = Path("evidence/v3-17a")
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


@st.cache_data(show_spinner=False, ttl=60)
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


@st.cache_data(show_spinner=False, ttl=60)
def load_reconcile_17a(reconcile_dir: str = str(DEFAULT_RECONCILE_DIR)) -> dict[str, Any]:
    """Read 17a reconcile_report.json with mandatory verify_report() fail-closed check."""
    from autotrade_v3.reconcile_v3.verify import verify_report

    rdir = Path(reconcile_dir)
    report_path = rdir / "reconcile_report.json"
    if report_path.is_file():
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "available": True,
                "verify_failed": True,
                "verify_errors": [f"Lỗi đọc JSON 17a: {exc}"],
                "live_trades_count": 0,
                "threshold": LIVE_SAMPLE_THRESHOLD,
                "status": "LỖI XÁC MINH",
                "drift_causes": {k: 0.0 for k in DRIFT_CAUSES},
                "combo_live": {},
                "vps_off_windows": [],
                "exit_change_markers": [],
            }

        vr = verify_report(data)
        if not vr.passed:
            return {
                "available": True,
                "verify_failed": True,
                "verify_errors": vr.errors,
                "live_trades_count": 0,
                "threshold": LIVE_SAMPLE_THRESHOLD,
                "status": "LỖI XÁC MINH",
                "drift_causes": {k: 0.0 for k in DRIFT_CAUSES},
                "combo_live": {},
                "vps_off_windows": [],
                "exit_change_markers": [],
            }

        sg = data.get("sample_governance") or {}
        sr = data.get("signal_reconciliation") or {}
        since_sr = sr.get("since_compare_from") or {}
        proj_23 = sr.get("projected_23_finalized_since_compare_from") or {}
        closed_n = int(sg.get("closed_orders") or 0)
        thresh = int(sg.get("min_closed_orders_conclusive") or LIVE_SAMPLE_THRESHOLD)

        main_orders = [o for o in (data.get("orders") or []) if o.get("is_after_compare_from")]
        combo_live: dict[str, dict[str, Any]] = {}
        for o in main_orders:
            ck = f"{o.get('symbol')}/{o.get('combo')}"
            entry = combo_live.setdefault(
                ck,
                {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0, "slips": [], "status": "CHƯA ĐỦ MẪU", "drift_pct": 0.0},
            )
            net_v = float(((o.get("items") or {}).get("pnl") or {}).get("live_net_pnl") or 0.0)
            fee_v = float(((o.get("items") or {}).get("fees") or {}).get("live_total_fee") or 0.0)
            slip_v = float(((o.get("items") or {}).get("entry") or {}).get("live_slippage_bps") or 0.0)
            entry["trades"] += 1
            if net_v > 0:
                entry["wins"] += 1
            entry["pnl"] += net_v
            entry["fees"] += fee_v
            entry["slips"].append(slip_v)
        for ck, entry in combo_live.items():
            t = entry["trades"]
            entry["wr"] = (entry["wins"] / t * 100.0) if t else 0.0
            entry["avg_slip_bps"] = sum(entry["slips"]) / t if t else 0.0

        cause_dist_raw = data.get("cause_distribution") or {}
        drift_causes = {
            "DỮ_LIỆU": float(cause_dist_raw.get("DỮ_LIỆU", 0)),
            "CHIẾN_LƯỢC": float(cause_dist_raw.get("CHIẾN_LƯỢC", 0)),
            "THỰC_THI": float(cause_dist_raw.get("MÔ_HÌNH_THỰC_THI", 0)),
            "HỆ_THỐNG": float(cause_dist_raw.get("HỆ_THỐNG", 0)),
            "VPS_TẮT": float(cause_dist_raw.get("VPS_TẮT", 0)),
            "CHƯA_GIẢI_THÍCH": float(cause_dist_raw.get("CHƯA_GIẢI_THÍCH", 0)),
        }
        vps_wins = [
            {"start": w.get("start_utc"), "end": w.get("end_utc")}
            for w in (data.get("vps_off_windows") or [])
            if w.get("start_utc") and w.get("end_utc")
        ]
        return {
            "available": True,
            "verify_failed": False,
            "verify_errors": [],
            "report_sha256": data.get("report_sha256", ""),
            "compare_from_utc": data.get("compare_from_utc", ""),
            "live_trades_count": closed_n,
            "threshold": thresh,
            "status": "CHƯA CÓ LỆNH SAU MỐC SO SÁNH" if closed_n == 0 else "CHƯA ĐỦ MẪU",
            "signal_matched": int(since_sr.get("matched_count") or 0),
            "signal_missing": int(since_sr.get("missing_count") or 0),
            "signal_extra": int(since_sr.get("extra_count") or 0),
            "projected_lab_signals_since_compare_from": int(proj_23.get("projected_lab_signals_since_compare_from") or 0),
            "avg_slippage_bps": (data.get("summary_overall_stats") or {}).get("avg_entry_slippage_bps"),
            "live_equity": [],
            "mirror_equity": [],
            "combo_live": combo_live,
            "drift_causes": drift_causes,
            "vps_off_windows": vps_wins,
            "exit_change_markers": ["2026-11-15T09:45:05Z", data.get("compare_from_utc", "2026-11-22T04:00:00Z")],
        }

    return {
        "available": False,
        "verify_failed": False,
        "verify_errors": [],
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
        .nl-rec-box{background:#0f172a;border:1px solid #35445b;border-radius:12px;padding:10px 14px;margin:4px 0 14px;color:#cbd5e1;font-size:.82rem;line-height:1.5;font-variant-numeric:tabular-nums}
        .nl-footer{border-top:1px solid #263244;margin-top:18px;padding-top:10px;color:#94a3b8;font-size:.76rem}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _live_status_badge(live_n: int, threshold: int, has_reconcile: bool, drifted: bool = False, verify_failed: bool = False) -> tuple[str, str]:
    if verify_failed:
        return "LỖI XÁC MINH 17a", "nl-bad"
    if not has_reconcile:
        return all_signals.vi("ninlab_status_no_reconcile"), "nl-warn"
    if live_n == 0:
        return "CHƯA CÓ LỆNH SAU MỐC SO SÁNH", "nl-warn"
    if live_n < threshold:
        return all_signals.vi("ninlab_status_insufficient"), "nl-warn"
    if drifted:
        return all_signals.vi("ninlab_status_drift"), "nl-bad"
    return all_signals.vi("ninlab_status_synced"), "nl-good"


def _build_main_equity_chart(payload: dict[str, Any], rec: dict[str, Any]) -> go.Figure:
    eq = payload.get("series", {}).get("equity_mtm") or []
    timestamps_ms = payload.get("series", {}).get("timestamps_ms") or []
    run_info = payload.get("run_info", {})
    profile = str(run_info.get("cost_profile") or "BASE")
    tw = run_info.get("time_window_utc", {})
    start_s = pd.to_datetime(tw.get("start", "2023-11-01"), utc=True)
    end_s = pd.to_datetime(tw.get("end", "2026-10-31"), utc=True)
    if eq and len(timestamps_ms) == len(eq):
        x_dates = pd.to_datetime(timestamps_ms, unit="ms", utc=True)
    else:
        x_dates = pd.date_range(start_s, end_s, periods=max(len(eq), 2), tz="UTC") if eq else []

    fig = go.Figure()
    if eq:
        fig.add_trace(
            go.Scatter(
                x=x_dates,
                y=eq,
                mode="lines",
                name=f"Backtest {profile} (3 năm)",
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

    fig.update_layout(title=f"Đường vốn 3 năm (ALL_SIGNALS_PORTFOLIO · {profile}) & Đối soát Live")
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
    fig.update_xaxes(title_text="bps", range=[0, 5.5])
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


def _render_splits_and_yearly(payload: dict[str, Any]) -> None:
    breakdowns = payload.get("breakdowns") or {}
    splits = breakdowns.get("splits") or []
    yearly = breakdowns.get("yearly") or []
    rec_info = breakdowns.get("reconciliation") or {}
    profile = str(rec_info.get("profile") or payload.get("run_info", {}).get("cost_profile") or "BASE")

    col_sp, col_yr = st.columns([1.2, 1.0])
    with col_sp:
        sp_rows = ""
        for sp in splits:
            net_v = float(sp["net_pnl"])
            cls = "nl-good" if net_v >= 0 else "nl-bad"
            window_lbl = f"{sp['start'][:10]} → {sp['end'][:10]}"
            sp_rows += (
                f"<tr>"
                f"<td><strong>{html.escape(str(sp['split']))}</strong></td>"
                f"<td>{html.escape(window_lbl)}</td>"
                f"<td style='text-align:right'>{int(sp['executed_trades']):,}</td>"
                f"<td style='text-align:right' class='{cls}'>{net_v:+,.2f}</td>"
                f"<td style='text-align:right'>{float(sp['roi_pct']):+.2f}%</td>"
                f"<td style='text-align:right'>${float(sp['m2m_dd']):,.2f}</td>"
                f"<td style='text-align:right'>${float(sp['max_dd_closed']):,.2f}</td>"
                f"<td style='text-align:right'>{int(sp['peak_concurrent_legs'])}</td>"
                f"<td style='text-align:right'>{float(sp['win_rate_pct']):.1f}%</td>"
                f"<td style='text-align:right'>{float(sp['profit_factor']):.2f}</td>"
                f"</tr>"
            )
        st.markdown(
            f'<div style="font-weight:700;margin:4px 0 4px;color:#f8fafc">'
            f'Tách cửa sổ V3-18 (DEVELOPMENT / VALIDATION / HOLDOUT · {html.escape(profile)})</div>'
            f'<div class="nl-table-wrap" data-testid="ninlab-splits-table"><table class="nl-table"><thead><tr>'
            f'<th>Cửa sổ</th><th>Khoảng UTC</th><th style="text-align:right">Lệnh</th>'
            f'<th style="text-align:right">Net (USD)</th><th style="text-align:right">ROI</th>'
            f'<th style="text-align:right">M2M DD</th><th style="text-align:right">DD đóng</th>'
            f'<th style="text-align:right">Peak legs</th><th style="text-align:right">Thắng</th>'
            f'<th style="text-align:right">PF</th>'
            f'</tr></thead><tbody>{sp_rows}</tbody></table></div>',
            unsafe_allow_html=True,
        )

    with col_yr:
        yr_rows = ""
        for yr in yearly:
            net_v = float(yr["net_pnl"])
            cls = "nl-good" if net_v >= 0 else "nl-bad"
            window_lbl = f"{yr['start']} → {yr['end_exclusive']}"
            yr_rows += (
                f"<tr>"
                f"<td><strong>{html.escape(str(yr['period']))}</strong></td>"
                f"<td>{html.escape(window_lbl)}</td>"
                f"<td style='text-align:right'>{int(yr['executed_trades']):,}</td>"
                f"<td style='text-align:right' class='{cls}'>{net_v:+,.2f}</td>"
                f"<td style='text-align:right'>{float(yr['roi_pct']):+.2f}%</td>"
                f"<td style='text-align:right'>${float(yr['m2m_dd']):,.2f}</td>"
                f"<td style='text-align:right'>${float(yr['max_dd_closed']):,.2f}</td>"
                f"<td style='text-align:right'>{int(yr['peak_concurrent_legs'])}</td>"
                f"</tr>"
            )
        st.markdown(
            f'<div style="font-weight:700;margin:4px 0 4px;color:#f8fafc">'
            f'Tổng theo năm (Y1 / Y2 / Y3 · {html.escape(profile)})</div>'
            f'<div class="nl-table-wrap" data-testid="ninlab-yearly-table"><table class="nl-table"><thead><tr>'
            f'<th>Năm</th><th>Khoảng UTC</th><th style="text-align:right">Lệnh</th>'
            f'<th style="text-align:right">Net (USD)</th><th style="text-align:right">ROI</th>'
            f'<th style="text-align:right">M2M DD</th><th style="text-align:right">DD đóng</th>'
            f'<th style="text-align:right">Peak legs</th>'
            f'</tr></thead><tbody>{yr_rows}</tbody></table></div>',
            unsafe_allow_html=True,
        )

    plus44_net = float(rec_info.get("plus44_summary_net") or 0.0)
    combo_sum = float(rec_info.get("plus44_combo_own_entry_sum") or 0.0)
    splits_sum = float(rec_info.get("plus44_splits_sum") or 0.0)
    yearly_sum = float(rec_info.get("plus44_yearly_sum") or 0.0)
    delta_sum = float(rec_info.get("plus44_delta_vs_summary") or 0.0)
    prune23_net = rec_info.get("prune23_summary_net")
    prune23_trades = rec_info.get("prune23_trades")
    bingx_plus44 = rec_info.get("bingx_plus44_net")
    bingx_prune23 = rec_info.get("bingx_prune23_net")

    prune_txt = (
        f"PRUNE23 = <strong>{float(prune23_net):+,.2f} USD</strong> ({int(prune23_trades or 0):,} lệnh)"
        if prune23_net is not None
        else "PRUNE23 = —"
    )
    bingx_txt = (
        f" · So với BINGX_PORTFOLIO cũ: PLUS44 {float(bingx_plus44):+,.2f} (chênh {plus44_net - float(bingx_plus44):+,.2f})"
        + (
            f", PRUNE23 {float(bingx_prune23):+,.2f} (chênh {float(prune23_net) - float(bingx_prune23):+,.2f})"
            if bingx_prune23 is not None and prune23_net is not None
            else ""
        )
        if bingx_plus44 is not None
        else ""
    )
    st.markdown(
        f'<div class="nl-rec-box" data-testid="ninlab-reconciliation-box">'
        f'<strong>Đối chiếu <code>PORTFOLIO_SUMMARY_ALL_SIGNALS.csv</code> ({html.escape(profile)}):</strong> '
        f'PLUS43 = <strong>{plus44_net:+,.2f} USD</strong> · '
        f'Tổng 43 combo <code>pnl_own_entry</code> = <strong>{combo_sum:+,.2f} USD</strong> · '
        f'Tổng DEV+VAL+HOLDOUT = <strong>{splits_sum:+,.2f} USD</strong> · '
        f'Tổng Y1+Y2+Y3 = <strong>{yearly_sum:+,.2f} USD</strong> · '
        f'Chênh lệch kế toán = <span class="nl-good">{delta_sum:+.4f} USD</span> · '
        f'{prune_txt}{bingx_txt}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_drawer(combo_row: dict[str, Any], rec: dict[str, Any], profile: str) -> None:
    ck = str(combo_row.get("combo_key", ""))
    src_group = str(combo_row.get("source_group", ""))
    tf = str(combo_row.get("timeframe", ""))
    live_info = (rec.get("combo_live") or {}).get(ck)
    own_pnl = float((combo_row.get("own_entry_pnl") or {}).get("value") or 0.0)
    fees = float((combo_row.get("fees") or {}).get("value") or 0.0)
    funding = float((combo_row.get("funding") or {}).get("value") or 0.0)
    slippage = float((combo_row.get("slippage") or {}).get("value") or 0.0)
    entries = int((combo_row.get("entries") or {}).get("value") or 0)
    wr = float((combo_row.get("leg_win_rate_pct") or {}).get("value") or 0.0)
    pf = (combo_row.get("net_pf_leg") or {}).get("value")
    pf_s = f"{float(pf):.2f}" if isinstance(pf, (int, float)) else "—"
    dev_own = float((combo_row.get("dev_net_own") or {}).get("value") or 0.0)
    val_own = float((combo_row.get("val_net_own") or {}).get("value") or 0.0)
    hold_own = float((combo_row.get("holdout_net_own") or {}).get("value") or 0.0)
    y1_own = float((combo_row.get("y1_net_own") or {}).get("value") or 0.0)
    y2_own = float((combo_row.get("y2_net_own") or {}).get("value") or 0.0)
    y3_own = float((combo_row.get("y3_net_own") or {}).get("value") or 0.0)

    items = [
        ("Nhóm / Khung", "—", f"{src_group} · {tf}", "—", "EXACT"),
        ("Số lệnh khớp", f"{live_info['trades']}" if live_info else "0", f"{entries}", "—" if not live_info else f"{live_info['trades']-entries:+d}", "EXACT" if live_info else "UNRESOLVED"),
        ("Tỷ lệ thắng (own_entry)", f"{live_info['wr']:.1f}%" if live_info else "—", f"{wr:.1f}%", "—", "RECONCILED" if live_info else "UNRESOLVED"),
        ("Hệ số lợi nhuận (own_entry)", "—", pf_s, "—", "RECONCILED"),
        ("PnL entry riêng (pnl_own_entry)", f"${live_info['pnl']:,.2f}" if live_info else "—", f"${own_pnl:,.2f}", "—", "EXACT" if live_info else "UNRESOLVED"),
        ("DEV / VAL / HOLDOUT (own)", "—", f"{dev_own:+.2f} / {val_own:+.2f} / {hold_own:+.2f}", "—", "EXACT"),
        ("Y1 / Y2 / Y3 (own)", "—", f"{y1_own:+.2f} / {y2_own:+.2f} / {y3_own:+.2f}", "—", "EXACT"),
        ("Phí giao dịch", f"${live_info['fees']:,.2f}" if live_info else "—", f"${fees:,.2f}", "—", "EXACT" if live_info else "UNRESOLVED"),
        (f"Trượt giá ({profile})", "—", f"${slippage:,.2f}", "—", "ESTIMATED"),
        ("Funding (Signed 3Y)", "—", f"${funding:,.2f}", "—", "ESTIMATED"),
        ("Trạng thái đối soát", all_signals.vi("ninlab_status_no_reconcile") if not live_info else live_info["status"], "Sẵn sàng", "—", "EXACT" if live_info else "UNRESOLVED"),
    ]
    rows_html = ""
    for name, lv, sm, df_s, ev in items:
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

    # PLUS44 ALL_SIGNALS_PORTFOLIO (BASE & STRESS) is the canonical Lab source.
    runs = plus44_source.list_runs()
    if not runs:
        st.error(all_signals.vi("no_runs"))
        return

    h_col1, h_col2, h_col3 = st.columns([3.0, 1.5, 1.5])
    with h_col1:
        st.markdown('<div class="v3-brand">AUTOTRADE V3 · NINLAB TERMINAL</div>', unsafe_allow_html=True)
        st.subheader(all_signals.vi("ninlab_title"))
    with h_col2:
        run_ids = [r.run_id for r in runs]
        default_idx = next((i for i, run_id in enumerate(run_ids) if run_id.endswith("__BASE")), 0)
        sel_run_id = st.selectbox(
            all_signals.vi("ninlab_kpi_run_view"),
            run_ids,
            index=default_idx,
            label_visibility="collapsed",
        )
    with h_col3:
        st.caption("43 chiến lược (PRUNE23 + TV20, loại TV13) · ALL_SIGNALS_PORTFOLIO · PnL theo pnl_own_entry")

    selected = next(r for r in runs if r.run_id == sel_run_id)
    st.session_state["v3_16_selected_run"] = selected.run_id

    # Mandatory fail-closed verification on open
    verification = plus44_source.verify(selected)
    payload = plus44_source.load_payload(selected) if verification.passed else None
    if not verification.passed or payload is None:
        st.error(all_signals.vi("ninlab_hash_fail_banner"))
        st.error(f"{all_signals.vi('verify_fail')}: {verification.detail}")
        return

    freshness = load_freshness_info()
    rec = load_reconcile_17a()
    if rec.get("verify_failed"):
        st.error(f"LỖI XÁC MINH ĐỐI SOÁT 17a (ẩn số liệu 17a): {'; '.join(rec.get('verify_errors') or [])}")

    acc = payload.get("kpi", {}).get("account", {})
    risk = payload.get("kpi", {}).get("risk", {})
    metrics_dict = payload.get("metrics") or {}
    init_cap = float((acc.get("initial_capital") or {}).get("value") or 1000.0)
    final_eq = float((acc.get("final_equity_mtm") or {}).get("value") or init_cap)
    net_3y = final_eq - init_cap
    roi_3y = (net_3y / init_cap * 100.0) if init_cap > 0 else 0.0
    m2m_dd_usd = float((risk.get("m2m_max_dd_dollars") or {}).get("value") or 0.0)
    m2m_dd_pct = float((risk.get("m2m_max_dd_pct") or {}).get("value") or 0.0)
    closed_dd_usd = float((risk.get("closed_max_dd_dollars") or {}).get("value") or 0.0)
    executed_entries = int(metrics_dict.get("executed_trades") or selected.entry_count or 0)
    skipped_cnt = int(metrics_dict.get("skipped_signals") or 0)
    peak_legs = int(metrics_dict.get("peak_concurrent_legs") or 0)
    cost_rows = payload.get("breakdowns", {}).get("cost_sensitivity", [])
    be_bps = compute_breakeven_bps(cost_rows)
    be_txt = f"{be_bps:.2f} bps" if be_bps is not None else "—"

    live_n = int(rec.get("live_trades_count") or 0)
    thresh = int(rec.get("threshold") or LIVE_SAMPLE_THRESHOLD)
    rec_failed = bool(rec.get("verify_failed"))
    live_lbl, live_cls = _live_status_badge(live_n, thresh, bool(rec.get("available")), bool(rec.get("drifted", False)), verify_failed=rec_failed)
    proj_sig_n = int(rec.get("projected_lab_signals_since_compare_from") or 0)
    live_sub_txt = (
        "Ẩn số do lỗi xác minh"
        if rec_failed
        else f"{live_n} / {thresh} lệnh · Lab sau mốc: {proj_sig_n} tín hiệu · Live hiện phủ PRUNE23"
    )
    dot_cls = f"nl-dot-{freshness['status']}"

    # 1. Top Status Strip (6 tiles)
    st.markdown(
        f'<div class="nl-grid" data-testid="ninlab-top-strip">'
        f'<div class="nl-tile"><div class="nl-tile-lbl">Lãi ròng 3 năm ({html.escape(selected.cost_profile)})</div>'
        f'<div class="nl-tile-val {"nl-good" if net_3y>=0 else "nl-bad"}">{"+" if net_3y>=0 else "-"}${abs(net_3y):,.2f}</div>'
        f'<div class="nl-tile-sub">ROI {roi_3y:+.1f}% · {executed_entries:,} lệnh · skipped {skipped_cnt}</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_dd_m2m"))}</div>'
        f'<div class="nl-tile-val nl-warn">${m2m_dd_usd:,.2f}</div>'
        f'<div class="nl-tile-sub">{m2m_dd_pct:.2f}% đỉnh · DD đóng ${closed_dd_usd:,.2f} · Peak {peak_legs} legs</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_breakeven"))}</div>'
        f'<div class="nl-tile-val nl-cyan">{html.escape(be_txt)}</div>'
        f'<div class="nl-tile-sub">mỗi chiều (BASE 2,0 · STRESS 5,0 bps)</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_live_vs_lab"))}</div>'
        f'<div class="nl-tile-val {live_cls}" style="font-size:1.02rem">{html.escape(live_lbl)}</div>'
        f'<div class="nl-tile-sub">{html.escape(live_sub_txt)}</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("ninlab_kpi_freshness"))}</div>'
        f'<div class="nl-tile-val" style="font-size:1.02rem"><span class="nl-dot {dot_cls}"></span>{html.escape(freshness["overall_status"])}</div>'
        f'<div class="nl-tile-sub">{html.escape(freshness["collected_at"][:19].replace("T"," "))}Z</div></div>'
        f'<div class="nl-tile"><div class="nl-tile-lbl">{html.escape(all_signals.vi("verification_status"))}</div>'
        f'<div class="nl-tile-val {"nl-good" if selected.validation_status=="VERIFIED" else "nl-warn"}" style="font-size:1.08rem">● {html.escape(all_signals.validation_status_vi(selected.validation_status))}</div>'
        f'<div class="nl-tile-sub">Commit {html.escape(selected.git_commit[:8])} · {html.escape(selected.run_id)}</div></div>'
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

    # 2. Main Equity Chart
    st.plotly_chart(_build_main_equity_chart(payload, rec), use_container_width=True, config={"displayModeBar": False})

    # 3. DEV / VAL / HOLDOUT Splits + Yearly Breakdown + Reconciliation
    _render_splits_and_yearly(payload)

    # 4. NinLab Combo Table (pnl_own_entry only) + Optional Right Detail Drawer
    combos_raw = payload.get("breakdowns", {}).get("combos", [])
    sparks = plus44_source.combo_sparklines(selected.run_id)

    st.markdown(f"**{all_signals.vi('ninlab_table_title')}** · *Cột PnL hiển thị theo `pnl_own_entry` (entry riêng từng lệnh, không theo VWAP)*")
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
        filtered.sort(key=lambda x: float((x.get("own_entry_pnl") or {}).get("value") or 0.0), reverse=True)
    elif sort_f == all_signals.vi("ninlab_sort_pnl_asc"):
        filtered.sort(key=lambda x: float((x.get("own_entry_pnl") or {}).get("value") or 0.0))
    else:
        filtered.sort(key=lambda x: str(x.get("combo_key", "")))

    max_abs_pnl = max([abs(float((c.get("own_entry_pnl") or {}).get("value") or 0.0)) for c in combos_raw] or [1.0])

    def _render_combo_table() -> None:
        rows_html = ""
        for c in filtered:
            ck = str(c.get("combo_key", ""))
            src_group = str(c.get("source_group", ""))
            tf = str(c.get("timeframe", ""))
            entries = int((c.get("entries") or {}).get("value") or 0)
            pnl = float((c.get("own_entry_pnl") or {}).get("value") or 0.0)
            dev_v = float((c.get("dev_net_own") or {}).get("value") or 0.0)
            val_v = float((c.get("val_net_own") or {}).get("value") or 0.0)
            hold_v = float((c.get("holdout_net_own") or {}).get("value") or 0.0)
            pnl_cls = "nl-good" if pnl >= 0 else "nl-bad"
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
                f"<td>{html.escape(src_group)} · {html.escape(tf)}</td>"
                f"<td style='text-align:right'>{entries:,}</td>"
                f"<td style='text-align:right' class='{pnl_cls}'>{pnl:+,.2f}</td>"
                f"<td style='text-align:right'>{dev_v:+,.2f}</td>"
                f"<td style='text-align:right'>{val_v:+,.2f}</td>"
                f"<td style='text-align:right'>{hold_v:+,.2f}</td>"
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
            f"<th>Nhóm · Khung</th>"
            f"<th style='text-align:right'>Lệnh</th>"
            f"<th style='text-align:right'>PnL entry riêng (pnl_own_entry)</th>"
            f"<th style='text-align:right'>DEV (own)</th>"
            f"<th style='text-align:right'>VAL (own)</th>"
            f"<th style='text-align:right'>HOLDOUT (own)</th>"
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
                _render_drawer(sel_row, rec, selected.cost_profile)
                if st.button(all_signals.vi("ninlab_drawer_close"), use_container_width=True):
                    st.session_state["ninlab_drawer_sel"] = "—"
                    st.rerun()
    else:
        _render_combo_table()

    # 5. Two compact side-by-side panels below the table
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

    f_info, f_btn0, f_btn1, f_btn2 = st.columns([2.9, 1.1, 0.8, 1.2])
    tol_cfg = load_tolerances()
    sync_state = read_sync_state(DEFAULT_RECONCILE_DIR)
    last_sync_utc = sync_state.get("finished_at_utc") or freshness.get("collected_at")
    fresh_lvl, last_vn_str, fresh_desc = evaluate_sync_freshness(
        last_sync_utc,
        warn_hours=float(tol_cfg.get("sync_warn_hours", 6)),
        critical_hours=float(tol_cfg.get("sync_critical_hours", 24)),
    )
    fresh_cls = "nl-good" if fresh_lvl == "GREEN" else ("nl-warn" if fresh_lvl == "AMBER" else "nl-bad")

    if sync_state.get("status") == "SUCCESS":
        fin_ts = sync_state.get("finished_at_utc")
        if fin_ts and st.session_state.get("ninlab_last_reloaded_sync") != fin_ts:
            st.session_state["ninlab_last_reloaded_sync"] = fin_ts
            st.cache_data.clear()
            st.rerun()

    with f_info:
        sync_status_msg = str(sync_state.get("message_vi") or "")
        sync_badge_html = (
            f" · <span class='{'nl-good' if sync_state.get('status')=='SUCCESS' else ('nl-bad' if sync_state.get('status')=='ERROR' else 'nl-warn')}'>"
            f"{html.escape(sync_status_msg)}</span>"
            if sync_status_msg
            else ""
        )
        st.markdown(
            f'<div class="nl-footer">'
            f"<span class='{fresh_cls}'>{html.escape(RECONCILE_LABELS_VI['sync_last_at'].format(time=last_vn_str))} ({html.escape(fresh_desc)})</span>"
            f"{sync_badge_html}<br>"
            f"run_id: <code>{html.escape(selected.run_id)}</code> · "
            f"commit: <code>{html.escape(selected.git_commit[:12])}</code> · "
            f"sinh: <code>{html.escape(selected.generated_at_utc[:19])}</code> · "
            f"băm chi phí: <code>{html.escape(selected.cost_profile_hash[:12])}</code>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with f_btn0:
        if st.button(RECONCILE_LABELS_VI["sync_btn_label"], use_container_width=True):
            accepted, msg = trigger_manual_sync(
                repo_root=Path.cwd(),
                data_root=_resolve_bingx_data_root(),
                evidence_dir=DEFAULT_RECONCILE_DIR,
                cooldown_seconds=int(tol_cfg.get("sync_cooldown_seconds", 60)),
            )
            if accepted:
                st.info(msg)
                st.rerun()
            else:
                st.warning(msg)
    with f_btn1:
        if st.button(all_signals.vi("reload"), use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with f_btn2:
        st.download_button(
            all_signals.vi("export_ai_json"),
            data=plus44_source.payload_bytes(payload),
            file_name=f"{selected.run_id}_dashboard.json",
            mime="application/json",
            use_container_width=True,
        )
