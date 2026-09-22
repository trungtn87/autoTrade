from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable

import psycopg


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    order_by: tuple[str, ...]
    upsert_sql: str


TABLES = (
    TableSpec(
        name="candles",
        columns=("symbol","timeframe","open_time","open","high","low","close","volume","close_time"),
        order_by=("symbol","timeframe","open_time"),
        upsert_sql="""
            INSERT INTO candles(
                symbol,timeframe,open_time,open,high,low,close,volume,close_time
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(symbol,timeframe,open_time) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                close_time=excluded.close_time
        """,
    ),
    TableSpec(
        name="processed_targets",
        columns=("event_id","target","processed_at","payload"),
        order_by=("event_id","target"),
        upsert_sql="""
            INSERT INTO processed_targets(event_id,target,processed_at,payload)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT(event_id,target) DO UPDATE SET
                processed_at=excluded.processed_at,
                payload=excluded.payload
        """,
    ),
    TableSpec(
        name="runtime_state",
        columns=("key","value","updated_at"),
        order_by=("key",),
        upsert_sql="""
            INSERT INTO runtime_state(key,value,updated_at)
            VALUES (%s,%s,%s)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
        """,
    ),
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS processed_targets (
    event_id TEXT NOT NULL,
    target TEXT NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    payload TEXT,
    PRIMARY KEY (event_id, target)
);

CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time BIGINT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    close_time BIGINT NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_time
ON candles(symbol, timeframe, open_time);

CREATE TABLE IF NOT EXISTS runtime_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _require_url(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    if not value.startswith(("postgres://", "postgresql://")):
        raise SystemExit(f"{name} must be a PostgreSQL connection URL")
    return value


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def fingerprint_rows(rows: Iterable[tuple[Any, ...]]) -> tuple[int, str]:
    h = hashlib.sha256()
    count = 0
    for row in rows:
        payload = json.dumps(
            [_json_value(v) for v in row],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=False,
        ).encode("utf-8")
        h.update(len(payload).to_bytes(8, "big"))
        h.update(payload)
        count += 1
    return count, h.hexdigest()


def create_schema(con) -> None:
    with con.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    con.commit()


def read_rows(con, spec: TableSpec) -> list[tuple[Any, ...]]:
    columns = ",".join(spec.columns)
    order = ",".join(spec.order_by)
    with con.cursor() as cur:
        cur.execute(f"SELECT {columns} FROM {spec.name} ORDER BY {order}")
        return list(cur.fetchall())


def audit(con) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for spec in TABLES:
        rows = read_rows(con, spec)
        count, digest = fingerprint_rows(rows)
        out[spec.name] = {"count": count, "sha256": digest}
    return out


def copy_pass(source, target, batch_size: int = 1000) -> dict[str, int]:
    copied: dict[str, int] = {}
    for spec in TABLES:
        rows = read_rows(source, spec)
        with target.cursor() as cur:
            for start in range(0, len(rows), batch_size):
                cur.executemany(spec.upsert_sql, rows[start:start + batch_size])
        target.commit()
        copied[spec.name] = len(rows)
    return copied


def exact_match(source_audit: dict, target_audit: dict) -> bool:
    return source_audit == target_audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently sync FINAL14 Postgres state to a new Postgres database."
    )
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()

    if args.max_passes < 1:
        raise SystemExit("--max-passes must be >= 1")
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")

    source_url = _require_url("SOURCE_DATABASE_URL")
    target_url = _require_url("TARGET_DATABASE_URL")
    if source_url == target_url:
        raise SystemExit("SOURCE_DATABASE_URL and TARGET_DATABASE_URL must be different")

    with psycopg.connect(source_url) as source, psycopg.connect(target_url) as target:
        create_schema(target)

        for attempt in range(1, args.max_passes + 1):
            copied = copy_pass(source, target, batch_size=args.batch_size)
            source_audit = audit(source)
            target_audit = audit(target)
            ok = exact_match(source_audit, target_audit)

            print(json.dumps({
                "pass": attempt,
                "copied": copied,
                "source": source_audit,
                "target": target_audit,
                "cutover_ready": ok,
            }, ensure_ascii=False, sort_keys=True))

            if ok:
                print("CUTOVER_READY=true")
                return 0

        print("CUTOVER_READY=false", file=sys.stderr)
        print(
            "Source changed during migration or target contains divergent rows. "
            "Do not change DATABASE_URL; run the sync again.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
