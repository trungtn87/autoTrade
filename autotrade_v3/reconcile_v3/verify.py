"""Xác minh tính toàn vẹn, tái lập và đối chiếu lại số tổng từ bảng từng lệnh (bước 2)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import math
from pathlib import Path
from typing import Any

from autotrade_v3.reconcile_v3.labels_vi import find_english_label_leaks
from autotrade_v3.reconcile_v3.report_vi import compute_canonical_hash

ZERO = Decimal("0")
TOL = Decimal("0.0001")


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


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    errors: list[str]
    report_sha256: str


def _verify_era_block(
    *,
    block_name: str,
    rows: list[dict[str, Any]],
    era_summaries: dict[str, Any],
    cause_dist: dict[str, int],
    sys_issues: list[dict[str, Any]],
    errors: list[str],
) -> None:
    for era_name, era_sum in era_summaries.items():
        sub = [r for r in rows if r.get("era") == era_name]
        if int(era_sum.get("orders_count", -1)) != len(sub):
            errors.append(f"[{block_name}] Sai orders_count kỷ nguyên {era_name}: {era_sum.get('orders_count')} vs {len(sub)}")
        sub_closed = sum(1 for r in sub if r.get("is_fully_closed"))
        if int(era_sum.get("closed_orders_count", -1)) != sub_closed:
            errors.append(f"[{block_name}] Sai closed_orders_count kỷ nguyên {era_name}: {era_sum.get('closed_orders_count')} vs {sub_closed}")

        checks = [
            ("live_gross_pnl_usd", sum((_d(r["items"]["pnl"]["live_gross_pnl"]) for r in sub), ZERO)),
            ("live_net_pnl_usd", sum((_d(r["items"]["pnl"]["live_net_pnl"]) for r in sub), ZERO)),
            ("live_total_fee_usd", sum((_d(r["items"]["fees"]["live_total_fee"]) for r in sub), ZERO)),
            ("sim_base_gross_pnl_usd", sum((_d(r["items"]["pnl"]["sim_base_gross_pnl"]) for r in sub), ZERO)),
            ("sim_base_net_pnl_usd", sum((_d(r["items"]["pnl"]["sim_base_net_pnl"]) for r in sub), ZERO)),
            ("sim_base_total_fee_usd", sum((_d(r["items"]["fees"]["sim_base_fee"]) for r in sub), ZERO)),
            ("sim_ideal_gross_pnl_usd", sum((_d(r["items"]["pnl"]["sim_ideal_gross_pnl"]) for r in sub), ZERO)),
            ("sim_ideal_net_pnl_usd", sum((_d(r["items"]["pnl"]["sim_ideal_net_pnl"]) for r in sub), ZERO)),
            ("sim_ideal_total_fee_usd", sum((_d(r["items"]["fees"]["sim_ideal_fee"]) for r in sub), ZERO)),
        ]
        for field_name, recomputed in checks:
            reported = _d(era_sum.get(field_name))
            if abs(reported - recomputed) > TOL:
                errors.append(f"[{block_name}] Sai lệch {era_name}.{field_name}: báo cáo={reported} vs tính lại={recomputed}")

    re_causes: dict[str, int] = {}
    for r in rows:
        c = str(r.get("root_cause") or "")
        re_causes[c] = re_causes.get(c, 0) + 1
    if dict(sorted(re_causes.items())) != dict(sorted(cause_dist.items())):
        errors.append(f"[{block_name}] Sai lệch cause_distribution: {cause_dist} vs {re_causes}")

    expected_sys = sorted(str(r.get("event_id") or "") for r in rows if r.get("root_cause") == "HỆ_THỐNG")
    reported_sys = sorted(str(it.get("event_id") or "") for it in sys_issues)
    if expected_sys != reported_sys:
        errors.append(f"[{block_name}] Sai lệch system_issues: {reported_sys} vs {expected_sys}")


def verify_report(payload: dict[str, Any], html_content: str | None = None) -> VerifyResult:
    """Tính lại toàn bộ số tổng từ bảng `orders` (cả khối chính từ compare_from và khối Lịch sử)."""
    errors: list[str] = []

    expected_sha = str(payload.get("report_sha256") or "")
    actual_sha = compute_canonical_hash(payload)
    if not expected_sha or expected_sha != actual_sha:
        errors.append(f"Sai lệch report_sha256: expected={expected_sha}, actual={actual_sha}")

    orders: list[dict[str, Any]] = payload.get("orders", [])
    main_rows = [r for r in orders if r.get("is_after_compare_from")]
    hist_rows = [r for r in orders if not r.get("is_after_compare_from")]

    sg = payload.get("sample_governance", {})
    if int(sg.get("total_orders", -1)) != len(main_rows):
        errors.append(f"Sai total_orders (từ compare_from): header={sg.get('total_orders')} vs rows={len(main_rows)}")

    actual_closed_main = sum(1 for r in main_rows if r.get("is_fully_closed"))
    if int(sg.get("closed_orders", -1)) != actual_closed_main:
        errors.append(f"Sai closed_orders (từ compare_from): header={sg.get('closed_orders')} vs rows={actual_closed_main}")

    overall = payload.get("summary_overall_stats", {})
    if main_rows:
        slips_main = [_d(r["items"]["entry"]["live_slippage_bps"]) for r in main_rows]
        avg_main = (sum(slips_main, ZERO) / Decimal(len(main_rows))).quantize(Decimal("0.01"))
        p95_main = _p95(slips_main).quantize(Decimal("0.01"))
        if abs(_d(overall.get("avg_entry_slippage_bps")) - avg_main) > TOL:
            errors.append(f"Sai avg_entry_slippage_bps: header={overall.get('avg_entry_slippage_bps')} vs actual={avg_main}")
        if abs(_d(overall.get("p95_entry_slippage_bps")) - p95_main) > TOL:
            errors.append(f"Sai p95_entry_slippage_bps: header={overall.get('p95_entry_slippage_bps')} vs actual={p95_main}")
    else:
        if overall.get("avg_entry_slippage_bps") is not None or overall.get("p95_entry_slippage_bps") is not None:
            errors.append("Không có lệnh sau compare_from nhưng summary_overall_stats slippage khác None")

    _verify_era_block(
        block_name="since_compare_from",
        rows=main_rows,
        era_summaries=payload.get("era_summaries", {}),
        cause_dist=payload.get("cause_distribution", {}),
        sys_issues=payload.get("system_issues", []),
        errors=errors,
    )

    hist = payload.get("historical", {})
    if int(hist.get("total_orders", -1)) != len(hist_rows):
        errors.append(f"Sai historical.total_orders: {hist.get('total_orders')} vs {len(hist_rows)}")
    _verify_era_block(
        block_name="historical",
        rows=hist_rows,
        era_summaries=hist.get("era_summaries", {}),
        cause_dist=hist.get("cause_distribution", {}),
        sys_issues=hist.get("system_issues", []),
        errors=errors,
    )

    if html_content is not None:
        leaks = find_english_label_leaks(html_content)
        if leaks:
            errors.append(f"Phát hiện nhãn tiếng Anh rò rỉ trong HTML: {leaks}")

    return VerifyResult(passed=(len(errors) == 0), errors=errors, report_sha256=actual_sha)


def verify_report_files(json_path: Path, html_path: Path | None = None) -> VerifyResult:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    html_text = html_path.read_text(encoding="utf-8") if (html_path and html_path.is_file()) else None
    return verify_report(payload, html_text)
