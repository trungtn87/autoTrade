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
        if "open_time" in df:
            df.index=pd.to_datetime(df["open_time"],unit="ms",utc=True)
        elif "time" in df:
            df.index=pd.to_datetime(df["time"],utc=True)
        else:
            raise ValueError(f"Cannot build DatetimeIndex from {p}")
    if df.index.tz is None:
        df.index=df.index.tz_localize("UTC")
    else:
        df.index=df.index.tz_convert("UTC")
    return df.sort_index()

def signal_counts(sigs, combo, start):
    if combo=="TIER":
        frame=sigs["tier"]; prefix="TIER"
    else:
        frame=sigs["1h"] if NATIVE_TF[combo]=="1h" else sigs["15m"]; prefix=combo
    if start is not None:
        frame=frame[frame.index>=start]
    return int(frame[f"{prefix}_L"].fillna(False).sum()), int(frame[f"{prefix}_S"].fillna(False).sum())

def run_variant(d15_bt,sigs,combo,label,args,start,ob15=None,ob1=None):
    r=backtest_parallel(
        d15_bt,sigs,enabled=[combo],
        sl_pct=args.sl_pct,tp_pct=args.tp_pct,
        initial_equity=args.initial_equity,
        margin_per_trade=args.margin_per_trade,
        leverage=args.leverage,
        commission_pct=args.commission,
        margin_mode=args.margin_mode,
        policy=args.policy,
        ob15=ob15,ob1h=ob1,
    )
    sl,ss=signal_counts(sigs,combo,start)
    row={
        "combo":combo,"native_tf":NATIVE_TF[combo],"variant":label,
        "signals_long":sl,"signals_short":ss,"signals_total":sl+ss,
        **r["stats"],
    }
    return row,r

def main():
    ap=argparse.ArgumentParser(description="Standalone per-combo calibration/research matrix")
    ap.add_argument("--symbol",required=True)
    ap.add_argument("--data15",required=True); ap.add_argument("--data1h",required=True)
    ap.add_argument("--data4h",required=True); ap.add_argument("--data6h",required=True)
    ap.add_argument("--combos",default=",".join(ALL))
    ap.add_argument("--variants",default="RAW")
    ap.add_argument("--initial-equity",type=float,default=1000.0)
    ap.add_argument("--margin-per-trade",type=float,default=1.0)
    ap.add_argument("--leverage",type=float,default=100.0)
    ap.add_argument("--margin-mode",default="isolated",choices=["isolated"])
    ap.add_argument("--commission",type=float,default=0.05,
                    help="Percent of notional per side; default is BingX taker 0.05")
    ap.add_argument("--tp-pct",type=float,default=0.011)
    ap.add_argument("--sl-pct",type=float,default=0.009)
    ap.add_argument("--policy",default="stop_first",choices=["tv_heuristic","stop_first","tp_first"])
    ap.add_argument("--trade-start",default="")
    ap.add_argument("--out-dir",default="results/by_combo")
    args=ap.parse_args()

    d15,d1,d4,d6=map(load,[args.data15,args.data1h,args.data4h,args.data6h])
    sigs=build_all_signals(d15,d1,d4,d6)
    trade_start=pd.Timestamp(args.trade_start,tz="UTC") if args.trade_start else None
    d15_bt=d15 if trade_start is None else d15[d15.index>=trade_start]
    if d15_bt.empty:
        raise ValueError("No 15m candles at/after --trade-start")

    combos=[x.strip().upper() for x in args.combos.split(",") if x.strip()]
    variants=[x.strip().upper() for x in args.variants.split(",") if x.strip()]
    bad=[c for c in combos if c not in ALL]
    if bad:
        raise ValueError(f"Unknown combos: {bad}")

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
                row,r=run_variant(d15_bt,sigs,combo,"RAW",args,trade_start)
            elif variant in ob_cache:
                ob15,ob1=ob_cache[variant]
                label="OB_0.0ATR" if variant=="OB0" else "OB_0.5ATR"
                row,r=run_variant(d15_bt,sigs,combo,label,args,trade_start,ob15,ob1)
            else:
                raise ValueError(f"Unknown variant: {variant}")

            row.update({
                "symbol":args.symbol,
                "trade_start":str(d15_bt.index.min()),
                "data_first_15m":str(d15.index.min()),
                "data_last_15m":str(d15.index.max()),
            })
            rows.append(row)

            r["trades"].to_csv(combo_dir/f"{variant}_trades.csv",index=False)
            r["equity"].to_csv(combo_dir/f"{variant}_equity.csv")
            r["vetoes"].to_csv(combo_dir/f"{variant}_vetoes.csv",index=False)
            open_rows=[]
            for key,tr in r["open_trades"].items():
                dct=dict(tr.__dict__)
                for k,v in list(dct.items()):
                    if isinstance(v,pd.Timestamp):
                        dct[k]=str(v)
                open_rows.append(dct)
            (combo_dir/f"{variant}_open_trades.json").write_text(
                json.dumps(open_rows,indent=2,default=str),encoding="utf-8"
            )

    summary=pd.DataFrame(rows)
    summary.to_csv(out/f"{args.symbol}_combo_matrix.csv",index=False)
    raw=summary[summary.variant=="RAW"].copy()
    raw.to_csv(out/f"{args.symbol}_RAW_by_combo.csv",index=False)

    payload={
        "symbol":args.symbol,
        "combos":combos,
        "variants":variants,
        "account":{
            "initial_equity_usd":args.initial_equity,
            "margin_mode":args.margin_mode,
            "margin_per_trade_usd":args.margin_per_trade,
            "leverage":args.leverage,
            "notional_per_trade_usd":args.margin_per_trade*args.leverage,
        },
        "execution":{
            "commission_pct_per_side":args.commission,
            "tp_pct":args.tp_pct,
            "sl_pct":args.sl_pct,
            "policy":args.policy,
        },
        "trade_start":str(d15_bt.index.min()),
        "data_15m":{
            "first":str(d15.index.min()),
            "last":str(d15.index.max()),
            "rows":len(d15),
        },
        "rows":rows,
    }
    (out/f"{args.symbol}_combo_matrix.json").write_text(
        json.dumps(payload,indent=2,default=str),encoding="utf-8"
    )
    print(raw[["combo","native_tf","signals_total","trades","win_rate","profit_factor","net_pnl","max_drawdown"]].to_string(index=False))

if __name__=="__main__":
    main()
