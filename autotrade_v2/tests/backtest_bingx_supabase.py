from __future__ import annotations

import json
import argparse
from pathlib import Path
import pandas as pd
from backtest_20final_windows_split import (
    INITIAL_EQUITY, NOTIONAL, FEE, CALLBACK_RATE,
    build_events, simulate_case, metrics, combo_key,
    FINAL14_CASES, NEW6_CASES, get_case,
)

WARMUP_BARS = 2400  # 25 days of 15-minute candles


def load_snapshot(path):
    df = pd.read_csv(path)
    assert len(df) == 3400, (path, len(df))
    assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
    assert df.open_time.is_unique
    assert (df.open_time.diff().dropna() == 900000).all()
    for col in ["open","high","low","close","volume"]:
        assert pd.to_numeric(df[col], errors="coerce").notna().all()
    assert ((df.high >= df[["open","low","close"]].max(axis=1)) &
            (df.low <= df[["open","high","close"]].min(axis=1))).all()
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True,exist_ok=True)
    data = {}
    for sym, path in (("BTC-USDT",args.btc),("ETH-USDT",args.eth)):
        raw = load_snapshot(path)
        d,events = build_events(sym,raw)
        assert len(d)==3400
        print("SUPABASE_DATA=" + json.dumps({"symbol":sym, "bars":len(d),
          "first_utc":str(d.index[0]),"last_utc":str(d.index[-1]),
          "event_counts":{combo_key(i):len(e) for i,e in events.items()}}),flush=True)
        data[sym]=(d,events)

    end = min(d.index[-1] for d,e in data.values()) + pd.Timedelta(minutes=15)
    # Main result uses all 3,400 DB bars; sensitivity panel excludes first 2,400.
    windows = [("FULL_DB_35D",0),("AFTER_WARMUP_10D",WARMUP_BARS)]
    summary=[]
    trade_rows=[]
    for window,start_i in windows:
        for sym,(d,events) in data.items():
            for combo in sorted(set(FINAL14_CASES[sym]) | set(NEW6_CASES[sym])):
                cfg=get_case(sym,combo)
                split=simulate_case(d,events.get(combo,[]),cfg,start_i,len(d),mode="split")
                hard=simulate_case(d,events.get(combo,[]),cfg,start_i,len(d),mode="hard")
                for r in split:
                    r.update({"window":window,"symbol":sym,"combo":combo_key(combo)})
                trade_rows.extend(split)
                hs=metrics(hard)
                ms=metrics(split)
                summary.append({
                    "window":window,"symbol":sym,"combo":combo_key(combo),"combo_id":int(combo),
                    "start_utc":str(d.index[start_i]),
                    "end_exclusive_utc":str(end),
                    **ms,
                    "hard_tp_pnl_usdt":hs["net_pnl_usdt"],
                    "delta_vs_hard_usdt":round(ms["net_pnl_usdt"]-hs["net_pnl_usdt"],4),
                })
        window_rows=[x for x in trade_rows if x["window"]==window]
        print("WINDOW_RESULT="+json.dumps({"window":window,**metrics(window_rows)}),flush=True)

    pd.DataFrame(summary).to_csv(out/"per_combo.csv",index=False)
    pd.DataFrame([{k:v for k,v in r.items() if k!="legs"} for r in trade_rows]).to_csv(out/"trades.csv",index=False)
    (out/"trade_details.json").write_text(json.dumps(trade_rows,indent=2),encoding="utf-8")
    summary_out={
        "ok":True,
        "data_source":"Supabase public.candles; BingX WS and REST 15m; BTC-USDT, ETH-USDT",
        "bars_per_symbol":3400,
        "first_candle_utc":str(data["BTC-USDT"][0].index[0]),
        "last_candle_utc":str(data["BTC-USDT"][0].index[-1]),
        "nominal_usdt_per_trade":NOTIONAL,
        "fee_per_side":FEE,
        "initial_equity_per_simulation":INITIAL_EQUITY,
        "activation_pct":0.005,
        "callback_rate":CALLBACK_RATE,
        "exit_mode":"50% hard TP per combo + 50% trailing activated entry +/- 0.5%; full SL resized after either half closes",
        "assumptions":"15m OHLC, SL before TP in ambiguous candles; prior-bar trailing best; trailing may close before TP; ignores same-activation candle trailing; no slippage/funding/liquidation/lot rounding; unfinished trades END_MTM; independent combo positions; first 2400 bars may be cold-start",
        "windows":[{"window":w,**metrics([r for r in trade_rows if r["window"]==w])} for w,_ in windows],
        "per_combo":summary
    }
    (out/"summary.json").write_text(json.dumps(summary_out,indent=2),encoding="utf-8")
    print("RESULT_JSON="+json.dumps(summary_out,separators=(",",":")),flush=True)


if __name__=="__main__":
    main()
