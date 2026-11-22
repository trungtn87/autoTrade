"""Xuất báo cáo JSON, HTML một trang tiếng Việt và manifest cho V3-18."""
from __future__ import annotations

import hashlib
import html
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.research_v3_18.combos_30 import (
    CROSS30_PR124_ANCESTOR,
    CROSS30_RUN_SOURCE_COMMIT,
    CROSS30_SOURCE_COMMIT,
    LIVE23_REGISTRY_REF,
    LIVE23_SOURCE_COMMIT,
    CrossSymbol30FixedStrategy,
)
from autotrade_v3.research_v3_18.labels_vi import LABELS_VI, find_english_label_leaks
from autotrade_v3.research_v3_18.regime_features import DEFAULT_CONFIG_PATH, load_research_config
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.tools.dataset_manifest import generate_dataset_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_DIR = PROJECT_ROOT / "evidence" / "v3-18"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _current_git_sha() -> str:
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


def _fmt_usd(val: float | None, *, signed: bool = True) -> str:
    if val is None:
        return "—"
    v = float(val)
    if signed:
        return f"{v:+.2f} USD"
    return f"{v:.2f} USD"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{float(val):.1f}%"


def _fmt_ci(ci: list[float] | None) -> str:
    if not ci or len(ci) != 2:
        return "—"
    return f"[{ci[0]:+.2f}, {ci[1]:+.2f}]"


def _heat_cell_style(cell: dict[str, Any]) -> str:
    if not bool(cell.get("conclusive")):
        return "background: rgba(148,163,184,0.10); color: #94a3b8;"
    pnl = float(cell.get("net_pnl_usd", 0.0))
    intensity = min(0.55, max(0.12, abs(pnl) / 40.0))
    if pnl > 0.0:
        return f"background: rgba(16,185,129,{intensity:.2f}); color: #ecfdf5;"
    if pnl < 0.0:
        return f"background: rgba(239,68,68,{intensity:.2f}); color: #fef2f2;"
    return "background: rgba(148,163,184,0.15); color: #cbd5e1;"


def _render_equity_svg(
    portfolios: list[dict[str, Any]],
    splits_cfg: dict[str, Any],
    candidate_ids: set[str],
) -> str:
    """Vẽ biểu đồ đường vốn SVG đa chuỗi trên 3 giai đoạn thời gian."""
    preferred = [
        "DANH_MỤC_23_MÃ_LIVE_CHÉO_30",
        "DANH_MỤC_30_MÃ_ĐẦY_ĐỦ",
        "DANH_MỤC_23_LAB11_PRUNE23_CHUẨN",
        "DANH_MỤC_23_LIVE_LỌC_BỎ_CHẾ_ĐỘ_XẤU_DEV",
        "DANH_MỤC_30_MÃ_LỌC_CHẾ_ĐỘ_DƯƠNG_DEV",
    ]
    by_id = {p["trial_id"]: p for p in portfolios}
    selected_ids: list[str] = [tid for tid in preferred if tid in by_id]
    for cid in candidate_ids:
        if cid in by_id and cid not in selected_ids:
            selected_ids.append(cid)
    if not selected_ids:
        return "<div class='muted'>Không có dữ liệu đường vốn để hiển thị.</div>"

    curves = [by_id[tid] for tid in selected_ids if by_id[tid].get("equity_curve")]
    if not curves:
        return "<div class='muted'>Không có điểm dữ liệu đường vốn.</div>"

    all_ts = [int(pt["timestamp_ms"]) for c in curves for pt in c["equity_curve"]]
    all_eq = [float(pt["equity"]) for c in curves for pt in c["equity_curve"]]
    t_min, t_max = min(all_ts), max(all_ts)
    eq_min, eq_max = min(all_eq), max(all_eq)
    if t_max <= t_min:
        t_max = t_min + 1
    if eq_max <= eq_min:
        eq_max = eq_min + 10.0
    pad_eq = max(5.0, (eq_max - eq_min) * 0.08)
    y_lo, y_hi = eq_min - pad_eq, eq_max + pad_eq

    w, h = 1120, 320
    m_left, m_right, m_top, m_bottom = 66, 20, 24, 34
    pw = w - m_left - m_right
    ph = h - m_top - m_bottom

    def sx(ts: int) -> float:
        return m_left + ((ts - t_min) / (t_max - t_min)) * pw

    def sy(eq: float) -> float:
        return m_top + ph - ((eq - y_lo) / (y_hi - y_lo)) * ph

    parts: list[str] = [
        f"<svg viewBox='0 0 {w} {h}' class='equity-svg' role='img' aria-label='Biểu đồ đường vốn danh mục ghép'>",
    ]

    split_colors = {
        "DEVELOPMENT": ("rgba(56,189,248,0.06)", "PHÁT_TRIỂN (24T)"),
        "VALIDATION": ("rgba(251,191,36,0.07)", "KIỂM_ĐỊNH (6T)"),
        "HOLDOUT": ("rgba(167,139,250,0.08)", "KIỂM_CHỨNG_CUỐI (6T)"),
    }
    for sp_key, (fill, label_vi) in split_colors.items():
        b = splits_cfg[sp_key]
        x0 = max(float(m_left), min(float(w - m_right), sx(int(b["start_ms"]))))
        x1 = max(float(m_left), min(float(w - m_right), sx(int(b["end_ms"]))))
        if x1 > x0:
            parts.append(
                f"<rect x='{x0:.1f}' y='{m_top}' width='{x1 - x0:.1f}' height='{ph}' fill='{fill}'/>"
            )
            parts.append(
                f"<text x='{(x0 + x1) / 2:.1f}' y='{m_top + 14}' text-anchor='middle' class='svg-split-lbl'>{_esc(label_vi)}</text>"
            )

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        yv = y_lo + frac * (y_hi - y_lo)
        yp = sy(yv)
        parts.append(
            f"<line x1='{m_left}' y1='{yp:.1f}' x2='{w - m_right}' y2='{yp:.1f}' stroke='rgba(148,163,184,0.16)' stroke-width='1'/>"
        )
        parts.append(
            f"<text x='{m_left - 8}' y='{yp + 4:.1f}' text-anchor='end' class='svg-axis-lbl'>{yv:.0f}</text>"
        )

    palette = ["#38bdf8", "#f59e0b", "#10b981", "#c084fc", "#f472b6", "#f87171"]
    legend_items: list[str] = []
    for idx, curve in enumerate(curves):
        color = palette[idx % len(palette)]
        dash = " stroke-dasharray='6 4'" if curve["trial_id"] == "DANH_MỤC_23_LAB11_PRUNE23_CHUẨN" else ""
        pts = " ".join(f"{sx(int(p['timestamp_ms'])):.1f},{sy(float(p['equity'])):.1f}" for p in curve["equity_curve"])
        parts.append(
            f"<polyline fill='none' stroke='{color}' stroke-width='2.1'{dash} points='{pts}'/>"
        )
        end_eq = float(curve["equity_curve"][-1]["equity"])
        legend_items.append(
            f"<span class='legend-item'><i style='background:{color}'></i>{_esc(curve['trial_id'])} ({end_eq:.1f} USD)</span>"
        )

    y1000 = sy(1000.0)
    if m_top <= y1000 <= m_top + ph:
        parts.append(
            f"<line x1='{m_left}' y1='{y1000:.1f}' x2='{w - m_right}' y2='{y1000:.1f}' stroke='rgba(255,255,255,0.28)' stroke-dasharray='3 3' stroke-width='1'/>"
        )

    parts.append(
        f"<text x='{m_left}' y='{h - 8}' class='svg-axis-lbl'>2023-11-01</text>"
    )
    parts.append(
        f"<text x='{w - m_right}' y='{h - 8}' text-anchor='end' class='svg-axis-lbl'>2026-10-31</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts) + f"<div class='legend-row'>{''.join(legend_items)}</div>"


def render_regime_report_html(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Kết xuất HTML báo cáo một trang tiếng Việt cho V3-18."""
    cfg = report["config"]
    combo_ana = report["combo_regime_analysis"]
    port_res = report["portfolio_experiments"]
    splits_cfg = cfg["splits"]
    stats_cfg = cfg["statistics"]
    min_n = int(stats_cfg["min_sample_size"])
    m_tests = int(port_res["total_trials_m"])
    alpha = float(port_res["family_wise_alpha"])
    alpha_min = float(port_res["holm_strictest_threshold"])
    candidates = port_res["candidate_watchlist"]
    candidate_ids = {str(c["trial_id"]) for c in candidates}

    warning_text = LABELS_VI["multiple_testing_warning"].format(
        m_tests=m_tests,
        alpha=alpha,
        alpha_min=alpha_min,
    )

    combo_rows_html: list[str] = []
    for row in combo_ana["per_combo"]:
        cat = row["catalog"]
        o_all = row["overall_all"]
        o_sp = row["overall_by_split"]
        live_badge = (
            "<span class='badge badge-live'>ĐANG LIVE</span>"
            if cat["is_live_23"]
            else "<span class='badge badge-nonlive'>NGOÀI LIVE</span>"
        )
        dir_vi = cat["directions_cross30"]
        combo_rows_html.append(
            "<tr>"
            f"<td class='mono'>{_esc(cat['combo_symbol'])}</td>"
            f"<td>{live_badge}</td>"
            f"<td class='mono'>{_esc(cat['timeframe'])}</td>"
            f"<td>{_esc(dir_vi)}</td>"
            f"<td class='mono'>{_esc(cat['layer2'])}</td>"
            f"<td class='mono'>{cat['tp_pct']*100:.2f}% / {cat['sl_pct']*100:.2f}%</td>"
            f"<td class='mono'>{_esc(cat['source_commit_cross30'][:7])}</td>"
            f"<td class='num'>{o_all['so_lenh']}</td>"
            f"<td class='num {_pnl_cls(o_sp['DEVELOPMENT']['net_pnl_usd'])}'>{o_sp['DEVELOPMENT']['net_pnl_usd']:+.2f}</td>"
            f"<td class='num {_pnl_cls(o_sp['VALIDATION']['net_pnl_usd'])}'>{o_sp['VALIDATION']['net_pnl_usd']:+.2f}</td>"
            f"<td class='num {_pnl_cls(o_sp['HOLDOUT']['net_pnl_usd'])}'>{o_sp['HOLDOUT']['net_pnl_usd']:+.2f}</td>"
            f"<td class='num bold {_pnl_cls(o_all['net_pnl_usd'])}'>{o_all['net_pnl_usd']:+.2f}</td>"
            "</tr>"
        )

    regime_order: list[str] = list(combo_ana["regime_order"])
    heatmap_headers = "".join(f"<th>{_esc(r)}</th>" for r in regime_order)
    heatmap_rows_html: list[str] = []
    for row in combo_ana["per_combo"]:
        cat = row["catalog"]
        tag = "LIVE" if cat["is_live_23"] else "NGOÀI"
        cells_html: list[str] = [
            f"<td class='mono sticky-col'>{_esc(cat['combo_symbol'])} <span class='sub-tag'>{tag}</span></td>"
        ]
        for reg_name in regime_order:
            cell = row["by_regime"].get(reg_name)
            if not cell:
                cells_html.append("<td class='hm-cell muted'>0 lệnh</td>")
                continue
            st = _heat_cell_style(cell)
            n = int(cell["so_lenh"])
            pnl = float(cell["net_pnl_usd"])
            if not cell["conclusive"]:
                cells_html.append(
                    f"<td class='hm-cell' style='{st}'>"
                    f"<div class='hm-insuff'>CHƯA ĐỦ MẪU</div>"
                    f"<div class='hm-sub'>{n} lệnh · {pnl:+.2f}</div>"
                    "</td>"
                )
            else:
                wr = _fmt_pct(cell["ty_le_thang_pct"])
                ci = _fmt_ci(cell["ky_vong_lenh_usd_ci95"])
                cells_html.append(
                    f"<td class='hm-cell' style='{st}'>"
                    f"<div class='hm-pnl'>{pnl:+.2f}</div>"
                    f"<div class='hm-sub'>{n} lệnh · {wr}</div>"
                    f"<div class='hm-ci'>KV95% {ci}</div>"
                    "</td>"
                )
        heatmap_rows_html.append(f"<tr>{''.join(cells_html)}</tr>")

    dim_labels_vi = {
        "che_do_thi_truong": "Chế độ thị trường tổng hợp",
        "cau_truc_smc": "Cấu trúc SMC (Phá cấu trúc)",
        "quet_thanh_khoan_smc": "Quét thanh khoản SMC",
        "vung_cung_cau_smc": "Vùng cung / cầu gần nhất",
        "che_do_thanh_khoan": "Thanh khoản / Khối lượng tương đối",
        "phien_giao_dich": "Phiên giao dịch (UTC)",
    }
    smc_rows_html: list[str] = []
    for dim_key in ("che_do_thi_truong", "cau_truc_smc", "quet_thanh_khoan_smc", "vung_cung_cau_smc", "che_do_thanh_khoan", "phien_giao_dich"):
        for item in combo_ana["dimension_summaries"].get(dim_key, []):
            a = item["all"]
            d = item["DEVELOPMENT"]
            v = item["VALIDATION"]
            h_s = item["HOLDOUT"]
            st_badge = "<span class='badge badge-ok'>ĐỦ MẪU</span>" if a["conclusive"] else "<span class='badge badge-warn'>CHƯA ĐỦ MẪU</span>"
            smc_rows_html.append(
                "<tr>"
                f"<td>{_esc(dim_labels_vi.get(dim_key, dim_key))}</td>"
                f"<td class='mono'>{_esc(item['label'])}</td>"
                f"<td>{st_badge}</td>"
                f"<td class='num'>{a['so_lenh']}</td>"
                f"<td class='num {_pnl_cls(a['net_pnl_usd'])}'>{a['net_pnl_usd']:+.2f}</td>"
                f"<td class='num'>{_fmt_pct(a['ty_le_thang_pct'])}</td>"
                f"<td class='num'>{a['pf'] if a['pf'] is not None else '—'}</td>"
                f"<td class='mono num'>{_fmt_ci(a['ky_vong_lenh_usd_ci95'])}</td>"
                f"<td class='num {_pnl_cls(d['net_pnl_usd'])}'>{d['net_pnl_usd']:+.2f}</td>"
                f"<td class='num {_pnl_cls(v['net_pnl_usd'])}'>{v['net_pnl_usd']:+.2f}</td>"
                f"<td class='num {_pnl_cls(h_s['net_pnl_usd'])}'>{h_s['net_pnl_usd']:+.2f}</td>"
                "</tr>"
            )

    port_rows_html: list[str] = []
    for p in port_res["portfolios"]:
        m_all = p["metrics"]["ALL"]
        m_dev = p["metrics"]["DEVELOPMENT"]
        m_val = p["metrics"]["VALIDATION"]
        m_hold = p["metrics"]["HOLDOUT"]
        dt = p["delta_vs_baseline"]
        st_t = p["stats_test"]
        row_cls = "row-base" if p["is_baseline"] else ("row-cand" if p["is_candidate_improved"] else "")
        cand_badge = (
            "<span class='badge badge-base'>CƠ SỞ 23 LIVE</span>"
            if p["is_baseline"]
            else (
                "<span class='badge badge-cand'>CẢI THIỆN CẢ 2 KỲ</span>"
                if p["is_candidate_improved"]
                else "<span class='badge badge-muted'>KHÔNG ĐẠT ĐỦ 2 KỲ</span>"
            )
        )
        p_txt = (
            "—"
            if p["is_baseline"]
            else f"KĐ:{st_t['val_p_holm']:.3f} / CC:{st_t['holdout_p_holm']:.3f}"
        )
        conc_txt = f"{_esc(m_all['top_combo_key'] or '—')} ({_fmt_pct(m_all['max_single_combo_share_pct'])})"
        port_rows_html.append(
            f"<tr class='{row_cls}'>"
            f"<td class='mono'>{_esc(p['trial_id'])}<div class='sub-desc'>{_esc(p['status_note'])}</div></td>"
            f"<td>{cand_badge}</td>"
            f"<td class='num'>{m_all['so_lenh']}</td>"
            f"<td class='num {_pnl_cls(m_dev['net_pnl_usd'])}'>{m_dev['net_pnl_usd']:+.2f} ({dt['DEVELOPMENT_net_pnl_usd']:+.2f})</td>"
            f"<td class='num {_pnl_cls(m_val['net_pnl_usd'])}'>{m_val['net_pnl_usd']:+.2f} ({dt['VALIDATION_net_pnl_usd']:+.2f})</td>"
            f"<td class='num {_pnl_cls(m_hold['net_pnl_usd'])}'>{m_hold['net_pnl_usd']:+.2f} ({dt['HOLDOUT_net_pnl_usd']:+.2f})</td>"
            f"<td class='num bold {_pnl_cls(m_all['net_pnl_usd'])}'>{m_all['net_pnl_usd']:+.2f}</td>"
            f"<td class='num'>{m_all['m2m_max_dd_usd']:.2f} ({_fmt_pct(m_all['m2m_max_dd_pct'])})</td>"
            f"<td class='num'>{m_val['m2m_max_dd_usd']:.2f} / {m_hold['m2m_max_dd_usd']:.2f}</td>"
            f"<td class='num'>{m_all['max_concurrent_positions']}</td>"
            f"<td class='mono'>{conc_txt}</td>"
            f"<td class='mono'>{_esc(p_txt)}</td>"
            "</tr>"
        )

    if candidates:
        cand_cards = "".join(
            "<div class='cand-card'>"
            f"<div class='cand-title'>{_esc(c['label_vi'])}</div>"
            f"<div class='cand-stats'>KIỂM_ĐỊNH: <b>{c['val_delta_usd']:+.2f} USD</b> · "
            f"KIỂM_CHỨNG_CUỐI: <b>{c['holdout_delta_usd']:+.2f} USD</b> · "
            f"Lệnh mở đồng thời tối đa: <b>{c['max_concurrent_positions']}</b> · "
            f"Tập trung 1 mã: <b>{_fmt_pct(c['max_single_combo_share_pct'])}</b></div>"
            f"<div class='cand-reason'>{_esc(c['reason_vi'])}</div>"
            "</div>"
            for c in candidates
        )
    else:
        cand_cards = (
            "<div class='cand-empty'>"
            "Chưa có cấu hình hay mã ngoài live nào đồng thời vượt danh mục cơ sở 23 mã trên CẢ hai kỳ "
            "KIỂM_ĐỊNH và KIỂM_CHỨNG_CUỐI trong ngưỡng sụt giảm M2M tối đa đã khóa."
            "</div>"
        )

    equity_svg_html = _render_equity_svg(port_res["portfolios"], splits_cfg, candidate_ids)
    o_all_sig = combo_ana["overall_all_signals"]["ALL"]

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_esc(LABELS_VI['page_title'])}</title>
  <style>
    :root {{
      --bg: #0b111b;
      --card: #121a28;
      --border: #1e293b;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --ok: #10b981;
      --bad: #ef4444;
      --warn: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 20px; background: var(--bg); color: var(--text);
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; font-size: 13px; line-height: 1.45;
    }}
    .wrap {{ max-width: 1480px; margin: 0 auto; }}
    .hdr {{ margin-bottom: 16px; padding: 16px 20px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; }}
    .hdr h1 {{ margin: 0 0 6px 0; font-size: 20px; color: #f8fafc; }}
    .hdr .sub {{ color: var(--muted); font-size: 12px; }}
    .note-box {{ margin-top: 10px; padding: 8px 12px; background: rgba(56,189,248,0.08); border-left: 3px solid var(--accent); border-radius: 4px; color: #bae6fd; font-size: 12px; }}
    .warn-box {{ margin: 12px 0; padding: 10px 14px; background: rgba(245,158,11,0.10); border-left: 3px solid var(--warn); border-radius: 4px; color: #fde68a; font-size: 12px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }}
    .kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }}
    .kpi-lbl {{ color: var(--muted); font-size: 11px; margin-bottom: 4px; }}
    .kpi-val {{ font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }}
    .card h2 {{ margin: 0 0 10px 0; font-size: 15px; color: #f8fafc; }}
    .tbl-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }}
    th {{ color: #cbd5e1; background: #0f172a; font-weight: 600; position: sticky; top: 0; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .bold {{ font-weight: 700; }}
    .pos {{ color: #34d399; }}
    .neg {{ color: #f87171; }}
    .zero {{ color: var(--muted); }}
    .muted {{ color: var(--muted); }}
    .badge {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 600; }}
    .badge-live {{ background: rgba(16,185,129,0.16); color: #6ee7b7; border: 1px solid rgba(16,185,129,0.35); }}
    .badge-nonlive {{ background: rgba(245,158,11,0.16); color: #fcd34d; border: 1px solid rgba(245,158,11,0.35); }}
    .badge-ok {{ background: rgba(56,189,248,0.15); color: #7dd3fc; }}
    .badge-warn {{ background: rgba(148,163,184,0.16); color: #cbd5e1; }}
    .badge-base {{ background: rgba(56,189,248,0.20); color: #38bdf8; }}
    .badge-cand {{ background: rgba(16,185,129,0.22); color: #34d399; }}
    .badge-muted {{ background: rgba(100,116,139,0.20); color: #94a3b8; }}
    .hm-cell {{ text-align: center; min-width: 118px; border: 1px solid #0b111b; padding: 5px 6px; }}
    .hm-pnl {{ font-weight: 700; font-size: 12px; }}
    .hm-sub {{ font-size: 10px; opacity: 0.9; }}
    .hm-ci {{ font-size: 10px; opacity: 0.78; font-family: ui-monospace, monospace; }}
    .hm-insuff {{ font-size: 10px; font-style: italic; font-weight: 600; }}
    .sticky-col {{ position: sticky; left: 0; background: #0f172a; z-index: 2; white-space: nowrap; }}
    .sub-tag {{ font-size: 10px; color: var(--muted); margin-left: 4px; }}
    .sub-desc {{ font-size: 10px; color: var(--muted); margin-top: 2px; }}
    .row-base {{ background: rgba(56,189,248,0.06); }}
    .row-cand {{ background: rgba(16,185,129,0.07); }}
    .equity-svg {{ width: 100%; height: auto; display: block; background: #0e1624; border-radius: 6px; }}
    .svg-split-lbl {{ fill: #94a3b8; font-size: 11px; font-weight: 600; }}
    .svg-axis-lbl {{ fill: #94a3b8; font-size: 10px; font-family: ui-monospace, monospace; }}
    .legend-row {{ display: flex; flex-wrap: wrap; gap: 14px; margin-top: 8px; font-size: 11px; color: #cbd5e1; }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
    .legend-item i {{ display: inline-block; width: 12px; height: 3px; border-radius: 2px; }}
    .cand-card {{ padding: 10px 14px; border: 1px solid rgba(16,185,129,0.35); background: rgba(16,185,129,0.07); border-radius: 8px; margin-bottom: 8px; }}
    .cand-title {{ font-weight: 700; color: #6ee7b7; margin-bottom: 4px; }}
    .cand-stats {{ font-size: 12px; margin-bottom: 4px; }}
    .cand-reason {{ font-size: 12px; color: #cbd5e1; }}
    .cand-empty {{ padding: 12px; background: rgba(148,163,184,0.08); border-radius: 8px; color: #cbd5e1; }}
    .meta-foot {{ font-size: 11px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 16px; }}
    @media (max-width: 1100px) {{ .kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
  </style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>{_esc(LABELS_VI['page_title'])}</h1>
    <div class="sub">{_esc(LABELS_VI['subtitle'])} · SHA nhánh: <span class="mono">{_esc(manifest['git_commit'][:12])}</span></div>
    <div class="note-box">{_esc(LABELS_VI['neutral_note'])}</div>
  </div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-lbl">{_esc(LABELS_VI['kpi_total_combos'])}</div><div class="kpi-val">{len(combo_ana['per_combo'])} mã</div></div>
    <div class="kpi"><div class="kpi-lbl">{_esc(LABELS_VI['kpi_live_split'])}</div><div class="kpi-val">{manifest['live_23_count']} / {manifest['non_live_7_count']}</div></div>
    <div class="kpi"><div class="kpi-lbl">{_esc(LABELS_VI['kpi_total_signals'])}</div><div class="kpi-val">{combo_ana['total_eval_signals']} ({o_all_sig['net_pnl_usd']:+.2f} USD)</div></div>
    <div class="kpi"><div class="kpi-lbl">{_esc(LABELS_VI['kpi_multiple_tests'])}</div><div class="kpi-val">{m_tests} phép thử</div></div>
    <div class="kpi"><div class="kpi-lbl">{_esc(LABELS_VI['kpi_holdout_rule'])}</div><div class="kpi-val">{len(candidates)} đề xuất</div></div>
    <div class="kpi"><div class="kpi-lbl">Ngưỡng đủ mẫu / ô</div><div class="kpi-val">&ge; {min_n} lệnh</div></div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_30_combos'])}</h2>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Mã cặp</th><th>Trạng thái</th><th>Khung</th><th>Chiều</th><th>Lớp 2</th><th>TP / SL</th><th>Nguồn</th>
            <th class="num">Tổng lệnh</th><th class="num">PHÁT_TRIỂN</th><th class="num">KIỂM_ĐỊNH</th><th class="num">KIỂM_CHỨNG_CUỐI</th><th class="num">Toàn kỳ 36T</th>
          </tr>
        </thead>
        <tbody>{''.join(combo_rows_html)}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_splits_and_regime'])}</h2>
    <div class="sub">
      Phân chia tĩnh: PHÁT_TRIỂN (2023-11-01 .. 2025-10-31) · KIỂM_ĐỊNH (2025-11-01 .. 2026-04-30) · KIỂM_CHỨNG_CUỐI (2026-05-01 .. 2026-10-31).
      Đặc trưng nhân quả nến 1H/4H dùng hoàn toàn dữ liệu đã đóng &le; t: xu hướng EMA50/150 1H &amp; độ dốc EMA150 4H, biến động ATR14 tương đối + phân vị 720 giờ, thanh khoản khối lượng/SMA20, phiên UTC (Á/Âu/Mỹ), cấu trúc SMC (swing 50), quét thanh khoản 20 nến, vùng cung/cầu gần nhất trong 1.0 ATR.
    </div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_heatmap'])}</h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th class="sticky-col">Mã cặp</th>{heatmap_headers}</tr></thead>
        <tbody>{''.join(heatmap_rows_html)}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_smc_summary'])}</h2>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Nhóm đặc trưng</th><th>Nhãn</th><th>Độ tin cậy mẫu</th>
            <th class="num">Số lệnh</th><th class="num">Lãi ròng (USD)</th><th class="num">Tỷ lệ thắng</th><th class="num">Hệ số lời/lỗ</th><th class="num">Khoảng 95% kỳ vọng</th>
            <th class="num">PHÁT_TRIỂN</th><th class="num">KIỂM_ĐỊNH</th><th class="num">KIỂM_CHỨNG_CUỐI</th>
          </tr>
        </thead>
        <tbody>{''.join(smc_rows_html)}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_equity_chart'])}</h2>
    {equity_svg_html}
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_portfolio'])}</h2>
    <div class="warn-box">{_esc(warning_text)}</div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Cấu hình thử nghiệm</th><th>Đánh giá 2 kỳ</th><th class="num">Số lệnh</th>
            <th class="num">PHÁT_TRIỂN (Chênh lệch)</th><th class="num">KIỂM_ĐỊNH (Chênh lệch)</th><th class="num">KIỂM_CHỨNG_CUỐI (Chênh lệch)</th>
            <th class="num">Toàn kỳ (USD)</th><th class="num">Sụt giảm M2M 36T</th><th class="num">Sụt giảm KĐ / CC</th>
            <th class="num">Mở đồng thời tối đa</th><th>Tập trung 1 mã</th><th>Giá trị p Holm (KĐ / CC)</th>
          </tr>
        </thead>
        <tbody>{''.join(port_rows_html)}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>{_esc(LABELS_VI['sec_candidates'])}</h2>
    {cand_cards}
  </div>

  <div class="card meta-foot">
    <span>SHA cấu hình: <b class="mono">{_esc(manifest['config_canonical_sha256'][:16])}</b></span>
    <span>SHA tập dữ liệu: <b class="mono">{_esc(manifest['dataset']['dataset_hash'][:16])}</b></span>
    <span>SHA báo cáo JSON: <b class="mono">{_esc(manifest['report_canonical_sha256'][:16])}</b></span>
    <span>SHA đặc tả 30 mã: <b class="mono">{_esc(manifest['combos_spec_hash'][:16])}</b></span>
  </div>
</div>
</body>
</html>
"""


def _pnl_cls(val: float) -> str:
    if float(val) > 1e-9:
        return "pos"
    if float(val) < -1e-9:
        return "neg"
    return "zero"


def write_v3_18_artifacts(
    *,
    ctx: dict[str, Any],
    combo_analysis: dict[str, Any],
    portfolio_results: dict[str, Any],
    evidence_dir: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Ghi đầy đủ `regime_report.json`, `regime_report.html` và `manifest.json` ra thư mục minh chứng."""
    out_dir = evidence_dir or DEFAULT_EVIDENCE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    cfg = ctx["config"]
    strat: CrossSymbol30FixedStrategy = ctx["strategy"]

    combo_clean = {k: v for k, v in combo_analysis.items() if k != "trades_annotated"}
    report_body: dict[str, Any] = {
        "schema_version": "1.0.0",
        "research_id": cfg["research_id"],
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": cfg,
        "source_commits": {
            "cross30_spec": CROSS30_SOURCE_COMMIT,
            "cross30_run": CROSS30_RUN_SOURCE_COMMIT,
            "cross30_ancestor": CROSS30_PR124_ANCESTOR,
            "live23": LIVE23_SOURCE_COMMIT,
            "live23_registry": LIVE23_REGISTRY_REF,
            "head_at_run": _current_git_sha(),
        },
        "combo_regime_analysis": combo_clean,
        "portfolio_experiments": portfolio_results,
    }
    report_sha256 = canonical_json_sha256(report_body)
    full_report = {**report_body, "report_sha256": report_sha256}

    report_path = out_dir / "regime_report.json"
    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_file_sha256 = _file_sha256(report_path)

    dl = DataLayer()
    ds_manifest = generate_dataset_manifest()
    data_manifest = dl.get_manifest()
    live_count = sum(1 for row in combo_clean["per_combo"] if row["catalog"]["is_live_23"])
    non_live_count = sum(1 for row in combo_clean["per_combo"] if not row["catalog"]["is_live_23"])

    manifest_pre_html: dict[str, Any] = {
        "schema_version": "1.0.0",
        "research_id": cfg["research_id"],
        "git_commit": _current_git_sha(),
        "source_commits": report_body["source_commits"],
        "dataset": {
            "dataset_hash": ds_manifest["dataset_hash"],
            "file_count": ds_manifest.get("file_count"),
            "data_manifest_hash": data_manifest.data_manifest_hash,
        },
        "config_path": "autotrade_v3/research_v3_18/config.json",
        "config_canonical_sha256": canonical_json_sha256(load_research_config(cfg_path)),
        "config_file_sha256": _file_sha256(cfg_path),
        "combos_spec_hash": strat.get_spec_hash(),
        "report_path": "regime_report.json",
        "report_canonical_sha256": report_sha256,
        "report_file_sha256": report_file_sha256,
        "html_path": "regime_report.html",
        "total_combos": len(combo_clean["per_combo"]),
        "live_23_count": live_count,
        "non_live_7_count": non_live_count,
        "total_eval_signals": combo_clean["total_eval_signals"],
        "total_trials_m": portfolio_results["total_trials_m"],
        "split_bounds": cfg["splits"],
    }

    html_text = render_regime_report_html(full_report, manifest_pre_html)
    leaks = find_english_label_leaks(html_text)
    if leaks:
        raise ValueError(f"Phát hiện nhãn tiếng Anh rò rỉ trong HTML: {leaks}")

    html_path = out_dir / "regime_report.html"
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
