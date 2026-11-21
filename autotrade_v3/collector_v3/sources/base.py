"""Giao diện cơ sở cho các nguồn thu dữ liệu độc lập."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Protocol

from ..models import DEFAULT_SYMBOLS, SourceResult


@dataclass
class CollectContext:
    data_dir: Path
    source_state: dict[str, Any]
    dry_run: bool
    now_ms: int
    collected_at: str
    since_ms: int | None = None
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    http_client: Any = None
    db_reader: Any = None
    max_pages_per_run: int | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("autotrade_v3.collector_v3"))


class BaseSource(Protocol):
    name: str

    def collect(self, ctx: CollectContext) -> SourceResult:
        """Thu dữ liệu cho một nguồn; tự cập nhật raw, parquet và trả về SourceResult."""
        ...
