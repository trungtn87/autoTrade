"""Seed the standalone research store from validated 1m artifacts.

No network calls and no trading credentials. 1m is aggregated deterministically
into 15m, then 1h/4h/6h are derived from the same source.
"""
import argparse
from pathlib import Path
import pandas as pd

OHLCV=["open","high","low","close","volume"]

def resample(df, rule):
    return df[OHLCV].resample(rule,origin="epoch",closed="left",label="left").agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
    ).dropna(subset=["open","high","low","close"])

def with_open_time(df):
    out=df.copy()
    out["open_time"]=(out.index.astype("int64")//1_000_000).astype("int64")
    return out[["open_time",*OHLCV]]

def load_1m(path):
    df=pd.read_pickle(path)
    if not isinstance(df.index,pd.DatetimeIndex):
        df.index=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    if df.index.tz is None: df.index=df.index.tz_localize("UTC")
    else: df.index=df.index.tz_convert("UTC")
    df=df.sort_index()
    if df.index.duplicated().any(): raise ValueError(f"Duplicate 1m timestamps in {path}")
    gaps=df.index.to_series().diff().dropna()
    if (gaps!=pd.Timedelta("1min")).any():
        bad=gaps[gaps!=pd.Timedelta("1min")]
        raise ValueError(f"1m seed has gaps: first={bad.index[0]} delta={bad.iloc[0]}")
    return df

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--seed-dir",default="data/seed")
    ap.add_argument("--store-dir",default="data/store")
    args=ap.parse_args()
    seed=Path(args.seed_dir); store=Path(args.store_dir); store.mkdir(parents=True,exist_ok=True)

    for slug in ["BTCUSDT","ETHUSDT"]:
        src=seed/f"{slug}_1m.pkl"
        if not src.exists(): raise FileNotFoundError(src)
        minute=load_1m(src)
        d15=resample(minute,"15min")
        if len(d15)*15 != len(minute):
            raise ValueError(f"{slug}: 1m seed is not exactly divisible into complete 15m bars")
        with_open_time(d15).to_pickle(store/f"{slug}_15m.pkl")
        for rule,suffix in [("1h","1h"),("4h","4h"),("6h","6h")]:
            with_open_time(resample(d15,rule)).to_pickle(store/f"{slug}_{suffix}.pkl")
        print(slug,len(minute),len(d15),d15.index.min(),d15.index.max())

if __name__=="__main__": main()
