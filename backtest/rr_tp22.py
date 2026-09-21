from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

from layer3_long import load, precompute, entry_variants, build_context
from two_trail_layer12 import WINDOWS, END, approved

INITIAL=1000.0
NOTIONAL=100.0
FEE=0.0005

TP_GRID=[0.5,0.8,1.0,1.2,1.5,1.8,2.0]
RR_GRID=[1.0,1.25,1.5,2.0,2.5,3.0]
MAX_SL=0.9

CASES={
 "BTCUSDT":{
  "C1":("MFI_L50_S50","SMC_STRICT"),
  "C2":("ADX_20","SMC_VETO_OB"),
  "C3":("ADX_26","SMC_VETO"),
  "C4":("ADX_23","SMC_VETO"),
  "C5":("VOL_1.5","SMC_STRICT"),
  "C6":("BODY_1.4","SMC_VETO_OB"),
  "C7":("BODY1.6_VOL2.0","OFF"),
  "C8":("ADX_28","SMC_STRICT"),
  "C9":("RSI45_FIXST","SMC_VETO"),
  "C10":("ADX_22","SMC_VETO"),
  "TIER":("ADX30_T27_T35_V1.3","OFF"),
 },
 "ETHUSDT":{
  "C1":("MFI_L50_S50","SMC_VETO"),
  "C2":("ADX_20","SMC_VETO_OB"),
  "C3":("ADX_34","SMC_STRICT"),
  "C4":("ADX_23","SMC_VETO"),
  "C5":("VOL_2.2","SMC_STRICT"),
  "C6":("BODY_1.8","SMC_VETO"),
  "C7":("BODY1.3_VOL1.5","OB"),
  "C8":("ADX_25","SMC_STRICT"),
  "C9":("RSI40_ORIGST","OB"),
  "C10":("ADX_28","SMC_STRICT"),
  "TIER":("ADX25_T27_T37_V1.8","SMC_STRICT_OB"),
 }
}

def hard_trade(H,L,C,entry_i,end_i,side,tp_pct,sl_pct):
    entry=float(C[entry_i])
    tp=entry*(1+tp_pct/100) if side=="L" else entry*(1-tp_pct/100)
    sl=entry*(1-sl_pct/100) if side=="L" else entry*(1+sl_pct/100)
    qty=NOTIONAL/entry
    for i in range(entry_i+1,end_i):
        hit_sl=(L[i]<=sl) if side=="L" else (H[i]>=sl)
        hit_tp=(H[i]>=tp) if side=="L" else (L[i]<=tp)
        if hit_sl or hit_tp:
            px=sl if hit_sl else tp
            gross=(px-entry)*qty*(1 if side=="L" else -1)
            fees=NOTIONAL*FEE + px*qty*FEE
            return i,gross-fees,fees,"SL" if hit_sl else "TP"
    i=end_i-1
    px=float(C[i])
    gross=(px-entry)*qty*(1 if side=="L" else -1)
    fees=NOTIONAL*FEE + px*qty*FEE
    return i,gross-fees,fees,"END_MTM"

def simulate(d,events,start,l2,tp_pct,sl_pct):
    idx=d.index
    a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in events if a<=e[0]<b]
    p=0; net=fees=pos=neg=0.0; trades=wins=losses=veto=0
    eq=INITIAL; peak=INITIAL; maxdd=0.0
    reasons={"TP":0,"SL":0,"END_MTM":0}
    while p<len(ev):
        ei,side,ot,sd,blocked=ev[p]
        if not approved(side,sd,blocked,l2):
            veto+=1; p+=1; continue
        xi,pnl,tf,reason=hard_trade(H,L,C,ei,b,side,tp_pct,sl_pct)
        trades+=1; net+=pnl; fees+=tf; eq+=pnl
        peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
        reasons[reason]=reasons.get(reason,0)+1
        if pnl>0: wins+=1; pos+=pnl
        elif pnl<0: losses+=1; neg+=-pnl
        p+=1
        while p<len(ev) and ev[p][0]<xi:
            p+=1
    return {
      "trades":trades,"wins":wins,"losses":losses,
      "win_rate":wins/trades*100 if trades else np.nan,
      "profit_factor":pos/neg if neg else (np.inf if pos else np.nan),
      "net_pnl":net,"fees":fees,"max_drawdown":maxdd,
      "ending_equity":INITIAL+net,"vetoes":veto,
      "tp_exits":reasons["TP"],"sl_exits":reasons["SL"],"end_mtm":reasons["END_MTM"]
    }

def configs():
    seen=set()
    for tp in TP_GRID:
        for rr in RR_GRID:
            sl=tp/rr
            if sl > MAX_SL + 1e-12:
                continue
            # Round only for stable grouping; simulation uses the rounded value.
            sl=round(sl,6)
            key=(tp,rr,sl)
            if key in seen: continue
            seen.add(key)
            yield tp,rr,sl

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True)
    ap.add_argument("--eth",required=True)
    ap.add_argument("--out-dir",default="results/rr_tp22")
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)

    all_rows=[]; winners=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        d=load(path); pc=precompute(d); variants=entry_variants(pc)
        lock={c:{"layer2_variant":CASES[sym][c][1]} for c in CASES[sym]}
        ctx=build_context(d,lock,variants)

        for combo,(entry_variant,l2) in CASES[sym].items():
            if entry_variant not in ctx[combo]:
                raise KeyError(f"{sym} {combo} missing {entry_variant}")
            ev=ctx[combo][entry_variant]
            rows=[]
            for tp,rr,sl in configs():
                rec={
                    "symbol":sym,"combo":combo,
                    "entry_variant":entry_variant,"layer2_variant":l2,
                    "tp_pct":tp,"rr":rr,"sl_pct":sl,
                }
                for wn,start in WINDOWS.items():
                    r=simulate(d,ev,start,l2,tp,sl)
                    for k,v in r.items():
                        rec[f"{k}_{wn}"]=v
                rec["PASS_NONNEG_ALL"]=all(
                    rec[f"net_pnl_{w}"]>=0 for w in ["6M","1Y","3Y","FULL"]
                )
                rows.append(rec); all_rows.append(rec)

            rdf=pd.DataFrame(rows)
            valid=rdf[rdf.PASS_NONNEG_ALL].copy()
            if len(valid):
                valid=valid.sort_values(
                    ["net_pnl_3Y","profit_factor_3Y","max_drawdown_3Y","net_pnl_1Y","net_pnl_6M"],
                    ascending=[False,False,True,False,False]
                )
                win=valid.iloc[0].to_dict(); win["status"]="PASS"
            else:
                rdf=rdf.sort_values(
                    ["net_pnl_3Y","profit_factor_3Y","max_drawdown_3Y"],
                    ascending=[False,False,True]
                )
                win=rdf.iloc[0].to_dict(); win["status"]="FAIL_NO_ALL_WINDOW_POSITIVE"
            winners.append(win)
            rdf.to_csv(out/f"{sym}_{combo}_RR_GRID.csv",index=False)

    pd.DataFrame(all_rows).to_csv(out/"RR_TP22_ALL_CONFIGS.csv",index=False)
    wdf=pd.DataFrame(winners).sort_values(["status","net_pnl_3Y"],ascending=[False,False])
    wdf.to_csv(out/"RR_TP22_WINNERS.csv",index=False)

    print("\nRR TP<=2% WINNERS\n",wdf[[
      "symbol","combo","status","entry_variant","layer2_variant",
      "tp_pct","sl_pct","rr",
      "net_pnl_6M","net_pnl_1Y","net_pnl_3Y","net_pnl_FULL",
      "profit_factor_3Y","max_drawdown_3Y","trades_3Y","win_rate_3Y"
    ]].to_string(index=False),flush=True)

    meta={
      "dataset":"hybrid 15m 2021-01-01 through 2026-09-20",
      "cases":22,
      "exit":"100% hard TP + 100% hard SL; no trailing; no partial exit",
      "tp_grid_pct":TP_GRID,
      "rr_grid_reward_over_risk":RR_GRID,
      "sl_formula":"SL_pct = TP_pct / RR",
      "max_sl_pct":MAX_SL,
      "entry_layer2":"best currently selected entry variant + Layer2 per case",
      "same_bar":"SL first if TP and SL both touched",
      "entry_candle":"no exit on entry candle",
      "window_end":"mark-to-market on final candle",
      "fee_per_side_pct":0.05,
      "notional_per_trade":100,
      "initial_equity":1000,
      "selection":"require PnL >=0 in 6M,1Y,3Y,FULL; maximize 3Y PnL then PF3Y then lower DD3Y"
    }
    (out/"run_meta.json").write_text(json.dumps(meta,indent=2))

if __name__=="__main__":
    main()
