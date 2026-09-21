from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd
from signals import build_all_signals
from backtest import backtest_parallel, ALL
from smc_ob import ob_context, OBConfig

def load(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".csv": df = pd.read_csv(p)
    else: df = pd.read_pickle(p)
    if not isinstance(df.index,pd.DatetimeIndex):
        if "open_time" in df: df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        elif "time" in df: df.index = pd.to_datetime(df["time"], utc=True)
        else: raise ValueError(f"Cannot create DatetimeIndex from {p}")
    if df.index.tz is None: df.index = df.index.tz_localize("UTC")
    else: df.index = df.index.tz_convert("UTC")
    return df.sort_index()

def stats_with_label(label: str, result: dict) -> dict:
    return {"variant": label, **result["stats"]}

def main():
    ap = argparse.ArgumentParser(description="RAW vs SMC Order Block research runner")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--data15", required=True)
    ap.add_argument("--data1h", required=True)
    ap.add_argument("--data4h", required=True)
    ap.add_argument("--data6h", required=True)
    ap.add_argument("--enabled", default=",".join(ALL))
    ap.add_argument("--commission", type=float, default=0.0)
    ap.add_argument("--policy", default="tv_heuristic", choices=["tv_heuristic","stop_first","tp_first"])
    ap.add_argument("--danger", default="0.0,0.5")
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    d15,d1,d4,d6 = map(load,[args.data15,args.data1h,args.data4h,args.data6h])
    sigs = build_all_signals(d15,d1,d4,d6)
    enabled = [x.strip() for x in args.enabled.split(",") if x.strip()]
    rows=[]
    raw=backtest_parallel(d15,sigs,enabled,commission_pct=args.commission,policy=args.policy)
    rows.append(stats_with_label("RAW",raw))
    raw["trades"].to_csv(out_dir/f"{args.symbol}_RAW_trades.csv",index=False)
    for danger in [float(x) for x in args.danger.split(",") if x.strip()]:
        cfg=OBConfig(danger_atr=danger)
        ob15=ob_context(d15,cfg); ob1=ob_context(d1,cfg)
        res=backtest_parallel(d15,sigs,enabled,commission_pct=args.commission,policy=args.policy,ob15=ob15,ob1h=ob1)
        label=f"OB_{danger:g}ATR"
        rows.append(stats_with_label(label,res))
        res["trades"].to_csv(out_dir/f"{args.symbol}_{label}_trades.csv",index=False)
        res["vetoes"].to_csv(out_dir/f"{args.symbol}_{label}_vetoes.csv",index=False)
    summary=pd.DataFrame(rows)
    summary.to_csv(out_dir/f"{args.symbol}_summary.csv",index=False)
    payload={"symbol":args.symbol,"enabled":enabled,"commission_pct":args.commission,"policy":args.policy,"variants":rows}
    (out_dir/f"{args.symbol}_summary.json").write_text(json.dumps(payload,indent=2,default=str),encoding="utf-8")
    print(summary.to_string(index=False))

if __name__ == "__main__":
    main()
