"""Check the real live adapter on historical signal and non-signal closes."""
from __future__ import annotations
import argparse,json,sys
import pandas as pd
from app.final14_config import FINAL14_CASES
from app.final14_exact_strategy import scan_latest

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--btc',required=True);ap.add_argument('--eth',required=True);ap.add_argument('--research-dir',required=True)
    args=ap.parse_args();sys.path.insert(0,args.research_dir)
    from layer3_long import load,precompute,entry_variants,build_context
    from two_trail_layer12 import approved
    from rr_tp22 import CASES
    report={}
    for symbol,path in [('BTC-USDT',args.btc),('ETH-USDT',args.eth)]:
        d=load(path);configs=FINAL14_CASES[symbol];allv=entry_variants(precompute(d));lock={};selected={}
        for combo,cfg in configs.items():
            name='TIER' if combo==11 else f'C{combo}'
            assert (cfg['entry_variant'],cfg['layer2'])==CASES[symbol.replace('-','')][name]
            lock[name]={'layer2_variant':cfg['layer2']}
            selected[name]=[v for v in allv[name] if v[0]==cfg['entry_variant']]
        ctx=build_context(d,lock,selected);expected={};points=set();coverage={}
        for combo,cfg in configs.items():
            name='TIER' if combo==11 else f'C{combo}'
            valid=[(pos,side) for pos,side,ot,sd,blocked in ctx[name][cfg['entry_variant']] if pos>=11999 and approved(side,sd,blocked,cfg['layer2'])]
            for pos,side in valid:expected.setdefault(pos,set()).add((combo,'BUY' if side=='L' else 'SELL'))
            for side in ['L','S']:
                candidates=[p for p,s in valid if s==side]
                coverage[f'{name}_{side}']=len(candidates)
                if candidates:points.add(candidates[-1])
        points.update(range(len(d)-8,len(d)))
        for pos in sorted(points):
            frame=d.iloc[pos-11999:pos+1].copy()
            # Canonical integer close timestamps at end of 15m interval.
            frame['close_time']=frame.index.as_unit('ns').asi8//1_000_000+899999
            signals=scan_latest(symbol,frame)
            actual={(s.combo,s.side) for s in signals}
            assert actual==expected.get(pos,set()),(symbol,str(d.index[pos]),actual,expected.get(pos,set()))
            for s in signals:
                cfg=configs[s.combo];entry=float(d.iloc[pos]['close']);direction=1 if s.side=='BUY' else -1
                assert abs(s.entry-entry)<1e-9
                # Same reference convention: percentage points converted ONCE.
                tp_pct=cfg['tp_pct']*100;sl_pct=cfg['sl_pct']*100
                assert abs(s.tp-entry*(1+direction*tp_pct/100))<1e-8
                assert abs(s.sl-entry*(1-direction*sl_pct/100))<1e-8
                assert s.close_time==int(d.index[pos].value//1_000_000+899999)
        report[symbol]={'historical_closes_checked':len(points),'coverage':coverage}
    print(json.dumps(report,indent=2));print('FINAL14 live adapter historical replay PASS')
if __name__=='__main__':main()
