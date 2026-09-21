from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from layer3_long import load, precompute, entry_variants, build_context, simulate as simulate_case
from two_trail_layer12 import WINDOWS, END, run_one_trade, approved

INITIAL=1000.0
NOTIONAL=100.0
FEE=0.0005

def load_candidates(path):
    return json.loads(Path(path).read_text())

def validate_case(d, events, case):
    rows=[]
    cfg={
      "sl_pct":case["sl_pct"],
      "t1_callback_pct":case["t1_callback_pct"],
      "t2_activation_pct":case["t2_activation_pct"],
      "t2_callback_pct":case["t2_callback_pct"],
      "protect_pct":case["protect_pct"],
    }
    for wn,start in WINDOWS.items():
        r=simulate_case(d,events,start,cfg,case["layer2_variant"])
        rows.append({"case_id":case["id"],"window":wn,**r})
    return rows

def prepare_symbol(path,cases):
    d=load(path); pc=precompute(d); vars=entry_variants(pc)
    fake_lock={c["combo"]:{"layer2_variant":c["layer2_variant"]} for c in cases}
    evall=build_context(d,fake_lock,vars)
    out={}
    for c in cases:
        v=c["entry_variant"]
        if v not in evall[c["combo"]]:
            raise KeyError(f"{c['id']} variant {v} not found")
        out[c["id"]]=evall[c["combo"]][v]
    return d,out

def portfolio_sim(btc,eth,events,cases,start):
    db=btc; de=eth
    # same canonical 15m timestamps are expected
    idx=db.index.intersection(de.index)
    a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    idx=idx[a:b]
    pos_b={t:i for i,t in enumerate(db.index)}
    pos_e={t:i for i,t in enumerate(de.index)}

    case_by_id={c["id"]:c for c in cases}
    event_map={}
    for c in cases:
        src=events[c["id"]]
        for ei,side,ot,sd,blocked in src:
            d=db if c["symbol"]=="BTCUSDT" else de
            t=d.index[ei]
            if t<start or t>=END: continue
            event_map.setdefault(t,[]).append((c["id"],side,sd,blocked))

    active={}
    balance=INITIAL
    realized=0.0
    peak=INITIAL
    maxdd=0.0
    max_open=0
    max_gross=0.0
    skipped=0
    trades=[]
    daily={}
    equity_curve=[]

    # state per active position uses manual conservative 2-tier update
    for t in idx:
        # process exits first
        for cid,st in list(active.items()):
            c=case_by_id[cid]
            d=db if c["symbol"]=="BTCUSDT" else de
            i=pos_b[t] if c["symbol"]=="BTCUSDT" else pos_e[t]
            rowH=float(d.high.iloc[i]); rowL=float(d.low.iloc[i])
            entry=st["entry"]; side=st["side"]
            base_stop=entry*(1-c["sl_pct"]/100) if side=="L" else entry*(1+c["sl_pct"]/100)
            protect_stop=(entry*(1+c["protect_pct"]/100) if side=="L" else entry*(1-c["protect_pct"]/100)) if c["protect_pct"]>0 else base_stop
            pstop=protect_stop if st["protection_active"] else base_stop
            gross=0.0; exitfees=0.0; half_qty=(NOTIONAL/2)/entry
            # T1
            if st["live1"]:
                stop1=max(pstop,st["trail1"]) if side=="L" and st["active1"] and np.isfinite(st["trail1"]) else (min(pstop,st["trail1"]) if side=="S" and st["active1"] and np.isfinite(st["trail1"]) else pstop)
                hit1=rowL<=stop1 if side=="L" else rowH>=stop1
                if hit1:
                    g=(stop1-entry)*half_qty*(1 if side=="L" else -1)
                    gross+=g; exitfees+=stop1*half_qty*FEE; st["live1"]=False
            # T2
            if st["live2"]:
                stop2=max(pstop,st["trail2"]) if side=="L" and st["active2"] and np.isfinite(st["trail2"]) else (min(pstop,st["trail2"]) if side=="S" and st["active2"] and np.isfinite(st["trail2"]) else pstop)
                hit2=rowL<=stop2 if side=="L" else rowH>=stop2
                if hit2:
                    g=(stop2-entry)*half_qty*(1 if side=="L" else -1)
                    gross+=g; exitfees+=stop2*half_qty*FEE; st["live2"]=False
            if gross or exitfees:
                balance += gross-exitfees
                st["pnl_acc"] += gross-exitfees
                st["fees_acc"] += exitfees
            if not st["live1"] and not st["live2"]:
                pnl=st["pnl_acc"]
                realized+=pnl
                trades.append({"case_id":cid,"symbol":c["symbol"],"combo":c["combo"],"side":side,"entry_time":st["entry_time"],"exit_time":t,"pnl":pnl,"fees":st["fees_acc"]})
                day=t.floor("D")
                daily[day]=daily.get(day,0.0)+pnl
                del active[cid]
                continue

            # updates effective next candle
            fav=rowH if side=="L" else rowL
            act1=entry*(1+0.005) if side=="L" else entry*(1-0.005)
            act2=entry*(1+c["t2_activation_pct"]/100) if side=="L" else entry*(1-c["t2_activation_pct"]/100)
            if st["live1"]:
                if not st["active1"] and ((side=="L" and fav>=act1) or (side=="S" and fav<=act1)):
                    st["active1"]=True; st["extreme1"]=fav
                    st["trail1"]=fav*(1-c["t1_callback_pct"]/100) if side=="L" else fav*(1+c["t1_callback_pct"]/100)
                    if c["protect_pct"]>0: st["protection_active"]=True
                elif st["active1"]:
                    if side=="L":
                        st["extreme1"]=max(st["extreme1"],fav); st["trail1"]=max(st["trail1"],st["extreme1"]*(1-c["t1_callback_pct"]/100))
                    else:
                        st["extreme1"]=min(st["extreme1"],fav); st["trail1"]=min(st["trail1"],st["extreme1"]*(1+c["t1_callback_pct"]/100))
            if st["live2"]:
                if not st["active2"] and ((side=="L" and fav>=act2) or (side=="S" and fav<=act2)):
                    st["active2"]=True; st["extreme2"]=fav
                    st["trail2"]=fav*(1-c["t2_callback_pct"]/100) if side=="L" else fav*(1+c["t2_callback_pct"]/100)
                elif st["active2"]:
                    if side=="L":
                        st["extreme2"]=max(st["extreme2"],fav); st["trail2"]=max(st["trail2"],st["extreme2"]*(1-c["t2_callback_pct"]/100))
                    else:
                        st["extreme2"]=min(st["extreme2"],fav); st["trail2"]=min(st["trail2"],st["extreme2"]*(1+c["t2_callback_pct"]/100))

        # new entries
        for cid,side,sd,blocked in event_map.get(t,[]):
            if cid in active: continue
            c=case_by_id[cid]
            if not approved(side,sd,blocked,c["layer2_variant"]): continue
            if balance < 1.0 + NOTIONAL*FEE:
                skipped+=1; continue
            d=db if c["symbol"]=="BTCUSDT" else de
            i=pos_b[t] if c["symbol"]=="BTCUSDT" else pos_e[t]
            entry=float(d.close.iloc[i]); entry_fee=NOTIONAL*FEE
            balance-=entry_fee
            active[cid]={
                "entry":entry,"side":side,"entry_time":t,
                "live1":True,"live2":True,"active1":False,"active2":False,
                "trail1":np.nan,"trail2":np.nan,"extreme1":np.nan,"extreme2":np.nan,
                "protection_active":False,"pnl_acc":-entry_fee,"fees_acc":entry_fee
            }

        # MTM account equity with all active positions
        unreal=0.0
        for cid,st in active.items():
            c=case_by_id[cid]; d=db if c["symbol"]=="BTCUSDT" else de
            i=pos_b[t] if c["symbol"]=="BTCUSDT" else pos_e[t]
            px=float(d.close.iloc[i]); qty=NOTIONAL/st["entry"]
            frac=(0.5 if st["live1"] else 0)+(0.5 if st["live2"] else 0)
            unreal+=(px-st["entry"])*qty*(1 if st["side"]=="L" else -1)*frac
        eq=balance+unreal
        peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
        max_open=max(max_open,len(active)); max_gross=max(max_gross,len(active)*NOTIONAL)
        equity_curve.append((t,eq,balance,unreal,len(active)))

    tdf=pd.DataFrame(trades)
    posp=float(tdf.loc[tdf.pnl>0,"pnl"].sum()) if len(tdf) else 0.0
    negp=float(-tdf.loc[tdf.pnl<0,"pnl"].sum()) if len(tdf) else 0.0
    return {
        "trades":len(tdf),"net_pnl":float(tdf.pnl.sum()) if len(tdf) else 0.0,
        "profit_factor":posp/negp if negp else (np.inf if posp else np.nan),
        "max_drawdown":maxdd,"ending_balance":balance,
        "max_open_positions":max_open,"max_gross_notional":max_gross,
        "skipped_margin":skipped,"open_positions_end":len(active),
        "worst_day_pnl":min(daily.values()) if daily else 0.0,
        "best_day_pnl":max(daily.values()) if daily else 0.0,
        "trades_df":tdf,
        "equity_df":pd.DataFrame(equity_curve,columns=["time","equity","balance","unrealized","open_positions"])
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True); ap.add_argument("--eth",required=True)
    ap.add_argument("--candidates",default="production_candidates_11.json")
    ap.add_argument("--out-dir",default="results/final11_validation")
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    cfg=load_candidates(args.candidates); cases=cfg["cases"]
    btc_cases=[c for c in cases if c["symbol"]=="BTCUSDT"]; eth_cases=[c for c in cases if c["symbol"]=="ETHUSDT"]
    db,eb=prepare_symbol(args.btc,btc_cases); de,ee=prepare_symbol(args.eth,eth_cases)
    events={**eb,**ee}

    indiv=[]
    for c in cases:
        d=db if c["symbol"]=="BTCUSDT" else de
        rows=validate_case(d,events[c["id"]],c)
        for r in rows:
            indiv.append({"symbol":c["symbol"],"combo":c["combo"],"entry_variant":c["entry_variant"],"layer2_variant":c["layer2_variant"],**r})
    idf=pd.DataFrame(indiv); idf.to_csv(out/"FINAL11_individual_windows.csv",index=False)

    summary=[]
    for cid,g in idf.groupby("case_id"):
        by={r.window:r for r in g.itertuples()}
        vals=[by[w].net_pnl for w in ["6M","1Y","3Y","FULL"]]
        summary.append({
            "case_id":cid,
            "symbol":g.iloc[0].symbol,"combo":g.iloc[0].combo,
            "entry_variant":g.iloc[0].entry_variant,"layer2_variant":g.iloc[0].layer2_variant,
            "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,
            "pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
            "pf_3Y":by["3Y"].profit_factor,"dd_3Y":by["3Y"].max_drawdown,
            "trades_3Y":by["3Y"].trades,
            "PASS_NONNEG_ALL_WINDOWS":all(v>=0 for v in vals)
        })
    sdf=pd.DataFrame(summary).sort_values("pnl_3Y",ascending=False)
    sdf.to_csv(out/"FINAL11_individual_summary.csv",index=False)

    prows=[]
    for wn,start in WINDOWS.items():
        r=portfolio_sim(db,de,events,cases,start)
        r["trades_df"].to_csv(out/f"PORTFOLIO_{wn}_trades.csv",index=False)
        r["equity_df"].to_csv(out/f"PORTFOLIO_{wn}_equity.csv",index=False)
        prows.append({k:v for k,v in r.items() if not k.endswith("_df")} | {"window":wn})
    pdf=pd.DataFrame(prows)
    pdf.to_csv(out/"FINAL11_portfolio_summary.csv",index=False)

    meta={
      "candidate_count":len(cases),
      "selection_locked":True,
      "no_parameter_optimization_in_this_run":True,
      "portfolio_model":"shared $1000 balance, fixed $100 notional per case, 2-tier trailing, same timestamps, MTM drawdown",
      "note":"Cross liquidation itself is not expected with <=11x$100 notional on $1000 under 0.9% hard stops; portfolio test focuses on concurrent exposure and account DD."
    }
    (out/"run_meta.json").write_text(json.dumps(meta,indent=2))
    print("\nINDIVIDUAL FINAL 11\n",sdf.to_string(index=False),flush=True)
    print("\nPORTFOLIO FINAL 11\n",pdf.to_string(index=False),flush=True)

if __name__=="__main__": main()
