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
COMBOS=["C2","C3","C4","C5","C6","C7","C8","C9","C10","TIER"]
NATIVE={"C2":"1h","C3":"1h","C4":"1h","C5":"15m","C6":"1h","C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h"}
ATR_COMBOS={"C3","C6"}

ATR_TP=[0.6,0.8,1.0,1.2,1.4,1.6,1.8,2.0,2.4]
ATR_SL=[0.4,0.6,0.8,1.0,1.2,1.4]
PCT_TP=[0.4,0.6,0.8,1.0,1.1,1.2,1.5,1.8,2.2]
PCT_SL=[0.3,0.4,0.5,0.6,0.7,0.8,0.9]

NOTIONAL=100.0
LEV=100.0
FEE=0.0005

def load(path):
    d=pd.read_pickle(path)
    if not isinstance(d.index,pd.DatetimeIndex):
        if "open_time" not in d:
            raise ValueError(f"{path}: missing open_time")
        d.index=pd.to_datetime(d["open_time"],unit="ms",utc=True)
    elif d.index.tz is None:
        d.index=d.index.tz_localize("UTC")
    else:
        d.index=d.index.tz_convert("UTC")
    return d.sort_index()

def prepare(path):
    d=load(path)
    sig=build_all_signals(d)
    pos={t:i for i,t in enumerate(d.index)}
    events={}
    for combo in COMBOS:
        if combo=="TIER":
            frame=sig["tier"]; prefix="TIER"
        elif NATIVE[combo]=="1h":
            frame=sig["1h"]; prefix=combo
        else:
            frame=sig["15m"]; prefix=combo
        ev=[]
        for side in ["L","S"]:
            col=f"{prefix}_{side}"
            for ot in frame.index[frame[col].fillna(False)]:
                et=ot+pd.Timedelta("45min") if NATIVE[combo]=="1h" else ot
                if et in pos and et<END:
                    atrv=float(frame.loc[ot,"ATR21"]) if "ATR21" in frame.columns else np.nan
                    ev.append((pos[et],side,atrv,ot))
        ev.sort(key=lambda x:x[0])
        events[combo]=ev
    return d,events

def simulate(d,ev,start,family,tpv,slv):
    idx=d.index
    a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in ev if a<=e[0]<b]
    sigL=sum(e[1]=="L" for e in ev); sigS=len(ev)-sigL
    p=0; eq=1000.0; peak=1000.0; maxdd=0.0
    trades=wins=losses=proxies=0; pos_sum=neg_sum=0.0
    gross_sum=fees=net=0.0; long_tr=short_tr=0; long_pnl=short_pnl=0.0; open_end=0

    while p<len(ev):
        ei,side,atrv,_=ev[p]
        entry=C[ei]; entry_fee=NOTIONAL*FEE
        eq-=entry_fee
        maxdd=max(maxdd,peak-eq)

        if family=="ATR":
            tp=entry+(atrv*tpv if side=="L" else -atrv*tpv)
            sl=entry+(-atrv*slv if side=="L" else atrv*slv)
        else:
            tp=entry*(1+(tpv/100.0 if side=="L" else -tpv/100.0))
            sl=entry*(1+(-slv/100.0 if side=="L" else slv/100.0))

        bank=entry*(1-1/LEV) if side=="L" else entry*(1+1/LEV)
        if side=="L":
            adverse=max(sl,bank)
            reason_adv="SL" if sl>=bank else "BANKRUPTCY_PROXY"
            hit=(H[ei+1:b]>=tp)|(L[ei+1:b]<=adverse)
        else:
            adverse=min(sl,bank)
            reason_adv="SL" if sl<=bank else "BANKRUPTCY_PROXY"
            hit=(L[ei+1:b]<=tp)|(H[ei+1:b]>=adverse)

        loc=np.flatnonzero(hit)
        if not len(loc):
            open_end=1
            break
        xi=ei+1+int(loc[0])

        hit_adv=(L[xi]<=adverse) if side=="L" else (H[xi]>=adverse)
        if hit_adv:  # stop_first if both barriers touched
            px=adverse; reason=reason_adv
        else:
            px=tp; reason="TP"

        qty=NOTIONAL/entry
        gross=(px-entry)*qty*(1 if side=="L" else -1)
        exit_fee=px*qty*FEE
        pnl=gross-entry_fee-exit_fee

        eq+=gross-exit_fee
        peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
        trades+=1; gross_sum+=gross; fees+=entry_fee+exit_fee; net+=pnl
        if pnl>0:
            wins+=1; pos_sum+=pnl
        elif pnl<0:
            losses+=1; neg_sum+=-pnl
        if reason=="BANKRUPTCY_PROXY": proxies+=1
        if side=="L":
            long_tr+=1; long_pnl+=pnl
        else:
            short_tr+=1; short_pnl+=pnl

        p+=1
        while p<len(ev) and ev[p][0]<xi:
            p+=1

    return {
        "signals_long":sigL,"signals_short":sigS,"signals_total":len(ev),
        "trades":trades,"long_trades":long_tr,"short_trades":short_tr,
        "wins":wins,"losses":losses,
        "win_rate":wins/trades*100 if trades else np.nan,
        "profit_factor":pos_sum/neg_sum if neg_sum else (np.inf if pos_sum else np.nan),
        "net_pnl":net,"gross_pnl":gross_sum,"fees":fees,
        "long_pnl":long_pnl,"short_pnl":short_pnl,
        "max_drawdown":maxdd,"ending_equity":eq,
        "bankruptcy_proxy":proxies,"open_positions_end":open_end,
    }

def grids(combo):
    if combo in ATR_COMBOS:
        return "ATR",[(tp,sl) for tp in ATR_TP for sl in ATR_SL]
    return "PCT",[(tp,sl) for tp in PCT_TP for sl in PCT_SL]

def sweep_symbol(symbol,path,out):
    d,events=prepare(path)
    all_broad=[]; all_rank=[]
    for combo in COMBOS:
        family,cfgs=grids(combo)
        rows=[]
        for tp,sl in cfgs:
            for wn,start in WINDOWS.items():
                r=simulate(d,events[combo],start,family,tp,sl)
                rows.append({"symbol":symbol,"combo":combo,"family":family,"tp":tp,"sl":sl,"window":wn,**r})
        broad=pd.DataFrame(rows)
        broad.to_csv(out/f"{symbol}_{combo}_L1_broad.csv",index=False)
        all_broad.append(broad)

        agg=[]
        for (tp,sl),g in broad.groupby(["tp","sl"]):
            by={r.window:r for r in g.itertuples()}
            pnls=[by[w].net_pnl for w in WINDOWS]
            pfs=[by[w].profit_factor for w in WINDOWS]
            agg.append({
                "symbol":symbol,"combo":combo,"family":family,"tp":tp,"sl":sl,
                "positive_windows":sum(v>0 for v in pnls),
                "min_pnl":min(pnls),"sum_pnl":sum(pnls),
                "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,
                "pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
                "pf_6M":by["6M"].profit_factor,"pf_1Y":by["1Y"].profit_factor,
                "pf_3Y":by["3Y"].profit_factor,"pf_FULL":by["FULL"].profit_factor,
                "min_pf":np.nanmin(pfs),
                "dd_max":max(by[w].max_drawdown for w in WINDOWS),
                "full_trades":by["FULL"].trades,
                "full_proxy":by["FULL"].bankruptcy_proxy,
                "full_wr":by["FULL"].win_rate,
            })
        rank=pd.DataFrame(agg).sort_values(
            ["positive_windows","min_pnl","min_pf","pnl_FULL"],
            ascending=[False,False,False,False]
        )
        rank.to_csv(out/f"{symbol}_{combo}_L1_ranked.csv",index=False)
        all_rank.append(rank)
        print(f"\n{symbol} {combo} {family} TOP 10",flush=True)
        print(rank.head(10).to_string(index=False),flush=True)

    pd.concat(all_broad,ignore_index=True).to_csv(out/f"{symbol}_L1_ALL_broad.csv",index=False)
    pd.concat(all_rank,ignore_index=True).to_csv(out/f"{symbol}_L1_ALL_ranked.csv",index=False)
    return pd.concat(all_rank,ignore_index=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True)
    ap.add_argument("--eth",required=True)
    ap.add_argument("--out-dir",default="results/layer1_remaining_long")
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    ranks=[]
    ranks.append(sweep_symbol("BTCUSDT",args.btc,out))
    ranks.append(sweep_symbol("ETHUSDT",args.eth,out))
    pd.concat(ranks,ignore_index=True).to_csv(out/"L1_REMAINING_ranked_all.csv",index=False)
    meta={
        "dataset_range":"2021-01-01 through 2026-09-20 23:45 UTC",
        "windows":{k:str(v) for k,v in WINDOWS.items()},
        "end_exclusive":str(END),
        "combos":COMBOS,
        "original_exit_family":{"ATR":["C3","C6"],"PCT":["C2","C4","C5","C7","C8","C9","C10","TIER"]},
        "atr_grid":{"tp":ATR_TP,"sl":ATR_SL},
        "pct_grid_percent":{"tp":PCT_TP,"sl":PCT_SL},
        "execution":{"margin_mode":"isolated","margin_per_trade_usd":1.0,"leverage":100,
                     "notional_per_trade_usd":100,"commission_pct_per_side":0.05,
                     "policy":"stop_first","no_exit_on_entry_candle":True}
    }
    (out/"L1_REMAINING_meta.json").write_text(json.dumps(meta,indent=2))

if __name__=="__main__":
    main()
