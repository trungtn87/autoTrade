#!/usr/bin/env python3
"""Verified Binance USD-M Futures M1 -> M15 warm-up for AutoTrade V3 ETH.

Local V3 Lab's July/August data is from Binance USDT perpetual futures;
those Parquet files are not tracked on GitHub. Fetch the corresponding original
Binance Vision monthly USD-M Futures M1 ZIPs, verify their public SHA256
checksums and compare the derived M15 price against V3's existing BingX M15.

Only INSERTs a missing older prefix of autotrade_v3.candles. Writes durable
provider/hash/time-range provenance to existing autotrade_v3.control_logs.
No private exchange API, no trading logic changes, no overwrites or deletes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import statistics
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

import psycopg

SYMBOL = "ETH-USDT"
FRAME = "15m"
PROVIDER = "BINANCE_USDM_FUTURES_M1_AGGREGATED_TO_M15"
STEP = 900000
MINUTE = 60000
TARGET = 12000
MONTHS = ("2026-07", "2026-08")
ARCHIVE_ROOT = "https://data.binance.vision/data/futures/um/monthly/klines/ETHUSDT/1m"


def fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def db_stats(conn) -> tuple[int, int, int]:
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*),min(open_time),max(open_time)
            FROM autotrade_v3.candles WHERE symbol=%s AND timeframe=%s""", (SYMBOL, FRAME))
        count, first, last = cur.fetchone()
    if not count or first is None or last is None:
        raise RuntimeError("Missing V3 ETH M15 anchor; refusing unanchored import")
    count, first, last = int(count), int(first), int(last)
    if first % STEP or last % STEP or count != (last-first)//STEP + 1:
        raise RuntimeError("V3 ETH M15 has gaps or misalignment; abort")
    return count, first, last


def read_public(url: str, max_bytes: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent":"AutoTrade-V3-ETH-M15-Warmup/1.0"})
    with urllib.request.urlopen(req, timeout=90) as response:
        if response.geturl().split("/")[2] != "data.binance.vision":
            raise RuntimeError("Untrusted redirect for Binance archive")
        data = response.read(max_bytes+1)
    if not data or len(data) > max_bytes:
        raise RuntimeError("Empty or oversized Binance archive response")
    return data


def aggregate_month(records: list[list[str]], month: str) -> list[tuple]:
    year, mon = map(int, month.split("-"))
    first = int(datetime(year, mon, 1, tzinfo=timezone.utc).timestamp()*1000)
    end = int(datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=timezone.utc).timestamp()*1000)
    expected = (end-first)//MINUTE
    if len(records) != expected:
        raise RuntimeError(f"{month} incomplete futures M1: {len(records)}/{expected}")
    result=[]
    minute_group=[]
    for i, row in enumerate(records):
        if len(row) < 7:
            raise RuntimeError(f"Invalid futures M1 CSV row {i}")
        try:
            timestamp=int(row[0])
            end_time=int(row[6])
            o,h,l,c,v=[float(row[j]) for j in (1,2,3,4,5)]
        except (ValueError, OverflowError) as exc:
            raise RuntimeError(f"Invalid futures M1 values at row {i}") from exc
        if timestamp != first+i*MINUTE or end_time != timestamp+MINUTE-1:
            raise RuntimeError(f"Invalid futures M1 time at index {i}")
        if not all(math.isfinite(x) for x in (o,h,l,c,v)) or not (l>0 and l<=o<=h and l<=c<=h and v>=0):
            raise RuntimeError(f"Invalid futures M1 OHLCV at {fmt(timestamp)}")
        minute_group.append((timestamp,o,h,l,c,v))
        if len(minute_group)==15:
            t=minute_group[0][0]
            if t%STEP:
                raise RuntimeError("Unaligned M15 aggregation")
            result.append((t,minute_group[0][1],max(x[2] for x in minute_group),
                           min(x[3] for x in minute_group),minute_group[-1][4],
                           sum(x[5] for x in minute_group),t+STEP-1))
            minute_group=[]
    if minute_group or len(result)*15 != expected:
        raise RuntimeError("Incomplete M15 aggregation")
    return result


def strip_verified_header(rows: list[list[str]]) -> list[list[str]]:
    """Binance monthly Futures CSVs may include a single column-header row.

    Ignore only the exact seven expected OHLCV/time header names; no arbitrary
    record is discarded. Every subsequent row still must pass strict M1 checks.
    """
    if not rows:
        raise RuntimeError("Empty Binance Futures monthly CSV")
    if rows[0] and rows[0][0].strip().isdigit():
        return rows
    if len(rows[0]) < 7:
        raise RuntimeError("Unexpected Binance CSV first row")
    normalized=[value.strip().lower().replace("_","").replace(" ","") for value in rows[0][:7]]
    if normalized != ["opentime","open","high","low","close","volume","closetime"]:
        raise RuntimeError(f"Unexpected Binance CSV header: {normalized}")
    print("RECOGNIZED verified Binance Futures OHLCV CSV header",flush=True)
    return rows[1:]


def fetch_month(month: str) -> tuple[list[tuple], str]:
    filename=f"ETHUSDT-1m-{month}.zip"
    url=f"{ARCHIVE_ROOT}/{filename}"
    payload=read_public(url,32*1024*1024)
    digest=hashlib.sha256(payload).hexdigest()
    check=read_public(url+".CHECKSUM",2048).decode("ascii").strip().split()
    if len(check)!=2 or check[0].lower()!=digest or check[1]!=filename:
        raise RuntimeError(f"Binance public SHA256 checksum mismatch: {filename}")
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        members=[name for name in z.namelist() if name.endswith(".csv")]
        if members != [filename[:-4]+".csv"] or z.getinfo(members[0]).file_size>30*1024*1024:
            raise RuntimeError("Invalid Binance ZIP CSV structure/size")
        csv_text=z.read(members[0]).decode("utf-8-sig")
    rows=strip_verified_header(list(csv.reader(io.StringIO(csv_text))))
    candles=aggregate_month(rows,month)
    print(f"CHECKSUM PASS {filename} sha256={digest[:16]} M1={len(rows)} M15={len(candles)}",flush=True)
    return candles,digest


def check_overlap(conn, history: dict[int, tuple], first: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT open_time,close FROM autotrade_v3.candles
            WHERE symbol=%s AND timeframe=%s AND open_time >= %s AND open_time < %s
            ORDER BY open_time LIMIT 96""",(SYMBOL,FRAME,first,first+96*STEP))
        overlap=cur.fetchall()
    if len(overlap)<48:
        raise RuntimeError(f"Insufficient BingX/ Binance M15 overlap: {len(overlap)}")
    differences=[]
    for when,close in overlap:
        item=history.get(int(when))
        if item is None: raise RuntimeError(f"No Binance M15 overlap at {fmt(int(when))}")
        differences.append(abs(float(item[4])-float(close))/float(close)*100)
    result={"bars":len(overlap),"median_close_diff_pct":round(statistics.median(differences),6),
            "max_close_diff_pct":round(max(differences),6)}
    print("BINGX_BINANCE_OVERLAP "+json.dumps(result,sort_keys=True),flush=True)
    if result["max_close_diff_pct"]>2.0:
        raise RuntimeError("More than 2% price divergence at venue overlap: abort")
    return result


def insert_batch(conn, bars: list[tuple], sha: dict, overlap: dict) -> int:
    if not bars:
        return 0
    records=[(SYMBOL,FRAME,*row) for row in bars]
    placeholders=",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s)"]*len(records))
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO autotrade_v3.candles
                (symbol,timeframe,open_time,open,high,low,close,volume,close_time)
                VALUES """+placeholders+
                " ON CONFLICT(symbol,timeframe,open_time) DO NOTHING",
                [v for row in records for v in row])
            inserted=int(cur.rowcount)
            if inserted:
                details={"source":PROVIDER,"owner_approved_cross_venue_warmup":True,
                         "first_ms":bars[0][0],"last_ms":bars[-1][0],
                         "inserted":inserted,"archive_sha256":sha,"overlap":overlap}
                cur.execute("""INSERT INTO autotrade_v3.control_logs
                    (layer,symbol,step,message,details,discord_status)
                    VALUES ('L1',%s,'eth_m15_binance_warmup',%s,%s::jsonb,'disabled')""",
                    (SYMBOL,f"Binance ETH M15 warm-up {inserted} bars {fmt(bars[0][0])}..{fmt(bars[-1][0])}",
                     json.dumps(details,sort_keys=True)))
    return inserted


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    url=os.getenv("DATABASE_URL","")
    if not url.startswith(("postgres://","postgresql://")):
        raise RuntimeError("DATABASE_URL Supabase PostgreSQL secret not configured")
    with psycopg.connect(url,connect_timeout=15,prepare_threshold=None,autocommit=True) as conn:
        count,first,last=db_stats(conn)
        target=last-(TARGET-1)*STEP
        needed=max(0,(first-target)//STEP)
        print(f"EXISTING ETH M15 {count} rows {fmt(first)}..{fmt(last)}; needed={needed}; mode={'APPLY' if args.apply else 'AUDIT'}",flush=True)
        if not needed:
            print("PASS 12000+ contiguous M15 candles already present")
            return 0
        months={}
        hashes={}
        for month in MONTHS:
            months[month],hashes[month]=fetch_month(month)
        lookup={r[0]:r for candles in months.values() for r in candles}
        times=list(range(target,first,STEP))
        if not times or any(t not in lookup for t in times):
            raise RuntimeError(f"Archives cannot cover prefix {fmt(target)}..{fmt(first-STEP)}")
        overlap=check_overlap(conn,lookup,first)
        print(f"VALIDATED {len(times)} Binance M15 bars {fmt(times[0])}..{fmt(times[-1])}",flush=True)
        if not args.apply:
            print("AUDIT PASS, no production writes")
            return 0
        count2,first2,last2=db_stats(conn)
        if first2!=first or last2<last or count2<count:
            raise RuntimeError("V3 older bound changed while validating; rerun")
        # Live can add new bars while archives download; follow the newest bound.
        target2=last2-(TARGET-1)*STEP
        times=list(range(target2,first2,STEP))
        if any(t not in lookup for t in times):
            raise RuntimeError("Archive cannot cover refreshed live target")
        print(f"PREWRITE: {len(times)} missing earlier bars, latest {fmt(last2)}",flush=True)
        added_total=0
        for i in range(0,len(times),1000):
            chunk=[lookup[t] for t in times[i:i+1000]]
            inserted=insert_batch(conn,chunk,hashes,overlap)
            added_total+=inserted
            print(f"INSERTED {inserted} M15; total={added_total}",flush=True)
        final_n,final_first,final_last=db_stats(conn)
        if final_n<TARGET:
            raise RuntimeError(f"VERIFY FAILED: final={final_n}/{TARGET}")
        print(f"PASS ETH M15 final={final_n}; oldest={fmt(final_first)}; newest={fmt(final_last)}; inserted={added_total}",flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}",file=sys.stderr)
        sys.exit(1)
