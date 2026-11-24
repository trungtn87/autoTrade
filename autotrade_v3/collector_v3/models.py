"""Mô hình dữ liệu, hằng số chuẩn hóa và kiểm tra độ chính xác cho collector_v3."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"
PROVENANCE_STATUSES = ("EXACT", "RECONCILED", "ESTIMATED", "UNRESOLVED")
RUN_STATUSES = ("COMPLETE", "INCOMPLETE", "GAP")
DEFAULT_SYMBOLS = ("BTC-USDT", "ETH-USDT")
FORBIDDEN_MARKET_SUBDIRS = ("spread",)


def utc_now_ms() -> int:
    """Trả về thời điểm UTC hiện tại tính bằng mili-giây."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def utc_iso(ts_ms: int | None = None) -> str:
    """Định dạng thời điểm UTC ISO-8601."""
    if ts_ms is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def parse_since_ms(value: str | int | None) -> int | None:
    """Chuyển chuỗi ngày/ISO hoặc mili-giây thành timestamp ms UTC."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        num = int(raw)
        return num * 1000 if num < 10_000_000_000 else num
    normalized = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def to_decimal_str(value: Any, *, field_name: str = "value", allow_none: bool = False) -> str | None:
    """Chuyển số hoặc chuỗi thành chuỗi Decimal chính xác, chống mất precision."""
    if value is None or value == "":
        if allow_none:
            return None
        raise ValueError(f"Thiếu giá trị Decimal bắt buộc cho trường '{field_name}'")
    if isinstance(value, bool):
        raise ValueError(f"Trường '{field_name}' không chấp nhận kiểu bool")
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Giá trị Decimal không hợp lệ ở '{field_name}': {value!r}") from exc
    if not dec.is_finite():
        raise ValueError(f"Giá trị Decimal không hữu hạn ở '{field_name}': {value!r}")
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
        if text == "-0":
            text = "0"
    return text


def to_id_str(value: Any, *, field_name: str = "id", allow_none: bool = False) -> str | None:
    """Chuẩn hóa ID thành chuỗi chính xác, từ chối float/ký hiệu khoa học."""
    if value is None or value == "":
        if allow_none:
            return None
        raise ValueError(f"Thiếu ID bắt buộc cho trường '{field_name}'")
    if isinstance(value, (bool, float)):
        raise ValueError(f"ID '{field_name}' không được ở dạng bool/float: {value!r}")
    text = str(value).strip()
    if not text:
        if allow_none:
            return None
        raise ValueError(f"ID '{field_name}' rỗng")
    if any(ch in text for ch in ("e", "E", "+")) and "." in text:
        raise ValueError(f"ID '{field_name}' bị mất độ chính xác dạng khoa học: {text!r}")
    return text


def sha256_file(path: Path) -> str:
    """Tính SHA-256 hex cho một tệp tin."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class RawArtifact:
    path: str
    sha256: str
    record_count: int
    collected_at: str


@dataclass
class ParquetArtifact:
    path: str
    sha256: str
    row_count: int
    schema_version: str = SCHEMA_VERSION


@dataclass
class SourceResult:
    source: str
    status: str  # COMPLETE | INCOMPLETE | GAP
    provenance: str  # EXACT | RECONCILED | ESTIMATED | UNRESOLVED
    collected_at: str
    min_ts_ms: int | None = None
    max_ts_ms: int | None = None
    new_records: int = 0
    total_records: int = 0
    cursor: Any = None
    gaps: list[dict[str, Any]] = field(default_factory=list)
    raw_files: list[RawArtifact] = field(default_factory=list)
    parquet_files: list[ParquetArtifact] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    error: str | None = None
    message_vi: str = ""

    def __post_init__(self) -> None:
        if self.status not in RUN_STATUSES:
            raise ValueError(f"Trạng thái không hợp lệ: {self.status}")
        if self.provenance not in PROVENANCE_STATUSES:
            raise ValueError(f"Mức độ tin cậy dữ liệu không hợp lệ: {self.provenance}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunManifest:
    run_id: str
    collected_at: str
    dry_run: bool
    overall_status: str
    sources: dict[str, SourceResult] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    message_vi: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "collected_at": self.collected_at,
            "dry_run": self.dry_run,
            "overall_status": self.overall_status,
            "schema_version": self.schema_version,
            "message_vi": self.message_vi,
            "sources": {k: v.to_dict() for k, v in self.sources.items()},
        }
