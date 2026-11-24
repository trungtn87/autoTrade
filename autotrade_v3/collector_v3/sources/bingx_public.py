"""Các nguồn dữ liệu thị trường công khai BingX (không cần khóa API)."""
from __future__ import annotations

from typing import Any

import pyarrow as pa

from ..http_client import NetworkError, RateLimitError
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

ONE_MIN_MS = 60_000
KLINE_LIMIT = 1440
DEFAULT_KLINE_LOOKBACK_MS = 24 * 60 * 60_000  # 24 giờ khi chạy lần đầu
DEFAULT_FUNDING_LOOKBACK_MS = 14 * 24 * 60 * 60_000  # 14 ngày khi chạy lần đầu
FUNDING_WINDOW_MS = 7 * 24 * 60 * 60_000  # Chia cửa sổ 7 ngày mỗi request

CANDLES_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("open_time_ms", pa.int64()),
    ("close_time_ms", pa.int64()),
    ("open", pa.string()),
    ("high", pa.string()),
    ("low", pa.string()),
    ("close", pa.string()),
    ("volume", pa.string()),
    ("provenance", pa.string()),
    ("collected_at", pa.string()),
])

MARK_PRICE_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("update_time_ms", pa.int64()),
    ("next_funding_time_ms", pa.int64()),
    ("mark_price", pa.string()),
    ("index_price", pa.string()),
    ("last_funding_rate", pa.string()),
    ("funding_interval_hours", pa.int64()),
    ("min_funding_rate", pa.string()),
    ("max_funding_rate", pa.string()),
    ("provenance", pa.string()),
    ("collected_at", pa.string()),
])

FUNDING_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("funding_time_ms", pa.int64()),
    ("funding_rate", pa.string()),
    ("mark_price", pa.string()),
    ("provenance", pa.string()),
    ("collected_at", pa.string()),
])

CONTRACT_RULES_SCHEMA = pa.schema([
    ("contract_id", pa.string()),
    ("symbol", pa.string()),
    ("tick_size", pa.string()),
    ("price_precision", pa.int64()),
    ("quantity_precision", pa.int64()),
    ("min_qty", pa.string()),
    ("min_usdt", pa.string()),
    ("maker_fee_rate", pa.string()),
    ("taker_fee_rate", pa.string()),
    ("status", pa.int64()),
    ("api_state_open", pa.string()),
    ("snapshot_ms", pa.int64()),
    ("provenance", pa.string()),
    ("collected_at", pa.string()),
])


def _extract_data_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("list", "rows", "data"):
            val = data.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    raise ValueError("Phản hồi BingX không chứa danh sách bản ghi hợp lệ")


def _detect_1m_gaps(open_times: list[int], expected_start_ms: int, expected_end_ms: int) -> list[dict[str, Any]]:
    if expected_end_ms < expected_start_ms:
        return []
    present = set(open_times)
    gaps: list[dict[str, Any]] = []
    gap_start: int | None = None
    cur = expected_start_ms
    while cur <= expected_end_ms:
        if cur not in present:
            if gap_start is None:
                gap_start = cur
        else:
            if gap_start is not None:
                gaps.append({
                    "start_ms": gap_start,
                    "end_ms": cur - ONE_MIN_MS,
                    "missing_bars": ((cur - gap_start) // ONE_MIN_MS),
                    "reason": "Thiếu nến 1m trong khoảng đã thu",
                })
                gap_start = None
        cur += ONE_MIN_MS
    if gap_start is not None:
        gaps.append({
            "start_ms": gap_start,
            "end_ms": expected_end_ms,
            "missing_bars": ((expected_end_ms - gap_start) // ONE_MIN_MS) + 1,
            "reason": "Thiếu nến 1m ở cuối khoảng đã thu",
        })
    return gaps


class BingXCandles1mSource:
    """Thu nến M1 công khai từ BingX (/openApi/swap/v3/quote/klines)."""

    name = "market_candles_1m"

    def collect(self, ctx: CollectContext) -> SourceResult:
        state = ctx.source_state
        cursors: dict[str, int] = dict(state.get("cursors") or {})
        pending: dict[str, int] = dict(state.get("pending_resume") or {})

        last_closed_open = (ctx.now_ms // ONE_MIN_MS) * ONE_MIN_MS - ONE_MIN_MS
        raw_files: list[RawArtifact] = []
        parquet_files: list[ParquetArtifact] = []
        all_gaps: list[dict[str, Any]] = []
        total_new = 0
        total_rows = 0
        min_ts: int | None = None
        max_ts: int | None = None
        incomplete_err: str | None = None
        pages_used = 0
        seq = 0

        for sym in ctx.symbols:
            if ctx.since_ms is not None:
                start_ms = (ctx.since_ms // ONE_MIN_MS) * ONE_MIN_MS
            elif sym in pending:
                start_ms = int(pending[sym])
            elif sym in cursors:
                start_ms = max(0, int(cursors[sym]) - 5 * ONE_MIN_MS)
            else:
                start_ms = last_closed_open - DEFAULT_KLINE_LOOKBACK_MS

            if start_ms > last_closed_open:
                start_ms = last_closed_open

            sym_rows: list[dict[str, Any]] = []
            cur = start_ms
            sym_interrupted = False

            while cur <= last_closed_open:
                if ctx.max_pages_per_run is not None and pages_used >= ctx.max_pages_per_run:
                    pending[sym] = cur
                    sym_interrupted = True
                    incomplete_err = f"Đạt giới hạn {ctx.max_pages_per_run} trang/lần chạy tại {sym}; lần sau tiếp tục."
                    break

                end_open = min(last_closed_open, cur + (KLINE_LIMIT - 1) * ONE_MIN_MS)
                end_param = end_open + ONE_MIN_MS - 1
                try:
                    payload = ctx.http_client.get_json(
                        "/openApi/swap/v3/quote/klines",
                        {
                            "symbol": sym,
                            "interval": "1m",
                            "startTime": cur,
                            "endTime": end_param,
                            "limit": KLINE_LIMIT,
                        },
                    )
                except (RateLimitError, NetworkError) as exc:
                    pending[sym] = cur
                    sym_interrupted = True
                    incomplete_err = str(exc)
                    break

                pages_used += 1
                items = _extract_data_list(payload)
                if not ctx.dry_run:
                    raw_files.append(
                        write_raw_json(
                            ctx.data_dir,
                            self.name,
                            {"symbol": sym, "startTime": cur, "endTime": end_param, "response": payload},
                            collected_at=ctx.collected_at,
                            seq=seq,
                            record_count=len(items),
                        )
                    )
                    seq += 1

                for item in items:
                    t_raw = item.get("time", item.get("openTime"))
                    if t_raw is None:
                        continue
                    open_ms = int(t_raw)
                    sym_rows.append({
                        "symbol": sym,
                        "open_time_ms": open_ms,
                        "close_time_ms": open_ms + ONE_MIN_MS - 1,
                        "open": to_decimal_str(item.get("open"), field_name="open"),
                        "high": to_decimal_str(item.get("high"), field_name="high"),
                        "low": to_decimal_str(item.get("low"), field_name="low"),
                        "close": to_decimal_str(item.get("close"), field_name="close"),
                        "volume": to_decimal_str(item.get("volume", "0"), field_name="volume"),
                        "provenance": "EXACT",
                        "collected_at": ctx.collected_at,
                    })

                cur = end_open + ONE_MIN_MS

            if not sym_interrupted:
                pending.pop(sym, None)

            if sym_rows:
                open_times = sorted({int(r["open_time_ms"]) for r in sym_rows})
                sym_min = open_times[0]
                sym_max = open_times[-1]
                min_ts = sym_min if min_ts is None else min(min_ts, sym_min)
                max_ts = sym_max if max_ts is None else max(max_ts, sym_max)
                cursors[sym] = max(int(cursors.get(sym, 0)), sym_max)
                if not sym_interrupted:
                    sym_gaps = _detect_1m_gaps(open_times, start_ms, last_closed_open)
                    for g in sym_gaps:
                        g["symbol"] = sym
                    all_gaps.extend(sym_gaps)

            if ctx.dry_run:
                total_new += len(sym_rows)
            else:
                new_cnt, tot_cnt, pq_art = upsert_parquet(
                    ctx.data_dir,
                    f"market/candles_1m/{sym}.parquet",
                    sym_rows,
                    ["symbol", "open_time_ms"],
                    CANDLES_SCHEMA,
                    sort_col="open_time_ms",
                )
                total_new += new_cnt
                total_rows += tot_cnt
                if pq_art is not None:
                    parquet_files.append(pq_art)

            if sym_interrupted:
                break

        if not ctx.dry_run:
            state["cursors"] = cursors
            state["pending_resume"] = pending

        if incomplete_err or pending:
            status = "INCOMPLETE"
            msg = f"Thu nến M1 chưa hoàn tất ({incomplete_err or 'còn trang dở'}); đã lưu phần đã thu để lần sau tiếp tục."
        elif all_gaps:
            status = "GAP"
            msg = f"Thu nến M1 xong nhưng phát hiện {len(all_gaps)} khoảng thiếu."
        else:
            status = "COMPLETE"
            msg = f"Thu nến M1 hoàn tất: +{total_new} bản ghi mới (tổng {total_rows})."

        return SourceResult(
            source=self.name,
            status=status,
            provenance="EXACT",
            collected_at=ctx.collected_at,
            min_ts_ms=min_ts,
            max_ts_ms=max_ts,
            new_records=total_new,
            total_records=total_rows,
            cursor=cursors,
            gaps=all_gaps,
            raw_files=raw_files,
            parquet_files=parquet_files,
            schema_version=SCHEMA_VERSION,
            error=incomplete_err,
            message_vi=msg,
        )


class BingXMarkPriceSource:
    """Thu giá mark/index công khai từ BingX (/openApi/swap/v2/quote/premiumIndex)."""

    name = "market_mark_price"

    def collect(self, ctx: CollectContext) -> SourceResult:
        raw_files: list[RawArtifact] = []
        parquet_files: list[ParquetArtifact] = []
        total_new = 0
        total_rows = 0
        min_ts: int | None = None
        max_ts: int | None = None
        cursors: dict[str, int] = dict(ctx.source_state.get("cursors") or {})
        seq = 0

        for sym in ctx.symbols:
            try:
                payload = ctx.http_client.get_json("/openApi/swap/v2/quote/premiumIndex", {"symbol": sym})
            except (RateLimitError, NetworkError) as exc:
                return SourceResult(
                    source=self.name,
                    status="INCOMPLETE",
                    provenance="EXACT",
                    collected_at=ctx.collected_at,
                    min_ts_ms=min_ts,
                    max_ts_ms=max_ts,
                    new_records=total_new,
                    total_records=total_rows,
                    cursor=cursors,
                    raw_files=raw_files,
                    parquet_files=parquet_files,
                    error=str(exc),
                    message_vi=f"Thu giá mark/index gián đoạn tại {sym}: {exc}",
                )

            data = payload.get("data")
            items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            if not ctx.dry_run:
                raw_files.append(
                    write_raw_json(
                        ctx.data_dir,
                        self.name,
                        {"symbol": sym, "response": payload},
                        collected_at=ctx.collected_at,
                        seq=seq,
                        record_count=len(items),
                    )
                )
                seq += 1

            sym_rows: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                upd = int(item.get("updateTime") or ctx.now_ms)
                nxt = int(item.get("nextFundingTime") or 0)
                min_ts = upd if min_ts is None else min(min_ts, upd)
                max_ts = upd if max_ts is None else max(max_ts, upd)
                cursors[sym] = max(int(cursors.get(sym, 0)), upd)
                sym_rows.append({
                    "symbol": str(item.get("symbol") or sym),
                    "update_time_ms": upd,
                    "next_funding_time_ms": nxt,
                    "mark_price": to_decimal_str(item.get("markPrice"), field_name="markPrice"),
                    "index_price": to_decimal_str(item.get("indexPrice"), field_name="indexPrice"),
                    "last_funding_rate": to_decimal_str(item.get("lastFundingRate", "0"), field_name="lastFundingRate"),
                    "funding_interval_hours": int(item.get("fundingIntervalHours") or 8),
                    "min_funding_rate": to_decimal_str(item.get("minFundingRate", "0"), field_name="minFundingRate"),
                    "max_funding_rate": to_decimal_str(item.get("maxFundingRate", "0"), field_name="maxFundingRate"),
                    "provenance": "EXACT",
                    "collected_at": ctx.collected_at,
                })

            if ctx.dry_run:
                total_new += len(sym_rows)
            else:
                new_cnt, tot_cnt, pq_art = upsert_parquet(
                    ctx.data_dir,
                    f"market/mark_price/{sym}.parquet",
                    sym_rows,
                    ["symbol", "update_time_ms"],
                    MARK_PRICE_SCHEMA,
                    sort_col="update_time_ms",
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
            min_ts_ms=min_ts,
            max_ts_ms=max_ts,
            new_records=total_new,
            total_records=total_rows,
            cursor=cursors,
            raw_files=raw_files,
            parquet_files=parquet_files,
            message_vi=f"Thu giá mark/index hoàn tất: +{total_new} bản ghi mới (tổng {total_rows}).",
        )


class BingXFundingSource:
    """Thu lịch sử funding rate công khai từ BingX (/openApi/swap/v2/quote/fundingRate)."""

    name = "market_funding"

    def collect(self, ctx: CollectContext) -> SourceResult:
        state = ctx.source_state
        cursors: dict[str, int] = dict(state.get("cursors") or {})
        pending: dict[str, int] = dict(state.get("pending_resume") or {})
        raw_files: list[RawArtifact] = []
        parquet_files: list[ParquetArtifact] = []
        total_new = 0
        total_rows = 0
        min_ts: int | None = None
        max_ts: int | None = None
        incomplete_err: str | None = None
        pages_used = 0
        seq = 0

        for sym in ctx.symbols:
            if ctx.since_ms is not None:
                start_ms = int(ctx.since_ms)
            elif sym in pending:
                start_ms = int(pending[sym])
            elif sym in cursors:
                start_ms = max(0, int(cursors[sym]) - 8 * 60 * 60_000)
            else:
                start_ms = ctx.now_ms - DEFAULT_FUNDING_LOOKBACK_MS

            sym_rows: list[dict[str, Any]] = []
            cur = start_ms
            sym_interrupted = False

            while cur <= ctx.now_ms:
                if ctx.max_pages_per_run is not None and pages_used >= ctx.max_pages_per_run:
                    pending[sym] = cur
                    sym_interrupted = True
                    incomplete_err = f"Đạt giới hạn {ctx.max_pages_per_run} trang/lần chạy tại {sym}."
                    break

                win_end = min(ctx.now_ms, cur + FUNDING_WINDOW_MS - 1)
                try:
                    payload = ctx.http_client.get_json(
                        "/openApi/swap/v2/quote/fundingRate",
                        {"symbol": sym, "startTime": cur, "endTime": win_end, "limit": 100},
                    )
                except (RateLimitError, NetworkError) as exc:
                    pending[sym] = cur
                    sym_interrupted = True
                    incomplete_err = str(exc)
                    break

                pages_used += 1
                items = _extract_data_list(payload)
                if not ctx.dry_run:
                    raw_files.append(
                        write_raw_json(
                            ctx.data_dir,
                            self.name,
                            {"symbol": sym, "startTime": cur, "endTime": win_end, "response": payload},
                            collected_at=ctx.collected_at,
                            seq=seq,
                            record_count=len(items),
                        )
                    )
                    seq += 1

                for item in items:
                    ft = int(item["fundingTime"])
                    sym_rows.append({
                        "symbol": str(item.get("symbol") or sym),
                        "funding_time_ms": ft,
                        "funding_rate": to_decimal_str(item.get("fundingRate"), field_name="fundingRate"),
                        "mark_price": to_decimal_str(item.get("markPrice", "0"), field_name="markPrice"),
                        "provenance": "EXACT",
                        "collected_at": ctx.collected_at,
                    })

                cur = win_end + 1

            if not sym_interrupted:
                pending.pop(sym, None)

            if sym_rows:
                times = [int(r["funding_time_ms"]) for r in sym_rows]
                min_ts = min(times) if min_ts is None else min(min_ts, min(times))
                max_ts = max(times) if max_ts is None else max(max_ts, max(times))
                cursors[sym] = max(int(cursors.get(sym, 0)), max(times))

            if ctx.dry_run:
                total_new += len(sym_rows)
            else:
                new_cnt, tot_cnt, pq_art = upsert_parquet(
                    ctx.data_dir,
                    f"market/funding/{sym}.parquet",
                    sym_rows,
                    ["symbol", "funding_time_ms"],
                    FUNDING_SCHEMA,
                    sort_col="funding_time_ms",
                )
                total_new += new_cnt
                total_rows += tot_cnt
                if pq_art is not None:
                    parquet_files.append(pq_art)

            if sym_interrupted:
                break

        if not ctx.dry_run:
            state["cursors"] = cursors
            state["pending_resume"] = pending

        status = "INCOMPLETE" if (incomplete_err or pending) else "COMPLETE"
        msg = (
            f"Thu funding rate chưa hoàn tất ({incomplete_err}); lần sau tiếp tục."
            if status == "INCOMPLETE"
            else f"Thu funding rate hoàn tất: +{total_new} bản ghi mới (tổng {total_rows})."
        )
        return SourceResult(
            source=self.name,
            status=status,
            provenance="EXACT",
            collected_at=ctx.collected_at,
            min_ts_ms=min_ts,
            max_ts_ms=max_ts,
            new_records=total_new,
            total_records=total_rows,
            cursor=cursors,
            raw_files=raw_files,
            parquet_files=parquet_files,
            error=incomplete_err,
            message_vi=msg,
        )


class BingXContractRulesSource:
    """Thu quy tắc hợp đồng công khai từ BingX (/openApi/swap/v2/quote/contracts)."""

    name = "market_contract_rules"

    def collect(self, ctx: CollectContext) -> SourceResult:
        try:
            payload = ctx.http_client.get_json("/openApi/swap/v2/quote/contracts", {})
        except (RateLimitError, NetworkError) as exc:
            return SourceResult(
                source=self.name,
                status="INCOMPLETE",
                provenance="EXACT",
                collected_at=ctx.collected_at,
                error=str(exc),
                message_vi=f"Thu quy tắc hợp đồng thất bại: {exc}",
            )

        items = _extract_data_list(payload)
        target_symbols = set(ctx.symbols)
        filtered = [it for it in items if str(it.get("symbol", "")) in target_symbols] if target_symbols else items

        raw_files: list[RawArtifact] = []
        if not ctx.dry_run:
            raw_files.append(
                write_raw_json(
                    ctx.data_dir,
                    self.name,
                    {"symbols": sorted(target_symbols), "response": payload},
                    collected_at=ctx.collected_at,
                    seq=0,
                    record_count=len(filtered),
                )
            )

        rows: list[dict[str, Any]] = []
        for it in filtered:
            rows.append({
                "contract_id": to_id_str(it.get("contractId"), field_name="contractId"),
                "symbol": str(it.get("symbol")),
                "tick_size": to_decimal_str(it.get("size"), field_name="size"),
                "price_precision": int(it.get("pricePrecision") or 0),
                "quantity_precision": int(it.get("quantityPrecision") or 0),
                "min_qty": to_decimal_str(it.get("tradeMinQuantity"), field_name="tradeMinQuantity"),
                "min_usdt": to_decimal_str(it.get("tradeMinUSDT", "0"), field_name="tradeMinUSDT"),
                "maker_fee_rate": to_decimal_str(it.get("makerFeeRate", "0"), field_name="makerFeeRate"),
                "taker_fee_rate": to_decimal_str(it.get("takerFeeRate", "0"), field_name="takerFeeRate"),
                "status": int(it.get("status", 0)),
                "api_state_open": str(it.get("apiStateOpen", "")),
                "snapshot_ms": ctx.now_ms,
                "provenance": "EXACT",
                "collected_at": ctx.collected_at,
            })

        parquet_files: list[ParquetArtifact] = []
        if ctx.dry_run:
            new_cnt = len(rows)
            tot_cnt = len(rows)
        else:
            new_cnt, tot_cnt, pq_art = upsert_parquet(
                ctx.data_dir,
                "market/contract_rules/contracts.parquet",
                rows,
                ["symbol", "contract_id"],
                CONTRACT_RULES_SCHEMA,
                sort_col="symbol",
            )
            if pq_art is not None:
                parquet_files.append(pq_art)

        return SourceResult(
            source=self.name,
            status="COMPLETE",
            provenance="EXACT",
            collected_at=ctx.collected_at,
            min_ts_ms=ctx.now_ms,
            max_ts_ms=ctx.now_ms,
            new_records=new_cnt,
            total_records=tot_cnt,
            cursor=ctx.now_ms,
            raw_files=raw_files,
            parquet_files=parquet_files,
            message_vi=f"Thu quy tắc hợp đồng hoàn tất: {len(rows)} hợp đồng (tổng {tot_cnt}).",
        )
