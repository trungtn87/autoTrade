from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from signals import build_all_signals

END=pd.Timestamp("2026-09-21T00:00:00Z")
WINDOWS={
    "6M":pd.Timestamp("2026-03-21T00:00:00Z"),
    "1Y":pd.Timestamp("2025-09-21T00:00:00Z"),
    "3Y":pd.Timestamp("2023-09-21T00:00:00Z"),
    "FULL":pd.Timestamp("2021-01-01T00:00:00Z"),
}
COMBOS=["C1","C2","C3","C4","C5","C6","C7","C8","C9","C10","TIER"]
NATIVE={"C1":"1h","C2":"1h","C3":"1h","C4":"1h","C5":"15m","C6":"1h","C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h"}
ATR_ONLY={"C3","C6"}
C1_FAMILIES=("ATR","PCT")

ATR_TP=[0.6,0.8,1.0,1.2,1.4,1.6,1.8,2.0,2.4,2.8,3.2]
ATR_SL=[0.4,0.6,0.8,1.0,1.2,1.4,1.6,1.8,2.0]
PCT_TP=[0.4,0.6,0.8,1.0,1.1,1.2,1.5,1.8,2.2,2.6,3.0]
PCT_SL=[0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.2,1.5]

INITIAL=1000.0
MARGIN=1.0
LEV=100.0
NOTIONAL=MARGIN*LEV
FEE=0.0005

def load(path):
    d=pd.read_pickle(path)
    if not isinstance(d.index,pd.DatetimeIndex):
        d.index=pd.to_datetime(d["open_time"],unit="ms",utc=True)
    elif d.index.tz is None:
        d.index=d.index.tz_localize("UTC")
    else:
        d.index=d.index.tz_convert("UTC")
    return d.sort_index()

def prepare(path):
    d=load(path); sig=build_all_signals(d); pos={t:i for i,t in enumerate(d.index)}
    out={}
    for combo in COMBOS:
        if combo=="TIER": frame=sig["tier"]; prefix="TIER"
        elif NATIVE[combo]=="1h": frame=sig["1h"]; prefix=combo
        else: frame=sig["15m"]; prefix=combo
        ev=[]
        for side in ("L","S"):
            for ot in frame.index[frame[f"{prefix}_{side}"].fillna(False)]:
                et=ot+pd.Timedelta("45min") if NATIVE[combo]=="1h" else ot
                if et in pos and et<END:
                    atrv=float(frame.loc[ot,"ATR21"]) if "ATR21" in frame else np.nan
                    ev.append((pos[et],side,atrv,ot))
        out[combo]=sorted(ev,key=lambda x:x[0])
    return d,out

def cross_liq_price(balance,entry,qty,side,mmr):
    # BingX cross proxy:
    # balance + unrealized_PnL == maintenance_margin + closing_fee.
    # Maintenance amount is assumed 0 for this tiny $100 notional tier.
    k=mmr+FEE
    notional=entry*qty
    if side=="L":
        num=notional-balance
        den=qty*(1-k)
        if den<=0 or num<=0: return None
        return num/den
    den=qty*(1+k)
    if den<=0:return None
    return (balance+notional)/den

def simulate(d,ev,start,family,tpv,slv,mmr):
    idx=d.index; a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in ev if a<=e[0]<b]
    sigL=sum(e[1]=="L" for e in ev); sigS=len(ev)-sigL
    p=0; balance=INITIAL; peak=INITIAL; maxdd=0.0
    trades=wins=losses=liqs=0; pos_sum=neg_sum=0.0
    net=gross_sum=fees=0.0; open_end=0
    long_tr=short_tr=0; long_pnl=short_pnl=0.0; skipped=0
    while p<len(ev):
        ei,side,atrv,_=ev[p]
        entry=C[ei]; entry_fee=NOTIONAL*FEE
        # Cross still requires initial margin capacity; fixed one-position-per-combo test.
        if balance < MARGIN+entry_fee:
            skipped+=1; p+=1; continue
        balance-=entry_fee
        maxdd=max(maxdd,peak-balance)

        if family=="ATR":
            tp=entry+(atrv*tpv if side=="L" else -atrv*tpv)
            sl=entry+(-atrv*slv if side=="L" else atrv*slv)
        else:
            tp=entry*(1+(tpv/100 if side=="L" else -tpv/100))
            sl=entry*(1+(-slv/100 if side=="L" else slv/100))

        qty=NOTIONAL/entry
        liq=cross_liq_price(balance,entry,qty,side,mmr)

        # The first adverse barrier is the stop or account-level liquidation,
        # whichever is closer to entry. If both touch in one candle, the closer
        # adverse barrier necessarily occurs first on a continuous price path.
        if side=="L":
            barriers=[("SL",sl)]
            if liq is not None and liq>0: barriers.append(("CROSS_LIQ",liq))
            reason_adv,adverse=max(barriers,key=lambda x:x[1])
            hit=(H[ei+1:b]>=tp)|(L[ei+1:b]<=adverse)
        else:
            barriers=[("SL",sl)]
            if liq is not None and liq>0: barriers.append(("CROSS_LIQ",liq))
            reason_adv,adverse=min(barriers,key=lambda x:x[1])
            hit=(L[ei+1:b]<=tp)|(H[ei+1:b]>=adverse)

        loc=np.flatnonzero(hit)
        if not len(loc):
            open_end=1; break
        xi=ei+1+int(loc[0])
        hit_adv=(L[xi]<=adverse) if side=="L" else (H[xi]>=adverse)
        px=adverse if hit_adv else tp
        reason=reason_adv if hit_adv else "TP"

        gross=(px-entry)*qty*(1 if side=="L" else -1)
        exit_fee=px*qty*FEE
        pnl=gross-entry_fee-exit_fee
        balance+=gross-exit_fee
        peak=max(peak,balance); maxdd=max(maxdd,peak-balance)
        trades+=1; net+=pnl; gross_sum+=gross; fees+=entry_fee+exit_fee
        if pnl>0:wins+=1;pos_sum+=pnl
        elif pnl<0:losses+=1;neg_sum+=-pnl
        if reason=="CROSS_LIQ":liqs+=1
        if side=="L": long_tr+=1;long_pnl+=pnl
        else: short_tr+=1;short_pnl+=pnl

        p+=1
        while p<len(ev) and ev[p][0]<xi:p+=1

    return {
        "signals_long":sigL,"signals_short":sigS,"signals_total":len(ev),
        "trades":trades,"long_trades":long_tr,"short_trades":short_tr,
        "wins":wins,"losses":losses,"win_rate":wins/trades*100 if trades else np.nan,
        "profit_factor":pos_sum/neg_sum if neg_sum else (np.inf if pos_sum else np.nan),
        "net_pnl":net,"gross_pnl":gross_sum,"fees":fees,
        "long_pnl":long_pnl,"short_pnl":short_pnl,
        "max_drawdown":maxdd,"ending_equity":balance,
        "cross_liquidations":liqs,"skipped_margin":skipped,"open_positions_end":open_end,
    }

def families(combo):
    if combo=="C1": return ["ATR","PCT"]
    if combo in ATR_ONLY: return ["ATR"]
    return ["PCT"]

def cfgs(family):
    if family=="ATR": return [(t,s) for t in ATR_TP for s in ATR_SL]
    return [(t,s) for t in PCT_TP for s in PCT_SL]

def sweep(symbol,path,out,mmr):
    d,events=prepare(path); ranks=[]
    for combo in COMBOS:
        for fam in families(combo):
            rows=[]
            for tp,sl in cfgs(fam):
                for wn,start in WINDOWS.items():
                    rows.append({"symbol":symbol,"combo":combo,"family":fam,"tp":tp,"sl":sl,
                                 "window":wn,"mmr":mmr,**simulate(d,events[combo],start,fam,tp,sl,mmr)})
            broad=pd.DataFrame(rows)
            broad.to_csv(out/f"{symbol}_{combo}_{fam}_cross_broad.csv",index=False)
            agg=[]
            for (tp,sl),g in broad.groupby(["tp","sl"]):
                by={r.window:r for r in g.itertuples()}
                pn=[by[w].net_pnl for w in WINDOWS]; pf=[by[w].profit_factor for w in WINDOWS]
                agg.append({
                    "symbol":symbol,"combo":combo,"family":fam,"tp":tp,"sl":sl,"mmr":mmr,
                    "positive_windows":sum(v>0 for v in pn),"min_pnl":min(pn),"sum_pnl":sum(pn),
                    "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,
                    "pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
                    "pf_6M":by["6M"].profit_factor,"pf_1Y":by["1Y"].profit_factor,
                    "pf_3Y":by["3Y"].profit_factor,"pf_FULL":by["FULL"].profit_factor,
                    "min_pf":np.nanmin(pf),"dd_max":max(by[w].max_drawdown for w in WINDOWS),
                    "full_trades":by["FULL"].trades,"full_cross_liq":by["FULL"].cross_liquidations,
                    "full_wr":by["FULL"].win_rate,
                })
            rank=pd.DataFrame(agg).sort_values(
                ["positive_windows","min_pnl","min_pf","pnl_FULL"],
                ascending=[False,False,False,False])
            rank.to_csv(out/f"{symbol}_{combo}_{fam}_cross_ranked.csv",index=False)
            ranks.append(rank)
            print(f"\n{symbol} {combo} {fam} CROSS TOP 5",flush=True)
            print(rank.head(5).to_string(index=False),flush=True)
    return pd.concat(ranks,ignore_index=True)

def stress_top(d,events,rank,combo,fam):
    top=rank[(rank.combo==combo)&(rank.family==fam)].iloc[0]
    rows=[]
    for mmr in [0.004,0.005,0.01]:
        for wn,start in WINDOWS.items():
            rows.append({"combo":combo,"family":fam,"tp":top.tp,"sl":top.sl,"mmr":mmr,"window":wn,
                         **simulate(d,events[combo],start,fam,float(top.tp),float(top.sl),mmr)})
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True);ap.add_argument("--eth",required=True)
    ap.add_argument("--out-dir",default="results/layer1_cross_long")
    ap.add_argument("--mmr",type=float,default=0.004)
    args=ap.parse_args()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    allrank=[]; stress=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        rank=sweep(sym,path,out,args.mmr);allrank.append(rank)
        d,events=prepare(path)
        for combo in COMBOS:
            for fam in families(combo):
                rr=rank[(rank.combo==combo)&(rank.family==fam)]
                if len(rr):
                    for x in stress_top(d,events,rr,combo,fam):
                        stress.append({"symbol":sym,**x})
    pd.concat(allrank,ignore_index=True).to_csv(out/"L1_CROSS_ranked_all.csv",index=False)
    pd.DataFrame(stress).to_csv(out/"L1_CROSS_top_mmr_stress.csv",index=False)
    meta={
      "model":"BingX cross account risk proxy",
      "formula":"balance + unrealized_pnl <= maintenance_margin + closing_fee",
      "maintenance_margin":"mark_notional * MMR; maintenance amount assumed 0 for $100 notional first tier",
      "base_mmr":args.mmr,"stress_mmr":[0.004,0.005,0.01],
      "initial_equity":INITIAL,"margin_per_trade":MARGIN,"leverage":LEV,"notional":NOTIONAL,
      "fee_per_side_pct":FEE*100,
      "note":"Funding and mark-vs-candle-price basis are not modeled."
    }
    (out/"L1_CROSS_meta.json").write_text(json.dumps(meta,indent=2))

if __name__=="__main__":main()
