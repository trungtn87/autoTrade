"""Xác minh tính toàn vẹn, tái lập và đối chiếu lại số tổng từ bảng từng lệnh."""
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


def verify_report(payload: dict[str, Any], html_content: str | None = None) -> VerifyResult:
    """Tính lại toàn bộ số tổng từ bảng `orders`, kiểm tra SHA-256 và rò rỉ nhãn tiếng Anh."""
    errors: list[str] = []

    # 1. Xác minh SHA-256 chuẩn tắc
    expected_sha = str(payload.get("report_sha256") or "")
    actual_sha = compute_canonical_hash(payload)
    if not expected_sha or expected_sha != actual_sha:
        errors.append(f"Sai lệch report_sha256: expected={expected_sha}, actual={actual_sha}")

    orders: list[dict[str, Any]] = payload.get("orders", [])
    sg = payload.get("sample_governance", {})
    if int(sg.get("total_orders", -1)) != len(orders):
        errors.append(f"Sai total_orders: header={sg.get('total_orders')} vs rows={len(orders)}")

    actual_closed = sum(1 for r in orders if r.get("is_fully_closed"))
    if int(sg.get("closed_orders", -1)) != actual_closed:
        errors.append(f"Sai closed_orders: header={sg.get('closed_orders')} vs rows={actual_closed}")

    # 2. Tính lại thống kê toàn mẫu
    slips_all = [_d(r["items"]["entry"]["live_slippage_bps"]) for r in orders]
    avg_all = (sum(slips_all, ZERO) / Decimal(len(orders))).quantize(Decimal("0.01")) if orders else ZERO
    p95_all = _p95(slips_all).quantize(Decimal("0.01"))
    overall = payload.get("summary_overall_stats", {})
    if abs(_d(overall.get("avg_entry_slippage_bps")) - avg_all) > TOL:
        errors.append(f"Sai avg_entry_slippage_bps: header={overall.get('avg_entry_slippage_bps')} vs actual={avg_all}")
    if abs(_d(overall.get("p95_entry_slippage_bps")) - p95_all) > TOL:
        errors.append(f"Sai p95_entry_slippage_bps: header={overall.get('p95_entry_slippage_bps')} vs actual={p95_all}")

    # 3. Tính lại từng kỷ nguyên từ orders
    for era_name, era_sum in payload.get("era_summaries", {}).items():
        sub = [r for r in orders if r.get("era") == era_name]
        if int(era_sum.get("orders_count", -1)) != len(sub):
            errors.append(f"Sai orders_count kỷ nguyên {era_name}: {era_sum.get('orders_count')} vs {len(sub)}")
        sub_closed = sum(1 for r in sub if r.get("is_fully_closed"))
        if int(era_sum.get("closed_orders_count", -1)) != sub_closed:
            errors.append(f"Sai closed_orders_count kỷ nguyên {era_name}: {era_sum.get('closed_orders_count')} vs {sub_closed}")

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
                errors.append(f"Sai lệch {era_name}.{field_name}: báo cáo={reported} vs tính lại={recomputed}")

    # 4. Tính lại phân bố nguyên nhân
    re_causes: dict[str, int] = {}
    for r in orders:
        c = str(r.get("root_cause") or "")
        re_causes[c] = re_causes.get(c, 0) + 1
    if dict(sorted(re_causes.items())) != dict(sorted(payload.get("cause_distribution", {}).items())):
        errors.append(f"Sai lệch cause_distribution: {payload.get('cause_distribution')} vs {re_causes}")

    # 5. Kiểm tra rò rỉ nhãn tiếng Anh trong HTML
    if html_content is not None:
        leaks = find_english_label_leaks(html_content)
        if leaks:
            errors.append(f"Phát hiện nhãn tiếng Anh rò rỉ trong HTML: {leaks}")

    return VerifyResult(passed=(len(errors) == 0), errors=errors, report_sha256=actual_sha)


def verify_report_files(json_path: Path, html_path: Path | None = None) -> VerifyResult:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    html_text = html_path.read_text(encoding="utf-8") if (html_path and html_path.is_file()) else None
    return verify_report(payload, html_text)
