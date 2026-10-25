from __future__ import annotations
"""Compare 100% hard TP versus 50% hard TP + 50% trailing using Supabase 15m bars.

Only the exactly 35-day Supabase period is evaluated. Previous historical bars
are signal warmup only and never counted in the trades/PnL.
"""
import argparse
import bisect
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.strategy.final14_config import FINAL14_CASES, NEW6_CASES, get_case
from backtest_20final_6windows import (
    build_events, run_trade, summarize, combo_key, NOTIONAL, FEE, INITIAL_EQUITY,
    TRAIL_ACTIVATION, TRAIL_CALLBACK,
)

WINDOW_DAYS = 35
WARMUP_BARS = 3400

def load_supabase(csv_path, symbol, historical_path):
    entire = pd.read_csv(csv_path)
    d = entire[entire.symbol == symbol].copy()
    assert len(d) == 3400, (symbol, len(d))
    d = d.drop(columns=["symbol"])
    d["open_time"] = d["open_time"].astype("int64")
    d.index = pd.to_datetime(d.open_time, unit="ms", utc=True)
    d.index.name = "open_time"
    d.sort_index(inplace=True)
    assert d.index.is_unique, f"{symbol} duplicated candles"
    diff = d.index.to_series().diff().dropna()
    assert bool((diff == pd.Timedelta(minutes=15)).all()), f"{symbol} gaps in Supabase"
    for col in ("open", "high", "low", "close", "volume"):
        assert bool(np.isfinite(d[col].to_numpy(float)).all()), f"{symbol} nonfinite {col}"
    assert bool(((d.high>=d[["open", "close", "low"]].max(axis=1)) &
                (d.low<=d[["open", "close", "high"]].min(axis=1))).all()), f"{symbol} bad OHLC"
    hist = pd.read_pickle(historical_path)
    if not isinstance(hist.index, pd.DatetimeIndex):
        hist.index = pd.to_datetime(hist.open_time, unit="ms", utc=True)
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize("UTC")
    else:
        hist.index = hist.index.tz_convert("UTC")
    warmup = hist.loc[hist.index < d.index[0]].tail(WARMUP_BARS)
    assert len(warmup) == WARMUP_BARS, f"{symbol} missing warmup bars"
    assert warmup.index[-1] + pd.Timedelta(minutes=15) == d.index[0], (
        symbol, "warmup discontinuity", warmup.index[-1], d.index[0])
    d = pd.concat([warmup, d], axis=0)
    assert d.index.is_unique
    return d

def hard_trade(H, L, C, idx, ei, side, cfg, end_i):
    entry = float(C[ei])
    direction = 1 if side == "L" else -1
    tp = entry * (1 + direction * float(cfg["tp_pct"]))
    sl = entry * (1 - direction * float(cfg["sl_pct"]))
    exit_i, price, reason = end_i-1, float(C[end_i-1]), "END_MTM"
    for i in range(ei+1, end_i):
        hit_sl = float(L[i]) <= sl if direction == 1 else float(H[i]) >= sl
        hit_tp = float(H[i]) >= tp if direction == 1 else float(L[i]) <= tp
        # Stop wins same-bar ambiguity, consistent with split simulation.
        if hit_sl or hit_tp:
            exit_i = i
            price = sl if hit_sl else tp
            reason = "SL" if hit_sl else "TP"
            break
    qty = NOTIONAL/entry
    gross = direction * (price-entry) * qty
    fees = NOTIONAL*FEE+price*qty*FEE
    return {
        "entry_time": str(idx[ei]), "exit_time": str(idx[exit_i]),
        "side": side, "entry_price": round(entry,8), "tp_price": round(tp,8),
        "sl_price": round(sl,8), "gross_pnl_usdt": round(gross,8),
        "fees_usdt": round(fees,8), "net_pnl_usdt": round(gross-fees,8),
        "closed": reason!="END_MTM", "reason": reason,
        "exit_index": int(exit_i), "entry_index": int(ei),
    }

def simulate(d, events, cfg, start_i, end_i, mode, symbol, combo):
    hi, lo, close = (d[col].to_numpy(float) for col in ("high", "low", "close"))
    idx = d.index
    filtered = [(ei,side) for ei,side in events if start_i <= ei < end_i-1]
    entries = [ei for ei,_ in filtered]
    rows = []
    p = 0
    while p < len(filtered):
        ei,side = filtered[p]
        if mode == "split":
            row = run_trade(hi,lo,close,idx,ei,side,cfg,end_i)
        else:
            row = hard_trade(hi,lo,close,idx,ei,side,cfg,end_i)
        row.update({"symbol":symbol, "combo":combo_key(combo),
                    "combo_id":combo, "mode":mode})
        rows.append(row)
        # One position per symbol/combo; a fresh entry can be placed at
        # the CLOSE of the candle where prior position exits.
        p = bisect.bisect_left(entries, row["exit_index"], p+1)
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--supabase-csv", required=True)
    ap.add_argument("--btc-history", required=True)
    ap.add_argument("--eth-history", required=True)
    ap.add_argument("--out", default="results/backtest_supabase_35days_compare")
    args=ap.parse_args()
    out_dir=Path(args.out)
    out_dir.mkdir(parents=True,exist_ok=True)
    datasets={}
    for symbol, hp in (("BTC-USDT",args.btc_history),("ETH-USDT",args.eth_history)):
        d=load_supabase(args.supabase_csv, symbol, hp)
        datasets[symbol]=build_events(symbol, d)
    end = min(d.index[-1]+pd.Timedelta(minutes=15) for d,_ in datasets.values())
    start = end-pd.Timedelta(days=WINDOW_DAYS)
    comparison={}
    combo_rows=[]
    trade_rows=[]
    for mode in ("hard", "split"):
        rows=[]
        for symbol,(d,events) in datasets.items():
            first=int(d.index.searchsorted(start))
            last=int(d.index.searchsorted(end))
            for combo in sorted(set(FINAL14_CASES[symbol]) | set(NEW6_CASES[symbol])):
                tr=simulate(d,events.get(combo,[]),get_case(symbol,combo),first,last,mode,symbol,combo)
                rows.extend(tr)
                combo_rows.append({"mode":mode,"symbol":symbol,
                                   "combo":combo_key(combo),"combo_id":combo,
                                   **summarize(tr,start,end)})
        trade_rows.extend(rows)
        comparison[mode]=summarize(rows,start,end)
        print("MODE_RESULT="+json.dumps({"mode":mode,**comparison[mode]}),flush=True)
    for a in ("net_pnl_usdt","max_drawdown_realized_usdt","win_rate_pct","trades","fees_usdt"):
        va=comparison["split"][a]; vb=comparison["hard"][a]
        comparison["difference_"+a]=round(va-vb,6) if va is not None and vb is not None else None
    meta={
        "ok":True, "strategy":"20FINAL_2026-09-25", "source":"Supabase FINAL14-Autotrade public.candles BTC/ETH 15m",
        "scope":"ONLY last exactly 35 days of Supabase, history is signal warmup ONLY",
        "source_supabase_csv":args.supabase_csv,
        "warmup":{s:3400 for s in datasets},
        "window_start_utc":str(start),"window_end_utc":str(end),
        "candles_per_symbol_in_window":35*24*4,
        "position_notional_usdt":NOTIONAL,
        "fee_per_side":FEE,
        "initial_equity_for_drawdown_usdt":INITIAL_EQUITY,
        "trailing_activation":TRAIL_ACTIVATION,
        "trailing_callback":TRAIL_CALLBACK,
        "comparison":comparison,
        "execution":"single independent position per symbol+combo; SL first on same candle; 15m close entry; trailing state updates next candle",
        "limitations":["conservative 15m OHLC approximation; no intrabar tick order",
                       "no leverage-specific liquidation","no funding","no slippage",
                       "warmup historical BTC/ETH before Supabase first candle is from hybrid dataset"]
    }
    (out_dir/"summary.json").write_text(json.dumps(meta,indent=2,ensure_ascii=False))
    pd.DataFrame(combo_rows).to_csv(out_dir/"combos_hard_vs_split.csv",index=False)
    pd.DataFrame(trade_rows).to_json(out_dir/"trades_hard_vs_split.jsonl",orient="records",lines=True,force_ascii=False)
    print("COMPARISON_COMPLETE="+json.dumps(meta,separators=(",",":"),ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
