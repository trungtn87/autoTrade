from __future__ import annotations

import json
from collections import defaultdict

import numpy as np
import pandas as pd

from app.data.historical import HistoricalKlineClient
from app.strategy.final14_config import FINAL14_CASES, NEW6_CASES, get_case, case_name
from app.strategy.final14_exact_strategy import _research_frame, _new6_filtered_signals
from app.strategy.final14_research.layer3_long import precompute, entry_variants, NATIVE
from app.strategy.final14_research.smc_structure import smc_direction
from app.strategy.final14_research.smc_ob import ob_context, OBConfig
from app.strategy.final14_research.two_trail_layer12 import approved

NOTIONAL = 100.0
FEE = 0.0005
INITIAL_EQUITY = 1000.0


def combo_key(combo: int) -> str:
    combo = int(combo)
    if combo >= 101:
        return case_name(combo)
    return "TIER" if combo == 11 else f"C{combo}"


def build_events(symbol: str, raw15: pd.DataFrame):
    d = _research_frame(raw15)
    configs = FINAL14_CASES[symbol]
    wanted = {
        combo_key(combo): {cfg["entry_variant"]}
        for combo, cfg in configs.items()
    }

    pc = precompute(d, wanted)
    allv = entry_variants(pc, wanted)
    selected = {}
    for combo, cfg in configs.items():
        cname = combo_key(combo)
        matches = [x for x in allv[cname] if x[0] == cfg["entry_variant"]]
        if len(matches) != 1:
            raise RuntimeError((symbol, cname, cfg["entry_variant"], len(matches)))
        selected[cname] = matches[0]

    d1 = pc["d1"]
    smc15 = smc_direction(d, 50, False)
    smc1 = smc_direction(d1, 50, False)
    obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
    ob15 = ob_context(d, obcfg)
    ob1 = ob_context(d1, obcfg)

    pos = {t: i for i, t in enumerate(d.index)}
    events = defaultdict(list)

    for combo, cfg in FINAL14_CASES[symbol].items():
        cname = combo_key(combo)
        _, L, S = selected[cname]
        native = NATIVE[cname]
        for side, ser in (("L", L), ("S", S)):
            for ot in ser.index[ser.fillna(False)]:
                et = ot + pd.Timedelta("45min") if native == "1h" else ot
                if et not in pos:
                    continue
                if native == "1h":
                    sd = int(smc1.loc[ot]) if ot in smc1.index else 0
                    blocked = bool(ob1.loc[ot, "buy_blocked" if side == "L" else "sell_blocked"]) if ot in ob1.index else False
                else:
                    sd = int(smc15.loc[ot]) if ot in smc15.index else 0
                    blocked = bool(ob15.loc[ot, "buy_blocked" if side == "L" else "sell_blocked"]) if ot in ob15.index else False
                if approved(side, sd, blocked, cfg["layer2"]):
                    events[int(combo)].append((pos[et], side, ot, "FINAL14"))

    for combo, cfg in NEW6_CASES[symbol].items():
        long1, short1 = _new6_filtered_signals(d1, combo, cfg, smc1, ob1)
        for side, ser in (("L", long1), ("S", short1)):
            for ot in ser.index[ser.fillna(False)]:
                et = ot + pd.Timedelta("45min")
                if et in pos:
                    events[int(combo)].append((pos[et], side, ot, "NEW6"))

    for combo in events:
        events[combo].sort(key=lambda x: x[0])
    return d, events



# Deterministic OHLC simulation; never touches BingX trading API.
# Hard TP stays per combo; trailing separately activates after a 0.5% favorable
# move from entry and uses its own 0.5% callback.
ACTIVATION_PCT = 0.005
CALLBACK_RATE = 0.005
WINDOWS = [("3M",3),("6M",6),("1Y",12),("3Y",36),("5Y",60)]


def check_dataset(d, symbol):
    assert len(d) >= 190000, (symbol, "insufficient history", len(d))
    idx = d.index
    if idx.has_duplicates:
        raise ValueError((symbol, "duplicate 15m opens"))
    delta = idx[1:] - idx[:-1]
    if not bool((delta == pd.Timedelta("15min")).all()):
        raise ValueError((symbol, "missing 15m intervals"))
    assert idx[0] <= pd.Timestamp("2021-01-01T00:00:00Z"), (symbol, str(idx[0]))
    assert idx[-1] >= pd.Timestamp("2026-09-20T23:45:00Z"), (symbol,str(idx[-1]))


def simulate_case(d, events, cfg, start_i, end_i, *, mode="split"):
    """Independent hard TP and 0.5%-from-entry trailing leg.

    Assumes SL before any profitable exit within ambiguous OHLC candles.
    Do not fabricate trailing execution on its activation candle. A trailing
    fill BEFORE TP closes only its half; pending hard TP remains for the rest.
    """
    h = d.high.to_numpy(dtype=float)
    l = d.low.to_numpy(dtype=float)
    c = d.close.to_numpy(dtype=float)
    idx = d.index
    ev = [e for e in events if start_i <= e[0] < end_i]
    rows = []
    p = 0
    while p < len(ev):
        ei, side, ot, strategy = ev[p]
        entry = float(c[ei])
        direction = 1 if side == "L" else -1
        tp = entry*(1+direction*float(cfg["tp_pct"]))
        sl = entry*(1-direction*float(cfg["sl_pct"]))
        activate = entry*(1+direction*ACTIVATION_PCT)
        active = False
        best = activate
        fixed_done = False
        trailing_done = False
        legs = []
        end_bar = end_i-1

        for i in range(ei+1,end_i):
            stop_hit = l[i]<=sl if direction>0 else h[i]>=sl
            tp_hit = h[i]>=tp if direction>0 else l[i]<=tp
            favorable = h[i] if direction>0 else l[i]
            adverse = l[i] if direction>0 else h[i]

            # Remaining full-size hard SL, reduced to own remaining qty
            # after either 50% exit; never assume an unprotected remainder.
            remaining = 1 - sum(w for w,px,reason in legs)
            if stop_hit:
                legs.append((remaining,sl,"SL_FULL" if remaining==1 else "SL_AFTER_PARTIAL"))
                end_bar = i
                break

            if mode=="hard":
                if tp_hit:
                    legs.append((1.0,tp,"TP_FULL"))
                    end_bar = i
                    break
                continue

            if not active:
                activation_hit = (favorable>=activate if direction>0 else favorable<=activate)
                if activation_hit:
                    active=True
                    best=float(favorable)
                    # A TP reached in the activation bar is independently
                    # valid. Trailing close on that same bar is NOT inferable.
                    if tp_hit:
                        legs.append((0.5,tp,"TP_HALF"))
                        fixed_done=True
                    continue
                continue

            # Both orders may be live. On ambiguous same-bar high/low,
            # defer the favorable TP if trailing threshold also touched.
            if not trailing_done:
                threshold=best*(1-direction*CALLBACK_RATE)
                hit=adverse<=threshold if direction>0 else adverse>=threshold
                if hit:
                    legs.append((0.5,threshold,"TRAILING"))
                    trailing_done=True
            if not fixed_done and not (trailing_done and
                    legs and legs[-1][2]=="TRAILING"):
                if tp_hit:
                    legs.append((0.5,tp,"TP_HALF"))
                    fixed_done=True
            if trailing_done and fixed_done:
                end_bar=i
                break
            if not trailing_done:
                best=max(best,float(favorable)) if direction>0 else min(best,float(favorable))
        else:
            remaining=1-sum(w for w,px,reason in legs)
            if remaining>0:
                legs.append((remaining,float(c[end_bar]),"END_MTM"))

        assert abs(sum(w for w,_,_ in legs)-1)<1e-9,legs
        qty=NOTIONAL/entry
        gross=sum(direction*(px-entry)*qty*w for w,px,_ in legs)
        fees=NOTIONAL*FEE+sum(px*qty*w*FEE for w,px,_ in legs)
        rows.append({
            "entry_time":str(idx[ei]),"exit_time":str(idx[end_bar]),
            "symbol":"","combo":"","side":side,"strategy":strategy,
            "entry":entry,"tp":tp,"sl":sl,
            "trailing_activation":activate,
            "legs":[{"fraction":w,"price":px,"reason":reason} for w,px,reason in legs],
            "exit_reason":legs[-1][2],
            "net_pnl":round(gross-fees,8),"fees":round(fees,8),
            "bars_held":int(end_bar-ei),
        })
        p+=1
        while p<len(ev) and ev[p][0]<=end_bar:
            p+=1
    return rows


def metrics(rows):
    pnls = [float(r["net_pnl"]) for r in rows]
    equity = INITIAL_EQUITY
    peak = equity
    drawdown = 0.
    for r in sorted(rows,key=lambda r:r["exit_time"]):
        equity += float(r["net_pnl"])
        peak = max(peak,equity)
        drawdown = max(drawdown,peak-equity)
    n = len(rows)
    return {
        "trades": n,
        "wins": sum(x > 0 for x in pnls),
        "losses": sum(x < 0 for x in pnls),
        "win_rate_pct": round(100 * sum(x>0 for x in pnls)/n,2) if n else None,
        "net_pnl_usdt": round(sum(pnls),4),
        "fees_usdt": round(sum(r["fees"] for r in rows),4),
        "max_dd_realized_usdt": round(drawdown,4),
        "tp_half_count": sum(any(x["reason"]=="TP_HALF" for x in r["legs"]) for r in rows),
        "trailing_close_count": sum(any(x["reason"]=="TRAILING" for x in r["legs"]) for r in rows),
        "sl_full_count": sum(r["exit_reason"]=="SL_FULL" for r in rows),
        "sl_after_tp_count": sum(r["exit_reason"]=="SL_AFTER_PARTIAL" for r in rows),
        "trailing_first_count": sum((r["legs"][0]["reason"]=="TRAILING") for r in rows),
    }


def main():
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--btc", required=True)
    ap.add_argument("--eth", required=True)
    ap.add_argument("--out-dir", default="backtest_results")
    args=ap.parse_args()
    outdir=Path(args.out_dir)
    outdir.mkdir(parents=True,exist_ok=True)
    datas={}
    for symbol,path in (("BTC-USDT",args.btc),("ETH-USDT",args.eth)):
        print("BUILD_SIGNALS",symbol,flush=True)
        raw=pd.read_pickle(path)
        d,events=build_events(symbol,raw)
        check_dataset(d,symbol)
        datas[symbol]=(d,events)
        print("READY",symbol,len(d),len(events),str(d.index[0]),str(d.index[-1]),flush=True)
    end=min(d.index[-1] for d,_ in datas.values())+pd.Timedelta("15min")
    assert end == pd.Timestamp("2026-09-21T00:00:00Z"),str(end)
    summary=[]
    trades=[]
    for label,months in WINDOWS:
        start=end-pd.DateOffset(months=months)
        for symbol,(d,events) in datas.items():
            start_i=int(d.index.searchsorted(start))
            end_i=int(d.index.searchsorted(end))
            for combo in sorted(set(FINAL14_CASES[symbol])|set(NEW6_CASES[symbol])):
                cfg=get_case(symbol,combo)
                key=combo_key(combo)
                split=simulate_case(d,events.get(combo,[]),cfg,start_i,end_i,mode="split")
                hard=simulate_case(d,events.get(combo,[]),cfg,start_i,end_i,mode="hard")
                for row in split:
                    row.update({"window":label,"symbol":symbol,"combo":key})
                trades.extend(split)
                summary.append({
                    "window":label,"symbol":symbol,"combo":key,
                    "combo_id":int(combo),"start_utc":str(start),"end_exclusive_utc":str(end),
                    **metrics(split),
                    "hard_tp_pnl_usdt":metrics(hard)["net_pnl_usdt"],
                    "delta_vs_hard_usdt":round(metrics(split)["net_pnl_usdt"]-metrics(hard)["net_pnl_usdt"],4),
                })
        total=[r for r in trades if r["window"]==label]
        print("WINDOW_RESULT="+json.dumps({"window":label,**metrics(total)},separators=(",",":")),flush=True)
    summary.sort(key=lambda r:(WINDOWS.index((r["window"],next(n for w,n in WINDOWS if w==r["window"]))),r["symbol"],r["combo_id"]))
    pd.DataFrame(summary).to_csv(outdir/"per_combo_windows.csv",index=False)
    pd.DataFrame(trades).drop(columns=["legs"]).to_csv(outdir/"trade_details.csv",index=False)
    with (outdir/"trade_details.json").open("w") as f:
        json.dump(trades,f,ensure_ascii=True,separators=(",",":"))
    output={
        "ok":True,
        "strategy":"20FINAL_2026-09-25",
        "exit_mode":"50% hard TP per combo + 50% exchange-native trailing activating at entry +/- 0.5%; callback 0.5%; hard SL resized after either exit",
        "data_source":"RUN17 hybrid Binance 2021-01-01..2026-03-31 + BingX 2026-04-01..2026-09-20",
        "end_exclusive_utc":str(end),
        "notional_per_trade_usdt":NOTIONAL,
        "fee_per_side":FEE,
        "activation_pct":ACTIVATION_PCT,
        "callback_rate":CALLBACK_RATE,
        "assumptions":"OHLC 15m; no exit on entry; SL-first ambiguity; trailing evaluated on prior best; no same-bar trailing activation close; trailing may close before TP; no slippage/funding/liquidation/API failure/lot-size rounding",
        "combo_results":summary,
        "windows":[{"window":w,**metrics([r for r in trades if r["window"]==w])} for w,_ in WINDOWS]
    }
    with (outdir/"summary.json").open("w") as f:
        json.dump(output,f,indent=2)
    print("RESULT_JSON="+json.dumps({"ok":True,"windows":output["windows"],"combo_results":summary},separators=(",",":")),flush=True)


if __name__=="__main__":
    main()
