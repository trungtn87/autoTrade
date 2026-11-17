#!/usr/bin/env python3
"""Backfill older BingX ETH-USDT 15m candles into autotrade_v3.candles.

Run in an internet-connected environment with the existing DATABASE_URL:
  python scripts/backfill_v3_eth_m15.py          # read-only audit
  python scripts/backfill_v3_eth_m15.py --apply  # insert missing older candles

Only ETH-USDT, timeframe=15m, with INSERT ... ON CONFLICT DO NOTHING.
Never deletes or updates existing market data. No exchange order or private API calls.
Requires: pip install psycopg[binary] httpx
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg

STEP = 15 * 60 * 1000
KEEP = 12_000
CHUNK = 1000  # One bounded BingX request, then persist immediately.
API = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
SYMBOL = "ETH-USDT"
FRAME = "15m"


def utc(ms: int | None) -> str:
    if ms is None:
        return "None"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def stats(conn: psycopg.Connection) -> tuple[int, int | None, int | None]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*), min(open_time), max(open_time)
            FROM autotrade_v3.candles
            WHERE symbol = %s AND timeframe = %s
        """, (SYMBOL, FRAME))
        row = cur.fetchone()
        assert row is not None
        return int(row[0]), row[1], row[2]


def validate_existing(n: int, first: int | None, last: int | None) -> None:
    if not n or first is None or last is None:
        raise RuntimeError("No existing ETH 15m anchor in database: refusing blind backfill")
    if first % STEP or last % STEP or first > last:
        raise RuntimeError("Existing ETH 15m timestamps are not aligned")
    expected_count = (last - first) // STEP + 1
    if n != expected_count:
        raise RuntimeError(f"Existing ETH 15m span has gaps: rows={n}, expected={expected_count}; repair first")


def extract(payload: dict, first: int, bars: int) -> list[tuple]:
    data = payload.get("data")
    if isinstance(data, dict):
        data = data.get("data") or data.get("list") or data.get("rows")
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected BingX data shape: {type(data).__name__}")
    parsed: dict[int, tuple] = {}
    for row in data:
        if isinstance(row, (list, tuple)) and len(row) >= 7:
            t, o, h, lo, c, vol, ct = row[:7]
        elif isinstance(row, dict):
            t = row.get("openTime", row.get("time"))
            o, h, lo, c, vol = (row.get(k) for k in ("open", "high", "low", "close", "volume"))
            ct = row.get("closeTime")
        else:
            raise RuntimeError("Malformed BingX OHLCV record")
        t = int(t)
        ct = t + STEP - 1 if ct is None else int(ct)
        o, h, lo, c, vol = map(float, (o, h, lo, c, vol))
        if not all(math.isfinite(x) for x in (o, h, lo, c, vol)):
            raise RuntimeError(f"Non-finite OHLCV at {t}")
        if not (lo > 0 and lo <= o <= h and lo <= c <= h and vol >= 0):
            raise RuntimeError(f"Invalid price/volume at {t}")
        if t % STEP or ct != t + STEP - 1:
            raise RuntimeError(f"Invalid 15m opening/closing boundary: {t}, {ct}")
        if t in parsed:
            raise RuntimeError(f"Duplicate BingX opening timestamp {t}")
        parsed[t] = (SYMBOL, FRAME, t, o, h, lo, c, vol, ct)
    expected = list(range(first, first + bars * STEP, STEP))
    if sorted(parsed) != expected:
        low = min(parsed) if parsed else None
        high = max(parsed) if parsed else None
        raise RuntimeError(f"BingX window mismatch: expected={utc(expected[0])}..{utc(expected[-1])} ({bars}); got={len(parsed)}, {utc(low)}..{utc(high)}")
    return [parsed[t] for t in expected]


def fetch(client: httpx.Client, first: int, bars: int, *, attempts: int) -> list[tuple]:
    # Matches the existing V3 HistoricalKlineClient endTime semantics.
    params = dict(symbol=SYMBOL, interval=FRAME,
                  startTime=first, endTime=first + bars * STEP - 1, limit=bars)
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(API, params=params)
            body = response.json()
            code = body.get("code") if isinstance(body, dict) else None
            if response.status_code == 200 and code in (0, "0"):
                return extract(body, first, bars)
            message = str(body.get("msg", ""))[:120] if isinstance(body, dict) else "bad JSON"
            retriable = response.status_code in (429, 500, 502, 503, 504) or str(code) in ("100410", "109425", "109429", "100500")
            if not retriable:
                raise RuntimeError(f"BingX error HTTP={response.status_code} code={code}, msg={message}")
            error = f"BingX busy/rate-limit HTTP={response.status_code}, code={code}, msg={message}"
        except (httpx.TransportError, ValueError) as exc:
            error = f"BingX transport: {type(exc).__name__}: {exc}"
        if attempt == attempts:
            raise RuntimeError(f"{error}; aborting safely, rerun resumes from committed rows")
        cooldown = min(60 * 2 ** (attempt - 1), 600)
        print(f"{error}. Waiting {cooldown}s, retry {attempt + 1}/{attempts}", flush=True)
        time.sleep(cooldown)
    raise AssertionError("unreachable")


def insert_rows(conn: psycopg.Connection, rows: list[tuple]) -> int:
    placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(rows))
    sql = ("INSERT INTO autotrade_v3.candles "
           "(symbol,timeframe,open_time,open,high,low,close,volume,close_time) VALUES "
           + placeholders + " ON CONFLICT (symbol,timeframe,open_time) DO NOTHING")
    # One atomic transaction per verified REST page (not 12,000 candles at once).
    # Connection uses autocommit for read-only queries, avoiding long open
    # transactions while BingX is throttling or backing off.
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql, [value for row in rows for value in row])
            count = int(cur.rowcount)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually fetch and insert, otherwise read-only audit")
    parser.add_argument("--limit", type=int, default=CHUNK, help="candles per BingX request (1..1000; default 1000)")
    parser.add_argument("--request-gap", type=float, default=4.0, help="minimum seconds between requests (default 4)")
    parser.add_argument("--attempts", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000 or args.request_gap < 2 or not 1 <= args.attempts <= 10:
        parser.error("--limit 1..1000, --request-gap >=2s, --attempts 1..10 required")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must refer to production Supabase PostgreSQL; no credential printing")
    with psycopg.connect(database_url, connect_timeout=15, prepare_threshold=None, autocommit=True) as conn:
        n, first, last = stats(conn)
        validate_existing(n, first, last)
        assert first is not None and last is not None
        wanted_first = last - (KEEP - 1) * STEP
        missing_before = max(0, (first - wanted_first) // STEP)
        print(f"ETH M15: {n} existing, oldest {utc(first)}, newest {utc(last)}")
        print(f"Target last {KEEP} closed candles through {utc(last)}; earliest needed {utc(wanted_first)}")
        print(f"Older candles to fetch: {missing_before}. Mode: {'APPLY' if args.apply else 'AUDIT ONLY'}")
        if not args.apply:
            print("No data fetched or written. To backfill, run with --apply.")
            return 0
        if n >= KEEP:
            print("PASS: at least 12,000 contiguous ETH M15 candles already present; nothing to insert.")
            return 0
        with httpx.Client(timeout=40.0, headers={"User-Agent": "autotrade-v3-eth-m15-backfill/1.0"}) as client:
            done = 0
            while first > wanted_first:
                bars = min(args.limit, (first - wanted_first) // STEP)
                batch_first = first - bars * STEP
                rows = fetch(client, batch_first, bars, attempts=args.attempts)
                inserted = insert_rows(conn, rows)
                done += inserted
                print(f"Committed {inserted:4d} / {bars:4d} ETH M15 candles | oldest now {utc(batch_first)} | total inserted {done}", flush=True)
                # Re-read DB because the live L1 writer may concurrently add newer closed candles.
                n, first, last = stats(conn)
                validate_existing(n, first, last)
                assert first is not None and last is not None
                wanted_first = last - (KEEP - 1) * STEP
                if n >= KEEP:
                    break
                time.sleep(args.request_gap)
        n, first, last = stats(conn)
        validate_existing(n, first, last)
        assert first is not None and last is not None
        print(f"FINAL: ETH M15 count={n}, first={utc(first)}, last={utc(last)}")
        if n < KEEP:
            raise RuntimeError(f"NOT COMPLETE: only {n}/{KEEP} candles, safe to rerun --apply")
        if first > last - (KEEP - 1) * STEP:
            raise RuntimeError("NOT COMPLETE: 12,000 consecutive M15 candles not confirmed")
        print("PASS: >=12,000 contiguous ETH 15m candles. Existing rows left unchanged.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
