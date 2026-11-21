"""Lưu trữ nguyên tử, khóa tiến trình, raw JSON không ghi đè, Parquet và manifest/sha256."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import time
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .models import (
    FORBIDDEN_MARKET_SUBDIRS,
    SCHEMA_VERSION,
    ParquetArtifact,
    RawArtifact,
    RunManifest,
    sha256_file,
    utc_iso,
)

_SECRET_PATTERNS = (
    re.compile(r"(postgres(?:ql)?://)[^\s'\"@]+@", re.IGNORECASE),
    re.compile(r"((?:API_KEY|API_SECRET|SECRET|TOKEN|PASSWORD|DATABASE_URL|SUPABASE_DB_URL)\s*=\s*)[^\s'\";]+", re.IGNORECASE),
)


class LockError(RuntimeError):
    """Lỗi khi đã có tiến trình collector_v3 khác đang chạy."""


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = redact_secrets(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def redact_secrets(text: str) -> str:
    """Ẩn thông tin nhạy cảm (chuỗi kết nối DB, khóa) trước khi ghi log."""
    out = text
    out = _SECRET_PATTERNS[0].sub(r"\1***:***@", out)
    out = _SECRET_PATTERNS[1].sub(r"\1***", out)
    return out


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj, "f")
    raise TypeError(f"Không thể tuần tự hóa JSON cho kiểu {type(obj).__name__}")


def atomic_write_text(path: Path, content: str) -> None:
    """Ghi tệp tạm cùng thư mục rồi đổi tên nguyên tử."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Ghi JSON nguyên tử (UTF-8, giữ nguyên thứ tự khóa, hỗ trợ Decimal)."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
    atomic_write_text(path, text + "\n")


class CollectorLock:
    """Khóa tệp tin ngăn hai tiến trình collector_v3 chạy chồng nhau."""

    def __init__(self, lock_path: Path, stale_seconds: int = 7200) -> None:
        self.lock_path = lock_path
        self.stale_seconds = stale_seconds
        self._acquired = False

    def _is_stale(self) -> bool:
        try:
            stat = self.lock_path.stat()
        except FileNotFoundError:
            return False
        age = time.time() - stat.st_mtime
        if age > self.stale_seconds:
            return True
        return False

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    info = json.dumps({"pid": os.getpid(), "acquired_at": utc_iso()})
                    os.write(fd, info.encode("utf-8"))
                finally:
                    os.close(fd)
                self._acquired = True
                return
            except FileExistsError:
                if self._is_stale():
                    self.lock_path.unlink(missing_ok=True)
                    continue
                raise LockError(f"Đang có tiến trình thu dữ liệu khác giữ khóa: {self.lock_path}")
        raise LockError(f"Không thể lấy khóa chạy collector: {self.lock_path}")

    def release(self) -> None:
        if self._acquired:
            self.lock_path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> CollectorLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


def setup_logger(log_dir: Path, name: str = "autotrade_v3.collector_v3") -> logging.Logger:
    """Khởi tạo logger xoay vòng trong data/bingx/logs/."""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = RotatingFileHandler(
            log_dir / "collector.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        fh.addFilter(_RedactFilter())
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.addFilter(_RedactFilter())
        logger.addHandler(fh)
        logger.addHandler(sh)
    return logger


class StateStore:
    """Quản lý cursor và trạng thái tiếp tục phân trang trong manifests/state.json."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": SCHEMA_VERSION, "sources": {}}
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state.json không hợp lệ")
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("sources", {})
        return data

    def save(self, state: dict[str, Any]) -> None:
        state["schema_version"] = SCHEMA_VERSION
        state["updated_at"] = utc_iso()
        atomic_write_json(self.state_path, state)


def write_raw_json(
    data_dir: Path,
    source: str,
    payload: Any,
    *,
    collected_at: str | None = None,
    seq: int = 0,
    record_count: int = 0,
) -> RawArtifact:
    """Lưu nguyên bản JSON vào raw/<nguồn>/<YYYY-MM-DD>/<ts>_<seq>.json (không ghi đè)."""
    now = datetime.now(timezone.utc)
    day_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%Y%m%dT%H%M%S%fZ")
    target_dir = data_dir / "raw" / source / day_str
    target_dir.mkdir(parents=True, exist_ok=True)
    idx = seq
    while True:
        target = target_dir / f"{ts_str}_{idx:03d}.json"
        if not target.exists():
            break
        idx += 1
    text = json.dumps(payload, ensure_ascii=False, default=_json_default)
    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, (text + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    rel = str(target.relative_to(data_dir)).replace("\\", "/")
    return RawArtifact(
        path=rel,
        sha256=sha256_file(target),
        record_count=record_count,
        collected_at=collected_at or utc_iso(),
    )


def upsert_parquet(
    data_dir: Path,
    rel_path: str,
    new_rows: list[dict[str, Any]],
    pk_cols: list[str],
    schema: pa.Schema,
    *,
    sort_col: str | None = None,
) -> tuple[int, int, ParquetArtifact | None]:
    """Gộp bản ghi mới vào Parquet hiện có, khử trùng theo khóa chính và ghi nguyên tử."""
    target = data_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_df = pd.DataFrame()
    if target.exists():
        existing_table = pq.read_table(target)
        existing_df = existing_table.to_pandas()

    if not new_rows and existing_df.empty:
        return 0, 0, None

    new_df = pd.DataFrame(new_rows) if new_rows else pd.DataFrame(columns=schema.names)
    if existing_df.empty:
        combined = new_df.copy()
        before_count = 0
    elif new_df.empty:
        combined = existing_df.copy()
        before_count = len(existing_df)
    else:
        before_count = len(existing_df)
        combined = pd.concat([existing_df, new_df], ignore_index=True)

    if pk_cols and not combined.empty:
        combined = combined.drop_duplicates(subset=pk_cols, keep="last")

    if sort_col and sort_col in combined.columns and not combined.empty:
        combined = combined.sort_values(sort_col, kind="mergesort")

    combined = combined.reset_index(drop=True)
    for field in schema:
        if field.name not in combined.columns:
            combined[field.name] = None
    combined = combined[schema.names]

    table = pa.Table.from_pandas(combined, schema=schema, preserve_index=False)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    total_count = len(combined)
    new_unique_count = max(0, total_count - before_count)
    norm_rel = rel_path.replace("\\", "/")
    artifact = ParquetArtifact(
        path=norm_rel,
        sha256=sha256_file(target),
        row_count=total_count,
        schema_version=SCHEMA_VERSION,
    )
    return new_unique_count, total_count, artifact


def save_run_manifest_and_coverage(data_dir: Path, manifest: RunManifest) -> None:
    """Lưu manifest của lần chạy, latest_manifest.json và bảng độ phủ coverage.json/parquet."""
    manifests_dir = data_dir / "manifests"
    runs_dir = manifests_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    payload = manifest.to_dict()
    atomic_write_json(runs_dir / f"{manifest.run_id}.json", payload)

    # Cập nhật latest_manifest.json: giữ nguồn cũ nếu lần này chỉ chạy --only
    latest_path = manifests_dir / "latest_manifest.json"
    merged_sources: dict[str, Any] = {}
    if latest_path.exists():
        try:
            prev = json.loads(latest_path.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and isinstance(prev.get("sources"), dict):
                merged_sources.update(prev["sources"])
        except Exception:
            pass
    for src_name, res in payload["sources"].items():
        merged_sources[src_name] = res

    latest_payload = {
        "run_id": manifest.run_id,
        "collected_at": manifest.collected_at,
        "dry_run": manifest.dry_run,
        "overall_status": manifest.overall_status,
        "schema_version": SCHEMA_VERSION,
        "message_vi": manifest.message_vi,
        "sources": merged_sources,
    }
    atomic_write_json(latest_path, latest_payload)

    coverage_rows: list[dict[str, Any]] = []
    for src_name in sorted(merged_sources.keys()):
        s = merged_sources[src_name]
        gaps = s.get("gaps") or []
        coverage_rows.append({
            "source": src_name,
            "status": str(s.get("status", "GAP")),
            "provenance": str(s.get("provenance", "UNRESOLVED")),
            "collected_at": str(s.get("collected_at", "")),
            "min_ts_ms": s.get("min_ts_ms"),
            "max_ts_ms": s.get("max_ts_ms"),
            "new_records": int(s.get("new_records", 0) or 0),
            "total_records": int(s.get("total_records", 0) or 0),
            "gap_count": len(gaps),
            "gaps_json": json.dumps(gaps, ensure_ascii=False),
            "cursor": json.dumps(s.get("cursor"), ensure_ascii=False, default=_json_default),
            "schema_version": str(s.get("schema_version", SCHEMA_VERSION)),
            "error": s.get("error") or "",
            "message_vi": str(s.get("message_vi", "")),
        })

    atomic_write_json(manifests_dir / "coverage.json", {
        "updated_at": manifest.collected_at,
        "schema_version": SCHEMA_VERSION,
        "sources": coverage_rows,
    })

    cov_schema = pa.schema([
        ("source", pa.string()),
        ("status", pa.string()),
        ("provenance", pa.string()),
        ("collected_at", pa.string()),
        ("min_ts_ms", pa.int64()),
        ("max_ts_ms", pa.int64()),
        ("new_records", pa.int64()),
        ("total_records", pa.int64()),
        ("gap_count", pa.int64()),
        ("gaps_json", pa.string()),
        ("cursor", pa.string()),
        ("schema_version", pa.string()),
        ("error", pa.string()),
        ("message_vi", pa.string()),
    ])
    cov_df = pd.DataFrame(coverage_rows) if coverage_rows else pd.DataFrame(columns=cov_schema.names)
    cov_table = pa.Table.from_pandas(cov_df, schema=cov_schema, preserve_index=False)
    cov_pq = manifests_dir / "coverage.parquet"
    tmp = cov_pq.with_name(f".{cov_pq.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        pq.write_table(cov_table, tmp, compression="zstd")
        os.replace(tmp, cov_pq)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def verify_warehouse(data_dir: Path) -> dict[str, Any]:
    """Kiểm tra toàn vẹn SHA-256 của raw + Parquet và xác nhận không có thư mục spread."""
    mismatches: list[str] = []
    missing: list[str] = []
    checked_files = 0

    for forbidden in FORBIDDEN_MARKET_SUBDIRS:
        if (data_dir / "market" / forbidden).exists():
            mismatches.append(f"Tồn tại thư mục bị cấm theo quyết định 15/11: market/{forbidden}")

    latest_path = data_dir / "manifests" / "latest_manifest.json"
    if not latest_path.exists():
        return {
            "ok": False,
            "checked_files": 0,
            "missing": ["manifests/latest_manifest.json"],
            "mismatches": mismatches,
            "message_vi": "Chưa có manifests/latest_manifest.json để kiểm tra toàn vẹn.",
        }

    manifest = json.loads(latest_path.read_text(encoding="utf-8"))
    sources = manifest.get("sources") or {}
    for src_name, src_data in sources.items():
        for bucket in ("raw_files", "parquet_files"):
            for item in src_data.get(bucket) or []:
                rel = item.get("path", "")
                expected_sha = item.get("sha256", "")
                fpath = data_dir / rel
                if not fpath.exists():
                    missing.append(f"{src_name}:{rel}")
                    continue
                checked_files += 1
                actual_sha = sha256_file(fpath)
                if actual_sha != expected_sha:
                    mismatches.append(f"{src_name}:{rel} (kỳ vọng {expected_sha[:12]}..., thực tế {actual_sha[:12]}...)")

    ok = (len(missing) == 0) and (len(mismatches) == 0)
    msg = (
        f"Kiểm tra toàn vẹn ĐẠT: {checked_files} tệp khớp SHA-256."
        if ok
        else f"Kiểm tra toàn vẹn LỖI: {len(missing)} tệp thiếu, {len(mismatches)} tệp sai SHA-256."
    )
    return {
        "ok": ok,
        "checked_files": checked_files,
        "missing": missing,
        "mismatches": mismatches,
        "message_vi": msg,
    }
