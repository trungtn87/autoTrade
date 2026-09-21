from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from app.final14_config import FINAL14_CASES, enabled_combos
from app.final14_strategy import (
    FIFTEEN_MIN_COMBOS,
    ONE_HOUR_COMBOS,
    _combo15_events,
    _combo60_events,
    _layer2_approved,
    _tier_events,
    order_block_context,
    smc_direction,
)
from app.timeframes import aggregate_15m


def production_events(symbol: str, raw: pd.DataFrame) -> dict[int, set[tuple[int,str]]]:
    m15=raw.reset_index(drop=True).copy()
    h1=aggregate_15m(m15,60)
    h4=aggregate_15m(m15,240)
    h6=aggregate_15m(m15,360)

    s15=smc_direction(m15,50,False)
    s1=smc_direction(h1,50,False)
    o15=order_block_context(m15)
    o1=order_block_context(h1)

    e15=_combo15_events(symbol,m15,h4)
    e1=_combo60_events(symbol,h1,h4,h6)
    tier=_tier_events(symbol,h1,h4)
    if tier is not None:
        e1[11]=tier

    out={}
    for combo,cfg in FINAL14_CASES[symbol].items():
        native15=combo in FIFTEEN_MIN_COMBOS
        frame=m15 if native15 else h1
        sd=s15 if native15 else s1
        ob=o15 if native15 else o1
        L,S=(e15 if native15 else e1)[combo]
        ev=set()
        for side,ser,direction in (("L",L,1),("S",S,-1)):
            idxs=ser.index[ser.fillna(False)]
            for i in idxs:
                blocked=bool(ob.loc[i,"buy_blocked" if side=="L" else "sell_blocked"])
                if not _layer2_approved(cfg["layer2"],int(sd.loc[i]),direction,blocked):
                    continue
                ot=int(frame.loc[i,"open_time"])
                et=ot if native15 else ot+45*60_000
                ev.add((et,side))
        out[combo]=ev
    return out


def research_events(symbol: str, pkl: str, research_dir: str):
    # Research modules use top-level names; add them only after production app
    # modules have already been imported.
    sys.path.insert(0,research_dir)
    import layer3_long as r
    from two_trail_layer12 import approved

    d=r.load(pkl)
    pc=r.precompute(d)
    variants=r.entry_variants(pc)
    lock={
        ("C"+str(k) if k!=11 else "TIER"):{"layer2_variant":v["layer2"]}
        for k,v in FINAL14_CASES[symbol].items()
    }
    ctx=r.build_context(d,lock,variants)
    out={}
    for combo,cfg in FINAL14_CASES[symbol].items():
        cname="TIER" if combo==11 else f"C{combo}"
        variant=cfg["entry_variant"]
        assert variant in ctx[cname], (symbol,cname,variant)
        ev=set()
        for pos,side,ot,sd,blocked in ctx[cname][variant]:
            if approved(side,sd,blocked,cfg["layer2"]):
                et=int(d.index[pos].value//1_000_000)
                ev.add((et,side))
        out[combo]=ev
    return out


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True)
    ap.add_argument("--eth",required=True)
    ap.add_argument("--research-dir",required=True)
    args=ap.parse_args()

    failures=[]
    report={}
    for symbol,pkl in (("BTC-USDT",args.btc),("ETH-USDT",args.eth)):
        raw=pd.read_pickle(pkl)
        if "open_time" not in raw.columns:
            raise RuntimeError("hybrid pkl missing open_time")
        prod=production_events(symbol,raw)
        ref=research_events(
            "BTCUSDT" if symbol=="BTC-USDT" else "ETHUSDT",
            pkl,args.research_dir,
        )
        # research config uses symbol without hyphen; rebuild lookup by combo only
        # ref keys already combo ints.
        sr={}
        for combo in enabled_combos(symbol):
            p=prod[combo]
            q=ref[combo]
            missing=sorted(q-p)
            extra=sorted(p-q)
            sr[str(combo)]={
                "production":len(p),
                "research":len(q),
                "missing":len(missing),
                "extra":len(extra),
                "missing_sample":missing[:5],
                "extra_sample":extra[:5],
            }
            if missing or extra:
                failures.append((symbol,combo,len(missing),len(extra)))
        report[symbol]=sr

    print(json.dumps(report,indent=2))
    if failures:
        raise SystemExit("FINAL14 parity FAIL: "+repr(failures))
    print("FINAL14 parity PASS: all 14 case event sets match research backtest")


if __name__=="__main__":
    main()
