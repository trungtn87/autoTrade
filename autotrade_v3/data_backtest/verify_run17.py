"""Gate B1: compare V3 replay on safe same-input RUN17 M15 CSV to frozen hashes.

Never unpickle data; this uses only CSV conversion produced by existing parity CI.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def verify(btc: Path, eth: Path, reference: Path, output: Path):
    import pandas as pd
    from .replay_20final import replay_symbol, compare_b1
    ref=json.loads(reference.read_text(encoding="utf-8"))
    results={}
    for sym,path in (("BTC-USDT",btc),("ETH-USDT",eth)):
        df=pd.read_csv(path)
        needed={"open_ms","open","high","low","close","volume"}
        if not needed.issubset(df.columns):
            raise ValueError(f"RUN17 M15 CSV missing columns: {path}")
        df.index=pd.to_datetime(df["open_ms"],unit="ms",utc=True)
        events,counts=replay_symbol(sym,df[["open","high","low","close","volume"]])
        results[sym]=counts
    decision=compare_b1(ref,results)
    record=dict(gate="B1",result=decision,
                reference_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
                same_input="RUN17_M15_CSV",
                data_csv_sha256={str(p.name):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (btc,eth)},
                combos_checked=sum(len(x) for x in results.values()),
                note="Same input only. This does not prove any live BingX fills.")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(record,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return record


def main():
    p=argparse.ArgumentParser(description="V3-02 B1 local RUN17 same-input parity")
    p.add_argument("--btc",type=Path,required=True)
    p.add_argument("--eth",type=Path,required=True)
    p.add_argument("--reference",type=Path,default=Path(__file__).with_name("run17_reference.json"))
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:r=verify(a.btc,a.eth,a.reference,a.output)
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        p.exit(2,f"B1 verification error: {exc}\n")
    print(json.dumps({"status":r["result"]["status"],"cases":r["combos_checked"],
                      "difference_count":len(r["result"]["differences"])}))
    return 0 if r["result"]["status"]=="PASS" else 1
if __name__=="__main__":raise SystemExit(main())
