"""Kiểm thử toàn diện cho bộ thu dữ liệu V3-17c (autotrade_v3.collector_v3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import pytest

from autotrade_v3.collector_v3.http_client import RateLimitError
from autotrade_v3.collector_v3.models import to_decimal_str, to_id_str
from autotrade_v3.collector_v3.runner import run_collector
from autotrade_v3.collector_v3.sources.base import CollectContext
from autotrade_v3.collector_v3.sources.bingx_public import (
    BingXCandles1mSource,
    BingXContractRulesSource,
    BingXFundingSource,
    BingXMarkPriceSource,
)
from autotrade_v3.collector_v3.sources.supabase_ro import (
    SupabaseAutotradeV3Source,
    assert_select_only_sql,
    build_analytics_sources,
)
from autotrade_v3.collector_v3.storage import (
    CollectorLock,
    LockError,
    StateStore,
    redact_secrets,
    verify_warehouse,
)

ONE_MIN_MS = 60_000


class _FakeBingXClient:
    """Client giả lập trả về nến 1m, mark price, funding và contract rules."""

    def __init__(self, *, fail_after_calls: int | None = None) -> None:
        self.calls = 0
        self.fail_after_calls = fail_after_calls

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        if self.fail_after_calls is not None and self.calls > self.fail_after_calls:
            raise RateLimitError("HTTP 429 giả lập giữa chừng")
        p = params or {}
        sym = str(p.get("symbol", "BTC-USDT"))
        if path == "/openApi/swap/v3/quote/klines":
            start = int(p["startTime"])
            end = int(p["endTime"])
            rows = []
            cur = start
            while cur <= end:
                rows.append({
                    "open": "59000.123456789012",
                    "high": "59010.5",
                    "low": "58990.25",
                    "close": "59005.0",
                    "volume": "1.2345",
                    "time": cur,
                })
                cur += ONE_MIN_MS
            return {"code": 0, "msg": "", "data": rows}
        if path == "/openApi/swap/v2/quote/premiumIndex":
            return {
                "code": 0,
                "msg": "",
                "data": {
                    "symbol": sym,
                    "markPrice": "59001.1",
                    "indexPrice": "59002.2",
                    "lastFundingRate": "0.00001000",
                    "nextFundingTime": 1795276800000,
                    "fundingIntervalHours": 8,
                    "minFundingRate": "-0.003000",
                    "maxFundingRate": "0.003000",
                    "updateTime": 1795248000000,
                },
            }
        if path == "/openApi/swap/v2/quote/fundingRate":
            return {
                "code": 0,
                "msg": "",
                "data": [
                    {
                        "symbol": sym,
                        "fundingRate": "0.00001800",
                        "fundingTime": int(p["startTime"]),
                        "markPrice": "59000.5",
                    }
                ],
            }
        if path == "/openApi/swap/v2/quote/contracts":
            return {
                "code": 0,
                "msg": "",
                "data": [
                    {
                        "contractId": "1000000000000000100",
                        "symbol": "BTC-USDT",
                        "size": "0.0001",
                        "quantityPrecision": 4,
                        "pricePrecision": 1,
                        "tradeMinQuantity": "0.0001",
                        "tradeMinUSDT": "2",
                        "makerFeeRate": "0.0002",
                        "takerFeeRate": "0.0005",
                        "status": 1,
                        "apiStateOpen": "true",
                    }
                ],
            }
        raise AssertionError(f"Unexpected path: {path}")


class _FakeDbReader:
    """Trình đọc Supabase giả lập chỉ-đọc với ID 19 chữ số và Decimal chính xác."""

    is_configured = True

    def __init__(self, rows_by_table: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.rows_by_table = rows_by_table or {}
        self.executed_sql: list[str] = []

    def fetch_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert_select_only_sql(sql)
        self.executed_sql.append(sql)
        if "information_schema.tables" in sql:
            return [{"table_name": f"v3_tbl_{i}"} for i in range(1, 6)]
        for tbl, rows in self.rows_by_table.items():
            if tbl in sql:
                if len(params) == 3:
                    cursor_min, limit, offset = int(params[0]), int(params[1]), int(params[2])
                    filtered = [r for r in rows if int(r.get("updated_ms", r.get("timestamp_ms", 0))) >= cursor_min]
                    return filtered[offset : offset + limit]
                if len(params) == 2:
                    limit, offset = int(params[0]), int(params[1])
                    return rows[offset : offset + limit]
                return rows
        return []


def test_idempotent_rerun_no_duplicates_and_no_raw_overwrite(tmp_path: Path) -> None:
    """Chạy lặp nhiều lần không tạo trùng bản ghi Parquet và không ghi đè tệp raw."""
    data_dir = tmp_path / "bingx"
    now_ms = 1_795_248_300_000  # tròn phút
    since_ms = now_ms - 5 * ONE_MIN_MS
    client = _FakeBingXClient()

    m1 = run_collector(
        data_dir=data_dir,
        since=since_ms,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=client,
        now_ms=now_ms,
    )
    assert m1.overall_status == "COMPLETE"
    res1 = m1.sources["market_candles_1m"]
    assert res1.new_records == 5
    assert res1.total_records == 5
    raw_count_1 = len(list((data_dir / "raw" / "market_candles_1m").rglob("*.json")))
    assert raw_count_1 == 1

    # Chạy lại cùng khoảng thời gian -> 0 bản ghi mới thêm, tổng vẫn 5, raw tăng lên 2 (không ghi đè)
    m2 = run_collector(
        data_dir=data_dir,
        since=since_ms,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=client,
        now_ms=now_ms,
    )
    res2 = m2.sources["market_candles_1m"]
    assert res2.new_records == 0
    assert res2.total_records == 5
    raw_count_2 = len(list((data_dir / "raw" / "market_candles_1m").rglob("*.json")))
    assert raw_count_2 == 2
    assert not (data_dir / "market" / "spread").exists()


def test_backfill_after_machine_off_old_cursor(tmp_path: Path) -> None:
    """Giả lập máy tắt lâu rồi bật lại (cursor cũ): tự động bù đủ phần thiếu."""
    data_dir = tmp_path / "bingx"
    t0 = 1_795_248_000_000
    t_later = t0 + 10 * ONE_MIN_MS
    client = _FakeBingXClient()

    # Lần 1 tại t0: thu từ t0 - 10m đến t0 - 1m (10 nến)
    run_collector(
        data_dir=data_dir,
        since=t0 - 10 * ONE_MIN_MS,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=client,
        now_ms=t0,
    )

    # Máy tắt 10 phút, bật lại ở t_later (không truyền --since, dùng cursor cũ trong state.json)
    m2 = run_collector(
        data_dir=data_dir,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=client,
        now_ms=t_later,
    )
    res2 = m2.sources["market_candles_1m"]
    assert res2.status == "COMPLETE"
    assert res2.new_records == 10
    assert res2.total_records == 20  # 10 cũ + 10 mới
    df = pq.read_table(data_dir / "market" / "candles_1m" / "BTC-USDT.parquet").to_pandas()
    assert len(df) == 20
    assert df["open_time_ms"].is_monotonic_increasing


def test_mid_pagination_cut_marks_incomplete_and_resumes(tmp_path: Path) -> None:
    """Phân trang bị cắt giữa chừng -> INCOMPLETE -> lần sau tiếp tục hoàn tất."""
    data_dir = tmp_path / "bingx"
    t_start = 1_795_248_000_000
    # 2000 phút -> cần 2 trang (limit 1440)
    t_now = t_start + 2000 * ONE_MIN_MS

    # Lần 1 bị cắt sau 1 trang (429)
    client_cut = _FakeBingXClient(fail_after_calls=1)
    m1 = run_collector(
        data_dir=data_dir,
        since=t_start,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=client_cut,
        now_ms=t_now,
    )
    assert m1.overall_status == "INCOMPLETE"
    assert m1.sources["market_candles_1m"].status == "INCOMPLETE"
    state = StateStore(data_dir / "manifests" / "state.json").load()
    assert "BTC-USDT" in state["sources"]["market_candles_1m"]["pending_resume"]

    # Lần 2 mạng ổn -> tiếp tục phần còn lại và hoàn tất COMPLETE
    client_ok = _FakeBingXClient()
    m2 = run_collector(
        data_dir=data_dir,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=client_ok,
        now_ms=t_now,
    )
    assert m2.overall_status == "COMPLETE"
    assert m2.sources["market_candles_1m"].status == "COMPLETE"
    assert m2.sources["market_candles_1m"].total_records == 2000
    state_after = StateStore(data_dir / "manifests" / "state.json").load()
    assert not state_after["sources"]["market_candles_1m"]["pending_resume"]


def test_precision_id_string_and_decimal_preserved(tmp_path: Path) -> None:
    """ID 19 chữ số và số tiền Decimal chính xác tuyệt đối, từ chối float/scientific."""
    with pytest.raises(ValueError):
        to_id_str(1.23e18, field_name="order_id")
    with pytest.raises(ValueError):
        to_decimal_str(float("nan"), field_name="price")

    data_dir = tmp_path / "bingx"
    big_order_id = "1847291038475619283"
    big_trade_id = "9988776655443322119"
    exact_price = "59123.456789012345"
    exact_fee = "-0.0001234567890123"

    fake_db = _FakeDbReader({
        "bingx_fills": [
            {
                "trade_id": big_trade_id,
                "order_id": big_order_id,
                "symbol": "BTC-USDT",
                "side": "BUY",
                "position_side": "LONG",
                "qty": "0.01500000",
                "price": exact_price,
                "realized_pnl": "12.345678901234",
                "fee": exact_fee,
                "timestamp_ms": 1795248000000,
            }
        ]
    })

    m = run_collector(
        data_dir=data_dir,
        only=["execution_fills"],
        db_reader=fake_db,
        http_client=_FakeBingXClient(),
    )
    assert m.sources["execution_fills"].status == "COMPLETE"
    df = pq.read_table(data_dir / "execution" / "fills" / "fills.parquet").to_pandas()
    assert df.loc[0, "trade_id"] == big_trade_id
    assert df.loc[0, "order_id"] == big_order_id
    assert df.loc[0, "price"] == exact_price
    assert df.loc[0, "fee"] == exact_fee


def test_mutation_detection_raw_delete_and_parquet_tamper(tmp_path: Path) -> None:
    """Mutation test: xóa 1 tệp raw hoặc sửa 1 dòng Parquet -> verify_warehouse FAIL."""
    data_dir = tmp_path / "bingx"
    now_ms = 1_795_248_300_000
    run_collector(
        data_dir=data_dir,
        since=now_ms - 3 * ONE_MIN_MS,
        only=["market_candles_1m"],
        symbols=("BTC-USDT",),
        http_client=_FakeBingXClient(),
        now_ms=now_ms,
    )
    assert verify_warehouse(data_dir)["ok"] is True

    # 1) Sửa 1 dòng trong Parquet -> sha256 mismatch
    pq_path = data_dir / "market" / "candles_1m" / "BTC-USDT.parquet"
    df = pq.read_table(pq_path).to_pandas()
    df.loc[0, "close"] = "99999.9"
    df.to_parquet(pq_path, index=False)
    rep1 = verify_warehouse(data_dir)
    assert rep1["ok"] is False
    assert len(rep1["mismatches"]) >= 1

    # 2) Xóa 1 tệp raw -> missing file
    raw_file = next((data_dir / "raw" / "market_candles_1m").rglob("*.json"))
    raw_file.unlink()
    rep2 = verify_warehouse(data_dir)
    assert rep2["ok"] is False
    assert len(rep2["missing"]) >= 1


def test_source_isolation_one_error_does_not_block_others(tmp_path: Path) -> None:
    """Một nguồn gặp lỗi không làm hỏng hoặc chặn các nguồn còn lại."""
    data_dir = tmp_path / "bingx"

    class _BrokenSource:
        name = "broken_source"

        def collect(self, ctx: CollectContext) -> Any:
            raise RuntimeError("Lỗi giả lập ở một nguồn")

    manifest = run_collector(
        data_dir=data_dir,
        sources=[_BrokenSource(), BingXContractRulesSource(), BingXMarkPriceSource()],
        symbols=("BTC-USDT",),
        http_client=_FakeBingXClient(),
    )
    assert manifest.sources["broken_source"].status == "INCOMPLETE"
    assert manifest.sources["market_contract_rules"].status == "COMPLETE"
    assert manifest.sources["market_mark_price"].status == "COMPLETE"
    assert (data_dir / "manifests" / "coverage.parquet").exists()
    assert (data_dir / "manifests" / "coverage.json").exists()


def test_lock_contention_and_sql_select_only(tmp_path: Path) -> None:
    """Kiểm tra khóa chống chạy chồng và bộ lọc SQL chỉ cho phép SELECT."""
    lock_path = tmp_path / ".collector.lock"
    with CollectorLock(lock_path):
        with pytest.raises(LockError):
            CollectorLock(lock_path).acquire()

    assert_select_only_sql("SELECT * FROM autotrade_analytics.bingx_orders WHERE updated_ms >= %s")
    for bad_sql in (
        "DELETE FROM autotrade_analytics.bingx_orders",
        "UPDATE autotrade_analytics.bingx_orders SET status='X'",
        "INSERT INTO autotrade_analytics.bingx_orders VALUES (1)",
        "DROP TABLE autotrade_analytics.bingx_orders",
        "SELECT 1; DROP TABLE x",
    ):
        with pytest.raises(ValueError):
            assert_select_only_sql(bad_sql)

    redacted = redact_secrets("DATABASE_URL=postgresql://user:secretpass@db.example.com:5432/postgres")
    assert "secretpass" not in redacted


def test_all_sources_dry_run_and_coverage_schema(tmp_path: Path) -> None:
    """Kiểm tra chạy đủ nguồn (kể cả 5 bảng autotrade_v3 giả lập) và bảng coverage."""
    data_dir = tmp_path / "bingx"
    fake_db = _FakeDbReader({
        "v3_tbl_1": [{"id": "1", "event": "SIGNAL"}],
        "v3_tbl_2": [{"id": "2", "event": "RISK"}],
        "v3_tbl_3": [{"id": "3", "event": "ORDER"}],
        "v3_tbl_4": [{"id": "4", "event": "FILL"}],
        "v3_tbl_5": [{"id": "5", "event": "CLOSE"}],
    })

    # Dry-run không ghi file Parquet
    dry = run_collector(
        data_dir=data_dir,
        dry_run=True,
        symbols=("BTC-USDT",),
        http_client=_FakeBingXClient(),
        db_reader=fake_db,
    )
    assert dry.dry_run is True
    assert not (data_dir / "manifests" / "state.json").exists()

    # Chạy thật tạo coverage.parquet và 5 bảng live_events
    real = run_collector(
        data_dir=data_dir,
        only=["market_contract_rules", "market_funding", "live_events_v3"],
        symbols=("BTC-USDT",),
        http_client=_FakeBingXClient(),
        db_reader=fake_db,
    )
    assert real.overall_status == "COMPLETE"
    cov = pq.read_table(data_dir / "manifests" / "coverage.parquet").to_pandas()
    assert set(cov["source"]) == {"market_contract_rules", "market_funding", "live_events_v3"}
    for i in range(1, 6):
        assert (data_dir / "live_events" / f"v3_tbl_{i}.parquet").exists()
    assert not (data_dir / "market" / "spread").exists()


def test_all_supabase_sources_emit_only_select_sql(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chứng minh mọi câu lệnh SQL từ 5 nguồn Supabase đều là SELECT và chạy trong READ ONLY."""
    recorded_sqls: list[str] = []

    class _RecordingDbReader:
        is_configured = True

        def fetch_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            assert_select_only_sql(sql)
            recorded_sqls.append(sql.strip())
            if "information_schema.tables" in sql:
                return [{"table_name": "v3_events"}]
            return []

    res = run_collector(
        data_dir=tmp_path / "bingx",
        only=[
            "execution_orders",
            "execution_fills",
            "execution_positions",
            "execution_income",
            "live_events_v3",
        ],
        db_reader=_RecordingDbReader(),
    )
    assert res.overall_status == "GAP"
    for src_name in ("execution_orders", "execution_fills", "execution_positions", "execution_income", "live_events_v3"):
        assert res.sources[src_name].status == "GAP"
        assert res.sources[src_name].gaps == [{"reason": "bảng nguồn rỗng"}]
    assert len(recorded_sqls) >= 5
    for sql in recorded_sqls:
        assert sql.upper().startswith("SELECT ")
        assert_select_only_sql(sql)

    # Kiểm tra SupabaseReadOnlyReader thiết lập transaction READ ONLY và chặn lệnh ghi
    from autotrade_v3.collector_v3.sources.supabase_ro import SupabaseReadOnlyReader
    import sys
    import types

    executed: list[tuple[str, tuple[Any, ...]]] = []
    connect_kwargs: list[dict[str, Any]] = []

    class _FakeCursor:
        def __enter__(self) -> "_FakeCursor":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            executed.append((sql, params))

        def fetchall(self) -> list[dict[str, Any]]:
            return [{"ok": 1}]

    class _FakeConn:
        def __enter__(self) -> "_FakeConn":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def cursor(self, **kwargs: Any) -> _FakeCursor:
            return _FakeCursor()

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = lambda dsn, **kw: (connect_kwargs.append(kw), _FakeConn())[1]  # type: ignore[attr-defined, func-returns-value]
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)

    reader = SupabaseReadOnlyReader("postgresql://user:pass@localhost:5432/db")
    with pytest.raises(ValueError):
        reader.fetch_rows("DELETE FROM autotrade_analytics.bingx_orders")
    assert executed == []

    rows = reader.fetch_rows("SELECT 1 AS ok")
    assert rows == [{"ok": 1}]
    assert connect_kwargs[0]["options"] == "-c default_transaction_read_only=on"
    assert executed[0][0] == "SET TRANSACTION READ ONLY"
    assert executed[1][0] == "SELECT 1 AS ok"


def test_live_events_v3_json_serialization_all_pg_types(tmp_path: Path) -> None:
    """live_events_v3 tuần tự hóa đủ 6 bảng với timestamptz, date, numeric/Decimal, UUID, bytes, jsonb."""
    from datetime import date, datetime, timedelta, timezone
    from decimal import Decimal
    from uuid import UUID

    tz_plus7 = timezone(timedelta(hours=7))
    uid = UUID("12345678-1234-5678-1234-567812345678")
    tables_data = {
        "candles": [{
            "id": uid,
            "symbol": "BTC-USDT",
            "ts": datetime(2026, 11, 21, 18, 27, 53, tzinfo=tz_plus7),
            "trade_date": date(2026, 11, 21),
            "close": Decimal("98765.43210987654321"),
            "meta": {"source": "bingx", "nested_dec": Decimal("0.00000001")},
        }],
        "strategy_signals": [{
            "id": uid,
            "created_at": datetime(2026, 11, 21, 11, 27, 53),  # naive -> UTC
            "score": Decimal("0.987654321012345678"),
            "payload": [{"k": "v", "ts": datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)}],
        }],
        "execution_orders": [{
            "order_id": "1987654321098765432",
            "price": Decimal("95123.4500"),
            "created_at": datetime(2026, 11, 21, 18, 0, 0, tzinfo=timezone.utc),
            "raw_bytes": b"\x00\x01\xfe\xff",
        }],
        "execution_results": [{
            "result_id": uid,
            "pnl": Decimal("-12.34567890"),
            "closed_at": datetime(2026, 11, 21, 18, 5, 0, tzinfo=timezone.utc),
        }],
        "execution_finances": [{
            "finance_id": "fin-1",
            "amount": Decimal("0.00000001"),
            "recorded_at": datetime(2026, 11, 21, 18, 10, 0, tzinfo=timezone.utc),
        }],
        "control_logs": [{
            "log_id": 101,
            "level": "INFO",
            "details": {"uid": uid, "ok": True},
            "logged_at": datetime(2026, 11, 21, 18, 15, 0, tzinfo=timezone.utc),
        }],
    }

    class _V3FakeDbReader(_FakeDbReader):
        def fetch_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            if "information_schema.tables" in sql:
                return [{"table_name": k} for k in tables_data]
            return super().fetch_rows(sql, params)

    data_dir = tmp_path / "bingx"
    res = run_collector(
        data_dir=data_dir,
        only=["live_events_v3"],
        db_reader=_V3FakeDbReader(tables_data),
    )
    assert res.overall_status == "COMPLETE"
    v3_res = res.sources["live_events_v3"]
    assert v3_res.status == "COMPLETE"
    assert v3_res.total_records == 6
    for tbl in tables_data:
        assert (data_dir / "live_events" / f"{tbl}.parquet").exists()
        assert f"{tbl}=1" in v3_res.message_vi

    # Kiểm tra raw JSON không mất precision Decimal và datetime chuẩn UTC Z
    raw_order_files = list((data_dir / "raw" / "live_events_v3" / "execution_orders").rglob("*.json"))
    assert len(raw_order_files) == 1
    raw_text = raw_order_files[0].read_text(encoding="utf-8")
    assert "95123.4500" in raw_text
    assert "2026-11-21T18:00:00Z" in raw_text
    assert verify_warehouse(data_dir)["ok"] is True


def test_empty_source_table_marks_gap_not_error(tmp_path: Path) -> None:
    """Bảng nguồn rỗng (0 dòng) phải đánh trạng thái GAP với lý do 'bảng nguồn rỗng', không giả vờ COMPLETE."""
    data_dir = tmp_path / "bingx"
    fake_db = _FakeDbReader({
        "bingx_orders": [{
            "order_id": "1001",
            "symbol": "BTC-USDT",
            "side": "BUY",
            "position_side": "LONG",
            "type": "LIMIT",
            "status": "FILLED",
            "price": "95000",
            "avg_price": "95000",
            "quantity": "0.01",
            "filled_qty": "0.01",
            "created_ms": 1794670000000,
            "updated_ms": 1794670001000,
        }],
        "bingx_position_history": [],
    })
    res = run_collector(
        data_dir=data_dir,
        only=["execution_orders", "execution_positions"],
        db_reader=fake_db,
    )
    assert res.sources["execution_orders"].status == "COMPLETE"
    pos = res.sources["execution_positions"]
    assert pos.status == "GAP"
    assert pos.total_records == 0
    assert pos.error is None
    assert pos.gaps == [{"reason": "bảng nguồn rỗng"}]
    assert "bảng nguồn rỗng" in pos.message_vi
    assert res.overall_status == "GAP"


@pytest.mark.parametrize(
    "err_msg",
    [
        "FATAL: password authentication failed for user \"postgres\"",
        "FATAL: ECIRCUITBREAKER: Too many authentication errors",
        "connection refused to host",
    ],
)
def test_supabase_early_stop_on_auth_or_circuit_breaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, err_msg: str) -> None:
    """Khi gặp lỗi đăng nhập / ECIRCUITBREAKER / kết nối, dừng mọi nguồn Supabase còn lại trong đợt đó."""
    calls: list[str] = []

    class _FailingDbReader:
        is_configured = True

        def fetch_rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            calls.append(sql)
            raise RuntimeError(err_msg)

    res = run_collector(
        data_dir=tmp_path / "bingx",
        only=[
            "execution_orders",
            "execution_fills",
            "execution_income",
            "execution_positions",
            "live_events_v3",
        ],
        db_reader=_FailingDbReader(),
    )
    assert len(calls) == 1, f"Kỳ vọng chỉ thử 1 lần rồi dừng sớm, thực tế gọi {len(calls)} lần"
    assert res.overall_status == "INCOMPLETE"
    for name in ("execution_orders", "execution_fills", "execution_income", "execution_positions", "live_events_v3"):
        assert res.sources[name].status == "INCOMPLETE"
        assert res.sources[name].error

    # Kiểm tra cả ở tầng SupabaseReadOnlyReader: lần 2 không gọi psycopg.connect nữa
    from autotrade_v3.collector_v3.sources.supabase_ro import SupabaseReadOnlyReader
    import sys
    import types

    connect_calls = 0

    def _fail_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal connect_calls
        connect_calls += 1
        raise ConnectionError(err_msg)

    fake_psycopg = types.ModuleType("psycopg")
    fake_psycopg.connect = _fail_connect  # type: ignore[attr-defined]
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)

    reader = SupabaseReadOnlyReader("postgresql://u:secret@db.example.com:5432/postgres")
    with pytest.raises(RuntimeError):
        reader.fetch_rows("SELECT 1")
    with pytest.raises(RuntimeError, match="dừng sớm"):
        reader.fetch_rows("SELECT 1")
    assert connect_calls == 1


def test_log_and_manifest_redact_db_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lỗi kết nối không được in URL, mật khẩu, hoặc thông tin kết nối ra log hay manifest."""
    import logging
    from autotrade_v3.collector_v3.sources.supabase_ro import SupabaseReadOnlyReader
    import sys
    import types

    secret_dsn = "postgresql://postgres.abc:SuperSecretPass123@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
    leaky_err = (
        f"connection failed to {secret_dsn} "
        "host=aws-0-ap-southeast-1.pooler.supabase.com port=6543 dbname=postgres user=postgres password=AnotherSecret456 "
        "url=https://xyzproject.supabase.co/rest/v1"
    )

    fake_psycopg = types.ModuleType("psycopg")

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError(leaky_err)

    fake_psycopg.connect = _boom  # type: ignore[attr-defined]
    fake_rows = types.ModuleType("psycopg.rows")
    fake_rows.dict_row = object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)

    # Reset logger handlers để setup_logger ghi vào tmp_path/bingx/logs/collector.log
    logger = logging.getLogger("autotrade_v3.collector_v3")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)

    data_dir = tmp_path / "bingx"
    res = run_collector(
        data_dir=data_dir,
        only=["execution_orders", "execution_fills"],
        db_reader=SupabaseReadOnlyReader(secret_dsn),
    )
    assert res.overall_status == "INCOMPLETE"

    forbidden_tokens = [
        "SuperSecretPass123",
        "AnotherSecret456",
        "aws-0-ap-southeast-1.pooler.supabase.com",
        "xyzproject.supabase.co",
        "postgres.abc",
    ]
    scanned_files = list((data_dir / "logs").glob("*.log*")) + list((data_dir / "manifests").rglob("*.json"))
    assert scanned_files, "Phải có file log và manifest để quét"
    for fpath in scanned_files:
        text = fpath.read_text(encoding="utf-8")
        for tok in forbidden_tokens:
            assert tok not in text, f"Phát hiện bí mật bị rò rỉ ({tok}) trong {fpath}"
