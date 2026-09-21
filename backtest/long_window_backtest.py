from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd
from signals import build_all_signals
from backtest import backtest_parallel

COMBOS=["C1","C2","C3","C4","C5","C6","C7","C8","C9","C10","TIER"]
NATIVE={"C1":"1h","C2":"1h","C3":"1h","C4":"1h","C5":"15m","C6":"1h","C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h"}
WINDOWS={
    "6M":"2026-03-21T00:00:00Z",
    "1Y":"2025-09-21T00:00:00Z",
    "3Y":"2023-09-21T00:00:00Z",
    "FULL":"2021-01-01T00:00:00Z",
}
END_EXCLUSIVE=pd.Timestamp("2026-09-21T00:00:00Z")

def load15(path):
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

def signal_counts(sigs,combo,start,end):
    if combo=="TIER":
        f=sigs["tier"]; p="TIER"
    elif NATIVE[combo]=="1h":
        f=sigs["1h"]; p=combo
    else:
        f=sigs["15m"]; p=combo
    m=(f.index>=start)&(f.index<end)
    q=f.loc[m]
    L=int(q[f"{p}_L"].fillna(False).sum())
    S=int(q[f"{p}_S"].fillna(False).sum())
    return L,S

def run_symbol(symbol,path,out):
    d=load15(path)
    if d.index.min()>pd.Timestamp("2021-01-01T00:00:00Z"):
        raise ValueError(f"{symbol}: dataset starts too late: {d.index.min()}")
    if d.index.max()<END_EXCLUSIVE-pd.Timedelta("15min"):
        raise ValueError(f"{symbol}: dataset ends too early: {d.index.max()}")
    sigs=build_all_signals(d)
    rows=[]
    trade_rows=[]
    for wname,start_s in WINDOWS.items():
        start=pd.Timestamp(start_s)
        bt=d[(d.index>=start)&(d.index<END_EXCLUSIVE)]
        for combo in COMBOS:
            r=backtest_parallel(
                bt,sigs,enabled=[combo],
                sl_pct=.009,tp_pct=.011,
                initial_equity=1000.0,
                margin_per_trade=1.0,
                leverage=100.0,
                commission_pct=.05,
                margin_mode="isolated",
                policy="stop_first",
            )
            t=r["trades"].copy()
            sl,ss=signal_counts(sigs,combo,start,END_EXCLUSIVE)
            if len(t):
                long_t=int((t.side=="L").sum()); short_t=int((t.side=="S").sum())
                long_pnl=float(t.loc[t.side=="L","pnl"].sum()); short_pnl=float(t.loc[t.side=="S","pnl"].sum())
            else:
                long_t=short_t=0; long_pnl=short_pnl=0.0
            st=r["stats"]
            row={
                "symbol":symbol,"window":wname,"start":str(start),"end_exclusive":str(END_EXCLUSIVE),
                "combo":combo,"native_tf":NATIVE[combo],
                "signals_long":sl,"signals_short":ss,"signals_total":sl+ss,
                "trades":int(st["trades"]),"long_trades":long_t,"short_trades":short_t,
                "wins":int(st["wins"]),"losses":int(st["losses"]),
                "win_rate":st["win_rate"],"profit_factor":st["profit_factor"],
                "net_pnl":st["net_pnl"],"gross_pnl":st["gross_pnl"],"fees":st["fees"],
                "long_pnl":long_pnl,"short_pnl":short_pnl,
                "max_drawdown":st["max_drawdown"],"ending_equity":st["ending_equity"],
                "bankruptcy_proxy":int(st["liquidations_proxy"]),
                "skipped_margin":int(st["skipped_margin"]),
                "open_positions_end":len(r["open_trades"]),
            }
            rows.append(row)
            if len(t):
                t.insert(0,"symbol",symbol);t.insert(1,"window",wname)
                trade_rows.append(t)
            print(symbol,wname,combo,"trades",row["trades"],"net",round(row["net_pnl"],4),"PF",row["profit_factor"],flush=True)
    sdf=pd.DataFrame(rows)
    sdf.to_csv(out/f"{symbol}_long_windows_BASE.csv",index=False)
    if trade_rows:
        pd.concat(trade_rows,ignore_index=True).to_csv(out/f"{symbol}_long_windows_BASE_trades.csv",index=False)
    return sdf

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--btc",required=True)
    ap.add_argument("--eth",required=True)
    ap.add_argument("--out-dir",default="results/long_windows")
    args=ap.parse_args()
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    allrows=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        allrows.append(run_symbol(sym,path,out))
    s=pd.concat(allrows,ignore_index=True)
    s.to_csv(out/"ALL_BASE_6M_1Y_3Y_FULL.csv",index=False)
    piv=s.pivot_table(index=["symbol","combo"],columns="window",values=["net_pnl","trades","profit_factor","win_rate","max_drawdown"],aggfunc="first")
    piv.to_csv(out/"ALL_BASE_pivot.csv")
    meta={
        "dataset":{
            "BTCUSDT":args.btc,"ETHUSDT":args.eth,
            "expected_range":"2021-01-01T00:00:00Z to 2026-09-21T00:00:00Z exclusive"
        },
        "windows":WINDOWS,
        "execution":{
            "initial_equity_usd":1000.0,"margin_mode":"isolated","margin_per_trade_usd":1.0,
            "leverage":100.0,"notional_per_trade_usd":100.0,
            "commission_pct_per_side":0.05,"intrabar_policy":"stop_first",
            "fixed_pct_exit_non_ATR":"TP 1.1% / SL 0.9%",
            "ATR_exit_C1_C3_C6":"TP 1.6 ATR21 / SL 1.4 ATR21",
            "liquidation_model":"bankruptcy proxy at 1/leverage adverse move"
        },
        "rows":len(s)
    }
    (out/"run_meta.json").write_text(json.dumps(meta,indent=2))
    print("\nSUMMARY")
    print(s[["symbol","window","combo","trades","win_rate","profit_factor","net_pnl","max_drawdown","bankruptcy_proxy"]].to_string(index=False))

if __name__=="__main__":
    main()
