"""Lớp đọc thuần cho dashboard ALL_SIGNALS V3-16.

Không hàm nào trong mô-đun này ghi vào kho run hoặc gọi mạng.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from autotrade_v3.tools.verify_report import verify_mt5_report


DEFAULT_ALL_SIGNALS_STORE = Path("runs/v3_16_all_signals")
DEFAULT_REPORT_STORE = Path("evidence/v3-16/reports")
SUPPORTED_PROFILES = ("IDEAL", "BASE", "STRESS")


@dataclass(frozen=True)
class RunReport:
    """Một run ALL_SIGNALS cùng vị trí artifact báo cáo (nếu có)."""

    run_id: str
    run_dir: Path
    report_dir: Path | None
    generated_at_utc: str
    cost_profile: str
    cost_profile_hash: str
    git_commit: str
    code_dirty: bool
    validation_status: str
    net_profit: float | None
    max_drawdown_m2m_pct: float | None
    entry_count: int | None


@dataclass(frozen=True)
class VerificationResult:
    """Kết quả kiểm tra fail-closed dùng trước khi render báo cáo."""

    passed: bool
    detail: str


def vi(key: str) -> str:
    """Lấy nhãn tiếng Việt từ nguồn duy nhất và fail rõ khi thiếu khóa."""
    from autotrade_v3.reports.labels_vi import LABELS_VI

    return LABELS_VI[key]


def validation_status_vi(status: str) -> str:
    """Chuyển trạng thái máy thành nhãn tiếng Việt dùng chung cho mọi trang."""
    key = {
        "VERIFIED": "verified",
        "PARTIALLY_VERIFIED": "partially_verified",
        "FAILED": "failed",
    }.get(status, "unverified")
    return vi(key)


def get_all_signals_store_dir() -> Path:
    return Path(os.environ.get("AUTOTRADE_V3_16_RUN_STORE_DIR", str(DEFAULT_ALL_SIGNALS_STORE)))


def get_report_store_dir() -> Path:
    return Path(os.environ.get("AUTOTRADE_V3_16_REPORT_DIR", str(DEFAULT_REPORT_STORE)))


def current_git_commit() -> str:
    """Đọc commit cục bộ, không gọi mạng và không làm thay đổi cây git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _metric_value(payload: dict[str, Any], *parts: str) -> Any:
    node: Any = payload
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node.get("value") if isinstance(node, dict) else None


def _report_dir_for(run_dir: Path, run_id: str, report_store: Path) -> Path | None:
    candidates = (run_dir, report_store / run_id)
    for candidate in candidates:
        if (candidate / "report_v1.json").is_file() and (candidate / "report_mt5.html").is_file():
            return candidate
    return None


def _validation_status(run_dir: Path) -> str:
    """validation.json là nguồn duy nhất; không bao giờ tin status trong manifest."""
    validation = _read_json(run_dir / "validation.json")
    if validation is None:
        return "UNVERIFIED"
    return str(validation.get("overall_status") or validation.get("overall_validation") or "UNVERIFIED")


def _manifest_dirs(store_dir: Path) -> list[Path]:
    if not store_dir.is_dir():
        return []
    direct = [path.parent for path in store_dir.glob("*/run_manifest.json")]
    if (store_dir / "run_manifest.json").is_file():
        direct.append(store_dir)
    return sorted(set(direct))


def list_all_signals_runs(
    store_dir: Path | None = None,
    report_store: Path | None = None,
) -> list[RunReport]:
    """Liệt kê run ALL_SIGNALS_PORTFOLIO mới nhất trước, rồi theo run_id."""
    store = store_dir or get_all_signals_store_dir()
    reports = report_store or get_report_store_dir()
    rows: list[RunReport] = []
    for run_dir in _manifest_dirs(store):
        manifest = _read_json(run_dir / "run_manifest.json")
        if manifest is None or str(manifest.get("execution_mode") or manifest.get("mode")) not in (
            "ALL_SIGNALS_PORTFOLIO",
            "BINGX_PORTFOLIO",
        ):
            continue
        run_id = str(manifest.get("run_id") or run_dir.name)
        profile = str(manifest.get("cost_profile") or "").upper()
        if profile not in SUPPORTED_PROFILES:
            continue
        report_dir = _report_dir_for(run_dir, run_id, reports)
        payload = _read_json(report_dir / "report_v1.json") if report_dir else None
        run_info = payload.get("run_info", {}) if payload else {}
        generated = str(
            (payload or {}).get("generated_at_utc")
            or manifest.get("generated_at_utc")
            or manifest.get("created_at_utc")
            or ""
        )
        rows.append(
            RunReport(
                run_id=run_id,
                run_dir=run_dir,
                report_dir=report_dir,
                generated_at_utc=generated,
                cost_profile=profile,
                cost_profile_hash=str(manifest.get("cost_profile_hash") or run_info.get("cost_profile", {}).get("hash") or ""),
                git_commit=str(manifest.get("git_sha") or run_info.get("git_commit") or ""),
                code_dirty=bool(manifest.get("code_dirty", False)),
                validation_status=_validation_status(run_dir),
                net_profit=_as_float(_metric_value(payload or {}, "kpi", "mt5_results", "total_net_profit")),
                max_drawdown_m2m_pct=_as_float(_metric_value(payload or {}, "kpi", "risk", "m2m_max_dd_pct")),
                entry_count=_as_int(_metric_value(payload or {}, "kpi", "volume", "accepted_entries")),
            )
        )
    return sorted(rows, key=lambda row: (row.generated_at_utc, row.run_id), reverse=True)


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def choose_default_run(runs: list[RunReport], requested_run_id: str | None = None) -> RunReport | None:
    """Ưu tiên run được yêu cầu, sau đó BASE mới nhất, cuối cùng run mới nhất."""
    if requested_run_id:
        match = next((run for run in runs if run.run_id == requested_run_id), None)
        if match:
            return match
    return next((run for run in runs if run.cost_profile == "BASE"), runs[0] if runs else None)


def runs_by_profile(runs: list[RunReport]) -> dict[str, RunReport]:
    """Lấy run mới nhất của mỗi profile từ danh sách đã sắp xếp."""
    result: dict[str, RunReport] = {}
    for run in runs:
        result.setdefault(run.cost_profile, run)
    return result


def load_report_json(run: RunReport) -> dict[str, Any] | None:
    if run.report_dir is None:
        return None
    return _read_json(run.report_dir / "report_v1.json")


def load_report_html(run: RunReport) -> str | None:
    if run.report_dir is None:
        return None
    try:
        return (run.report_dir / "report_mt5.html").read_text(encoding="utf-8")
    except OSError:
        return None


def report_json_bytes(run: RunReport) -> bytes | None:
    if run.report_dir is None:
        return None
    try:
        return (run.report_dir / "report_v1.json").read_bytes()
    except OSError:
        return None


def _verify_indexed_hashes(run: RunReport) -> tuple[bool, str]:
    """Kiểm tra hash nếu ARTIFACTS.json có khai báo hai artifact báo cáo."""
    index = _read_json(run.run_dir / "ARTIFACTS.json")
    if not index or run.report_dir is None:
        return True, ""
    artifacts = index.get("artifacts", {})
    for filename in ("report_v1.json", "report_mt5.html"):
        item = artifacts.get(filename)
        if not isinstance(item, dict) or not item.get("sha256"):
            continue
        actual = hashlib.sha256((run.report_dir / filename).read_bytes()).hexdigest()
        if actual != str(item["sha256"]):
            return False, f"HASH_MISMATCH: {filename}"
    return True, ""


def verify_open_report(run: RunReport) -> VerificationResult:
    """Verify report so với ledger mỗi lần gọi; không cache kết quả."""
    if run.report_dir is None:
        return VerificationResult(False, "MISSING_REPORT")
    hash_ok, hash_detail = _verify_indexed_hashes(run)
    if not hash_ok:
        return VerificationResult(False, hash_detail)
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            passed = verify_mt5_report(
                run.run_dir,
                run.report_dir / "report_v1.json",
                run.report_dir / "report_mt5.html",
            )
    except Exception as exc:  # fail-closed: artifact hỏng không được làm sập UI
        return VerificationResult(False, f"VERIFY_EXCEPTION: {type(exc).__name__}: {exc}")
    return VerificationResult(bool(passed), output.getvalue().strip())


def comparison_row(payload: dict[str, Any]) -> dict[str, float | None]:
    """Trích bốn số so sánh bắt buộc từ JSON đã verify."""
    return {
        "net": _as_float(_metric_value(payload, "kpi", "mt5_results", "total_net_profit")),
        "pf": _as_float(_metric_value(payload, "kpi", "risk", "net_pf_leg")),
        "drawdown": _as_float(_metric_value(payload, "kpi", "risk", "m2m_max_dd_pct")),
        "break_even": _as_float(
            _metric_value(payload, "kpi", "cost_reality", "break_even_slippage_per_side_bps")
        ),
    }
