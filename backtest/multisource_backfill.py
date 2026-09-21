from __future__ import annotations
import argparse, csv, hashlib, io, json, time, urllib.parse, urllib.request, zipfile
from pathlib import Path
import pandas as pd
import numpy as np

UA={"User-Agent":"Mozilla/5.0 research-backtest/1.0"}
STEP_MS=15*60*1000
OHLCV=["open","high","low","close","volume"]

def get_bytes(url, timeout=30):
    req=urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), int(getattr(r,"status",200))

def get_json(base, params, timeout=30):
    url=base+"?"+urllib.parse.urlencode(params)
    raw,status=get_bytes(url,timeout)
    return json.loads(raw), status, url

def utc_ms(s):
    return int(pd.Timestamp(s,tz="UTC").timestamp()*1000)

def probe_bingx_swap(sym,start,end):
    obj,status,url=get_json("https://open-api.bingx.com/openApi/swap/v3/quote/klines",
        {"symbol":sym.replace("USDT","-USDT"),"interval":"15m","startTime":utc_ms(start),"endTime":utc_ms(end),"limit":96})
    data=obj.get("data",[]) if isinstance(obj,dict) else []
    return {"ok":obj.get("code")==0 and len(data)>0,"rows":len(data),"code":obj.get("code"),"url":url}

def probe_bingx_hist(sym,start,end):
    obj,status,url=get_json("https://open-api.bingx.com/openApi/market/his/v1/kline",
        {"symbol":sym.replace("USDT","-USDT"),"interval":"15m","startTime":utc_ms(start),"endTime":utc_ms(end),"limit":96})
    data=obj.get("data",[]) if isinstance(obj,dict) else []
    return {"ok":obj.get("code")==0 and len(data)>0,"rows":len(data),"code":obj.get("code"),"url":url}

def probe_bingx_spot(sym,start,end):
    obj,status,url=get_json("https://open-api.bingx.com/openApi/spot/v2/market/kline",
        {"symbol":sym.replace("USDT","-USDT"),"interval":"15m","startTime":utc_ms(start),"endTime":utc_ms(end),"limit":96})
    data=obj.get("data",[]) if isinstance(obj,dict) else []
    return {"ok":obj.get("code")==0 and len(data)>0,"rows":len(data),"code":obj.get("code"),"url":url}

def probe_binance_api(sym,start,end):
    obj,status,url=get_json("https://fapi.binance.com/fapi/v1/klines",
        {"symbol":sym,"interval":"15m","startTime":utc_ms(start),"endTime":utc_ms(end)-1,"limit":96})
    return {"ok":isinstance(obj,list) and len(obj)>0,"rows":len(obj) if isinstance(obj,list) else 0,"url":url}

def vision_url(sym,year,month):
    name=f"{sym}-15m-{year:04d}-{month:02d}.zip"
    return f"https://data.binance.vision/data/futures/um/monthly/klines/{sym}/15m/{name}"

def probe_binance_vision(sym,year,month=1):
    url=vision_url(sym,year,month)
    raw,status=get_bytes(url,30)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist()
        with z.open(names[0]) as f:
            first=f.readline().decode("utf-8","ignore").strip()
    return {"ok":len(raw)>100 and first!="","bytes":len(raw),"first_line":first[:120],"url":url}

def probe_bybit(sym,start,end):
    obj,status,url=get_json("https://api.bybit.com/v5/market/kline",
        {"category":"linear","symbol":sym,"interval":"15","start":utc_ms(start),"end":utc_ms(end)-1,"limit":96})
    data=((obj.get("result") or {}).get("list") or []) if isinstance(obj,dict) else []
    return {"ok":obj.get("retCode")==0 and len(data)>0,"rows":len(data),"retCode":obj.get("retCode"),"url":url}

def probe_okx(sym,start,end):
    inst=sym.replace("USDT","-USDT-SWAP")
    obj,status,url=get_json("https://www.okx.com/api/v5/market/history-candles",
        {"instId":inst,"bar":"15m","after":utc_ms(end),"limit":96})
    data=obj.get("data",[]) if isinstance(obj,dict) else []
    a,b=utc_ms(start),utc_ms(end)
    inwin=sum(1 for r in data if a <= int(r[0]) < b)
    return {"ok":obj.get("code")=="0" and inwin>0,"rows":len(data),"in_window":inwin,"code":obj.get("code"),"url":url}

PROBERS={
 "bingx_swap":probe_bingx_swap,
 "bingx_historical":probe_bingx_hist,
 "bingx_spot":probe_bingx_spot,
 "binance_api":probe_binance_api,
 "bybit_linear":probe_bybit,
 "okx_swap":probe_okx,
}

def parse_binance_zip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name=z.namelist()[0]
        data=z.read(name)
    df=pd.read_csv(io.BytesIO(data),header=None)
    # tolerate optional header row
    if not str(df.iloc[0,0]).replace(".0","").isdigit():
        df=df.iloc[1:].reset_index(drop=True)
    df=df.iloc[:,:6]
    df.columns=["open_time","open","high","low","close","volume"]
    for c in df.columns:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    df=df.dropna()
    ot=df.open_time.astype("int64")
    # futures are ms; handle us defensively
    if ot.iloc[0] > 10**14:
        ot=ot//1000
    df["open_time"]=ot
    df.index=pd.to_datetime(df.open_time,unit="ms",utc=True)
    return df[["open_time",*OHLCV]]

def validate(df,start,end):
    a=utc_ms(start); b=utc_ms(end)
    times=df.open_time.astype("int64")
    expected=(b-a)//STEP_MS
    dup=int(times.duplicated().sum())
    idx=np.sort(times.to_numpy())
    gaps=int(np.sum(np.diff(idx)!=STEP_MS)) if len(idx)>1 else 0
    invalid=int(((df.high<df[["open","close"]].max(axis=1)) |
                 (df.low>df[["open","close"]].min(axis=1)) |
                 (df.low>df.high) |
                 (df[["open","high","low","close"]]<=0).any(axis=1) |
                 (df.volume<0)).sum())
    missing=int(expected-len(set(times[(times>=a)&(times<b)])))
    return {"rows":len(df),"expected_rows":expected,"duplicates":dup,"non15m_gaps":gaps,
            "missing_intervals":missing,"invalid_rows":invalid,
            "first":str(df.index.min()),"last":str(df.index.max()),
            "valid":dup==0 and gaps==0 and missing==0 and invalid==0}

def download_vision_month(sym,year,month,cache):
    url=vision_url(sym,year,month)
    name=url.rsplit("/",1)[-1]
    p=cache/name
    if p.exists():
        raw=p.read_bytes()
    else:
        raw,_=get_bytes(url,60)
        p.write_bytes(raw)
    # verify published checksum when accessible
    checksum_ok=None
    try:
        c,_=get_bytes(url+".CHECKSUM",20)
        expected=c.decode().split()[0].strip()
        checksum_ok=hashlib.sha256(raw).hexdigest()==expected
        if checksum_ok is False:
            raise ValueError(f"Checksum mismatch {name}")
    except Exception:
        checksum_ok=None
    return parse_binance_zip(raw), checksum_ok

def build_binance(sym,start_year,end_year,end_month,outdir):
    cache=outdir/"downloads"; cache.mkdir(parents=True,exist_ok=True)
    chunks=[]; checks=[]
    for y in range(start_year,end_year+1):
        maxm=end_month if y==end_year else 12
        for m in range(1,maxm+1):
            try:
                d,ck=download_vision_month(sym,y,m,cache)
                chunks.append(d); checks.append({"year":y,"month":m,"rows":len(d),"checksum_ok":ck})
                print(f"{sym} vision {y}-{m:02d}: {len(d)}",flush=True)
            except Exception as e:
                raise RuntimeError(f"{sym} vision {y}-{m:02d} failed: {e}")
    df=pd.concat(chunks).sort_index()
    df=df[~df.index.duplicated(keep="last")]
    return df,checks

def merge_with_bingx_tail(binance,store_file,boundary):
    if not store_file.exists():
        return binance.copy(),{"tail_used":False}
    bx=pd.read_pickle(store_file)
    if "open_time" not in bx.columns:
        bx=bx.copy(); bx["open_time"]=(pd.to_datetime(bx.index,utc=True).astype("int64")//1_000_000)
    bx.index=pd.to_datetime(bx.open_time,unit="ms",utc=True)
    bx=bx[["open_time",*OHLCV]]
    cut=pd.Timestamp(boundary,tz="UTC")
    left=binance[binance.index<cut]
    right=bx[bx.index>=cut]
    out=pd.concat([left,right]).sort_index()
    out=out[~out.index.duplicated(keep="last")]
    return out,{"tail_used":True,"boundary":boundary,"binance_rows":len(left),"bingx_rows":len(right)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--out-dir",default="data/multisource")
    ap.add_argument("--store-dir",default="data/store")
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    report={"probe":{},"build":{}}
    years=range(2021,2027)
    symbols=["BTCUSDT","ETHUSDT"]
    # Probe a 24h window in mid-year; 2026 uses May which is inside known BingX sample.
    for sym in symbols:
        report["probe"][sym]={}
        for y in years:
            start=f"{y}-05-15"; end=f"{y}-05-16"
            yr={}
            for name,fn in PROBERS.items():
                try:
                    yr[name]=fn(sym,start,end)
                except Exception as e:
                    yr[name]={"ok":False,"error":repr(e)}
            try:
                yr["binance_vision"]=probe_binance_vision(sym,y,5)
            except Exception as e:
                yr["binance_vision"]={"ok":False,"error":repr(e)}
            report["probe"][sym][str(y)]=yr
            print("PROBE",sym,y,{k:v.get("ok") for k,v in yr.items()},flush=True)
            (out/"probe_report.json").write_text(json.dumps(report,indent=2))

    # Build complete Binance USD-M 15m history 2021-01 through 2026-08.
    for sym in symbols:
        try:
            df,checks=build_binance(sym,2021,2026,8,out)
            v=validate(df,"2021-01-01","2026-09-01")
            p=out/f"{sym}_binance_um_15m.pkl"; df.to_pickle(p)
            hybrid,meta=merge_with_bingx_tail(df,Path(args.store_dir)/f"{sym}_15m.pkl","2026-04-01")
            vh=validate(hybrid,"2021-01-01","2026-09-21")
            hp=out/f"{sym}_hybrid_15m.pkl"; hybrid.to_pickle(hp)
            report["build"][sym]={
                "binance_vision":{"file":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"validation":v,"months":checks},
                "hybrid_binance_to_bingx":{"file":str(hp),"sha256":hashlib.sha256(hp.read_bytes()).hexdigest(),"validation":vh,"merge":meta}
            }
        except Exception as e:
            report["build"][sym]={"error":repr(e)}
        (out/"probe_report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report["build"],indent=2))
    # Success when at least one complete backtest dataset exists for both symbols.
    ok=all(report["build"].get(s,{}).get("hybrid_binance_to_bingx",{}).get("validation",{}).get("valid",False) for s in symbols)
    raise SystemExit(0 if ok else 2)

if __name__=="__main__":
    main()
