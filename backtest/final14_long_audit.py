from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd

from layer3_long import load, precompute, entry_variants, build_context
from two_trail_layer12 import WINDOWS, END, approved

INITIAL=1000.0
NOTIONAL=100.0
FEE=0.0005

def sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def validate_data(path):
    d=load(path)
    idx=d.index
    dif=idx.to_series().diff().dropna()
    bad_gap=int((dif!=pd.Timedelta("15min")).sum())
    dup=int(idx.duplicated().sum())
    ohlc_bad=int(((d.high<d[["open","close","low"]].max(axis=1)) |
                  (d.low>d[["open","close","high"]].min(axis=1)) |
                  (d.high<d.low)).sum())
    nan_ohlcv=int(d[["open","high","low","close","volume"]].isna().any(axis=1).sum())
    return {
      "path":str(path),"sha256":sha256(path),"rows":int(len(d)),
      "first":str(idx[0]),"last":str(idx[-1]),
      "duplicates":dup,"non_15m_gaps":bad_gap,
      "invalid_ohlc":ohlc_bad,"nan_ohlcv":nan_ohlcv,
      "pass":dup==0 and bad_gap==0 and ohlc_bad==0 and nan_ohlcv==0
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
            return i,px,gross-fees,fees,"SL" if hit_sl else "TP"
    i=end_i-1; px=float(C[i])
    gross=(px-entry)*qty*(1 if side=="L" else -1)
    fees=NOTIONAL*FEE + px*qty*FEE
    return i,px,gross-fees,fees,"END_MTM"

def simulate(d,events,start,l2,tp_pct,sl_pct):
    idx=d.index; a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END))
    H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in events if a<=e[0]<b]
    p=0; net=fees=pos=neg=0.0; trades=wins=losses=veto=0
    eq=INITIAL; peak=INITIAL; dd=0.0; trade_rows=[]
    while p<len(ev):
        ei,side,ot,sd,blocked=ev[p]
        if not approved(side,sd,blocked,l2):
            veto+=1; p+=1; continue
        xi,px,pnl,tf,reason=hard_trade(H,L,C,ei,b,side,tp_pct,sl_pct)
        entry=float(C[ei])
        trades+=1; net+=pnl; fees+=tf; eq+=pnl; peak=max(peak,eq); dd=max(dd,peak-eq)
        if pnl>0: wins+=1; pos+=pnl
        elif pnl<0: losses+=1; neg+=-pnl
        trade_rows.append({
          "entry_time":str(idx[ei]),"exit_time":str(idx[xi]),"side":side,
          "entry":entry,"exit":px,"reason":reason,"pnl":pnl,
          "bars_held":int(xi-ei),"smc_dir":int(sd),"ob_blocked":bool(blocked)
        })
        p+=1
        while p<len(ev) and ev[p][0]<xi: p+=1
    return {
      "trades":trades,"wins":wins,"losses":losses,
      "win_rate":wins/trades*100 if trades else np.nan,
      "profit_factor":pos/neg if neg else (np.inf if pos else np.nan),
      "net_pnl":net,"fees":fees,"max_drawdown":dd,
      "ending_equity":INITIAL+net,"vetoes":veto
    },trade_rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True); ap.add_argument("--eth",required=True)
    ap.add_argument("--config",default="final14_locked_rr.json")
    ap.add_argument("--out-dir",default="results/final14_long_audit")
    args=ap.parse_args()
    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True)
    cfg=json.loads(Path(args.config).read_text())

    data_audit={}
    summaries=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        data_audit[sym]=validate_data(path)
        if not data_audit[sym]["pass"]:
            raise SystemExit(f"DATA FAIL {sym}: {data_audit[sym]}")
        d=load(path); pc=precompute(d); variants=entry_variants(pc)
        lock={c:{"layer2_variant":v["layer2"]} for c,v in cfg["cases"][sym].items()}
        ctx=build_context(d,lock,variants)
        for combo,c in cfg["cases"][sym].items():
            ev=ctx[combo][c["entry_variant"]]
            for wn,start in WINDOWS.items():
                r,tr=simulate(d,ev,start,c["layer2"],float(c["tp_pct"]),float(c["sl_pct"]))
                summaries.append({"symbol":sym,"combo":combo,"window":wn,**c,**r})
                if wn=="FULL":
                    pd.DataFrame(tr).to_csv(out/f"trades_{sym}_{combo}_FULL.csv",index=False)

    sdf=pd.DataFrame(summaries)
    sdf.to_csv(out/"FINAL14_LONG_AUDIT_WINDOWS.csv",index=False)

    wide=[]
    for (sym,combo),g in sdf.groupby(["symbol","combo"]):
        by={r.window:r for r in g.itertuples()}
        wide.append({
          "symbol":sym,"combo":combo,
          "entry_variant":by["3Y"].entry_variant,"layer2":by["3Y"].layer2,
          "tp_pct":by["3Y"].tp_pct,"sl_pct":by["3Y"].sl_pct,"rr":by["3Y"].rr,
          "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,
          "pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
          "pf_3Y":by["3Y"].profit_factor,"dd_3Y":by["3Y"].max_drawdown,
          "trades_3Y":by["3Y"].trades,
          "PASS_NONNEG_ALL":all(by[w].net_pnl>=0 for w in WINDOWS)
        })
    wdf=pd.DataFrame(wide).sort_values("pnl_3Y",ascending=False)
    wdf.to_csv(out/"FINAL14_LONG_AUDIT_SUMMARY.csv",index=False)
    Path(out/"data_audit.json").write_text(json.dumps(data_audit,indent=2))
    meta={
      "config":cfg,
      "data_audit":data_audit,
      "engine":"locked FINAL14 hard TP/SL replay; no optimization",
      "semantics":{
        "entry":"native signal candle close mapped to canonical 15m",
        "no_exit_entry_candle":True,"same_bar_tp_sl":"SL first",
        "one_position_per_combo":True,"window_end":"mark to market",
        "fee_per_side":FEE,"notional":NOTIONAL,"initial_equity":INITIAL
      }
    }
    Path(out/"run_meta.json").write_text(json.dumps(meta,indent=2))
    print("\nDATA AUDIT\n"+json.dumps(data_audit,indent=2),flush=True)
    print("\nFINAL14 LONG AUDIT\n"+wdf.to_string(index=False),flush=True)
    if not bool(wdf.PASS_NONNEG_ALL.all()):
        raise SystemExit("FINAL14 FAIL: at least one locked case has a negative window")
    print("FINAL14 PASS: 14/14 nonnegative all windows",flush=True)

if __name__=="__main__": main()
