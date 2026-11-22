"""Xuất báo cáo JSON, HTML một trang tiếng Việt ít chữ và manifest cho V3-18b."""
from __future__ import annotations

import hashlib
import html
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.research_v3_18.combos_30 import LIVE23_REGISTRY_REF, LIVE23_SOURCE_COMMIT
from autotrade_v3.research_v3_18.regime_features import load_research_config
from autotrade_v3.research_v3_18b import DEFAULT_CONFIG_PATH
from autotrade_v3.research_v3_18b.labels_vi import (
    LABELS_VI_18B,
    find_english_label_leaks_v3_18b,
)
from autotrade_v3.strategy.v3_lab_11 import V3Lab11Strategy
from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.tools.dataset_manifest import generate_dataset_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_DIR = PROJECT_ROOT / "evidence" / "v3-18b"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "KHÔNG_RÕ"


def _esc(val: Any) -> str:
    return html.escape(str(val), quote=True)


def _pnl_cls(val: float) -> str:
    if float(val) > 1e-9:
        return "pos"
    if float(val) < -1e-9:
        return "neg"
    return "zero"


def _fmt_pct(val: float | None) -> str:
    return "—" if val is None else f"{float(val):.1f}%"


def _heat_style(cell: dict[str, Any]) -> str:
    if not bool(cell.get("conclusive")):
        return "background: rgba(148,163,184,0.10); color: #94a3b8;"
    pnl = float(cell.get("net_pnl_usd", 0.0))
    alpha = min(0.55, max(0.14, abs(pnl) / 30.0))
    if pnl > 0.0:
        return f"background: rgba(16,185,129,{alpha:.2f}); color: #ecfdf5;"
    if pnl < 0.0:
        return f"background: rgba(239,68,68,{alpha:.2f}); color: #fef2f2;"
    return "background: rgba(148,163,184,0.15); color: #cbd5e1;"


def _render_trials_rows(rows: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for p in rows:
        m = p["metrics"]
        d = p["delta_vs_baseline"]
        st = p["stats_test"]
        row_cls = "row-cand" if p["is_candidate_improved"] else ""
        badge = (
            "<span class='badge badge-cand'>VƯỢT CẢ 2 KỲ</span>"
            if p["is_candidate_improved"]
            else "<span class='badge badge-muted'>CHƯA ĐẠT 2 KỲ</span>"
        )
        m_dev = m["DEVELOPMENT"]
        m_val = m["VALIDATION"]
        m_hold = m["HOLDOUT"]
        m_all = m["ALL"]
        top_txt = f"{_esc(m_all['top_combo_key'] or '—')} ({_fmt_pct(m_all['max_single_combo_share_pct'])})"
        p_txt = f"{st['val_p_holm']:.4f} / {st['holdout_p_holm']:.4f}"
        out.append(
            f"<tr class='{row_cls}'>"
            f"<td class='mono'>{_esc(p['trial_id'])}<div class='sub-desc'>{_esc(p['build_status_vi'])}</div></td>"
            f"<td>{badge}</td>"
            f"<td class='num'>{m_all['so_lenh']}</td>"
            f"<td class='num {_pnl_cls(d['DEVELOPMENT_net_pnl_usd'])}'>{m_dev['net_pnl_usd']:+.2f} ({d['DEVELOPMENT_net_pnl_usd']:+.2f})</td>"
            f"<td class='num {_pnl_cls(d['VALIDATION_net_pnl_usd'])}'>{m_val['net_pnl_usd']:+.2f} ({d['VALIDATION_net_pnl_usd']:+.2f})</td>"
            f"<td class='num {_pnl_cls(d['HOLDOUT_net_pnl_usd'])}'>{m_hold['net_pnl_usd']:+.2f} ({d['HOLDOUT_net_pnl_usd']:+.2f})</td>"
            f"<td class='num bold {_pnl_cls(d['ALL_net_pnl_usd'])}'>{m_all['net_pnl_usd']:+.2f} ({d['ALL_net_pnl_usd']:+.2f})</td>"
            f"<td class='num'>{m_all['m2m_max_dd_usd']:.2f} ({m_val['m2m_max_dd_usd']:.2f}/{m_hold['m2m_max_dd_usd']:.2f})</td>"
            f"<td class='num'>{m_all['max_concurrent_positions']}</td>"
            f"<td class='mono'>{top_txt}</td>"
            f"<td class='mono num'>{_esc(p_txt)}</td>"
            "</tr>"
        )
    return "".join(out)


def _render_equity_svg(
    trials: list[dict[str, Any]],
    splits_cfg: dict[str, Any],
    candidate_ids: list[str],
) -> str:
    by_id = {t["trial_id"]: t for t in trials}
    picks: list[str] = ["LAB11_PRUNE23_CO_SO"]
    for cid in candidate_ids[:3]:
        if cid not in picks:
            picks.append(cid)
    for extra_id in ("LOC_BO_PHIEN_MY", "LOC_BO_GAN_VUNG_CUNG_CAU_NGUOC_CHIEU", "LOC_KET_HOP_BO_PHIEN_MY_VA_VUNG_NGUOC"):
        if extra_id in by_id and extra_id not in picks and len(picks) < 5:
            picks.append(extra_id)

    curves = [by_id[k] for k in picks if k in by_id and by_id[k].get("equity_curve")]
    if not curves:
        return "<div class='muted'>Không có dữ liệu đường vốn.</div>"

    all_ts = [int(p["timestamp_ms"]) for c in curves for p in c["equity_curve"]]
    all_eq = [float(p["equity"]) for c in curves for p in c["equity_curve"]]
    t_min, t_max = min(all_ts), max(all_ts)
    e_min, e_max = min(all_eq), max(all_eq)
    if t_max <= t_min:
        t_max = t_min + 1
    pad = max(5.0, (e_max - e_min) * 0.08)
    y_lo, y_hi = e_min - pad, e_max + pad

    w, h = 1120, 310
    ml, mr, mt, mb = 64, 20, 22, 32
    pw, ph = w - ml - mr, h - mt - mb

    def sx(ts: int) -> float:
        return ml + ((ts - t_min) / (t_max - t_min)) * pw

    def sy(eq: float) -> float:
        return mt + ph - ((eq - y_lo) / (y_hi - y_lo)) * ph

    parts = [f"<svg viewBox='0 0 {w} {h}' class='equity-svg' role='img' aria-label='Đường vốn sổ chung'>"]
    split_styles = (
        ("DEVELOPMENT", "rgba(56,189,248,0.06)", "PHÁT_TRIỂN (24T)"),
        ("VALIDATION", "rgba(251,191,36,0.07)", "KIỂM_ĐỊNH (6T)"),
        ("HOLDOUT", "rgba(167,139,250,0.08)", "KIỂM_CHỨNG_CUỐI (6T)"),
    )
    for k, fill, lbl in split_styles:
        b = splits_cfg[k]
        x0 = max(float(ml), min(float(w - mr), sx(int(b["start_ms"]))))
        x1 = max(float(ml), min(float(w - mr), sx(int(b["end_ms"]))))
        if x1 > x0:
            parts.append(f"<rect x='{x0:.1f}' y='{mt}' width='{x1 - x0:.1f}' height='{ph}' fill='{fill}'/>")
            parts.append(f"<text x='{(x0 + x1) / 2:.1f}' y='{mt + 13}' text-anchor='middle' class='svg-split-lbl'>{_esc(lbl)}</text>")

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        yv = y_lo + frac * (y_hi - y_lo)
        yp = sy(yv)
        parts.append(f"<line x1='{ml}' y1='{yp:.1f}' x2='{w - mr}' y2='{yp:.1f}' stroke='rgba(148,163,184,0.16)'/>")
        parts.append(f"<text x='{ml - 8}' y='{yp + 4:.1f}' text-anchor='end' class='svg-axis-lbl'>{yv:.0f}</text>")

    palette = ["#38bdf8", "#10b981", "#f59e0b", "#c084fc", "#f472b6"]
    legend: list[str] = []
    for i, c in enumerate(curves):
        col = palette[i % len(palette)]
        pts = " ".join(f"{sx(int(p['timestamp_ms'])):.1f},{sy(float(p['equity'])):.1f}" for p in c["equity_curve"])
        parts.append(f"<polyline fill='none' stroke='{col}' stroke-width='2.1' points='{pts}'/>")
        end_v = float(c["equity_curve"][-1]["equity"])
        legend.append(f"<span class='legend-item'><i style='background:{col}'></i>{_esc(c['trial_id'])} ({end_v:.2f} USD)</span>")

    parts.append(f"<text x='{ml}' y='{h - 8}' class='svg-axis-lbl'>2023-11-01</text>")
    parts.append(f"<text x='{w - mr}' y='{h - 8}' text-anchor='end' class='svg-axis-lbl'>2026-10-31</text></svg>")
    return "\n".join(parts) + f"<div class='legend-row'>{''.join(legend)}</div>"


def render_v3_18b_html(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Kết xuất HTML báo cáo một trang tiếng Việt cho V3-18b."""
    cfg = report["config"]
    parity = report["parity_v3_16"]
    combo_ana = report["combo_regime_analysis_23"]
    exps = report["experiments"]
    trials = exps["trials"]
    cands = exps["candidate_watchlist"]
    m_tests = int(exps["total_trials_m"])
    alpha = float(exps["family_wise_alpha"])
    alpha_min = float(exps["holm_strictest_threshold"])
    min_n = int(cfg["statistics"]["min_sample_size"])

    warn_txt = LABELS_VI_18B["selection_bias_warning"].format(
        m_tests=m_tests,
        alpha=alpha,
        alpha_min=alpha_min,
    )

    ref_s = parity["v3_16_ref_summary"]
    rep_s = parity["reproduced_full_bingx"]
    rec_s = parity["reconciliation_v3_18_vs_v3_16"]
    eff = rec_s["effects_breakdown_usd"]
    base_trial = next(t for t in trials if t["is_baseline"])
    base_m = base_trial["metrics"]

    rows_23_html: list[str] = []
    for row in combo_ana["per_combo"]:
        cat = row["catalog"]
        o_all = row["overall_all"]
        o_sp = row["overall_by_split"]
        rows_23_html.append(
            "<tr>"
            f"<td class='mono'>{_esc(cat['combo_symbol'])}</td>"
            f"<td class='mono'>{_esc(cat['timeframe'])}</td>"
            f"<td>{_esc(cat['directions_lab11'])}</td>"
            f"<td>{_esc(cat['lab11_upgrade_vi'])}</td>"
            f"<td class='mono'>{_esc(cat['layer2'])}</td>"
            f"<td class='mono'>{cat['tp_pct']*100:.2f}% / {cat['sl_pct']*100:.2f}%</td>"
            f"<td class='num'>{o_all['so_lenh']}</td>"
            f"<td class='num {_pnl_cls(o_sp['DEVELOPMENT']['net_pnl_usd'])}'>{o_sp['DEVELOPMENT']['net_pnl_usd']:+.2f}</td>"
            f"<td class='num {_pnl_cls(o_sp['VALIDATION']['net_pnl_usd'])}'>{o_sp['VALIDATION']['net_pnl_usd']:+.2f}</td>"
            f"<td class='num {_pnl_cls(o_sp['HOLDOUT']['net_pnl_usd'])}'>{o_sp['HOLDOUT']['net_pnl_usd']:+.2f}</td>"
            f"<td class='num bold {_pnl_cls(o_all['net_pnl_usd'])}'>{o_all['net_pnl_usd']:+.2f}</td>"
            f"<td class='num'>{o_all['pf'] if o_all['pf'] is not None else '—'}</td>"
            f"<td class='num'>{o_all['max_dd_usd']:.2f}</td>"
            "</tr>"
        )

    reg_order = list(combo_ana["regime_order"])
    hm_hdr = "".join(f"<th>{_esc(r)}</th>" for r in reg_order)
    hm_rows: list[str] = []
    for row in combo_ana["per_combo"]:
        cat = row["catalog"]
        cells = [f"<td class='mono sticky-col'>{_esc(cat['combo_symbol'])}</td>"]
        for rname in reg_order:
            c = row["by_regime"].get(rname)
            if not c:
                cells.append("<td class='hm-cell muted'>0 lệnh</td>")
                continue
            st = _heat_style(c)
            n = int(c["so_lenh"])
            pnl = float(c["net_pnl_usd"])
            if not c["conclusive"]:
                cells.append(
                    f"<td class='hm-cell' style='{st}'><div class='hm-insuff'>CHƯA ĐỦ MẪU</div>"
                    f"<div class='hm-sub'>{n} lệnh · {pnl:+.2f}</div></td>"
                )
            else:
                cells.append(
                    f"<td class='hm-cell' style='{st}'><div class='hm-pnl'>{pnl:+.2f}</div>"
                    f"<div class='hm-sub'>{n} lệnh · {_fmt_pct(c['ty_le_thang_pct'])}</div></td>"
                )
        hm_rows.append(f"<tr>{''.join(cells)}</tr>")

    add_rows = [t for t in trials if t["group_type"] == "THEM_7"]
    drop_rows = [t for t in trials if t["group_type"] == "BOT_23"]
    filt_rows = [t for t in trials if t["group_type"] == "LOC_3"]

    cand_html = (
        "".join(
            "<div class='cand-card'>"
            f"<div class='cand-title'>{_esc(c['trial_id'])} ({_esc(c['group_label_vi'])})</div>"
            f"<div class='cand-stats'>KIỂM_ĐỊNH: <b>{c['val_net_pnl_usd']:+.2f} USD</b> (chênh {c['val_delta_usd']:+.2f}) · "
            f"KIỂM_CHỨNG_CUỐI: <b>{c['holdout_net_pnl_usd']:+.2f} USD</b> (chênh {c['holdout_delta_usd']:+.2f}) · "
            f"Toàn kỳ 36T: <b>{c['all_net_pnl_usd']:+.2f} USD</b> · Đồng thời tối đa: <b>{c['max_concurrent_positions']}</b> · "
            f"Tập trung 1 mã: <b>{_fmt_pct(c['max_single_combo_share_pct'])}</b></div>"
            f"<div class='cand-reason'>{_esc(c['reason_vi'])}</div>"
            "</div>"
            for c in cands
        )
        if cands
        else "<div class='cand-empty'>Chưa thấy bằng chứng cấu hình nào đồng thời vượt cơ sở Lab 11 trên cả KIỂM_ĐỊNH và KIỂM_CHỨNG_CUỐI trong ngưỡng sụt giảm M2M tối đa 1,25x.</div>"
    )

    live_ph = report["live_comparison_placeholder"]
    eq_svg = _render_equity_svg(trials, cfg["splits"], [str(c["trial_id"]) for c in cands])

    tbl_head = (
        "<thead><tr><th>Thử nghiệm</th><th>Trạng thái 2 kỳ</th><th class='num'>Lệnh</th>"
        "<th class='num'>PHÁT_TRIỂN (Chênh)</th><th class='num'>KIỂM_ĐỊNH (Chênh)</th><th class='num'>KIỂM_CHỨNG_CUỐI (Chênh)</th>"
        "<th class='num'>36T (Chênh)</th><th class='num'>DD 36T (KĐ/CC)</th>"
        "<th class='num'>Mở đồng thời</th><th>Tập trung 1 mã</th><th class='num'>p Holm (KĐ/CC)</th></tr></thead>"
    )

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_esc(LABELS_VI_18B['page_title'])}</title>
  <style>
    :root {{ --bg:#0b111b; --card:#121a28; --border:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --accent:#38bdf8; --ok:#10b981; --bad:#ef4444; --warn:#f59e0b; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:20px; background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; font-size:13px; line-height:1.45; }}
    .wrap {{ max-width: 1480px; margin: 0 auto; }}
    .hdr {{ margin-bottom:16px; padding:16px 20px; background:var(--card); border:1px solid var(--border); border-radius:10px; }}
    .hdr h1 {{ margin:0 0 6px 0; font-size:20px; color:#f8fafc; }}
    .sub {{ color:var(--muted); font-size:12px; }}
    .warn-box {{ margin-top:10px; padding:10px 14px; background:rgba(245,158,11,0.10); border-left:3px solid var(--warn); border-radius:4px; color:#fde68a; font-size:12px; }}
    .note-box {{ margin-top:8px; padding:8px 12px; background:rgba(56,189,248,0.08); border-left:3px solid var(--accent); border-radius:4px; color:#bae6fd; font-size:12px; }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; margin-bottom:16px; }}
    .kpi {{ background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px 14px; }}
    .kpi-lbl {{ color:var(--muted); font-size:11px; margin-bottom:4px; }}
    .kpi-val {{ font-size:17px; font-weight:700; font-variant-numeric:tabular-nums; }}
    .card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 16px; margin-bottom:16px; }}
    .card h2 {{ margin:0 0 10px 0; font-size:15px; color:#f8fafc; }}
    .tbl-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:12px; }}
    th,td {{ padding:6px 8px; border-bottom:1px solid var(--border); text-align:left; vertical-align:middle; }}
    th {{ color:#cbd5e1; background:#0f172a; font-weight:600; }}
    .mono {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:11px; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .bold {{ font-weight:700; }}
    .pos {{ color:#34d399; }}
    .neg {{ color:#f87171; }}
    .zero {{ color:var(--muted); }}
    .muted {{ color:var(--muted); }}
    .badge {{ display:inline-block; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:600; }}
    .badge-cand {{ background:rgba(16,185,129,0.22); color:#34d399; }}
    .badge-muted {{ background:rgba(100,116,139,0.20); color:#94a3b8; }}
    .row-cand {{ background:rgba(16,185,129,0.07); }}
    .sub-desc {{ font-size:10px; color:var(--muted); }}
    .hm-cell {{ text-align:center; min-width:112px; border:1px solid #0b111b; padding:5px 6px; }}
    .hm-pnl {{ font-weight:700; font-size:12px; }}
    .hm-sub {{ font-size:10px; opacity:0.9; }}
    .hm-insuff {{ font-size:10px; font-style:italic; font-weight:600; }}
    .sticky-col {{ position:sticky; left:0; background:#0f172a; z-index:2; white-space:nowrap; }}
    .equity-svg {{ width:100%; height:auto; display:block; background:#0e1624; border-radius:6px; }}
    .svg-split-lbl {{ fill:#94a3b8; font-size:11px; font-weight:600; }}
    .svg-axis-lbl {{ fill:#94a3b8; font-size:10px; font-family:ui-monospace,monospace; }}
    .legend-row {{ display:flex; flex-wrap:wrap; gap:14px; margin-top:8px; font-size:11px; color:#cbd5e1; }}
    .legend-item {{ display:inline-flex; align-items:center; gap:6px; }}
    .legend-item i {{ display:inline-block; width:12px; height:3px; border-radius:2px; }}
    .cand-card {{ padding:10px 14px; border:1px solid rgba(16,185,129,0.35); background:rgba(16,185,129,0.07); border-radius:8px; margin-bottom:8px; }}
    .cand-title {{ font-weight:700; color:#6ee7b7; margin-bottom:4px; }}
    .cand-stats {{ font-size:12px; margin-bottom:4px; }}
    .cand-reason {{ font-size:12px; color:#cbd5e1; }}
    .cand-empty {{ padding:12px; background:rgba(148,163,184,0.08); border-radius:8px; color:#cbd5e1; }}
    .meta-foot {{ font-size:11px; color:var(--muted); display:flex; flex-wrap:wrap; gap:16px; }}
  </style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>{_esc(LABELS_VI_18B['page_title'])}</h1>
    <div class="sub">{_esc(LABELS_VI_18B['subtitle'])} · SHA: <span class="mono">{_esc(manifest['git_commit'][:12])}</span></div>
    <div class="warn-box">{_esc(warn_txt)}</div>
    <div class="note-box">Ghi chú tách biệt nguồn: Số liệu 23 mã bên dưới hoàn toàn thuộc phiên bản <b>V3_LAB_11_PRUNE23@11.0.0</b> (chế độ sổ chung kiểu BingX + funding); bảng 30 mã ở V3-18 thuộc <b>đặc tả 20FINAL không lọc</b> và không dùng làm số live.</div>
  </div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-lbl">Cơ sở duy nhất</div><div class="kpi-val">23 mã Lab 11</div></div>
    <div class="kpi"><div class="kpi-lbl">Đối khớp V3-16 (tới 22/11)</div><div class="kpi-val">{rep_s['net_pnl_usd']:+.2f} USD</div></div>
    <div class="kpi"><div class="kpi-lbl">Cơ sở 36T (3Y khóa)</div><div class="kpi-val">{base_m['ALL']['net_pnl_usd']:+.2f} USD</div></div>
    <div class="kpi"><div class="kpi-lbl">Lệnh khớp 36T / DD</div><div class="kpi-val">{base_m['ALL']['so_lenh']} / {base_m['ALL']['m2m_max_dd_usd']:.2f} USD</div></div>
    <div class="kpi"><div class="kpi-lbl">Tổng số phép thử</div><div class="kpi-val">{m_tests} (7+23+3)</div></div>
    <div class="kpi"><div class="kpi-lbl">Vượt cả 2 kỳ / Mẫu ô</div><div class="kpi-val">{len(cands)} cấu hình / &ge;{min_n}</div></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_parity'])}</h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Mục đối khớp / Phân rã</th><th>Chế độ</th><th>Cửa sổ thời gian</th><th>Funding</th><th class='num'>Tín hiệu</th><th class='num'>Lệnh khớp</th><th class='num'>Bỏ qua</th><th class='num'>Lãi ròng (USD)</th><th class='num'>Khớp chuẩn</th></tr></thead>
        <tbody>
          <tr><td>Run gốc V3-16 (V3_LAB_11_PRUNE23_BASE)</td><td>Sổ chung kiểu BingX</td><td>2023-11-01..2026-11-22</td><td>Có ({ref_s['funding_net_cashflow_usd']:+.2f} USD)</td><td class='num'>{ref_s['total_signals']}</td><td class='num'>{ref_s['accepted_entries']}</td><td class='num'>{ref_s['skipped_signals']}</td><td class='num bold pos'>{ref_s['net_pnl_usd']:+.4f}</td><td class='mono'>Chuẩn gốc</td></tr>
          <tr><td>Dựng lại V3-18b toàn kỳ forward</td><td>Sổ chung kiểu BingX</td><td>2023-11-01..2026-11-22</td><td>Có ({rep_s['funding_net_cashflow_usd']:+.2f} USD)</td><td class='num'>{rep_s['total_signals']}</td><td class='num'>{rep_s['accepted_entries']}</td><td class='num'>{rep_s['skipped_signals']}</td><td class='num bold pos'>{rep_s['net_pnl_usd']:+.4f}</td><td class='mono'>Lệch {rep_s['net_pnl_diff_usd']:.4f} USD</td></tr>
          <tr><td>Số V3-18 (nhánh trước, mọi tín hiệu)</td><td>Chấp nhận mọi tín hiệu</td><td>2023-11-01..2026-11-01</td><td>Không</td><td class='num'>{rec_s['v3_18_all_signals_3y_no_funding']['signals']}</td><td class='num'>{rec_s['v3_18_all_signals_3y_no_funding']['trades']}</td><td class='num'>0</td><td class='num'>{rec_s['v3_18_all_signals_3y_no_funding']['net_pnl_usd']:+.4f}</td><td class='mono'>Điểm đầu</td></tr>
          <tr><td>Chuyển sang sổ chung kiểu BingX (chưa funding)</td><td>Sổ chung kiểu BingX</td><td>2023-11-01..2026-11-01</td><td>Không</td><td class='num'>{rec_s['bingx_3y_no_funding']['signals']}</td><td class='num'>{rec_s['bingx_3y_no_funding']['trades']}</td><td class='num'>{rec_s['bingx_3y_no_funding']['skipped_signals']}</td><td class='num'>{rec_s['bingx_3y_no_funding']['net_pnl_usd']:+.4f}</td><td class='mono'>{eff['effect_mode_bingx_gating_3y']:+.2f} USD</td></tr>
          <tr><td><b>Cơ sở V3-18b (36T khóa + funding)</b></td><td>Sổ chung kiểu BingX</td><td>2023-11-01..2026-11-01</td><td>Có ({rec_s['v3_18b_baseline_3y_with_funding']['funding_net_cashflow_usd']:+.2f} USD)</td><td class='num'>{rec_s['v3_18b_baseline_3y_with_funding']['signals']}</td><td class='num'>{rec_s['v3_18b_baseline_3y_with_funding']['trades']}</td><td class='num'>{rec_s['v3_18b_baseline_3y_with_funding']['skipped_signals']}</td><td class='num bold pos'>{rec_s['v3_18b_baseline_3y_with_funding']['net_pnl_usd']:+.4f}</td><td class='mono'>Funding {eff['effect_funding_3y']:+.2f} / Fwd {eff['effect_forward_2026_11_01_to_22']:+.2f}</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_combos_23'])}</h2>
    <div class="tbl-wrap"><table><thead><tr><th>Mã cặp</th><th>Khung</th><th>Chiều Lab 11</th><th>Nâng cấp Lab 11</th><th>Lớp 2</th><th>TP / SL</th><th class='num'>Lệnh 36T</th><th class='num'>PHÁT_TRIỂN</th><th class='num'>KIỂM_ĐỊNH</th><th class='num'>KIỂM_CHỨNG_CUỐI</th><th class='num'>Toàn kỳ 36T</th><th class='num'>Hệ số lời/lỗ</th><th class='num'>DD đóng (USD)</th></tr></thead><tbody>{''.join(rows_23_html)}</tbody></table></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_heatmap_23'])}</h2>
    <div class="tbl-wrap"><table><thead><tr><th class='sticky-col'>Mã Lab 11</th>{hm_hdr}</tr></thead><tbody>{''.join(hm_rows)}</tbody></table></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_add_7'])}</h2>
    <div class="tbl-wrap"><table>{tbl_head}<tbody>{_render_trials_rows(add_rows)}</tbody></table></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_drop_23'])}</h2>
    <div class="tbl-wrap"><table>{tbl_head}<tbody>{_render_trials_rows(drop_rows)}</tbody></table></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_filters_3'])}</h2>
    <div class="tbl-wrap"><table>{tbl_head}<tbody>{_render_trials_rows(filt_rows)}</tbody></table></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_equity'])}</h2>
    {eq_svg}
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_candidates'])}</h2>
    {cand_html}
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI_18B['sec_live_placeholder'])}</h2>
    <div class="sub">{_esc(live_ph['note_vi'])} · Mốc so sánh: <span class="mono">{_esc(live_ph['compare_from_utc'])}</span> · Trạng thái: <span class="mono">{_esc(live_ph['status'])}</span></div>
  </div>

  <div class="card meta-foot">
    <span>SHA cấu hình: <b class="mono">{_esc(manifest['config_canonical_sha256'][:16])}</b></span>
    <span>SHA dữ liệu: <b class="mono">{_esc(manifest['dataset']['dataset_hash'][:16])}</b></span>
    <span>SHA báo cáo JSON: <b class="mono">{_esc(manifest['report_canonical_sha256'][:16])}</b></span>
    <span>SHA đặc tả Lab 11: <b class="mono">{_esc(manifest['lab11_prune23_spec_hash'][:16])}</b></span>
  </div>
</div>
</body>
</html>
"""


def write_v3_18b_artifacts(
    *,
    ctx: dict[str, Any],
    parity_res: dict[str, Any],
    combo_analysis: dict[str, Any],
    experiments_res: dict[str, Any],
    evidence_dir: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Ghi `lab11_report.json`, `lab11_report.html` và `manifest.json` ra thư mục minh chứng V3-18b."""
    out_dir = evidence_dir or DEFAULT_EVIDENCE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    cfg = ctx["config"]
    strat_prune23: V3Lab11Prune23Strategy = ctx["strat_prune23"]
    strat_all30: V3Lab11Strategy = ctx["strat_all30"]

    parity_clean = {k: v for k, v in parity_res.items() if k != "baseline_3y_sim_result"}
    combo_clean = {k: v for k, v in combo_analysis.items() if k != "annotated_trades"}

    live_placeholder = {
        "compare_from_utc": str(cfg["dataset"]["live_compare_from_utc"]),
        "status": "CHỜ_DỮ_LIỆU_LIVE_SAU_NÀY",
        "live_orders_observed": None,
        "live_net_pnl_usd": None,
        "note_vi": "Khung giữ chỗ ngoại tuyến để ghép dữ liệu khớp lệnh thực tế trên BingX từ mốc compare_from trở đi ở vòng đối soát tiếp theo (không gọi Supabase/BingX trong bước này).",
    }

    report_body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "research_id": cfg["research_id"],
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": cfg,
        "source_commits": {
            "live23_commit": LIVE23_SOURCE_COMMIT,
            "live23_registry": LIVE23_REGISTRY_REF,
            "head_at_run": _git_sha(),
        },
        "v3_18_old_table_notice_vi": (
            "Các bảng 30 mã ở V3-18 thuộc đặc tả 20FINAL không lọc (lối thoát/độ trễ/bộ lọc khác với live); "
            "mọi số theo mã trong V3-18b đều là phiên bản V3_LAB_11_PRUNE23@11.0.0 chế độ sổ chung kiểu BingX + funding."
        ),
        "parity_v3_16": parity_clean,
        "combo_regime_analysis_23": combo_clean,
        "experiments": experiments_res,
        "live_comparison_placeholder": live_placeholder,
    }
    report_sha256 = canonical_json_sha256(report_body)
    full_report = {**report_body, "report_sha256": report_sha256}

    report_path = out_dir / "lab11_report.json"
    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_file_sha256 = _file_sha256(report_path)

    dl = DataLayer()
    ds_manifest = generate_dataset_manifest()
    data_manifest = dl.get_manifest()

    manifest_pre_html: dict[str, Any] = {
        "schema_version": "1.0.0",
        "research_id": cfg["research_id"],
        "git_commit": _git_sha(),
        "dataset": {
            "dataset_hash": ds_manifest["dataset_hash"],
            "data_manifest_hash": data_manifest.data_manifest_hash,
        },
        "config_path": "autotrade_v3/research_v3_18b/config.json",
        "config_canonical_sha256": canonical_json_sha256(load_research_config(cfg_path)),
        "config_file_sha256": _file_sha256(cfg_path),
        "lab11_prune23_spec_hash": strat_prune23.get_spec_hash(),
        "lab11_all30_spec_hash": strat_all30.get_spec_hash(),
        "report_path": "lab11_report.json",
        "report_canonical_sha256": report_sha256,
        "report_file_sha256": report_file_sha256,
        "html_path": "lab11_report.html",
        "live_23_count": len(combo_clean["per_combo"]),
        "total_trials_m": int(experiments_res["total_trials_m"]),
        "counts_by_group": experiments_res["counts_by_group"],
        "split_bounds": cfg["splits"],
    }

    html_text = render_v3_18b_html(full_report, manifest_pre_html)
    leaks = find_english_label_leaks_v3_18b(html_text)
    if leaks:
        raise ValueError(f"Phát hiện nhãn tiếng Anh rò rỉ trong HTML V3-18b: {leaks}")

    html_path = out_dir / "lab11_report.html"
    html_path.write_text(html_text, encoding="utf-8")
    html_file_sha256 = _file_sha256(html_path)

    manifest = {**manifest_pre_html, "html_file_sha256": html_file_sha256}
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "report_path": str(report_path),
        "html_path": str(html_path),
        "manifest_path": str(manifest_path),
        "report_sha256": report_sha256,
        "html_sha256": html_file_sha256,
    }
