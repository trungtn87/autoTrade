"""V3-01: inspect existing M1 Parquet offline. Never downloads market data."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

SYMBOLS = ("BTCUSDT", "ETHUSDT")
MINUTE = 60000
def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for data in iter(lambda:f.read(2**20),b""):
            h.update(data)
    return h.hexdigest()
def utc_ms(s):
    return int(datetime.strptime(s,"%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()*1000)
def column(names, *opts):
    for o in opts:
        for n in names:
            if n.lower()==o.lower():return '"' + n.replace('"','""') + '"'
    raise ValueError(f"Required Parquet column missing: {opts}")
def inspect(root:Path, out:Path, source_map:Path, checksums:Path,
            start="2023-11-01", end="2026-11-01"):
    import duckdb
    root=root.resolve()
    out=out.resolve()
    if not root.is_dir() or root==out or root in out.parents:
        raise ValueError("Root must exist; output must be outside raw data")
    raw=sorted(x for x in root.rglob("*.parquet") if x.is_file())
    if not raw or any(x.is_symlink() for x in raw):
        raise ValueError("No Parquet files, or unsafe symlink in raw data")
    names=[str(x.relative_to(root)).replace("\\","/") for x in raw]
    hashes={name:sha256(path) for name,path in zip(names,raw)}
    catalog=json.loads(source_map.read_text(encoding="utf-8"))
    expected=json.loads(checksums.read_text(encoding="utf-8"))
    if not isinstance(catalog,dict) or not isinstance(catalog.get("files"),list):
        raise ValueError("Invalid catalog: require files:[{path,provider,market_type}]")
    if set(expected)!=set(names) or any(expected[k].lower()!=hashes[k] for k in names):
        raise ValueError("Source manifest checksum mismatch or missing file")
    byname={row["path"]:row for row in catalog["files"]}
    if set(byname)!=set(names) or len(catalog["files"])!=len(names):
        raise ValueError("Source map must cover every file exactly once")
    for row in catalog["files"]:
        if row["provider"].upper() not in ("BINGX","BINANCE","MIXED") or row["market_type"].upper()!="USDT_PERP":
            raise ValueError("Unverified provider / market_type; do not substitute spot for futures")
    if utc_ms(end)<=utc_ms(start):
        raise ValueError("Invalid UTC range")
    out.mkdir(parents=True,exist_ok=True)
    db=duckdb.connect(str(out/"research.duckdb"))
    db.execute("SET TimeZone = 'UTC'")
    try:
        db.execute("CREATE OR REPLACE TABLE v3_sources(file VARCHAR,provider VARCHAR,market_type VARCHAR,sha256 VARCHAR)")
        db.executemany("INSERT INTO v3_sources VALUES(?,?,?,?)",[
            (str(path),byname[name]["provider"].upper(),"USDT_PERP",hashes[name])
            for name,path in zip(names,raw)])
        paths=[str(x) for x in raw]
        nameset={row[0] for row in db.execute(
          "DESCRIBE SELECT * FROM read_parquet(?,union_by_name=true,filename=true,hive_partitioning=false)",
          [paths]).fetchall()}
        # A mixed-provider Parquet requires per-row venue attribution.
        row_venue=next((v for v in nameset if v.lower() in
                        ("provider","source_exchange","exchange","venue","source")),None)
        if any(row["provider"].upper()=="MIXED" for row in catalog["files"]) and row_venue is None:
            raise ValueError("MIXED provider file requires a per-row venue column")
        row_expr=("upper(trim(cast(p."+column(nameset,row_venue)+" AS VARCHAR)))"
                  if row_venue is not None else None)
        provider_expr=("coalesce(nullif("+row_expr+",''),s.provider)"
                       if row_expr else "s.provider")
        conflict_expr=("CASE WHEN s.provider <> 'MIXED' AND "+
                       row_expr+" IS NOT NULL AND "+row_expr+
                       " <> '' AND "+row_expr+" <> s.provider THEN 1 ELSE 0 END"
                       if row_expr else "0")
        price_basis_verified=all(row.get("price_type","").upper()=="LAST"
                                for row in catalog["files"])
        basis_key=next((v for v in nameset if v.lower()=="price_basis"),None)
        market_key=next((v for v in nameset if v.lower()=="market"),None)
        basis_expr=("lower(trim(cast(p."+column(nameset,basis_key)+" AS VARCHAR)))"
                    if basis_key else "NULL")
        market_expr=("trim(cast(p."+column(nameset,market_key)+" AS VARCHAR))"
                     if market_key else "NULL")
        ts=column(nameset,"open_ms","open_time_ms","open_time","timestamp_ms","timestamp")
        o=column(nameset,"open","o");h=column(nameset,"high","h")
        l=column(nameset,"low","l");c=column(nameset,"close","c")
        v=column(nameset,"volume","vol")
        symbol=column(nameset,"symbol","pair")
        filesql="["+",".join("'"+p.replace("'","''")+"'" for p in paths)+"]"
        db.execute(f"""CREATE OR REPLACE VIEW v3_m1 AS
          SELECT upper(replace(cast(p.{symbol} AS VARCHAR),'-','')) symbol,
                 try_cast(p.{ts} AS BIGINT) open_ms,
                 try_cast(p.{o} AS DOUBLE) open, try_cast(p.{h} AS DOUBLE) high,
                 try_cast(p.{l} AS DOUBLE) low, try_cast(p.{c} AS DOUBLE) AS "close",
                 try_cast(p.{v} AS DOUBLE) volume, {provider_expr} AS provider, s.market_type,
                 {conflict_expr} AS provider_conflict,
                 {basis_expr} AS recorded_price_basis, {market_expr} AS recorded_market,
                 p.filename source_file
          FROM read_parquet({filesql},union_by_name=true,filename=true,hive_partitioning=false) p
          JOIN v3_sources s ON p.filename=s.file""")
        result=db.execute("""SELECT symbol, count(*) AS row_count, count(DISTINCT open_ms) AS minute_count,
          count(*) FILTER(WHERE open_ms IS NULL OR open_ms<? OR open_ms>=? OR open_ms%60000<>0) bad_time,
          count(*) FILTER(WHERE provider IS NULL OR provider NOT IN ('BINANCE','BINGX')
                OR market_type <> 'USDT_PERP' OR provider_conflict <> 0
                OR (recorded_price_basis IS NOT NULL AND recorded_price_basis <> 'last')
                OR (recorded_market IS NOT NULL AND recorded_market NOT IN ('USD-M perpetual','USDT perpetual'))
                ) AS bad_provenance,
          count(*) FILTER(WHERE open IS NULL OR high IS NULL OR low IS NULL OR "close" IS NULL
                 OR volume IS NULL OR NOT isfinite(open) OR NOT isfinite(high)
                 OR NOT isfinite(low) OR NOT isfinite("close") OR NOT isfinite(volume)
                 OR open<=0 OR low<=0 OR high<=0 OR "close"<=0
                 OR high<greatest(open,"close",low) OR low<0
                 OR low>least(open,"close",high) OR volume<0) bad_ohlcv
          FROM v3_m1 GROUP BY symbol ORDER BY symbol""",
          [utc_ms(start),utc_ms(end)]).fetchall()
        exp=(utc_ms(end)-utc_ms(start))//MINUTE
        metrics=[dict(symbol=s,rows=n,unique_minutes=u,expected=exp,
          missing=max(0,exp-u),duplicates=max(0,n-u),bad_timestamp=bt,bad_provenance=prov,bad_ohlcv=bp)
          for s,n,u,bt,prov,bp in result]
        segments=[dict(symbol=s,month=m,provider=p,rows=n,unique_minutes=u)
          for s,m,p,n,u in db.execute("""SELECT symbol,
          strftime(to_timestamp(open_ms/1000.0),'%Y-%m'),provider,
          count(*),count(DISTINCT open_ms)
          FROM v3_m1 GROUP BY 1,2,3 ORDER BY 1,2,3""").fetchall()]
        merged=[dict(symbol=s,month=m,rows=n,unique_minutes=u)
          for s,m,n,u in db.execute("""SELECT symbol,
          strftime(to_timestamp(open_ms/1000.0),'%Y-%m'),
          count(*),count(DISTINCT open_ms)
          FROM v3_m1 GROUP BY 1,2 ORDER BY 1,2""").fetchall()]
        source_lookup={(r["symbol"],r["month"],r["provider"]):r for r in segments}
        merged_lookup={(r["symbol"],r["month"]):r for r in merged}
        monthly=[]
        cursor=date.fromisoformat(start)
        end_date=date.fromisoformat(end)
        while cursor<end_date:
            next_month=(date(cursor.year+1,1,1) if cursor.month==12 else
                        date(cursor.year,cursor.month+1,1))
            stop=min(end_date,next_month)
            month=cursor.strftime("%Y-%m")
            expected=(stop-cursor).days*24*60
            for sym in SYMBOLS:
                total=merged_lookup.get((sym,month),{})
                b=source_lookup.get((sym,month,"BINANCE"),{})
                x=source_lookup.get((sym,month,"BINGX"),{})
                binance_minutes=b.get("unique_minutes",0)
                merged_minutes=total.get("unique_minutes",0)
                b_coverage=round(100*binance_minutes/expected,6)
                monthly.append({
                    "symbol":sym,"month":month,"expected_minutes":expected,
                    "hybrid_rows":total.get("rows",0),
                    "hybrid_unique":merged_minutes,
                    "hybrid_missing":max(0,expected-merged_minutes),
                    "hybrid_status":"PASS" if merged_minutes==expected and
                                    total.get("rows",0)==expected else "INCOMPLETE",
                    "binance_rows":b.get("rows",0),
                    "binance_unique":binance_minutes,
                    "binance_coverage_pct":b_coverage,
                    "binance_status":"PASS" if binance_minutes/expected>=0.9995
                                      else "INCOMPLETE",
                    "bingx_supplement_rows":x.get("rows",0),
                    "bingx_supplement_unique":x.get("unique_minutes",0),
                })
            cursor=stop
        hybrid_complete=(set(m["symbol"] for m in metrics)==set(SYMBOLS)
          and all(m["rows"]==exp and m["unique_minutes"]==exp and
                  m["bad_timestamp"]==0 and m["bad_provenance"]==0 and
                  m["bad_ohlcv"]==0 for m in metrics)
          and all(x["hybrid_status"]=="PASS" for x in monthly))
        binance_complete=all(x["binance_status"]=="PASS" for x in monthly)
        research_ready=hybrid_complete and price_basis_verified
        status=("FAIL" if not hybrid_complete else
                "INCONCLUSIVE" if not price_basis_verified else
                "PASS" if binance_complete else
                "HYBRID_PASS_SOURCE_INCOMPLETE")
        report={
          "gate":"A","status":status,"hybrid_gate":"PASS" if hybrid_complete else "FAIL",
          "binance_source_gate":"PASS" if binance_complete else "INCOMPLETE",
          "research_ready":research_ready,
          "price_basis_verified_last":price_basis_verified,
          "scope":"OFFLINE_OLD_M1_NO_DOWNLOAD",
          "start_utc":start,"end_utc":end,
          "file_count":len(raw),"sha256":hashes,"metrics":metrics,
          "source_segments":segments,"monthly":monthly,
          "warning":"HYBRID PASS does not mean Binance monthly partitions all passed. "
            "BingX supplement is a separate venue. Execution on BingX is NOT validated."
        }
        (out/"v3_m1_qa.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
        return report
    finally:
        db.close()
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input-root",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--source-map",type=Path,required=True)
    p.add_argument("--checksums",type=Path,required=True)
    p.add_argument("--start",default="2023-11-01")
    p.add_argument("--end",default="2026-11-01")
    a=p.parse_args()
    try:r=inspect(a.input_root,a.output_dir,a.source_map,a.checksums,a.start,a.end)
    except (ValueError,OSError,KeyError,TypeError) as e:p.exit(2,f"V3-01 error: {e}\n")
    print(json.dumps({"status":r["status"],"metrics":r["metrics"]},indent=2))
    return 0 if r["research_ready"] else 1
if __name__=="__main__":raise SystemExit(main())
