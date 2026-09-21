"""Public-market backfill for research only.

This module never reads BingX credentials. It uses only the public perpetual-futures
K-line endpoint and stores a standalone research dataset under data/store.
"""
import argparse, json, hashlib, platform, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from bingx_data import BingXClient, download_history, validate

OHLCV = ["open","high","low","close","volume"]

def resample(df, rule):
    return df[OHLCV].resample(rule, origin="epoch", closed="left", label="left").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna(subset=["open","high","low","close"])

def with_open_time(df):
    out=df.copy()
    out["open_time"]=(out.index.astype("int64") // 1_000_000).astype("int64")
    return out[["open_time",*OHLCV]]

def merge_strict(old, new, step):
    x=pd.concat([old,new]).sort_index()
    x=x[~x.index.duplicated(keep="last")]
    if len(x)>1:
        gaps=x.index.to_series().diff().dropna()
        bad=gaps[gaps != pd.Timedelta(step)]
        if len(bad):
            raise ValueError(f"Research store has {len(bad)} non-{step} gaps; first={bad.index[0]} delta={bad.iloc[0]}")
    return x

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--symbols",default="BTC-USDT,ETH-USDT")
    p.add_argument("--start",required=True)
    p.add_argument("--end",required=True)
    p.add_argument("--out-dir",default="data")
    p.add_argument("--sleep",type=float,default=1.05)
    args=p.parse_args()

    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    store=out/"store"; store.mkdir(parents=True,exist_ok=True)
    manifest={
        "source":"BingX public perpetual-futures K-lines",
        "credentials_used":False,
        "endpoint":"/openApi/swap/v3/quote/klines",
        "requested_start":args.start,
        "requested_end_exclusive":args.end,
        "created_at":str(pd.Timestamp.now(tz="UTC")),
        "environment":{"python":sys.version,"platform":platform.platform(),"pandas":pd.__version__},
        "datasets":{},"errors":[],"status":"INCOMPLETE"
    }

    def worker(symbol):
        slug=symbol.replace("-","")
        client=BingXClient()
        temp=out/f"{slug}_backfill_15m.pkl"
        fresh=download_history(client,symbol,"15m",args.start,args.end,temp,sleep_s=args.sleep)
        report=validate(fresh,"15m",args.start,args.end)
        if not report["valid"]:
            raise ValueError(f"Backfill invalid: {report}")

        target=store/f"{slug}_15m.pkl"
        if target.exists():
            old=pd.read_pickle(target)
            old=old[OHLCV] if set(OHLCV).issubset(old.columns) else old
            if not isinstance(old.index,pd.DatetimeIndex):
                old.index=pd.to_datetime(old["open_time"],unit="ms",utc=True)
            old.index=old.index.tz_convert("UTC") if old.index.tz else old.index.tz_localize("UTC")
            merged=merge_strict(old[OHLCV],fresh[OHLCV],"15min")
        else:
            merged=fresh[OHLCV].copy()

        with_open_time(merged).to_pickle(target)
        for rule,suffix in [("1h","1h"),("4h","4h"),("6h","6h")]:
            with_open_time(resample(merged,rule)).to_pickle(store/f"{slug}_{suffix}.pkl")

        return {
            "backfill":report,
            "store":{"rows_15m":len(merged),"first":str(merged.index.min()),"last":str(merged.index.max()),
                     "sha256":hashlib.sha256(target.read_bytes()).hexdigest()}
        }

    symbols=[s.strip() for s in args.symbols.split(",") if s.strip()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs={pool.submit(worker,s):s for s in symbols}
        for fut in as_completed(jobs):
            s=jobs[fut]
            try: manifest["datasets"][s]=fut.result()
            except Exception as e: manifest["errors"].append({"symbol":s,"error":str(e)})
            (out/"manifest.json").write_text(json.dumps(manifest,indent=2))

    manifest["status"]="PASS" if not manifest["errors"] and len(manifest["datasets"])==len(symbols) else "FAIL"
    (out/"manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))
    if manifest["status"]!="PASS": raise SystemExit(1)

if __name__=="__main__": main()
