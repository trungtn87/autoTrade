from __future__ import annotations
import argparse, json, sys

from app.final14_config import FINAL14_CASES
from app.final14_research import layer3_long as prod_l3
from app.final14_research.two_trail_layer12 import approved as prod_approved


def prod_events(path,symbol):
    d=prod_l3.load(path); pc=prod_l3.precompute(d); av=prod_l3.entry_variants(pc)
    lock={}; selected={}
    for combo,cfg in FINAL14_CASES[symbol].items():
        cname="TIER" if combo==11 else f"C{combo}"
        lock[cname]={"layer2_variant":cfg["layer2"]}
        m=[x for x in av[cname] if x[0]==cfg["entry_variant"]]
        assert len(m)==1,(symbol,cname,len(m))
        selected[cname]=m
    ctx=prod_l3.build_context(d,lock,selected)
    out={}
    for combo,cfg in FINAL14_CASES[symbol].items():
        cname="TIER" if combo==11 else f"C{combo}"
        out[combo]={
            (int(d.index[pos].value//1_000_000),side,int(sd),bool(blocked))
            for pos,side,ot,sd,blocked in ctx[cname][cfg["entry_variant"]]
            if prod_approved(side,sd,blocked,cfg["layer2"])
        }
    return out


def ref_events(path,symbol,research_dir):
    if research_dir not in sys.path:
        sys.path.insert(0,research_dir)
    import layer3_long as ref_l3
    from two_trail_layer12 import approved as ref_approved
    d=ref_l3.load(path); pc=ref_l3.precompute(d); av=ref_l3.entry_variants(pc)
    lock={}; selected={}
    for combo,cfg in FINAL14_CASES[symbol].items():
        cname="TIER" if combo==11 else f"C{combo}"
        lock[cname]={"layer2_variant":cfg["layer2"]}
        m=[x for x in av[cname] if x[0]==cfg["entry_variant"]]
        assert len(m)==1,(symbol,cname,len(m))
        selected[cname]=m
    ctx=ref_l3.build_context(d,lock,selected)
    out={}
    for combo,cfg in FINAL14_CASES[symbol].items():
        cname="TIER" if combo==11 else f"C{combo}"
        out[combo]={
            (int(d.index[pos].value//1_000_000),side,int(sd),bool(blocked))
            for pos,side,ot,sd,blocked in ctx[cname][cfg["entry_variant"]]
            if ref_approved(side,sd,blocked,cfg["layer2"])
        }
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True);ap.add_argument("--eth",required=True)
    ap.add_argument("--research-dir",required=True)
    args=ap.parse_args()
    report={};fail=[]
    for symbol,path in [("BTC-USDT",args.btc),("ETH-USDT",args.eth)]:
        p=prod_events(path,symbol);r=ref_events(path,symbol,args.research_dir)
        sr={}
        for combo in sorted(p):
            miss=sorted(r[combo]-p[combo]);extra=sorted(p[combo]-r[combo])
            sr[str(combo)]={"production":len(p[combo]),"research":len(r[combo]),
                            "missing":len(miss),"extra":len(extra),
                            "missing_sample":miss[:3],"extra_sample":extra[:3]}
            if miss or extra: fail.append((symbol,combo,len(miss),len(extra)))
        report[symbol]=sr
    print(json.dumps(report,indent=2))
    if fail: raise SystemExit("FINAL14 exact source parity FAIL: "+repr(fail))
    print("FINAL14 exact source parity PASS: 14/14 event sets identical")


if __name__=="__main__":main()
