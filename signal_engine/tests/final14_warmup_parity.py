from __future__ import annotations
import argparse, json
import pandas as pd

from app.final14_config import FINAL14_CASES
from app.final14_research.layer3_long import precompute, entry_variants, build_context, load
from app.final14_research.two_trail_layer12 import approved, END


def event_sets(d, symbol):
    pc=precompute(d)
    allv=entry_variants(pc)
    cases=FINAL14_CASES[symbol]
    lock={}
    selected={}
    for combo,cfg in cases.items():
        cname="TIER" if combo==11 else f"C{combo}"
        lock[cname]={"layer2_variant":cfg["layer2"]}
        matches=[x for x in allv[cname] if x[0]==cfg["entry_variant"]]
        assert len(matches)==1,(symbol,cname,cfg["entry_variant"],len(matches))
        selected[cname]=matches
    ctx=build_context(d,lock,selected)
    out={}
    for combo,cfg in cases.items():
        cname="TIER" if combo==11 else f"C{combo}"
        ev=set()
        for pos,side,ot,sd,blocked in ctx[cname][cfg["entry_variant"]]:
            if approved(side,sd,blocked,cfg["layer2"]):
                ev.add((int(d.index[pos].value//1_000_000),side))
        out[combo]=ev
    return out


def compare(full,tail,cutoff_ms):
    report={}; ok=True
    for combo in full:
        a={x for x in full[combo] if x[0]>=cutoff_ms}
        b={x for x in tail[combo] if x[0]>=cutoff_ms}
        miss=sorted(a-b); extra=sorted(b-a)
        report[str(combo)]={"full":len(a),"tail":len(b),"missing":len(miss),"extra":len(extra),
                            "missing_sample":miss[:3],"extra_sample":extra[:3]}
        ok=ok and not miss and not extra
    return ok,report


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True);ap.add_argument("--eth",required=True)
    args=ap.parse_args()
    result={}
    for symbol,path in [("BTC-USDT",args.btc),("ETH-USDT",args.eth)]:
        d=load(path)
        full=event_sets(d,symbol)
        sym={}
        for bars in [3400,6000,12000]:
            td=d.tail(bars).copy()
            tail=event_sets(td,symbol)
            cutoff=max(td.index[0]+pd.Timedelta(days=90), END-pd.Timedelta(days=30))
            ok,report=compare(full,tail,int(cutoff.value//1_000_000))
            sym[str(bars)]={"pass":ok,"cutoff":str(cutoff),"report":report}
        result[symbol]=sym
    print(json.dumps(result,indent=2))
    failures=[s for s in result if not result[s]["12000"]["pass"]]
    if failures:
        raise SystemExit("12000-bar warmup parity FAIL: "+repr(failures))
    print("FINAL14 12000-bar warmup parity PASS for last 30d after >=90d stabilization")


if __name__=="__main__":main()
