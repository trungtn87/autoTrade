from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np
import pandas as pd

from signals import build_all_signals
from smc_structure import smc_direction
from smc_ob import ob_context, OBConfig

END=pd.Timestamp("2026-09-21T00:00:00Z")
WINDOWS={
    "6M":pd.Timestamp("2026-03-21T00:00:00Z"),
    "1Y":pd.Timestamp("2025-09-21T00:00:00Z"),
    "3Y":pd.Timestamp("2023-09-21T00:00:00Z"),
    "FULL":pd.Timestamp("2021-01-01T00:00:00Z"),
}
COMBOS=["C1","C2","C3","C4","C5","C6","C7","C8","C9","C10","TIER"]
NATIVE={"C1":"1h","C2":"1h","C3":"1h","C4":"1h","C5":"15m","C6":"1h",
        "C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h"}

# Two-tier trailing: no fixed TP.
# T1 activation is locked to the user's current +0.5% concept.
T1_ACT=0.005
SL_GRID=[0.005,0.007,0.009]                # 0.5 / 0.7 / 0.9%
T1_CB_GRID=[0.003,0.005]                   # 0.3 / 0.5%
T2_ACT_GRID=[0.008,0.010,0.012]            # 0.8 / 1.0 / 1.2%
T2_CB_GRID=[0.005,0.008]                   # 0.5 / 0.8%
PROTECT_GRID=[0.0,0.002]                   # none / remaining 50% to +0.2% once T1 activates

INITIAL=1000.0
NOTIONAL=100.0
FEE=0.0005                                # 0.05% per side
HALF=NOTIONAL/2.0

L2_VARIANTS=[
    "OFF",
    "OB",
    "SMC_VETO",
    "SMC_STRICT",
    "SMC_VETO_OB",
    "SMC_STRICT_OB",
]

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
    d=load(path)
    sig=build_all_signals(d)
    d1=sig["ohlcv1h"]
    smc15=smc_direction(d,50,False)
    smc1=smc_direction(d1,50,False)
    cfg=OBConfig(pivot_len=5,search_bars=12,max_age=80,danger_atr=0.5)
    ob15=ob_context(d,cfg)
    ob1=ob_context(d1,cfg)
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
        for side in ("L","S"):
            col=f"{prefix}_{side}"
            for ot in frame.index[frame[col].fillna(False)]:
                et=ot+pd.Timedelta("45min") if NATIVE[combo]=="1h" else ot
                if et not in pos or et>=END: continue
                if NATIVE[combo]=="1h":
                    sd=int(smc1.loc[ot]) if ot in smc1.index else 0
                    blocked=bool(ob1.loc[ot,"buy_blocked" if side=="L" else "sell_blocked"]) if ot in ob1.index else False
                else:
                    sd=int(smc15.loc[ot]) if ot in smc15.index else 0
                    blocked=bool(ob15.loc[ot,"buy_blocked" if side=="L" else "sell_blocked"]) if ot in ob15.index else False
                ev.append((pos[et],side,ot,sd,blocked))
        events[combo]=sorted(ev,key=lambda x:x[0])
    return d,events

def approved(side,smcdir,obblocked,variant):
    edir=1 if side=="L" else -1
    if "STRICT" in variant:
        smc_ok=(smcdir==edir)
    elif "VETO" in variant:
        smc_ok=(smcdir!=-edir)
    else:
        smc_ok=True
    ob_ok=(not obblocked) if ("OB" in variant) else True
    return smc_ok and ob_ok

def _half_exit_gross(entry,px,side):
    qty=HALF/entry
    return (px-entry)*qty*(1 if side=="L" else -1)

def run_one_trade(H,L,C,entry_i,end_i,side,sl_pct,t1_cb,t2_act,t2_cb,protect_pct):
    """Conservative 15m trailing semantics.
    - No exit/activation on entry candle.
    - Existing stop/trail is checked first on each later candle.
    - New activation/extreme updates from the current candle become effective next candle.
    - Each half is 50% notional.
    Returns (exit_index, pnl, fees, reasons tuple, success), or success=False if still open.
    """
    entry=float(C[entry_i])
    if side=="L":
        base_stop=entry*(1-sl_pct)
        act1=entry*(1+T1_ACT); act2=entry*(1+t2_act)
        protect_stop=entry*(1+protect_pct) if protect_pct>0 else base_stop
    else:
        base_stop=entry*(1+sl_pct)
        act1=entry*(1-T1_ACT); act2=entry*(1-t2_act)
        protect_stop=entry*(1-protect_pct) if protect_pct>0 else base_stop

    live1=True; live2=True
    active1=False; active2=False
    trail1=np.nan; trail2=np.nan
    extreme1=np.nan; extreme2=np.nan
    protection_active=False
    gross=0.0
    exit_fees=0.0
    reasons=[]

    for i in range(entry_i+1,end_i):
        # Stops effective at candle OPEN are based only on prior candles.
        pstop=protect_stop if protection_active else base_stop
        if live1:
            if side=="L":
                stop1=max(pstop,trail1) if active1 and np.isfinite(trail1) else pstop
                hit1=L[i]<=stop1
            else:
                stop1=min(pstop,trail1) if active1 and np.isfinite(trail1) else pstop
                hit1=H[i]>=stop1
            if hit1:
                gross+=_half_exit_gross(entry,stop1,side)
                exit_fees+=stop1*(HALF/entry)*FEE
                live1=False
                reasons.append("T1_TRAIL" if active1 and ((side=="L" and trail1>=pstop) or (side=="S" and trail1<=pstop)) else ("PROTECT" if protection_active else "SL"))

        if live2:
            if side=="L":
                stop2=max(pstop,trail2) if active2 and np.isfinite(trail2) else pstop
                hit2=L[i]<=stop2
            else:
                stop2=min(pstop,trail2) if active2 and np.isfinite(trail2) else pstop
                hit2=H[i]>=stop2
            if hit2:
                gross+=_half_exit_gross(entry,stop2,side)
                exit_fees+=stop2*(HALF/entry)*FEE
                live2=False
                reasons.append("T2_TRAIL" if active2 and ((side=="L" and trail2>=pstop) or (side=="S" and trail2<=pstop)) else ("PROTECT" if protection_active else "SL"))

        if not live1 and not live2:
            entry_fee=NOTIONAL*FEE
            pnl=gross-entry_fee-exit_fees
            return i,pnl,entry_fee+exit_fees,tuple(reasons),True

        # Update/activate only AFTER current-candle stop checks.
        if side=="L":
            fav=float(H[i])
            if live1:
                if not active1 and fav>=act1:
                    active1=True; extreme1=fav; trail1=extreme1*(1-t1_cb)
                    if protect_pct>0: protection_active=True
                elif active1:
                    extreme1=max(extreme1,fav); trail1=max(trail1,extreme1*(1-t1_cb))
            if live2:
                if not active2 and fav>=act2:
                    active2=True; extreme2=fav; trail2=extreme2*(1-t2_cb)
                elif active2:
                    extreme2=max(extreme2,fav); trail2=max(trail2,extreme2*(1-t2_cb))
        else:
            fav=float(L[i])
            if live1:
                if not active1 and fav<=act1:
                    active1=True; extreme1=fav; trail1=extreme1*(1+t1_cb)
                    if protect_pct>0: protection_active=True
                elif active1:
                    extreme1=min(extreme1,fav); trail1=min(trail1,extreme1*(1+t1_cb))
            if live2:
                if not active2 and fav<=act2:
                    active2=True; extreme2=fav; trail2=extreme2*(1+t2_cb)
                elif active2:
                    extreme2=min(extreme2,fav); trail2=min(trail2,extreme2*(1+t2_cb))

    return end_i-1,0.0,0.0,("OPEN_END",),False

def simulate(d,events,start,params,variant="OFF"):
    idx=d.index
    a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in events if a<=e[0]<b]

    sl,t1cb,t2act,t2cb,protect=params
    p=0; net=0.0; fees=0.0; pos_sum=0.0; neg_sum=0.0
    trades=wins=losses=0; open_end=0; vetoes=0
    curve=INITIAL; peak=INITIAL; maxdd=0.0
    reason_counts={}

    while p<len(ev):
        ei,side,ot,smcdir,obblocked=ev[p]
        if not approved(side,smcdir,obblocked,variant):
            vetoes+=1; p+=1; continue
        xi,pnl,tf,rs,done=run_one_trade(H,L,C,ei,b,side,sl,t1cb,t2act,t2cb,protect)
        if not done:
            open_end=1
            break
        trades+=1; net+=pnl; fees+=tf; curve+=pnl
        peak=max(peak,curve); maxdd=max(maxdd,peak-curve)
        if pnl>0: wins+=1; pos_sum+=pnl
        elif pnl<0: losses+=1; neg_sum+=-pnl
        for r in rs: reason_counts[r]=reason_counts.get(r,0)+1
        p+=1
        while p<len(ev) and ev[p][0]<xi:
            p+=1

    return {
        "trades":trades,"wins":wins,"losses":losses,
        "win_rate":wins/trades*100 if trades else np.nan,
        "profit_factor":pos_sum/neg_sum if neg_sum else (np.inf if pos_sum else np.nan),
        "net_pnl":net,"fees":fees,"max_drawdown":maxdd,
        "ending_equity":INITIAL+net,"vetoes":vetoes,"open_positions_end":open_end,
        "sl_pct":sl*100,"t1_activation_pct":T1_ACT*100,"t1_callback_pct":t1cb*100,
        "t2_activation_pct":t2act*100,"t2_callback_pct":t2cb*100,
        "protect_pct":protect*100,
        "t1_trail_exits":reason_counts.get("T1_TRAIL",0),
        "t2_trail_exits":reason_counts.get("T2_TRAIL",0),
        "protect_exits":reason_counts.get("PROTECT",0),
        "sl_half_exits":reason_counts.get("SL",0),
    }

def configs():
    for sl in SL_GRID:
        for c1 in T1_CB_GRID:
            for a2 in T2_ACT_GRID:
                for c2 in T2_CB_GRID:
                    for pr in PROTECT_GRID:
                        yield (sl,c1,a2,c2,pr)

def rank_layer1(df):
    rows=[]
    keys=["sl_pct","t1_callback_pct","t2_activation_pct","t2_callback_pct","protect_pct"]
    for vals,g in df.groupby(keys,dropna=False):
        by={r.window:r for r in g.itertuples()}
        recent=[by["6M"].net_pnl,by["1Y"].net_pnl,by["3Y"].net_pnl]
        rows.append({
            **dict(zip(keys,vals)),
            "recent_positive":sum(x>0 for x in recent),
            "all_recent_positive":all(x>0 for x in recent),
            "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,
            "pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
            "pf_3Y":by["3Y"].profit_factor,"pf_1Y":by["1Y"].profit_factor,
            "dd_3Y":by["3Y"].max_drawdown,"dd_FULL":by["FULL"].max_drawdown,
            "trades_3Y":by["3Y"].trades,"trades_FULL":by["FULL"].trades,
        })
    r=pd.DataFrame(rows)
    # 3Y is primary. 1Y/6M are confirmation. Full is stress information only.
    r=r.sort_values(
        ["all_recent_positive","recent_positive","pnl_3Y","pf_3Y","dd_3Y","pnl_1Y","pnl_6M"],
        ascending=[False,False,False,False,True,False,False]
    ).reset_index(drop=True)
    r["layer1_rank"]=np.arange(1,len(r)+1)
    return r

def params_from_rank(row):
    return (row.sl_pct/100,row.t1_callback_pct/100,row.t2_activation_pct/100,
            row.t2_callback_pct/100,row.protect_pct/100)

def process_symbol(symbol,path,out):
    d,events=prepare(path)
    summary_l1=[]; ranks={}

    for combo in COMBOS:
        rows=[]
        for cfg in configs():
            for wn,start in WINDOWS.items():
                rows.append({"symbol":symbol,"combo":combo,"window":wn,
                             **simulate(d,events[combo],start,cfg,"OFF")})
        broad=pd.DataFrame(rows)
        broad.to_csv(out/f"{symbol}_{combo}_L1_2TRAIL_broad.csv",index=False)
        rank=rank_layer1(broad)
        rank.insert(0,"combo",combo); rank.insert(0,"symbol",symbol)
        rank.to_csv(out/f"{symbol}_{combo}_L1_2TRAIL_ranked.csv",index=False)
        ranks[combo]=rank
        summary_l1.append(rank.head(10))
        print(f"\n{symbol} {combo} L1 TOP 5",flush=True)
        print(rank.head(5).to_string(index=False),flush=True)

    pd.concat(summary_l1,ignore_index=True).to_csv(out/f"{symbol}_L1_TOP10_ALL.csv",index=False)

    # Layer 2: test the top 3 L1 configs to reduce selection brittleness.
    l2rows=[]
    for combo in COMBOS:
        top=ranks[combo].head(3)
        for rr in top.itertuples():
            cfg=params_from_rank(rr)
            for variant in L2_VARIANTS:
                for wn,start in WINDOWS.items():
                    x=simulate(d,events[combo],start,cfg,variant)
                    l2rows.append({
                        "symbol":symbol,"combo":combo,"layer1_rank":int(rr.layer1_rank),
                        "variant":variant,"window":wn,**x
                    })
    l2=pd.DataFrame(l2rows)
    l2.to_csv(out/f"{symbol}_L2_SMC_OB_broad.csv",index=False)

    # Aggregate L2 using same temporal priority.
    ag=[]
    keys=["combo","layer1_rank","variant","sl_pct","t1_callback_pct","t2_activation_pct","t2_callback_pct","protect_pct"]
    for vals,g in l2.groupby(keys,dropna=False):
        by={r.window:r for r in g.itertuples()}
        recent=[by["6M"].net_pnl,by["1Y"].net_pnl,by["3Y"].net_pnl]
        ag.append({
            **dict(zip(keys,vals)),
            "recent_positive":sum(x>0 for x in recent),
            "all_recent_positive":all(x>0 for x in recent),
            "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,
            "pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
            "pf_3Y":by["3Y"].profit_factor,"dd_3Y":by["3Y"].max_drawdown,
            "trades_3Y":by["3Y"].trades,"vetoes_3Y":by["3Y"].vetoes,
        })
    ar=pd.DataFrame(ag).sort_values(
        ["combo","all_recent_positive","recent_positive","pnl_3Y","pf_3Y","dd_3Y"],
        ascending=[True,False,False,False,False,True]
    )
    ar["layer2_rank_within_combo"]=ar.groupby("combo").cumcount()+1
    ar.to_csv(out/f"{symbol}_L2_SMC_OB_ranked.csv",index=False)
    print(f"\n{symbol} L2 WINNERS",flush=True)
    print(ar[ar.layer2_rank_within_combo==1].to_string(index=False),flush=True)
    return pd.concat(summary_l1,ignore_index=True),ar

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True); ap.add_argument("--eth",required=True)
    ap.add_argument("--out-dir",default="results/two_trail_layer12")
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    l1all=[];l2all=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        a,b=process_symbol(sym,path,out);l1all.append(a);l2all.append(b)
    pd.concat(l1all,ignore_index=True).to_csv(out/"L1_2TRAIL_TOP10_ALL.csv",index=False)
    pd.concat(l2all,ignore_index=True).to_csv(out/"L2_SMC_OB_RANKED_ALL.csv",index=False)
    meta={
        "dataset":"hybrid BTC/ETH 15m 2021-01-01 through 2026-09-20",
        "priority":"3Y primary; 1Y and 6M confirmation; FULL stress",
        "exit":"no fixed TP; two trailing halves 50/50",
        "t1_activation_pct":0.5,
        "sl_grid_pct":[x*100 for x in SL_GRID],
        "t1_callback_grid_pct":[x*100 for x in T1_CB_GRID],
        "t2_activation_grid_pct":[x*100 for x in T2_ACT_GRID],
        "t2_callback_grid_pct":[x*100 for x in T2_CB_GRID],
        "protect_grid_pct":[x*100 for x in PROTECT_GRID],
        "trailing_semantics":"new activation/extreme updates are effective from next 15m candle",
        "layer2_variants":L2_VARIANTS,
        "smc":{"swing_len":50,"confluence":False,"same_bar":True},
        "ob":{"pivot_len":5,"search_bars":12,"max_age":80,"danger_atr":0.5},
        "execution":{"initial_equity":1000,"notional_per_trade":100,"fee_pct_per_side":0.05,
                     "one_position_per_combo":True,"no_exit_on_entry_candle":True}
    }
    (out/"run_meta.json").write_text(json.dumps(meta,indent=2))

if __name__=="__main__":
    main()
