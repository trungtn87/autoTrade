"""Trình chạy đồng bộ thủ công trên dashboard một trang (nút 'Đồng bộ').

NGOẠI LỆ CÓ CHỦ Ý CỦA NGUYÊN TẮC DASHBOARD CHỈ ĐỌC:
- Nút 'Đồng bộ' chạy `python -m autotrade_v3.reconcile_v3 refresh` trong tiến trình con (không chặn UI).
- Chỉ ghi vào `data/bingx/`, `evidence/v3-17a/` và cache dashboard; KHÔNG ghi vào bất kỳ bảng Supabase/BingX nào.
- Chỉ đọc Supabase qua collector_v3 bằng biến môi trường `SUPABASE_DB_URL` của tiến trình.
- An toàn: khóa chống chạy đồng thời, chặn bấm liên tiếp (tối thiểu 60s), dừng ngay khi lỗi xác thực (không retry),
  và che toàn bộ chuỗi kết nối / mật khẩu / API key trước khi hiển thị hoặc ghi trạng thái.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from typing import Any

from autotrade_v3.reconcile_v3.labels_vi import RECONCILE_LABELS_VI as L

VN_TZ = timezone(timedelta(hours=7))

_PROCESS_LOCK = threading.Lock()
_ACTIVE_THREADS: dict[str, threading.Thread] = {}

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"postgres(?:ql)?://[^\s'\"<>]+", re.IGNORECASE),
    re.compile(r"(password|passwd|pwd|secret|api_?key|token)\s*[:=]\s*[^\s,;'\"]+", re.IGNORECASE),
)


def redact_secrets(text: str) -> str:
    """Che toàn bộ chuỗi kết nối DB, mật khẩu, API key khỏi thông báo lỗi và log."""
    out = str(text or "")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[ĐÃ_CHE_BÍ_MẬT]", out)
    for env_name in ("SUPABASE_DB_URL", "BINGX_API_KEY", "BINGX_API_SECRET"):
        val = os.environ.get(env_name, "")
        if val and len(val) >= 4:
            out = out.replace(val, "[ĐÃ_CHE_BÍ_MẬT]")
    return out


def format_vn_time(iso_utc: str | None) -> str:
    """Chuyển timestamp UTC ISO sang giờ Việt Nam (UTC+7)."""
    if not iso_utc or iso_utc == "—":
        return "Chưa có"
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_vn = dt.astimezone(VN_TZ)
        return dt_vn.strftime("%H:%M:%S %d/%m/%Y")
    except ValueError:
        return "Chưa rõ"


def evaluate_sync_freshness(
    last_sync_utc: str | None,
    *,
    warn_hours: float = 6.0,
    critical_hours: float = 24.0,
    now_utc: datetime | None = None,
) -> tuple[str, str, str]:
    """Đánh giá độ trễ lần đồng bộ cuối: GREEN (<=6h), AMBER (>6h, cam), RED (>24h, đỏ)."""
    if not last_sync_utc or last_sync_utc == "—":
        return "RED", "Chưa có", "Chưa từng đồng bộ"
    try:
        dt = datetime.fromisoformat(str(last_sync_utc).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return "RED", "Chưa rõ", "Lỗi định dạng thời gian đồng bộ"

    now = now_utc or datetime.now(timezone.utc)
    age_hours = (now - dt).total_seconds() / 3600.0
    vn_str = dt.astimezone(VN_TZ).strftime("%H:%M:%S %d/%m/%Y")
    if age_hours > critical_hours:
        return "RED", vn_str, f"Quá {critical_hours:g} giờ ({age_hours:.1f}h)"
    if age_hours > warn_hours:
        return "AMBER", vn_str, f"Quá {warn_hours:g} giờ ({age_hours:.1f}h)"
    return "GREEN", vn_str, f"Cập nhật ({age_hours:.1f}h trước)"


def _state_path(evidence_dir: Path) -> Path:
    return evidence_dir / ".sync_state.json"


def _lock_path(evidence_dir: Path) -> Path:
    return evidence_dir / ".sync.lock"


def read_sync_state(evidence_dir: Path) -> dict[str, Any]:
    sp = _state_path(evidence_dir)
    if not sp.is_file():
        return {"status": "IDLE", "message_vi": "", "started_at_utc": None, "finished_at_utc": None, "pid": None}
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"status": "IDLE", "message_vi": "", "started_at_utc": None, "finished_at_utc": None, "pid": None}


def _write_sync_state(evidence_dir: Path, state: dict[str, Any]) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _state_path(evidence_dir).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_lock_active(evidence_dir: Path) -> bool:
    lp = _lock_path(evidence_dir)
    if not lp.is_file():
        return False
    # Kiểm tra stale lock quá 30 phút
    try:
        age = datetime.now(timezone.utc).timestamp() - lp.stat().st_mtime
        if age > 1800:
            lp.unlink(missing_ok=True)
            return False
    except OSError:
        return False
    return True


def trigger_manual_sync(
    *,
    repo_root: Path,
    data_root: Path,
    evidence_dir: Path,
    cooldown_seconds: int = 60,
    skip_collector: bool = False,
) -> tuple[bool, str]:
    """Khởi chạy đồng bộ thủ công trong tiến trình con (không chặn giao diện).

    Trả về (accepted, message_vi).
    """
    if not skip_collector and not os.environ.get("SUPABASE_DB_URL", "").strip():
        msg = L["sync_missing_env"]
        _write_sync_state(
            evidence_dir,
            {
                "status": "ERROR",
                "message_vi": L["sync_error_prefix"].format(reason=msg),
                "started_at_utc": None,
                "finished_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "pid": None,
            },
        )
        return False, msg

    with _PROCESS_LOCK:
        cur = read_sync_state(evidence_dir)
        if cur.get("status") == "RUNNING" or _is_lock_active(evidence_dir):
            return False, L["sync_already_running"]

        last_done = cur.get("finished_at_utc") or cur.get("started_at_utc")
        if last_done:
            try:
                last_dt = datetime.fromisoformat(str(last_done).replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed < cooldown_seconds:
                    wait_s = int(cooldown_seconds - elapsed) + 1
                    return False, L["sync_cooldown_msg"].format(sec=cooldown_seconds) + f" (còn {wait_s}s)"
            except ValueError:
                pass

        evidence_dir.mkdir(parents=True, exist_ok=True)
        _lock_path(evidence_dir).write_text(str(os.getpid()), encoding="utf-8")
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_sync_state(
            evidence_dir,
            {
                "status": "RUNNING",
                "message_vi": L["sync_running"],
                "started_at_utc": now_iso,
                "finished_at_utc": None,
                "pid": None,
            },
        )

    cmd = [
        sys.executable,
        "-m",
        "autotrade_v3.reconcile_v3",
        "refresh",
        "--data-root",
        str(data_root),
        "--output-dir",
        str(evidence_dir),
        "--repo-root",
        str(repo_root),
    ]
    if skip_collector:
        cmd.append("--skip-collector")

    def _worker() -> None:
        try:
            sub_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            proc = subprocess.Popen(
                cmd,
                cwd=str(repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=sub_env,
            )
            _write_sync_state(
                evidence_dir,
                {
                    "status": "RUNNING",
                    "message_vi": L["sync_running"],
                    "started_at_utc": now_iso,
                    "finished_at_utc": None,
                    "pid": proc.pid,
                },
            )
            stdout, stderr = proc.communicate(timeout=600)
            fin_dt = datetime.now(timezone.utc)
            fin_iso = fin_dt.isoformat().replace("+00:00", "Z")
            fin_vn = fin_dt.astimezone(VN_TZ).strftime("%H:%M")

            if proc.returncode == 0:
                summary_line = (stdout or "").strip().splitlines()[-1] if (stdout or "").strip() else ""
                _write_sync_state(
                    evidence_dir,
                    {
                        "status": "SUCCESS",
                        "message_vi": L["sync_done_at"].format(time=fin_vn),
                        "summary_line": redact_secrets(summary_line),
                        "started_at_utc": now_iso,
                        "finished_at_utc": fin_iso,
                        "pid": proc.pid,
                    },
                )
            else:
                raw_err = (stderr or stdout or f"Mã thoát {proc.returncode}").strip()
                clean_err = redact_secrets(raw_err)[:300]
                _write_sync_state(
                    evidence_dir,
                    {
                        "status": "ERROR",
                        "message_vi": L["sync_error_prefix"].format(reason=clean_err),
                        "started_at_utc": now_iso,
                        "finished_at_utc": fin_iso,
                        "pid": proc.pid,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            fin_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            _write_sync_state(
                evidence_dir,
                {
                    "status": "ERROR",
                    "message_vi": L["sync_error_prefix"].format(reason=redact_secrets(str(exc))[:300]),
                    "started_at_utc": now_iso,
                    "finished_at_utc": fin_iso,
                    "pid": None,
                },
            )
        finally:
            _lock_path(evidence_dir).unlink(missing_ok=True)

    t = threading.Thread(target=_worker, daemon=True, name="ninlab-manual-sync")
    _ACTIVE_THREADS[str(evidence_dir)] = t
    t.start()
    return True, L["sync_running"]
