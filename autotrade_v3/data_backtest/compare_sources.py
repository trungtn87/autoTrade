"""Compare V3-02 approved signal events by immutable symbol/combo/side/time identity.

A cross-venue comparison is sensitivity (B2), never a same-input parity claim.
"""
from __future__ import annotations
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def utc_ms(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def signatures(paths: list[Path], start_ms: int, end_ms: int) -> set[tuple]:
    found = set()
    for path in paths:
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            needed = {"symbol", "combo", "side", "approved", "signal_close_ms"}
            if not needed.issubset(reader.fieldnames or []):
                raise ValueError(f"missing event columns in {path}")
            for row in reader:
                if str(row["approved"]).lower() not in ("true", "1"):
                    continue
                when = int(row["signal_close_ms"])
                if not start_ms <= when < end_ms:
                    continue
                identity = (row["symbol"], row["combo"], row["side"], when)
                if identity in found:
                    raise ValueError(f"duplicate event identity in {path}")
                found.add(identity)
    return found


def compare(a: set[tuple], b: set[tuple]) -> dict:
    both, union = a & b, a | b
    by_combo = []
    for sym, combo in sorted({(x[0], x[1]) for x in union}):
        old = {x for x in a if x[0] == sym and x[1] == combo}
        new = {x for x in b if x[0] == sym and x[1] == combo}
        common = old & new
        by_combo.append(dict(symbol=sym, combo=combo,
                             baseline=len(old), derived=len(new),
                             common=len(common), baseline_only=len(old-new),
                             derived_only=len(new-old),
                             jaccard_pct=round(len(common)*100/len(old|new), 3)
                             if old|new else None))
    return dict(baseline=len(a), derived=len(b), common=len(both),
                baseline_only=len(a-b), derived_only=len(b-a),
                jaccard_pct=round(len(both)*100/len(union), 3)
                if union else None, per_combo=by_combo)


def run(btc_csv, eth_csv, hybrid_csv, start, switch, end, out):
    if not utc_ms(start) < utc_ms(switch) < utc_ms(end):
        raise ValueError("expected start < venue-switch < end")
    results = {}
    for label, begin, stop in (
        ("same_venue", start, switch),
        ("different_venue", switch, end),
        ("all_overlap", start, end),
    ):
        a = signatures([btc_csv, eth_csv], utc_ms(begin), utc_ms(stop))
        b = signatures([hybrid_csv], utc_ms(begin), utc_ms(stop))
        results[label] = dict(window_utc=[begin,stop], **compare(a,b))
    output = dict(
        status="B2_SOURCE_SENSITIVITY_ONLY_NOT_EXACT_PARITY",
        baseline="RUN17_M15_HYBRID",
        derived="M1_BINANCE_PLUS_BINGX_DERIVED_M15",
        note="Same-time event overlap is not win rate or trade count. "
             "Indicator inputs may remain cross-venue-mixed after switch.",
        periods=results)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(output,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return output


def main():
    p=argparse.ArgumentParser(description="V3-02 source-sensitivity (B2), offline")
    p.add_argument("--run17-btc",type=Path,required=True)
    p.add_argument("--run17-eth",type=Path,required=True)
    p.add_argument("--hybrid-events",type=Path,required=True)
    p.add_argument("--start",default="2024-01-01")
    p.add_argument("--venue-switch",default="2026-04-01")
    p.add_argument("--end",default="2026-09-21")
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    try:
        report=run(a.run17_btc,a.run17_eth,a.hybrid_events,
                   a.start,a.venue_switch,a.end,a.out)
    except (OSError,ValueError) as e:
        p.exit(2,f"B2 comparison failed: {e}\n")
    print(json.dumps({k: {f:v for f,v in data.items() if f not in ("per_combo",)}
                      for k,data in report["periods"].items()},sort_keys=True))
    return 0
if __name__=="__main__":raise SystemExit(main())
