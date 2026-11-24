"""Phân tách kỷ nguyên cấu trúc thoát, lịch sử commit live và suy luận cửa sổ VPS_TẮT."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any

ERA_1_50TP_50TRAIL = "ERA_1_50TP_50TRAIL"
ERA_2_FIXED_TP_SL = "ERA_2_FIXED_TP_SL"


@dataclass(frozen=True)
class LiveCommitInfo:
    sha: str
    date_iso: str
    subject: str


@dataclass(frozen=True)
class VpsOffWindow:
    start_ms: int
    end_ms: int
    start_utc: str
    end_utc: str
    duration_hours: float
    source: str
    classification: str  # "VPS_TẮT" | "NO_LOG_ACTIVITY"
    note: str


def _parse_iso_ms(val: str) -> int:
    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _ms_to_iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def classify_order_era(order_row: dict[str, Any]) -> str:
    """Xác định kỷ nguyên cấu trúc thoát từ cột exit_mode của execution_orders."""
    exit_mode = str(order_row.get("exit_mode") or "").strip().lower()
    if exit_mode == "split_trailing":
        return ERA_1_50TP_50TRAIL
    if exit_mode == "fixed_tp_sl":
        return ERA_2_FIXED_TP_SL
    raise ValueError(
        f"Không xác định được kỷ nguyên từ exit_mode={exit_mode!r} cho event_id={order_row.get('event_id')}"
    )


def list_live_commits(repo_root: Path | None = None, limit: int = 40) -> list[LiveCommitInfo]:
    """Liệt kê các commit/PR thay đổi autotrade_v3/live trên origin/main kèm ngày (chỉ đọc git)."""
    cwd = str(repo_root) if repo_root else None
    try:
        proc = subprocess.run(
            ["git", "log", "origin/main", f"-n{limit}", "--format=%h|%ad|%s", "--date=iso", "--", "autotrade_v3/live"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        commits: list[LiveCommitInfo] = []
        for line in proc.stdout.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append(LiveCommitInfo(sha=parts[0], date_iso=parts[1], subject=parts[2]))
        return commits
    except (OSError, subprocess.SubprocessError):
        return []


def infer_vps_off_windows(
    control_logs: list[dict[str, Any]],
    candles_15m: list[dict[str, Any]],
    *,
    min_gap_seconds: int = 7200,
) -> tuple[list[VpsOffWindow], list[VpsOffWindow]]:
    """Suy ra cửa sổ VPS_TẮT và các khoảng trống nhật ký từ control_logs và nến 15m.

    Trả về (vps_off_windows, other_log_gaps).
    Cửa sổ VPS_TẮT được xác định khi khoảng trống control_logs (hoặc nến 15m) khớp với
    thời điểm ngừng hoạt động cuối kỳ (2026-11-21 ~17:30 UTC -> 2026-11-22 ~02:30 UTC) hoặc thiếu nến 15m.
    """
    vps_off: list[VpsOffWindow] = []
    other_gaps: list[VpsOffWindow] = []

    # 1. Kiểm tra khoảng trống nến 15m
    for sym in ("BTC-USDT", "ETH-USDT"):
        ts = sorted(int(r["open_time"]) for r in candles_15m if r.get("symbol") == sym)
        for a, b in zip(ts, ts[1:]):
            if b - a > 900_000:
                vps_off.append(
                    VpsOffWindow(
                        start_ms=a + 900_000,
                        end_ms=b,
                        start_utc=_ms_to_iso(a + 900_000),
                        end_utc=_ms_to_iso(b),
                        duration_hours=round((b - a - 900_000) / 3_600_000.0, 2),
                        source=f"candles_15m:{sym}",
                        classification="VPS_TẮT",
                        note=f"Thiếu nến 15m {sym}",
                    )
                )

    # 2. Kiểm tra khoảng trống control_logs
    times_ms = sorted(_parse_iso_ms(str(r["created_at"])) for r in control_logs if r.get("created_at"))
    for a, b in zip(times_ms, times_ms[1:]):
        diff_sec = (b - a) / 1000.0
        if diff_sec >= min_gap_seconds:
            start_utc = _ms_to_iso(a)
            end_utc = _ms_to_iso(b)
            dur_h = round(diff_sec / 3600.0, 2)
            # Cửa sổ 21/11 ~17:30 UTC -> 22/11 ~02:30 UTC (đổi mật khẩu DB)
            is_known_vps_off = b >= _parse_iso_ms("2026-11-22T00:00:00Z") and a >= _parse_iso_ms("2026-11-21T17:00:00Z")
            win = VpsOffWindow(
                start_ms=a,
                end_ms=b,
                start_utc=start_utc,
                end_utc=end_utc,
                duration_hours=dur_h,
                source="control_logs",
                classification="VPS_TẮT" if is_known_vps_off else "NO_LOG_ACTIVITY",
                note=(
                    "Khoảng trống control_logs khi tắt VPS (đổi mật khẩu database)"
                    if is_known_vps_off
                    else "Khoảng trống giữa hai lần ghi control_logs (có thể do không có sự kiện/lỗi)"
                ),
            )
            if is_known_vps_off:
                vps_off.append(win)
            else:
                other_gaps.append(win)

    return vps_off, other_gaps


def is_in_vps_off_window(ts_ms: int, vps_off_windows: list[VpsOffWindow]) -> VpsOffWindow | None:
    """Kiểm tra một timestamp ms có nằm trong cửa sổ VPS_TẮT hay không."""
    for win in vps_off_windows:
        if win.start_ms <= ts_ms <= win.end_ms:
            return win
    return None
