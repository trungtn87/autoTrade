"""Public-only BingX candles. UTC half-open ranges, bounded requests, strict coverage."""
from __future__ import annotations
import json,time,urllib.request,urllib.parse
from pathlib import Path
import pandas as pd
BASE_URL='https://open-api.bingx.com'
ENDPOINT='/openApi/swap/v3/quote/klines'
INTERVAL_MS={'1m':60000,'15m':900000,'1h':3600000,'4h':14400000,'6h':21600000}
COLS=['open_time','open','high','low','close','volume']
def utc(value):
    t=pd.Timestamp(value)
    return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')
class BingXClient:
    def klines(self,symbol,interval,start_ms,end_ms,limit=500):
        query=urllib.parse.urlencode(dict(symbol=symbol,interval=interval,startTime=start_ms,endTime=end_ms,limit=limit))
        for attempt in range(5):
            try:
                with urllib.request.urlopen(BASE_URL+ENDPOINT+'?'+query,timeout=30) as r: obj=json.load(r)
                if obj.get('code')!=0: raise RuntimeError(str(obj))
                if not isinstance(obj.get('data'),list): raise ValueError('Invalid API data')
                return obj['data']
            except Exception:
                if attempt==4: raise
                time.sleep(min(2**attempt,8))
def parse_klines(rows):
    records=[]
    for x in rows:
        if not isinstance(x,dict): raise ValueError('Unexpected kline schema')
        records.append([int(x.get('time',x.get('openTime')))]+[float(x[k]) for k in COLS[1:]])
    df=pd.DataFrame(records,columns=COLS)
    df.index=pd.to_datetime(df.open_time,unit='ms',utc=True)
    return df.sort_index()
def validate(df,interval,start,end):
    step=INTERVAL_MS[interval]; a=int(utc(start).timestamp()*1000); b=int(utc(end).timestamp()*1000)
    if a%step or b%step or b<=a: raise ValueError('Range must be aligned, nonempty [start,end) UTC')
    expected=set(range(a,b,step)); times=df.open_time.astype('int64') if len(df) else pd.Series([],dtype='int64')
    actual=set(times); bad=0
    if len(df):
        import numpy as np
        v=df[COLS[1:]]
        bad=int((~np.isfinite(v).all(axis=1)|(df.low>df[['open','close']].min(axis=1))|(df.high<df[['open','close']].max(axis=1))|(df.low>df.high)|(df[COLS[1:5]]<=0).any(axis=1)|(df.volume<0)).sum())
    report=dict(rows=len(df),expected_rows=len(expected),first=None if df.empty else str(df.index.min()),last=None if df.empty else str(df.index.max()),duplicates=int(times.duplicated().sum()),missing_intervals=len(expected-actual),out_of_range=len(actual-expected),invalid_rows=bad)
    report['valid']=not any(report[k] for k in ['duplicates','missing_intervals','out_of_range','invalid_rows'])
    return report

def download_history(client,symbol,interval,start,end,out_path=None,sleep_s=.12):
    step=INTERVAL_MS[interval]; a=int(utc(start).timestamp()*1000); b=int(utc(end).timestamp()*1000)
    if a%step or b%step or b<=a: raise ValueError('Unaligned range')
    if b>int(pd.Timestamp.now(tz='UTC').timestamp()*1000)//step*step: raise ValueError('Range includes unclosed candles')
    path=Path(out_path) if out_path else None
    # Per-page JSON checkpoints survive a failed job. Revalidate on every reuse.
    cache=path.parent/'pages'/symbol/interval if path else None
    if cache: cache.mkdir(parents=True,exist_ok=True)
    chunks=[]
    def fetch(lo,hi,depth=0):
        page=cache/f'{lo}-{hi}.json' if cache else None
        if page and page.exists(): rows=json.loads(page.read_text())
        else:
            rows=client.klines(symbol,interval,lo,hi,min(500,(hi-lo)//step));time.sleep(sleep_s)
        df=parse_klines(rows)
        # Some endpoints include the end boundary. It belongs to the next page.
        df=df[(df.open_time>=lo)&(df.open_time<hi)]
        if df.open_time.duplicated().any(): raise ValueError(f'Duplicate API candles: {symbol} {interval} {lo}')
        report=validate(df,interval,pd.to_datetime(lo,unit='ms',utc=True),pd.to_datetime(hi,unit='ms',utc=True))
        if report['valid']:
            if page and not page.exists():
                tmp=page.with_suffix('.tmp');tmp.write_text(json.dumps(rows));tmp.replace(page)
            return df
        if report['invalid_rows']: raise ValueError(f'Invalid OHLCV: {report}')
        if depth<3 and (hi-lo)//step>1:
            mid=lo+((hi-lo)//step//2)*step
            return pd.concat([fetch(lo,mid,depth+1),fetch(mid,hi,depth+1)])
        raise ValueError(f'Incomplete BingX data {symbol} {interval} [{lo},{hi}): {report}')
    for lo in range(a,b,500*step):
        hi=min(b,lo+500*step);chunks.append(fetch(lo,hi))
        if len(chunks)%25==0: print(f'{symbol} {interval}: {lo-a}/{b-a} ms covered',flush=True)
    df=pd.concat(chunks).sort_index(); report=validate(df,interval,start,end)
    if not report['valid']: raise ValueError(str(report))
    if path:
        path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');df.to_pickle(tmp);tmp.replace(path)
    return df
