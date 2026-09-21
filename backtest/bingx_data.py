from __future__ import annotations
import os, time, hmac, hashlib, json
from pathlib import Path
from urllib.parse import urlencode
import requests
import pandas as pd

BASE_URL = 'https://open-api.bingx.com'
ENDPOINT = '/openApi/swap/v3/quote/klines'
INTERVAL_MS = {
    '1m':60_000,'3m':180_000,'5m':300_000,'15m':900_000,'30m':1_800_000,
    '1h':3_600_000,'2h':7_200_000,'4h':14_400_000,'6h':21_600_000,'8h':28_800_000,
    '12h':43_200_000,'1d':86_400_000,'3d':259_200_000,'1w':604_800_000,
}

class BingXClient:
    def __init__(self, api_key: str|None=None, secret: str|None=None, base_url: str=BASE_URL, timeout: int=20):
        self.api_key = api_key or os.getenv('BINGX_API_KEY')
        self.secret = secret or os.getenv('BINGX_API_SECRET')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.s = requests.Session()

    def _signed_params(self, params: dict) -> tuple[dict, dict]:
        p = dict(params)
        p['timestamp'] = int(time.time()*1000)
        headers = {}
        if self.api_key and self.secret:
            query = urlencode(sorted((k, v) for k,v in p.items() if v is not None))
            p['signature'] = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            headers['X-BX-APIKEY'] = self.api_key
        return p, headers

    def klines(self, symbol: str, interval: str, start_ms: int|None=None, end_ms: int|None=None, limit: int=1440):
        p={'symbol':symbol,'interval':interval,'limit':min(int(limit),1440)}
        if start_ms is not None: p['startTime']=int(start_ms)
        if end_ms is not None: p['endTime']=int(end_ms)
        p,h=self._signed_params(p)
        r=self.s.get(self.base_url+ENDPOINT, params=p, headers=h, timeout=self.timeout)
        r.raise_for_status()
        obj=r.json()
        if isinstance(obj,dict) and obj.get('code') not in (None,0):
            raise RuntimeError(f"BingX error: {obj}")
        return obj.get('data', obj)

def parse_klines(rows) -> pd.DataFrame:
    rec=[]
    for x in rows or []:
        if isinstance(x, dict):
            ot=x.get('time',x.get('openTime')); ct=x.get('closeTime')
            rec.append([ot,x.get('open'),x.get('high'),x.get('low'),x.get('close'),x.get('volume'),ct])
        else:
            rec.append([x[0],x[1],x[2],x[3],x[4],x[5],x[6] if len(x)>6 else None])
    df=pd.DataFrame(rec,columns=['open_time','open','high','low','close','volume','close_time'])
    if df.empty: return df
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    df['open_time']=pd.to_numeric(df['open_time'],errors='coerce').astype('Int64')
    df['close_time']=pd.to_numeric(df['close_time'],errors='coerce').astype('Int64')
    df=df.dropna(subset=['open_time']).drop_duplicates('open_time').sort_values('open_time')
    df.index=pd.to_datetime(df['open_time'].astype('int64'),unit='ms',utc=True)
    return df

def download_history(client: BingXClient, symbol: str, interval: str, start: str|pd.Timestamp, end: str|pd.Timestamp,
                     out_path: str|Path|None=None, sleep_s: float=0.12) -> pd.DataFrame:
    if interval not in INTERVAL_MS: raise ValueError(f'Unsupported interval: {interval}')
    start_ts=pd.Timestamp(start, tz='UTC') if pd.Timestamp(start).tzinfo is None else pd.Timestamp(start).tz_convert('UTC')
    end_ts=pd.Timestamp(end, tz='UTC') if pd.Timestamp(end).tzinfo is None else pd.Timestamp(end).tz_convert('UTC')
    cur=int(start_ts.timestamp()*1000); end_ms=int(end_ts.timestamp()*1000); step=INTERVAL_MS[interval]
    chunks=[]
    while cur <= end_ms:
        batch_end=min(end_ms, cur + step*1439)
        rows=client.klines(symbol,interval,cur,batch_end,1440)
        df=parse_klines(rows)
        if df.empty:
            cur=batch_end+step; time.sleep(sleep_s); continue
        chunks.append(df)
        last=int(df['open_time'].max())
        nxt=max(last+step, batch_end+step if last < cur else last+step)
        if nxt <= cur: break
        cur=nxt
        time.sleep(sleep_s)
    if not chunks: return pd.DataFrame()
    out=pd.concat(chunks).sort_values('open_time').drop_duplicates('open_time')
    out=out[(out.index>=start_ts)&(out.index<=end_ts)]
    if out_path:
        p=Path(out_path); p.parent.mkdir(parents=True,exist_ok=True)
        if p.suffix.lower()=='.csv': out.to_csv(p,index=False)
        else: out.to_pickle(p)
    return out

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('--symbol',required=True); ap.add_argument('--interval',required=True)
    ap.add_argument('--start',required=True); ap.add_argument('--end',required=True); ap.add_argument('--out',required=True)
    args=ap.parse_args()
    cli=BingXClient()
    df=download_history(cli,args.symbol,args.interval,args.start,args.end,args.out)
    print(json.dumps({'rows':len(df),'first':None if df.empty else str(df.index.min()),'last':None if df.empty else str(df.index.max()),'out':args.out}))
