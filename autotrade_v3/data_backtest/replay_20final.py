"""Local-only 20FINAL signal replay. Reuses V2 pure research indicator code."""
from __future__ import annotations
import argparse, csv, hashlib, json, subprocess, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS=("BTC-USDT","ETH-USDT")
M15_MS=900_000
WARMUP=3400

def modules():
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/"autotrade_v2"))
    from app.strategy.final14_config import FINAL14_CASES,NEW6_CASES,case_name
    from app.strategy.final14_exact_strategy import _new6_raw_signals,_new6_filtered_signals
    from app.strategy.final14_research.layer3_long import precompute,entry_variants,NATIVE
    from app.strategy.final14_research.smc_structure import smc_direction
    from app.strategy.final14_research.smc_ob import ob_context,OBConfig
    from app.strategy.final14_research.two_trail_layer12 import approved
    return locals()

def digest(rows):
    return hashlib.sha256(json.dumps(rows,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()

def row_event(sym,combo,name,native,side,ot,pos,sd,blocked,passed,d):
    import pandas as pd
    last_open=ot+(pd.Timedelta(minutes=45) if native=="1h" else pd.Timedelta(0))
    if d.index[pos]!=last_open:
        raise AssertionError("Cannot publish signal before native candle close")
    close_ms=int(d.index[pos].value//1_000_000+M15_MS-1)
    return dict(symbol=sym,combo_id=int(combo),combo=name,timeframe=native,
                side="BUY" if side=="L" else "SELL",
                native_open_ms=int(ot.value//1_000_000),signal_close_ms=close_ms,
                bar_position=pos,smc_dir=int(sd),ob_blocked=bool(blocked),
                approved=bool(passed),warmup_ready=pos>=WARMUP-1,
                price_provider_at_close=str(d.iloc[pos].get("provider","UNKNOWN")))

def replay_symbol(sym,d,m=None):
    import pandas as pd
    m=m or modules()
    if not isinstance(d.index,pd.DatetimeIndex) or d.index.tz is None:
        raise ValueError("Need timestamp-indexed UTC M15 candles")
    d=d.tz_convert("UTC")
    t=d.index.asi8//1_000_000
    if len(d)<WARMUP or d.index.has_duplicates or not d.index.is_monotonic_increasing or (t%M15_MS).any() or (t[1:]-t[:-1]!=M15_MS).any():
        raise ValueError("M15 gapped, misaligned or insufficient live warmup")
    configs=m["FINAL14_CASES"][sym]
    wanted={("TIER" if c==11 else f"C{c}"):{v["entry_variant"]}
            for c,v in configs.items()}
    pc=m["precompute"](d,wanted)
    selected=m["entry_variants"](pc,wanted)
    d1=pc["d1"]
    smc15=m["smc_direction"](d,50,False)
    smc1=m["smc_direction"](d1,50,False)
    obcfg=m["OBConfig"](pivot_len=5,search_bars=12,max_age=80,danger_atr=0.5)
    ob15=m["ob_context"](d,obcfg)
    ob1=m["ob_context"](d1,obcfg)
    posmap={ts:i for i,ts in enumerate(d.index)}
    events=[];counts={}
    for combo,cfg in sorted(configs.items()):
        name="TIER" if combo==11 else f"C{combo}"
        native=m["NATIVE"][name]
        variants=[x for x in selected[name] if x[0]==cfg["entry_variant"]]
        if len(variants)!=1:raise RuntimeError(f"Missing locked variant {sym}/{name}")
        _,L,S=variants[0]
        rows=[]
        for side,series in (("L",L),("S",S)):
            for ot in series.index[series.fillna(False)]:
                last=ot+(pd.Timedelta(minutes=45) if native=="1h" else pd.Timedelta(0))
                if last not in posmap:continue
                sd=int((smc1 if native=="1h" else smc15).loc[ot])
                ob=(ob1 if native=="1h" else ob15).loc[ot]
                blocked=bool(ob["buy_blocked" if side=="L" else "sell_blocked"])
                ok=bool(m["approved"](side,sd,blocked,cfg["layer2"]))
                rows.append((posmap[last],side,ot,sd,blocked,ok))
        rows.sort(key=lambda x:x[0])
        raw=[[int(p),side,int(ot.value),int(sd),bool(blocked)] for p,side,ot,sd,blocked,ok in rows]
        passed=[x for x,r in zip(raw,rows) if r[5]]
        counts[name]=dict(combo_id=combo,raw_events=len(raw),passed_events=len(passed),
                          raw_sha256=digest(raw),passed_sha256=digest(passed),
                          timeframe=native)
        events.extend(row_event(sym,combo,name,native,side,ot,p,sd,blocked,ok,d)
                      for p,side,ot,sd,blocked,ok in rows)
    for combo,cfg in sorted(m["NEW6_CASES"][sym].items()):
        name=m["case_name"](combo)
        L,S=m["_new6_raw_signals"](d1,combo)
        PL,PS=m["_new6_filtered_signals"](d1,combo,cfg,smc1,ob1)
        raw=[];passed=[]
        for ot in d1.index:
            lng,shrt=bool(L.loc[ot]),bool(S.loc[ot])
            if lng==shrt:continue
            last=ot+pd.Timedelta(minutes=45)
            if last not in posmap:continue
            side="L" if lng else "S"
            sd=int(smc1.loc[ot])
            blocked=bool(ob1.loc[ot,"buy_blocked" if lng else "sell_blocked"])
            ok=bool(PL.loc[ot] or PS.loc[ot])
            item=[int(ot.value),side,sd,blocked]
            raw.append(item)
            if ok:passed.append(item)
            events.append(row_event(sym,combo,name,"1h",side,ot,posmap[last],sd,blocked,ok,d))
        counts[name]=dict(combo_id=combo,raw_events=len(raw),passed_events=len(passed),
                          raw_sha256=digest(raw),passed_sha256=digest(passed),timeframe="1h")
    if len(counts)!=len(configs)+len(m["NEW6_CASES"][sym]):
        raise AssertionError("20FINAL selected combo missing")
    events.sort(key=lambda e:(e["signal_close_ms"],e["combo_id"],e["side"]))
    return events,counts

def m15_from_catalog(catalog,symbol,start,end):
    import duckdb,pandas as pd
    def ms(s):return int(datetime.strptime(s,"%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()*1000)
    con=duckdb.connect(str(catalog),read_only=True)
    try:
        data=con.execute("""SELECT floor(open_ms/900000)::BIGINT*900000 AS open_ms,
          arg_min(open,open_ms) AS open,max(high) AS high,min(low) AS low,
          arg_max("close",open_ms) AS close,sum(volume) AS volume,
          count(*) AS m1_count,
          CASE WHEN count(DISTINCT provider)=1 THEN min(provider) ELSE 'MIXED' END AS provider
          FROM v3_m1 WHERE symbol=? AND open_ms>=? AND open_ms<?
          GROUP BY 1 ORDER BY 1""",
          [symbol.replace("-",""),ms(start),ms(end)]).df()
    finally:con.close()
    if data.empty or (data["m1_count"]!=15).any():
        raise ValueError("Incomplete 15-minute bucket; no forward fill allowed")
    data.index=pd.to_datetime(data["open_ms"],unit="ms",utc=True)
    return data[["open","high","low","close","volume","provider"]]

def compare_b1(reference,actual):
    errors=[]
    for sym in SYMBOLS:
        expected=reference["results"].get(sym,{})
        got=actual.get(sym,{})
        if set(expected)!=set(got):
            errors.append(dict(symbol=sym,field="combo_set"));continue
        for combo,ref in expected.items():
            for key in ("raw_events","passed_events","raw_sha256","passed_sha256"):
                if ref[key]!=got[combo].get(key):errors.append(dict(symbol=sym,combo=combo,field=key))
    return dict(status="PASS" if not errors else "FAIL",differences=errors)

def run(catalog,out,start,end):
    out.mkdir(parents=True,exist_ok=True)
    m=modules();events=[];counts={}
    for sym in SYMBOLS:
        frame=m15_from_catalog(catalog,sym,start,end)
        rows,c=replay_symbol(sym,frame,m)
        events+=rows;counts[sym]=c
    fields=["symbol","combo_id","combo","timeframe","side","native_open_ms",
            "signal_close_ms","bar_position","smc_dir","ob_blocked","approved",
            "warmup_ready","price_provider_at_close"]
    with (out/"signal_events.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(events)
    month=Counter()
    for x in events:
        if x["approved"]:
            day=datetime.fromtimestamp(x["signal_close_ms"]/1000,tz=timezone.utc).strftime("%Y-%m")
            month[(x["symbol"],x["combo"],day,"READY" if x["warmup_ready"] else "WARMUP_ONLY")]+=1
    root=Path(__file__).resolve().parents[2]
    relevant=[root/"autotrade_v2/app/strategy/final14_config.py",
              root/"autotrade_v2/app/strategy/final14_exact_strategy.py",
              *sorted((root/"autotrade_v2/app/strategy/final14_research").glob("*.py"))]
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in relevant}
    try:gitsha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):gitsha="NOT_AVAILABLE"
    report=dict(status="SIGNAL_REPLAY_RESEARCH",input="M1_DERIVED_M15_HYBRID",
        b1_same_run17_input="NOT_EVALUATED_DIFFERENT_INPUT",window_utc=[start,end],
        source_git_sha=gitsha,source_code_sha256=hashes,counts=counts,
        approved_by_month=[dict(symbol=s,combo=c,month_utc=t,readiness=r,count=n)
          for (s,c,t,r),n in sorted(month.items())],
        caveat="Candidates are not closed trades; M1 venue changes affect later rolling features.")
    (out/"replay_report.json").write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
    return report

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--catalog",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    p.add_argument("--start",default="2023-11-01")
    p.add_argument("--end",default="2026-11-01")
    a=p.parse_args()
    try:r=run(a.catalog,a.out_dir,a.start,a.end)
    except (ValueError,OSError,KeyError,RuntimeError) as exc:p.exit(2,f"V3-02 failed: {exc}\n")
    print(json.dumps({"status":r["status"],"counts":{
        s:{c:v["passed_events"] for c,v in x.items()} for s,x in r["counts"].items()}},sort_keys=True))
    return 0
if __name__=="__main__":raise SystemExit(main())
