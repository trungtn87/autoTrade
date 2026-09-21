from __future__ import annotations
import argparse, json, math
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
FEE=0.0005
NOTIONAL=100.0
LEV=100.0

ATR_TP=[0.6,0.8,1.0,1.2,1.4,1.6,1.8,2.0,2.4]
ATR_SL=[0.4,0.6,0.8,1.0,1.2,1.4]
PCT_TP=[0.4,0.6,0.8,1.0,1.2,1.5,1.8,2.2]
PCT_SL=[0.3,0.4,0.5,0.6,0.7,0.8,0.9]

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
    s=sig["1h"]
    # C1 native 1H signal enters on final 15m candle of the hour.
    evt=[]
    idx15=d.index
    pos={t:i for i,t in enumerate(idx15)}
    for side,col in [("L","C1_L"),("S","C1_S")]:
        for ot in s.index[s[col].fillna(False)]:
            et=ot+pd.Timedelta("45min")
            if et in pos and et<END:
                evt.append((pos[et],side,float(s.loc[ot,"ATR21"]),ot))
    evt.sort(key=lambda x:x[0])
    return d,evt

def simulate(d,events,start,family,tpv,slv):
    idx=d.index
    a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    # event subset from window
    ev=[e for e in events if a<=e[0]<b]
    signals_l=sum(e[1]=="L" for e in ev); signals_s=len(ev)-signals_l
    p=0; eq=1000.0; peak=eq; maxdd=0.0
    wins=losses=proxies=0; net=gross_sum=fees=0.0
    long_tr=short_tr=0; long_pnl=short_pnl=0.0
    trades=0; open_end=0
    while p<len(ev):
        ei,side,atr,_=ev[p]
        entry=C[ei]
        entry_fee=NOTIONAL*FEE
        eq-=entry_fee; peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
        if family=="ATR":
            if side=="L":
                tp=entry+atr*tpv; sl=entry-atr*slv
            else:
                tp=entry-atr*tpv; sl=entry+atr*slv
        else:
            tp_pct=tpv/100.0; sl_pct=slv/100.0
            if side=="L":
                tp=entry*(1+tp_pct); sl=entry*(1-sl_pct)
            else:
                tp=entry*(1-tp_pct); sl=entry*(1+sl_pct)
        bank=entry*(1-1/LEV) if side=="L" else entry*(1+1/LEV)
        if side=="L":
            adverse=max(sl,bank); adv_reason="SL" if sl>=bank else "BANKRUPTCY_PROXY"
            hit=(H[ei+1:b]>=tp)|(L[ei+1:b]<=adverse)
        else:
            adverse=min(sl,bank); adv_reason="SL" if sl<=bank else "BANKRUPTCY_PROXY"
            hit=(L[ei+1:b]<=tp)|(H[ei+1:b]>=adverse)
        loc=np.flatnonzero(hit)
        if len(loc)==0:
            open_end=1
            break
        xi=ei+1+int(loc[0])
        if side=="L":
            hit_tp=H[xi]>=tp; hit_adv=L[xi]<=adverse
        else:
            hit_tp=L[xi]<=tp; hit_adv=H[xi]>=adverse
        # stop_first when both happen
        if hit_adv:
            px=adverse; reason=adv_reason
        else:
            px=tp; reason="TP"
        qty=NOTIONAL/entry
        g=(px-entry)*qty*(1 if side=="L" else -1)
        exit_fee=px*qty*FEE
        pnl=g-entry_fee-exit_fee
        eq+=g-exit_fee
        peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
        trades+=1; gross_sum+=g; fees+=entry_fee+exit_fee; net+=pnl
        if pnl>0:wins+=1
        elif pnl<0:losses+=1
        if reason=="BANKRUPTCY_PROXY":proxies+=1
        if side=="L":long_tr+=1;long_pnl+=pnl
        else:short_tr+=1;short_pnl+=pnl
        # signals while open were ignored; signal on exit candle is allowed.
        p+=1
        while p<len(ev) and ev[p][0]<xi:
            p+=1
    gp=net if False else 0
    # second-pass PF from trade pnl would be expensive to store; accumulate directly
    # recompute compactly by rerun is unnecessary, so track positive/negative sums in vars below.
    return {
        "signals_long":signals_l,"signals_short":signals_s,"signals_total":len(ev),
        "trades":trades,"wins":wins,"losses":losses,
        "win_rate":(wins/trades*100 if trades else np.nan),
        "net_pnl":net,"gross_pnl":gross_sum,"fees":fees,
        "max_drawdown":maxdd,"ending_equity":eq,
        "bankruptcy_proxy":proxies,"open_positions_end":open_end,
        "long_trades":long_tr,"short_trades":short_tr,
        "long_pnl":long_pnl,"short_pnl":short_pnl,
    }

def simulate_with_pf(d,events,start,family,tpv,slv):
    # same execution as simulate, with positive/negative net trade sums for PF
    idx=d.index; a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in events if a<=e[0]<b]
    signals_l=sum(e[1]=="L" for e in ev); signals_s=len(ev)-signals_l
    p=0; eq=1000.0; peak=eq; maxdd=0.0
    wins=losses=proxies=0; net=gross_sum=fees=0.0; pos_sum=neg_sum=0.0
    long_tr=short_tr=0; long_pnl=short_pnl=0.0; trades=0; open_end=0
    while p<len(ev):
        ei,side,atr,_=ev[p]; entry=C[ei]; entry_fee=NOTIONAL*FEE
        eq-=entry_fee; maxdd=max(maxdd,peak-eq)
        if family=="ATR":
            tp=entry+(atr*tpv if side=="L" else -atr*tpv)
            sl=entry+(-atr*slv if side=="L" else atr*slv)
        else:
            tp=entry*(1+(tpv/100 if side=="L" else -tpv/100))
            sl=entry*(1+(-slv/100 if side=="L" else slv/100))
        bank=entry*(1-1/LEV) if side=="L" else entry*(1+1/LEV)
        if side=="L":
            adverse=max(sl,bank); adv_reason="SL" if sl>=bank else "BANKRUPTCY_PROXY"
            hit=(H[ei+1:b]>=tp)|(L[ei+1:b]<=adverse)
        else:
            adverse=min(sl,bank); adv_reason="SL" if sl<=bank else "BANKRUPTCY_PROXY"
            hit=(L[ei+1:b]<=tp)|(H[ei+1:b]>=adverse)
        loc=np.flatnonzero(hit)
        if not len(loc): open_end=1; break
        xi=ei+1+int(loc[0])
        if side=="L": hit_adv=L[xi]<=adverse
        else: hit_adv=H[xi]>=adverse
        if hit_adv: px=adverse; reason=adv_reason
        else: px=tp; reason="TP"
        qty=NOTIONAL/entry
        g=(px-entry)*qty*(1 if side=="L" else -1)
        exit_fee=px*qty*FEE
        pnl=g-entry_fee-exit_fee
        eq+=g-exit_fee; peak=max(peak,eq); maxdd=max(maxdd,peak-eq)
        trades+=1; gross_sum+=g; fees+=entry_fee+exit_fee; net+=pnl
        if pnl>0:wins+=1;pos_sum+=pnl
        elif pnl<0:losses+=1;neg_sum+=-pnl
        if reason=="BANKRUPTCY_PROXY":proxies+=1
        if side=="L":long_tr+=1;long_pnl+=pnl
        else:short_tr+=1;short_pnl+=pnl
        p+=1
        while p<len(ev) and ev[p][0]<xi:p+=1
    return {
        "signals_long":signals_l,"signals_short":signals_s,"signals_total":len(ev),
        "trades":trades,"wins":wins,"losses":losses,
        "win_rate":wins/trades*100 if trades else np.nan,
        "profit_factor":pos_sum/neg_sum if neg_sum else np.inf if pos_sum else np.nan,
        "net_pnl":net,"gross_pnl":gross_sum,"fees":fees,
        "max_drawdown":maxdd,"ending_equity":eq,
        "bankruptcy_proxy":proxies,"open_positions_end":open_end,
        "long_trades":long_tr,"short_trades":short_tr,"long_pnl":long_pnl,"short_pnl":short_pnl,
    }

def sweep_symbol(symbol,path,out):
    d,events=prepare(path)
    rows=[]
    configs=[("ATR",tp,sl) for tp in ATR_TP for sl in ATR_SL]
    configs += [("PCT",tp,sl) for tp in PCT_TP for sl in PCT_SL]
    # explicitly include original ATR 1.6/1.4
    for family,tp,sl in configs:
        for wn,start in WINDOWS.items():
            r=simulate_with_pf(d,events,start,family,tp,sl)
            rows.append({"symbol":symbol,"family":family,"tp":tp,"sl":sl,"window":wn,**r})
    df=pd.DataFrame(rows)
    df.to_csv(out/f"{symbol}_C1_L1_broad.csv",index=False)
    agg=[]
    for (family,tp,sl),g in df.groupby(["family","tp","sl"]):
        by={r.window:r for r in g.itertuples()}
        vals=[by[w].net_pnl for w in WINDOWS]
        pfs=[by[w].profit_factor for w in WINDOWS]
        agg.append({
            "symbol":symbol,"family":family,"tp":tp,"sl":sl,
            "positive_windows":sum(v>0 for v in vals),
            "min_pnl":min(vals),"sum_pnl":sum(vals),
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
    a=pd.DataFrame(agg)
    a=a.sort_values(["positive_windows","min_pnl","min_pf","pnl_FULL"],ascending=[False,False,False,False])
    a.to_csv(out/f"{symbol}_C1_L1_ranked.csv",index=False)
    print("\n",symbol,"TOP 20")
    print(a.head(20).to_string(index=False))
    return df,a

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True);ap.add_argument("--eth",required=True)
    ap.add_argument("--out-dir",default="results/c1_layer1_long")
    args=ap.parse_args()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    all_rank=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        _,a=sweep_symbol(sym,path,out);all_rank.append(a)
    pd.concat(all_rank,ignore_index=True).to_csv(out/"C1_L1_ranked_all.csv",index=False)
    meta={"windows":{k:str(v) for k,v in WINDOWS.items()},"end_exclusive":str(END),
          "execution":{"notional":100,"leverage":100,"fee_per_side_pct":0.05,"policy":"stop_first"},
          "atr_grid":{"tp":ATR_TP,"sl":ATR_SL},"pct_grid_percent":{"tp":PCT_TP,"sl":PCT_SL}}
    (out/"C1_L1_meta.json").write_text(json.dumps(meta,indent=2))

if __name__=="__main__":main()
