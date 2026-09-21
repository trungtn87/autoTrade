"""Data-only pipeline. Never launch strategy research on incomplete data."""
import argparse,json,hashlib,platform,sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import pandas as pd
import numpy as np
from bingx_data import BingXClient,download_history,validate,utc

def main():
    p=argparse.ArgumentParser();p.add_argument('--symbols',default='BTC-USDT,ETH-USDT');p.add_argument('--start',default='2026-04-01');p.add_argument('--end',default='2026-09-21');p.add_argument('--out-dir',default='data');p.add_argument('--intervals',default='1m,15m,1h,4h,6h');args=p.parse_args()
    if args.intervals!='1m,15m,1h,4h,6h':p.error('This validated pipeline requires 1m,15m,1h,4h,6h')
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    manifest=dict(source='BingX perpetual futures',requested_start=args.start,requested_end_exclusive=args.end,created_at=str(pd.Timestamp.now(tz='UTC')),environment=dict(python=sys.version,platform=platform.platform(),pandas=pd.__version__,numpy=np.__version__),datasets={},errors=[],status='INCOMPLETE')
    def worker(symbol):
        slug=symbol.replace('-','');client=BingXClient();reports={}
        # Download native candles and independently compare against 1m aggregation.
        minute=download_history(client,symbol,'1m',args.start,args.end,out/f'{slug}_1m.pkl')
        for interval in args.intervals.split(','):
            path=out/f'{slug}_{interval}.pkl'
            d=minute if interval=='1m' else download_history(client,symbol,interval,args.start,args.end,path)
            r=validate(d,interval,args.start,args.end)
            if interval!='1m':
                freq='15min' if interval=='15m' else interval
                agg=minute.resample(freq,origin='epoch',closed='left',label='left').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
                mismatches={c:int((~np.isclose(d[c].to_numpy(),agg[c].to_numpy(),rtol=1e-7 if c=='volume' else 1e-10,atol=1e-8)).sum()) for c in agg.columns}
                r['aggregation_mismatches']=mismatches
                if any(mismatches.values()):r['valid']=False
            r['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();reports[interval]=r
        return reports
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs={pool.submit(worker,s):s for s in args.symbols.split(',')}
        for future in as_completed(jobs):
            s=jobs[future]
            try:manifest['datasets'][s]=future.result()
            except Exception as e:manifest['errors'].append({'symbol':s,'error':str(e)})
            (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    good=not manifest['errors'] and all(r['valid'] for rows in manifest['datasets'].values() for r in rows.values())
    manifest['status']='PASS' if good else 'FAIL';(out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))
    if not good:raise SystemExit(1)
if __name__=='__main__':main()
