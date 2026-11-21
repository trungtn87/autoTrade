"""Nguồn dữ liệu chỉ-đọc từ Supabase (autotrade_analytics.* và autotrade_v3.*)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
import re
from typing import Any

import pyarrow as pa

from ..models import (
    SCHEMA_VERSION,
    ParquetArtifact,
    RawArtifact,
    SourceResult,
    to_decimal_str,
    to_id_str,
)
from ..storage import upsert_parquet, write_raw_json
from .base import CollectContext

OVERLAP_MS = 5 * 60_000
DEFAULT_PAGE_SIZE = 1000

_FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|MERGE|CALL|COPY|DO)\b",
    re.IGNORECASE,
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def assert_select_only_sql(sql: str) -> None:
    """Chặn mọi câu lệnh SQL không phải SELECT đơn lẻ."""
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        raise ValueError("Câu lệnh SQL rỗng không được phép")
    if ";" in cleaned:
        raise ValueError("Không cho phép nhiều câu lệnh SQL trong một truy vấn")
    upper = cleaned.upper()
    if not (upper.startswith("SELECT ") or upper.startswith("WITH ")):
        raise ValueError(f"Chỉ cho phép truy vấn SELECT; từ chối: {cleaned[:40]!r}")
    m = _FORBIDDEN_SQL_RE.search(cleaned)
    if m:
        raise ValueError(f"Phát hiện từ khóa SQL bị cấm ({m.group(1)}) trong chế độ chỉ-đọc")


def _validate_ident(name: str) -> str:
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"Tên định danh SQL không hợp lệ: {name!r}")
    return name


def _normalize_cell(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return to_decimal_str(val, allow_none=True)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False, sort_keys=True)
    return val


class SupabaseReadOnlyReader:
    """Trình đọc PostgreSQL/Supabase ở chế độ chỉ-đọc nghiêm ngặt."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn if dsn is not None else (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "")

    @property
    def is_configured(self) -> bool:
        return bool(self._dsn.strip())

    def fetch_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Thực thi một câu SELECT trong transaction READ ONLY."""
        assert_select_only_sql(sql)
        if not self.is_configured:
            raise RuntimeError("Chưa cấu hình biến môi trường SUPABASE_DB_URL hoặc DATABASE_URL")
        import psycopg  # type: ignore[import-not-found]
        from psycopg.rows import dict_row  # type: ignore[import-not-found]

        with psycopg.connect(
            self._dsn,
            options="-c default_transaction_read_only=on",
            connect_timeout=15,
        ) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(r) for r in rows]


def _unconfigured_result(source_name: str, ctx: CollectContext) -> SourceResult:
    return SourceResult(
        source=source_name,
        status="GAP",
        provenance="UNRESOLVED",
        collected_at=ctx.collected_at,
        gaps=[{"reason": "Chưa cấu hình DATABASE_URL/SUPABASE_DB_URL trên máy local"}],
        message_vi=f"Nguồn {source_name}: chưa cấu hình DATABASE_URL/SUPABASE_DB_URL trên máy local (bỏ qua an toàn).",
    )


class SupabaseAnalyticsTableSource:
    """Thu tăng dần một bảng từ schema autotrade_analytics (chỉ SELECT)."""

    def __init__(
        self,
        *,
        name: str,
        table: str,
        parquet_rel: str,
        pk_cols: list[str],
        cursor_col: str,
        schema: pa.Schema,
        id_cols: set[str],
        decimal_cols: set[str],
        int_cols: set[str],
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.name = name
        self.table = _validate_ident(table)
        self.parquet_rel = parquet_rel
        self.pk_cols = pk_cols
        self.cursor_col = _validate_ident(cursor_col)
        self.schema = schema
        self.id_cols = id_cols
        self.decimal_cols = decimal_cols
        self.int_cols = int_cols
        self.page_size = page_size

    def _transform_row(self, row: dict[str, Any], collected_at: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in self.schema:
            col = field.name
            if col == "provenance":
                out[col] = "EXACT"
                continue
            if col == "collected_at":
                out[col] = collected_at
                continue
            raw_val = row.get(col)
            if col in self.id_cols:
                out[col] = to_id_str(raw_val, field_name=col, allow_none=True) or ""
            elif col in self.decimal_cols:
                out[col] = to_decimal_str(raw_val, field_name=col, allow_none=True) or "0"
            elif col in self.int_cols:
                out[col] = int(raw_val) if raw_val is not None else 0
            else:
                val = _normalize_cell(raw_val)
                out[col] = "" if val is None else str(val)
        return out

    def collect(self, ctx: CollectContext) -> SourceResult:
        if ctx.db_reader is None or not ctx.db_reader.is_configured:
            return _unconfigured_result(self.name, ctx)

        state = ctx.source_state
        pending = state.get("pending_resume") or {}
        if ctx.since_ms is not None:
            cursor_val = int(ctx.since_ms)
            offset = 0
        elif pending:
            cursor_val = int(pending.get("cursor_ms", 0))
            offset = int(pending.get("offset", 0))
        else:
            prev_cursor = int(state.get("cursor_ms") or 0)
            cursor_val = max(0, prev_cursor - OVERLAP_MS) if prev_cursor > 0 else 0
            offset = 0

        sql = (
            f"SELECT * FROM autotrade_analytics.{self.table} "
            f"WHERE {self.cursor_col} >= %s "
            f"ORDER BY {self.cursor_col} ASC LIMIT %s OFFSET %s"
        )

        raw_files: list[RawArtifact] = []
        collected_rows: list[dict[str, Any]] = []
        max_cursor = int(state.get("cursor_ms") or 0)
        min_ts: int | None = None
        max_ts: int | None = None
        pages_used = 0
        seq = 0
        interrupted_err: str | None = None

        while True:
            if ctx.max_pages_per_run is not None and pages_used >= ctx.max_pages_per_run:
                interrupted_err = f"Đạt giới hạn {ctx.max_pages_per_run} trang/lần chạy."
                if not ctx.dry_run:
                    state["pending_resume"] = {"cursor_ms": cursor_val, "offset": offset}
                break

            try:
                batch = ctx.db_reader.fetch_rows(sql, (cursor_val, self.page_size, offset))
            except Exception as exc:
                interrupted_err = f"Lỗi đọc Supabase ({self.table}): {exc}"
                if not ctx.dry_run:
                    state["pending_resume"] = {"cursor_ms": cursor_val, "offset": offset}
                break

            pages_used += 1
            if not batch:
                if not ctx.dry_run:
                    state.pop("pending_resume", None)
                break

            if not ctx.dry_run:
                raw_files.append(
                    write_raw_json(
                        ctx.data_dir,
                        self.name,
                        {"table": f"autotrade_analytics.{self.table}", "cursor_ms": cursor_val, "offset": offset, "rows": batch},
                        collected_at=ctx.collected_at,
                        seq=seq,
                        record_count=len(batch),
                    )
                )
                seq += 1

            for r in batch:
                norm = self._transform_row(r, ctx.collected_at)
                collected_rows.append(norm)
                c_val = int(r.get(self.cursor_col) or 0)
                if c_val > 0:
                    min_ts = c_val if min_ts is None else min(min_ts, c_val)
                    max_ts = c_val if max_ts is None else max(max_ts, c_val)
                    max_cursor = max(max_cursor, c_val)

            if len(batch) < self.page_size:
                if not ctx.dry_run:
                    state.pop("pending_resume", None)
                break
            offset += len(batch)

        parquet_files: list[ParquetArtifact] = []
        if ctx.dry_run:
            new_cnt = len(collected_rows)
            tot_cnt = len(collected_rows)
        else:
            new_cnt, tot_cnt, pq_art = upsert_parquet(
                ctx.data_dir,
                self.parquet_rel,
                collected_rows,
                self.pk_cols,
                self.schema,
                sort_col=self.cursor_col,
            )
            if pq_art is not None:
                parquet_files.append(pq_art)
            if max_cursor > 0:
                state["cursor_ms"] = max_cursor

        status = "INCOMPLETE" if interrupted_err else "COMPLETE"
        msg = (
            f"Nguồn {self.name} chưa hoàn tất ({interrupted_err}); lần sau tiếp tục."
            if status == "INCOMPLETE"
            else f"Nguồn {self.name} hoàn tất: +{new_cnt} bản ghi mới (tổng {tot_cnt})."
        )
        return SourceResult(
            source=self.name,
            status=status,
            provenance="EXACT",
            collected_at=ctx.collected_at,
            min_ts_ms=min_ts,
            max_ts_ms=max_ts,
            new_records=new_cnt,
            total_records=tot_cnt,
            cursor=max_cursor if max_cursor > 0 else state.get("cursor_ms"),
            raw_files=raw_files,
            parquet_files=parquet_files,
            schema_version=SCHEMA_VERSION,
            error=interrupted_err,
            message_vi=msg,
        )


def build_analytics_sources() -> list[SupabaseAnalyticsTableSource]:
    """Khởi tạo 4 nguồn execution từ autotrade_analytics.*."""
    return [
        SupabaseAnalyticsTableSource(
            name="execution_orders",
            table="bingx_orders",
            parquet_rel="execution/orders/orders.parquet",
            pk_cols=["order_id"],
            cursor_col="updated_ms",
            schema=pa.schema([
                ("order_id", pa.string()),
                ("symbol", pa.string()),
                ("side", pa.string()),
                ("position_side", pa.string()),
                ("type", pa.string()),
                ("status", pa.string()),
                ("price", pa.string()),
                ("avg_price", pa.string()),
                ("quantity", pa.string()),
                ("filled_qty", pa.string()),
                ("created_ms", pa.int64()),
                ("updated_ms", pa.int64()),
                ("provenance", pa.string()),
                ("collected_at", pa.string()),
            ]),
            id_cols={"order_id"},
            decimal_cols={"price", "avg_price", "quantity", "filled_qty"},
            int_cols={"created_ms", "updated_ms"},
        ),
        SupabaseAnalyticsTableSource(
            name="execution_fills",
            table="bingx_fills",
            parquet_rel="execution/fills/fills.parquet",
            pk_cols=["trade_id", "symbol"],
            cursor_col="timestamp_ms",
            schema=pa.schema([
                ("trade_id", pa.string()),
                ("order_id", pa.string()),
                ("symbol", pa.string()),
                ("side", pa.string()),
                ("position_side", pa.string()),
                ("qty", pa.string()),
                ("price", pa.string()),
                ("realized_pnl", pa.string()),
                ("fee", pa.string()),
                ("timestamp_ms", pa.int64()),
                ("provenance", pa.string()),
                ("collected_at", pa.string()),
            ]),
            id_cols={"trade_id", "order_id"},
            decimal_cols={"qty", "price", "realized_pnl", "fee"},
            int_cols={"timestamp_ms"},
        ),
        SupabaseAnalyticsTableSource(
            name="execution_income",
            table="bingx_income",
            parquet_rel="execution/income/income.parquet",
            pk_cols=["income_key"],
            cursor_col="timestamp_ms",
            schema=pa.schema([
                ("income_key", pa.string()),
                ("symbol", pa.string()),
                ("income_type", pa.string()),
                ("amount", pa.string()),
                ("asset", pa.string()),
                ("timestamp_ms", pa.int64()),
                ("trade_id", pa.string()),
                ("provenance", pa.string()),
                ("collected_at", pa.string()),
            ]),
            id_cols={"income_key", "trade_id"},
            decimal_cols={"amount"},
            int_cols={"timestamp_ms"},
        ),
        SupabaseAnalyticsTableSource(
            name="execution_positions",
            table="bingx_position_history",
            parquet_rel="execution/positions/positions.parquet",
            pk_cols=["position_key"],
            cursor_col="timestamp_ms",
            schema=pa.schema([
                ("position_key", pa.string()),
                ("symbol", pa.string()),
                ("timestamp_ms", pa.int64()),
                ("raw", pa.string()),
                ("provenance", pa.string()),
                ("collected_at", pa.string()),
            ]),
            id_cols={"position_key"},
            decimal_cols=set(),
            int_cols={"timestamp_ms"},
        ),
    ]


class SupabaseAutotradeV3Source:
    """Thu chỉ-đọc các bảng trong schema autotrade_v3 (5 bảng live events)."""

    name = "live_events_v3"

    def __init__(self, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self.page_size = page_size

    def collect(self, ctx: CollectContext) -> SourceResult:
        if ctx.db_reader is None or not ctx.db_reader.is_configured:
            return _unconfigured_result(self.name, ctx)

        try:
            tables_rows = ctx.db_reader.fetch_rows(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'autotrade_v3' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name",
                (),
            )
        except Exception as exc:
            return SourceResult(
                source=self.name,
                status="INCOMPLETE",
                provenance="UNRESOLVED",
                collected_at=ctx.collected_at,
                error=str(exc),
                message_vi=f"Không thể liệt kê bảng trong schema autotrade_v3: {exc}",
            )

        table_names = [_validate_ident(str(r["table_name"])) for r in tables_rows]
        if not table_names:
            return SourceResult(
                source=self.name,
                status="GAP",
                provenance="UNRESOLVED",
                collected_at=ctx.collected_at,
                gaps=[{"reason": "Schema autotrade_v3 chưa có bảng nào trên Supabase"}],
                message_vi="Schema autotrade_v3 chưa có bảng nào trên Supabase.",
            )

        cursors: dict[str, str] = dict(ctx.source_state.get("cursors") or {})
        raw_files: list[RawArtifact] = []
        parquet_files: list[ParquetArtifact] = []
        total_new = 0
        total_rows = 0
        seq = 0

        for tbl in table_names:
            sql = f"SELECT * FROM autotrade_v3.{tbl} LIMIT %s OFFSET %s"
            offset = 0
            tbl_rows: list[dict[str, Any]] = []
            while True:
                batch = ctx.db_reader.fetch_rows(sql, (self.page_size, offset))
                if not batch:
                    break
                if not ctx.dry_run:
                    raw_files.append(
                        write_raw_json(
                            ctx.data_dir,
                            f"{self.name}/{tbl}",
                            {"table": f"autotrade_v3.{tbl}", "offset": offset, "rows": batch},
                            collected_at=ctx.collected_at,
                            seq=seq,
                            record_count=len(batch),
                        )
                    )
                    seq += 1
                for row in batch:
                    norm = {k: ("" if _normalize_cell(v) is None else str(_normalize_cell(v))) for k, v in row.items()}
                    row_json = json.dumps(norm, ensure_ascii=False, sort_keys=True)
                    norm["_row_hash"] = hashlib.sha256(row_json.encode("utf-8")).hexdigest()
                    norm["provenance"] = "EXACT"
                    norm["collected_at"] = ctx.collected_at
                    tbl_rows.append(norm)
                if len(batch) < self.page_size:
                    break
                offset += len(batch)

            cursors[tbl] = ctx.collected_at
            if ctx.dry_run:
                total_new += len(tbl_rows)
            elif tbl_rows:
                cols = sorted({k for r in tbl_rows for k in r.keys()})
                pq_schema = pa.schema([(c, pa.string()) for c in cols])
                new_cnt, tot_cnt, pq_art = upsert_parquet(
                    ctx.data_dir,
                    f"live_events/{tbl}.parquet",
                    tbl_rows,
                    ["_row_hash"],
                    pq_schema,
                )
                total_new += new_cnt
                total_rows += tot_cnt
                if pq_art is not None:
                    parquet_files.append(pq_art)

        if not ctx.dry_run:
            ctx.source_state["cursors"] = cursors

        return SourceResult(
            source=self.name,
            status="COMPLETE",
            provenance="EXACT",
            collected_at=ctx.collected_at,
            new_records=total_new,
            total_records=total_rows,
            cursor=cursors,
            raw_files=raw_files,
            parquet_files=parquet_files,
            message_vi=f"Thu {len(table_names)} bảng autotrade_v3 hoàn tất: +{total_new} bản ghi mới (tổng {total_rows}).",
        )
