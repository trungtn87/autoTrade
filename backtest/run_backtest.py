from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
from signals import build_all_signals,resample_ohlcv
from backtest import backtest_parallel,ALL
from smc_ob import ob_context,OBConfig

def load(path):
    p=Path(path)
    df=pd.read_csv(p) if p.suffix.lower()=='.csv' else pd.read_pickle(p)
    if not isinstance(df.index,pd.DatetimeIndex):
        if 'open_time' in df: df.index=pd.to_datetime(df.open_time,unit='ms',utc=True)
        elif 'time' in df: df.index=pd.to_datetime(df.time,utc=True)
    if df.index.tz is None: df.index=df.index.tz_localize('UTC')
    else: df.index=df.index.tz_convert('UTC')
    return df.sort_index()

ap=argparse.ArgumentParser()
ap.add_argument('--data15',required=True)
ap.add_argument('--data1h',default='')
ap.add_argument('--data4h',default='')
ap.add_argument('--data6h',default='')
ap.add_argument('--enabled',default=','.join(ALL))
ap.add_argument('--ob',action='store_true'); ap.add_argument('--danger',type=float,default=0.0)
ap.add_argument('--commission',type=float,default=0.0); ap.add_argument('--policy',default='tv_heuristic',choices=['tv_heuristic','stop_first','tp_first'])
ap.add_argument('--trades-out',default='')
ap.add_argument('--summary-out',default='')
args=ap.parse_args()
d15=load(args.data15)
d1=load(args.data1h) if args.data1h else resample_ohlcv(d15,'1h')
d4=load(args.data4h) if args.data4h else resample_ohlcv(d15,'4h')
d6=load(args.data6h) if args.data6h else resample_ohlcv(d15,'6h')
sigs=build_all_signals(d15,d1,d4,d6)
ob15=ob1=None
if args.ob:
    cfg=OBConfig(danger_atr=args.danger); ob15=ob_context(d15,cfg); ob1=ob_context(d1,cfg)
r=backtest_parallel(d15,sigs,args.enabled.split(','),commission_pct=args.commission,policy=args.policy,ob15=ob15,ob1h=ob1)
print(json.dumps(r['stats'],indent=2,default=str))
if args.trades_out:
    Path(args.trades_out).parent.mkdir(parents=True,exist_ok=True)
    r['trades'].to_csv(args.trades_out,index=False)
if args.summary_out:
    p=Path(args.summary_out); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(r['stats'],indent=2,default=str),encoding='utf-8')
