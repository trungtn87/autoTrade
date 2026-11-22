"""Sinh báo cáo đối soát JSON (khóa tiếng Anh) và HTML tiếng Việt theo khung V3-16."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import hashlib
import html
import json
import math
from typing import Any

from autotrade_v3.collector_v3.models import to_decimal_str
from autotrade_v3.reconcile_v3.eras import ERA_1_50TP_50TRAIL, ERA_2_FIXED_TP_SL, LiveCommitInfo, VpsOffWindow
from autotrade_v3.reconcile_v3.labels_vi import RECONCILE_LABELS_VI as L
from autotrade_v3.reconcile_v3.loader import LocalStoreData
from autotrade_v3.reconcile_v3.reconcile_orders import OrderReconcileRow
from autotrade_v3.reconcile_v3.signals_reconcile import SignalReconcileSummary

ZERO = Decimal("0")


def _d(val: Any) -> Decimal:
    if val is None or val == "":
        return ZERO
    return Decimal(str(val))


def _p95(values: list[Decimal]) -> Decimal:
    if not values:
        return ZERO
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(0.95 * len(s)) - 1))
    return s[idx]


def compute_era_summary(order_rows: list[OrderReconcileRow], era_name: str) -> dict[str, Any]:
    """Tính tổng hợp cho một kỷ nguyên trực tiếp từ các dòng lệnh."""
    subset = [r for r in order_rows if r.era == era_name]
    n_orders = len(subset)
    n_closed = sum(1 for r in subset if r.is_fully_closed)
    slips = [_d(r.items["entry"]["live_slippage_bps"]) for r in subset]
    avg_slip = (sum(slips, ZERO) / Decimal(n_orders)) if n_orders else ZERO
    p95_slip = _p95(slips)

    live_gross = sum((_d(r.items["pnl"]["live_gross_pnl"]) for r in subset), ZERO)
    live_net = sum((_d(r.items["pnl"]["live_net_pnl"]) for r in subset), ZERO)
    live_fee = sum((_d(r.items["fees"]["live_total_fee"]) for r in subset), ZERO)

    sim_base_gross = sum((_d(r.items["pnl"]["sim_base_gross_pnl"]) for r in subset), ZERO)
    sim_base_net = sum((_d(r.items["pnl"]["sim_base_net_pnl"]) for r in subset), ZERO)
    sim_base_fee = sum((_d(r.items["fees"]["sim_base_fee"]) for r in subset), ZERO)

    sim_ideal_gross = sum((_d(r.items["pnl"]["sim_ideal_gross_pnl"]) for r in subset), ZERO)
    sim_ideal_net = sum((_d(r.items["pnl"]["sim_ideal_net_pnl"]) for r in subset), ZERO)
    sim_ideal_fee = sum((_d(r.items["fees"]["sim_ideal_fee"]) for r in subset), ZERO)

    est_funding = sum((_d(r.items["funding"]["estimated_funding_usd"]) for r in subset), ZERO)
    matched_outcome = sum(1 for r in subset if r.items["close_outcome"]["matched_outcome"])

    return {
        "era": era_name,
        "orders_count": n_orders,
        "closed_orders_count": n_closed,
        "matched_outcome_count": matched_outcome,
        "avg_entry_slippage_bps": to_decimal_str(avg_slip.quantize(Decimal("0.01"))),
        "p95_entry_slippage_bps": to_decimal_str(p95_slip.quantize(Decimal("0.01"))),
        "live_gross_pnl_usd": to_decimal_str(live_gross),
        "live_net_pnl_usd": to_decimal_str(live_net),
        "live_total_fee_usd": to_decimal_str(live_fee),
        "sim_base_gross_pnl_usd": to_decimal_str(sim_base_gross),
        "sim_base_net_pnl_usd": to_decimal_str(sim_base_net),
        "sim_base_total_fee_usd": to_decimal_str(sim_base_fee),
        "sim_ideal_gross_pnl_usd": to_decimal_str(sim_ideal_gross),
        "sim_ideal_net_pnl_usd": to_decimal_str(sim_ideal_net),
        "sim_ideal_total_fee_usd": to_decimal_str(sim_ideal_fee),
        "net_pnl_diff_vs_base_usd": to_decimal_str(live_net - sim_base_net),
        "fee_diff_vs_base_usd": to_decimal_str(live_fee - sim_base_fee),
        "estimated_funding_usd": to_decimal_str(est_funding.quantize(Decimal("0.000001"))),
    }


def compute_cause_distribution(order_rows: list[OrderReconcileRow]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in order_rows:
        dist[r.root_cause] = dist.get(r.root_cause, 0) + 1
    return dict(sorted(dist.items()))


def compute_canonical_hash(payload: dict[str, Any]) -> str:
    """Tính SHA-256 chuẩn tắc của JSON (loại trừ trường report_sha256)."""
    clean = {k: v for k, v in payload.items() if k != "report_sha256"}
    raw = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_report_payload(
    *,
    store: LocalStoreData,
    signal_summary: SignalReconcileSummary,
    order_rows: list[OrderReconcileRow],
    vps_off_windows: list[VpsOffWindow],
    other_log_gaps: list[VpsOffWindow],
    live_commits: list[LiveCommitInfo],
    tolerances: dict[str, Any],
) -> dict[str, Any]:
    """Tạo payload JSON đầy đủ với khóa tiếng Anh và số Decimal chính xác."""
    n_closed_total = sum(1 for r in order_rows if r.is_fully_closed)
    min_sample = int(tolerances.get("min_closed_orders_conclusive", 60))
    is_small_sample = n_closed_total < min_sample

    all_slips = [_d(r.items["entry"]["live_slippage_bps"]) for r in order_rows]
    avg_slip_all = (sum(all_slips, ZERO) / Decimal(len(order_rows))) if order_rows else ZERO
    p95_slip_all = _p95(all_slips)

    era_summaries = {
        ERA_1_50TP_50TRAIL: compute_era_summary(order_rows, ERA_1_50TP_50TRAIL),
        ERA_2_FIXED_TP_SL: compute_era_summary(order_rows, ERA_2_FIXED_TP_SL),
    }
    cause_dist = compute_cause_distribution(order_rows)
    system_issues = [
        {
            "event_id": r.event_id,
            "era": r.era,
            "detail_vi": r.system_issue_note or r.root_cause_detail_vi,
        }
        for r in order_rows
        if r.root_cause == "HỆ_THỐNG"
    ]

    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "tool": "autotrade_v3.reconcile_v3",
        "manifest_run_id": store.manifest.get("run_id"),
        "manifest_sha256": store.manifest_sha256,
        "sample_governance": {
            "total_orders": len(order_rows),
            "closed_orders": n_closed_total,
            "min_closed_orders_conclusive": min_sample,
            "is_small_sample": is_small_sample,
            "conclusive": not is_small_sample,
            "warning_vi": L["small_sample_warning"].format(n_closed=n_closed_total, min_sample=min_sample),
        },
        "summary_overall_stats": {
            "signal_match_rate_vs_live_pct": signal_summary.match_rate_vs_live_pct,
            "avg_entry_slippage_bps": to_decimal_str(avg_slip_all.quantize(Decimal("0.01"))),
            "p95_entry_slippage_bps": to_decimal_str(p95_slip_all.quantize(Decimal("0.01"))),
            "note_vi": "Không gộp PnL hai kỷ nguyên thành một con số; xem era_summaries.",
        },
        "era_summaries": era_summaries,
        "cause_distribution": cause_dist,
        "system_issues": system_issues,
        "sources_coverage": [asdict(c) for c in store.coverage],
        "vps_off_windows": [asdict(w) for w in vps_off_windows],
        "other_control_log_gaps": [asdict(w) for w in other_log_gaps],
        "live_commits": [asdict(c) for c in live_commits],
        "tolerances": tolerances,
        "signal_reconciliation": {
            "lab_total_in_window": signal_summary.lab_total_in_window,
            "live_total": signal_summary.live_total,
            "matched_count": signal_summary.matched_count,
            "missing_count": signal_summary.missing_count,
            "extra_count": signal_summary.extra_count,
            "vps_off_count": signal_summary.vps_off_count,
            "time_skew_count": signal_summary.time_skew_count,
            "match_rate_vs_live_pct": signal_summary.match_rate_vs_live_pct,
            "notes": signal_summary.notes,
            "items": [asdict(it) for it in signal_summary.items],
        },
        "orders": [r.to_dict() for r in order_rows],
    }
    payload["report_sha256"] = compute_canonical_hash(payload)
    return payload


def render_html_report(payload: dict[str, Any]) -> str:
    """Sinh báo cáo HTML 100% nhãn tiếng Việt theo khung V3-16."""
    sg = payload["sample_governance"]
    sig_rec = payload["signal_reconciliation"]
    era_1 = payload["era_summaries"][ERA_1_50TP_50TRAIL]
    era_2 = payload["era_summaries"][ERA_2_FIXED_TP_SL]

    def _esc(v: Any) -> str:
        return html.escape("" if v is None else str(v))

    def _era_label(era_code: str) -> str:
        return L["era_1_name"] if era_code == ERA_1_50TP_50TRAIL else L["era_2_name"]

    era_cards_html = ""
    for era_obj in (era_1, era_2):
        era_cards_html += f"""
        <div class="card">
          <h3>{_esc(_era_label(era_obj['era']))}</h3>
          <div class="kpi-grid">
            <div class="kpi"><span class="k">Số lệnh / đóng hẳn</span><span class="v">{era_obj['orders_count']} / {era_obj['closed_orders_count']}</span></div>
            <div class="kpi"><span class="k">Khớp kết quả đóng</span><span class="v">{era_obj['matched_outcome_count']} / {era_obj['orders_count']}</span></div>
            <div class="kpi"><span class="k">Trượt giá vào TB / p95 (bps)</span><span class="v">{_esc(era_obj['avg_entry_slippage_bps'])} / {_esc(era_obj['p95_entry_slippage_bps'])}</span></div>
            <div class="kpi"><span class="k">PnL Ròng Live ($)</span><span class="v">{_esc(era_obj['live_net_pnl_usd'])}</span></div>
            <div class="kpi"><span class="k">PnL Ròng Mô phỏng BASE ($)</span><span class="v">{_esc(era_obj['sim_base_net_pnl_usd'])}</span></div>
            <div class="kpi"><span class="k">PnL Ròng Mô phỏng IDEAL ($)</span><span class="v">{_esc(era_obj['sim_ideal_net_pnl_usd'])}</span></div>
            <div class="kpi"><span class="k">Chênh lệch PnL Ròng (Live - BASE) ($)</span><span class="v">{_esc(era_obj['net_pnl_diff_vs_base_usd'])}</span></div>
            <div class="kpi"><span class="k">Phí Live vs BASE ($)</span><span class="v">{_esc(era_obj['live_total_fee_usd'])} vs {_esc(era_obj['sim_base_total_fee_usd'])}</span></div>
            <div class="kpi"><span class="k">Funding ước lượng (ESTIMATED) ($)</span><span class="v">{_esc(era_obj['estimated_funding_usd'])}</span></div>
          </div>
        </div>
        """

    sources_rows_html = "".join(
        f"<tr><td>{_esc(s['source'])}</td><td>{s['row_count']}</td>"
        f"<td>{_esc(s['min_ts_utc'] or 'Không có')}</td><td>{_esc(s['max_ts_utc'] or 'Không có')}</td>"
        f"<td>{_esc(s['status'])}</td><td><code>{_esc((s['sha256'] or '')[:12])}</code></td><td>{_esc(s['note'])}</td></tr>"
        for s in payload["sources_coverage"]
    )

    vps_rows_html = "".join(
        f"<tr><td>{_esc(w['classification'])}</td><td>{_esc(w['start_utc'])}</td><td>{_esc(w['end_utc'])}</td>"
        f"<td>{w['duration_hours']} giờ</td><td>{_esc(w['source'])}</td><td>{_esc(w['note'])}</td></tr>"
        for w in payload["vps_off_windows"]
    )

    cause_rows_html = "".join(
        f"<tr><td><strong>{_esc(k)}</strong></td><td>{v}</td></tr>"
        for k, v in payload["cause_distribution"].items()
    )

    sys_issues_html = (
        "".join(
            f"<li><code>{_esc(it['event_id'])}</code> ({_esc(it['era'])}): {_esc(it['detail_vi'])}</li>"
            for it in payload["system_issues"]
        )
        if payload["system_issues"]
        else "<li>Không có lỗi HỆ_THỐNG nào được ghi nhận.</li>"
    )

    commits_rows_html = "".join(
        f"<tr><td><code>{_esc(c['sha'])}</code></td><td>{_esc(c['date_iso'])}</td><td>{_esc(c['subject'])}</td></tr>"
        for c in payload["live_commits"]
    )

    sig_rows_html = "".join(
        f"<tr><td><code>{_esc(it['event_id'])}</code></td><td>{_esc(it['signal_close_utc'])}</td>"
        f"<td><strong>{_esc(it['status'])}</strong></td><td>{_esc(it['reason_code'])}</td><td>{_esc(it['reason_detail_vi'])}</td></tr>"
        for it in sig_rec["items"]
    )

    order_rows_html = ""
    for r in payload["orders"]:
        it = r["items"]
        e = it["entry"]
        tp_sl = it["tp_sl"]
        tr = it["trailing"]
        lg = it["legs_and_merged"]
        fe = it["fees"]
        fu = it["funding"]
        pn = it["pnl"]
        oc = it["close_outcome"]
        shared_txt = f"Vị thế gộp ({len(r['shared_peers'])} cặp)" if r["is_shared_position"] else "Độc lập"
        trail_txt = (
            f"KH={_esc(tr.get('activation_price'))} / CB={_esc(tr.get('callback_rate'))} ({_esc(tr.get('trailing_status'))})"
            if tr.get("applicable")
            else "Không áp dụng"
        )
        order_rows_html += f"""
        <tr>
          <td><code>{_esc(r['event_id'])}</code><br><small>{_esc(r['overall_provenance'])}</small></td>
          <td>{_esc(r['era'])}</td>
          <td>{_esc(it['signal']['reconcile_status'])}<br><small>{_esc(it['signal']['live_signal_time'])}</small></td>
          <td>Kỳ vọng: {_esc(e['expected_entry'])}<br>Khớp Live: {_esc(e['live_avg_price'])}<br>Mô phỏng: {_esc(e['sim_base_entry'])}<br>Trượt: {_esc(e['live_slippage_bps'])} bps</td>
          <td>TP: {_esc(tp_sl['live_tp_price'])} ({_esc(tp_sl['live_tp_status'])})<br>SL: {_esc(tp_sl['live_sl_price'])} ({_esc(tp_sl['live_sl_status'])})</td>
          <td>{trail_txt}</td>
          <td>Live: {lg['live_legs_count']} leg | Sim: {lg['sim_legs_count']} leg<br><small>{_esc(shared_txt)}</small></td>
          <td>Live: {_esc(fe['live_total_fee'])}<br>BASE: {_esc(fe['sim_base_fee'])}<br>Lệch: {_esc(fe['abs_diff'])} ({_esc(fe['pct_diff'])}%)</td>
          <td>DB: NULL<br>Ước lượng: {_esc(fu['estimated_funding_usd'])}<br><small>({fu['funding_periods']} kỳ, ESTIMATED)</small></td>
          <td>Gộp Live/Sim: {_esc(pn['live_gross_pnl'])} / {_esc(pn['sim_base_gross_pnl'])}<br>Ròng Live/Sim: {_esc(pn['live_net_pnl'])} / {_esc(pn['sim_base_net_pnl'])}<br>Lệch ròng: {_esc(pn['net_abs_diff'])} ({_esc(pn['net_pct_diff'])}%)</td>
          <td>Live: {_esc(oc['live_outcome'])}<br>BASE: {_esc(oc['sim_base_outcome'])}<br>IDEAL: {_esc(oc['sim_ideal_outcome'])}</td>
          <td><strong>{_esc(r['root_cause'])}</strong><br><small>{_esc(r['root_cause_detail_vi'])}</small></td>
        </tr>
        """

    tol_items_html = "".join(
        f"<tr><td><code>{_esc(k)}</code></td><td>{_esc(v)}</td></tr>"
        for k, v in payload["tolerances"].items()
    )

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(L['page_title'])}</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 16px; line-height: 1.5; }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  h1, h2, h3 {{ color: #f8fafc; margin-top: 24px; }}
  .banner {{ background: #7c2d12; border: 1px solid #f97316; padding: 12px 16px; border-radius: 8px; margin: 12px 0; font-weight: 600; }}
  .info-box {{ background: #1e293b; border: 1px solid #334155; padding: 12px 16px; border-radius: 8px; margin: 12px 0; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin: 12px 0; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
  .kpi {{ background: #0f172a; padding: 10px 12px; border-radius: 6px; border: 1px solid #334155; }}
  .kpi .k {{ display: block; font-size: 12px; color: #94a3b8; }}
  .kpi .v {{ display: block; font-size: 16px; font-weight: 700; color: #38bdf8; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  th, td {{ border: 1px solid #334155; padding: 8px; text-align: left; vertical-align: top; }}
  th {{ background: #1e293b; color: #f8fafc; position: sticky; top: 0; }}
  tr:nth-child(even) {{ background: #162032; }}
  code {{ background: #0f172a; padding: 2px 5px; border-radius: 4px; font-size: 12px; color: #cbd5e1; }}
  .table-wrap {{ overflow-x: auto; }}
</style>
</head>
<body>
<div class="container">
  <h1>{_esc(L['page_title'])}</h1>
  <p>{_esc(L['subtitle'])}</p>
  <div class="banner">{_esc(sg['warning_vi'])}</div>
  <div class="info-box">
    <div><strong>Mã đợt thu kho (manifest):</strong> <code>{_esc(payload['manifest_run_id'])}</code> | <strong>SHA-256 báo cáo:</strong> <code>{_esc(payload['report_sha256'])}</code></div>
    <div><strong>Hồ sơ chi phí:</strong> {_esc(L['cost_base_label'])} | {_esc(L['cost_ideal_label'])}</div>
    <div><small>{_esc(L['funding_note'])}</small></div>
  </div>

  <h2>{_esc(L['sec_summary'])}</h2>
  <div class="info-box">
    <strong>Tỷ lệ tín hiệu khớp (so với Live):</strong> {sig_rec['match_rate_vs_live_pct']}% ({sig_rec['matched_count']}/{sig_rec['live_total']}) |
    <strong>Trượt giá vào lệnh TB / p95 (toàn mẫu):</strong> {_esc(payload['summary_overall_stats']['avg_entry_slippage_bps'])} / {_esc(payload['summary_overall_stats']['p95_entry_slippage_bps'])} bps |
    <em>{_esc(payload['summary_overall_stats']['note_vi'])}</em>
  </div>
  {era_cards_html}

  <h2>{_esc(L['sec_causes'])}</h2>
  <div class="table-wrap"><table><thead><tr><th>Nguyên nhân</th><th>Số lượng</th></tr></thead><tbody>{cause_rows_html}</tbody></table></div>

  <h2>{_esc(L['sec_system_issues'])}</h2>
  <div class="card"><ul>{sys_issues_html}</ul></div>

  <h2>{_esc(L['sec_vps_off'])}</h2>
  <div class="table-wrap"><table><thead><tr><th>Loại</th><th>Bắt đầu (UTC)</th><th>Kết thúc (UTC)</th><th>Thời lượng</th><th>Nguồn</th><th>Ghi chú</th></tr></thead><tbody>{vps_rows_html}</tbody></table></div>

  <h2>{_esc(L['sec_signal_reconcile'])}</h2>
  <div class="info-box">
    Lab trong cửa sổ: {sig_rec['lab_total_in_window']} | Live: {sig_rec['live_total']} |
    {_esc(L['sig_matched'])}: {sig_rec['matched_count']} | {_esc(L['sig_missing'])}: {sig_rec['missing_count']} |
    {_esc(L['sig_extra'])}: {sig_rec['extra_count']} | {_esc(L['sig_vps_off'])}: {sig_rec['vps_off_count']} |
    {_esc(L['sig_time_skew'])}: {sig_rec['time_skew_count']}
  </div>
  <div class="table-wrap"><table><thead><tr><th>Mã sự kiện</th><th>Thời điểm đóng nến (UTC)</th><th>Trạng thái</th><th>Mã lý do</th><th>Chi tiết</th></tr></thead><tbody>{sig_rows_html}</tbody></table></div>

  <h2>{_esc(L['sec_orders_table'])}</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>{_esc(L['col_event_id'])}</th>
          <th>{_esc(L['col_era'])}</th>
          <th>{_esc(L['col_signal'])}</th>
          <th>{_esc(L['col_entry'])}</th>
          <th>{_esc(L['col_tp_sl'])}</th>
          <th>{_esc(L['col_trailing'])}</th>
          <th>{_esc(L['col_legs'])}</th>
          <th>{_esc(L['col_fees'])}</th>
          <th>{_esc(L['col_funding'])}</th>
          <th>{_esc(L['col_pnl'])}</th>
          <th>{_esc(L['col_outcome'])}</th>
          <th>{_esc(L['col_cause'])}</th>
        </tr>
      </thead>
      <tbody>{order_rows_html}</tbody>
    </table>
  </div>

  <h2>{_esc(L['sec_sources'])}</h2>
  <div class="table-wrap"><table><thead><tr><th>Nguồn</th><th>Số dòng</th><th>Từ (UTC)</th><th>Đến (UTC)</th><th>Trạng thái</th><th>SHA-256</th><th>Ghi chú</th></tr></thead><tbody>{sources_rows_html}</tbody></table></div>

  <h2>{_esc(L['sec_tolerances'])}</h2>
  <div class="table-wrap"><table><thead><tr><th>Tham số</th><th>Giá trị</th></tr></thead><tbody>{tol_items_html}</tbody></table></div>

  <h2>{_esc(L['sec_git_commits'])}</h2>
  <div class="table-wrap"><table><thead><tr><th>Commit</th><th>Ngày giờ</th><th>Nội dung</th></tr></thead><tbody>{commits_rows_html}</tbody></table></div>
</div>
</body>
</html>
"""
