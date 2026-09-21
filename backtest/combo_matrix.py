from __future__ import annotations
import argparse
from pathlib import Path
import json
import pandas as pd

from signals import build_all_signals
from backtest import backtest_parallel, ALL
from smc_ob import ob_context, OBConfig

NATIVE_TF = {
    "C1":"1h","C2":"1h","C3":"1h","C4":"1h","C5":"15m",
    "C6":"1h","C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h",
}

def load(path):
    p=Path(path)
    df=pd.read_csv(p) if p.suffix.lower()==".csv" else pd.read_pickle(p)
    if not isinstance(df.index,pd.DatetimeIndex):
        if "open_time" in df: df.index=pd.to_datetime(df["open_time"],unit="ms",utc=True)
        elif "time" in df: df.index=pd.to_datetime(df["time"],utc=True)
        else: raise ValueError(f"Cannot build DatetimeIndex from {p}")
    if df.index.tz is None: df.index=df.index.tz_localize("UTC")
    else: df.index=df.index.tz_convert("UTC")
    return df.sort_index()

def signal_counts(sigs, combo, start):
    if combo=="TIER": frame=sigs["tier"]; prefix="TIER"
    else: frame=sigs["1h"] if NATIVE_TF[combo]=="1h" else sigs["15m"]; prefix=combo
    if start is not None: frame=frame[frame.index>=start]
    return int(frame[f"{prefix}_L"].fillna(False).sum()), int(frame[f"{prefix}_S"].fillna(False).sum())

def run_variant(d15_bt,sigs,combo,label,commission,policy,start,ob15=None,ob1=None):
    r=backtest_parallel(d15_bt,sigs,enabled=[combo],commission_pct=commission,policy=policy,ob15=ob15,ob1h=ob1)
    sl,ss=signal_counts(sigs,combo,start)
    row={"combo":combo,"native_tf":NATIVE_TF[combo],"variant":label,
         "signals_long":sl,"signals_short":ss,"signals_total":sl+ss,**r["stats"]}
    return row,r

def main():
    ap=argparse.ArgumentParser(description="Standalone per-combo calibration/research matrix")
    ap.add_argument("--symbol",required=True)
    ap.add_argument("--data15",required=True); ap.add_argument("--data1h",required=True)
    ap.add_argument("--data4h",required=True); ap.add_argument("--data6h",required=True)
    ap.add_argument("--combos",default=",".join(ALL))
    ap.add_argument("--variants",default="RAW")
    ap.add_argument("--commission",type=float,default=0.0)
    ap.add_argument("--policy",default="stop_first",choices=["tv_heuristic","stop_first","tp_first"])
    ap.add_argument("--trade-start",default="")
    ap.add_argument("--out-dir",default="results/by_combo")
    args=ap.parse_args()

    d15,d1,d4,d6=map(load,[args.data15,args.data1h,args.data4h,args.data6h])
    sigs=build_all_signals(d15,d1,d4,d6)
    trade_start=pd.Timestamp(args.trade_start,tz="UTC") if args.trade_start else None
    d15_bt=d15 if trade_start is None else d15[d15.index>=trade_start]
    if d15_bt.empty: raise ValueError("No 15m candles at/after --trade-start")

    combos=[x.strip().upper() for x in args.combos.split(",") if x.strip()]
    variants=[x.strip().upper() for x in args.variants.split(",") if x.strip()]
    bad=[c for c in combos if c not in ALL]
    if bad: raise ValueError(f"Unknown combos: {bad}")

    out=Path(args.out_dir); out.mkdir(parents=True,exist_ok=True); rows=[]
    ob_cache={}
    if "OB0" in variants:
        cfg=OBConfig(danger_atr=0.0); ob_cache["OB0"]=(ob_context(d15,cfg),ob_context(d1,cfg))
    if "OB05" in variants:
        cfg=OBConfig(danger_atr=0.5); ob_cache["OB05"]=(ob_context(d15,cfg),ob_context(d1,cfg))

    for combo in combos:
        combo_dir=out/combo; combo_dir.mkdir(parents=True,exist_ok=True)
        for variant in variants:
            if variant=="RAW":
                row,r=run_variant(d15_bt,sigs,combo,"RAW",args.commission,args.policy,trade_start)
            elif variant in ob_cache:
                ob15,ob1=ob_cache[variant]; label="OB_0.0ATR" if variant=="OB0" else "OB_0.5ATR"
                row,r=run_variant(d15_bt,sigs,combo,label,args.commission,args.policy,trade_start,ob15,ob1)
            else: raise ValueError(f"Unknown variant: {variant}")
            row.update({"symbol":args.symbol,"trade_start":str(d15_bt.index.min()),
                        "data_first_15m":str(d15.index.min()),"data_last_15m":str(d15.index.max())})
            rows.append(row)
            if not r["trades"].empty: r["trades"].to_csv(combo_dir/f"{variant}_trades.csv",index=False)
            if not r["vetoes"].empty: r["vetoes"].to_csv(combo_dir/f"{variant}_vetoes.csv",index=False)

    summary=pd.DataFrame(rows)
    summary.to_csv(out/f"{args.symbol}_combo_matrix.csv",index=False)
    raw=summary[summary.variant=="RAW"].copy()
    raw.to_csv(out/f"{args.symbol}_RAW_by_combo.csv",index=False)
    payload={"symbol":args.symbol,"combos":combos,"variants":variants,"commission_pct":args.commission,
             "policy":args.policy,"trade_start":str(d15_bt.index.min()),
             "data_15m":{"first":str(d15.index.min()),"last":str(d15.index.max()),"rows":len(d15)},"rows":rows}
    (out/f"{args.symbol}_combo_matrix.json").write_text(json.dumps(payload,indent=2,default=str),encoding="utf-8")
    print(raw[["combo","native_tf","signals_total","trades","win_rate","profit_factor","net_pnl","max_drawdown"]].to_string(index=False))

if __name__=="__main__": main()
