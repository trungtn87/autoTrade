"""AutoTrade V3-04 — offline, read-only evidence reconstruction.

Consumes a *sanitized local CSV export*, never connects to Supabase or BingX.
Order IDs are matched by exact symbol and id, not price/time proximity. Evidence
is deliberately insufficient for "verified" unless full identities and fills
can be proven and position allocation is not ambiguous.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = 1
M1_START = 1698796800000  # 2023-11-01T00:00:00Z
M1_END = 1793491200000  # 2026-11-01T00:00:00Z
REQUIRED = {
    "signal_links": {"entry_order_id", "signal_id", "symbol", "side", "combo", "signal_time_ms"},
    "bingx_orders": {"order_id", "symbol", "side", "position_side", "type", "status", "filled_qty", "position_id"},
    "bingx_fills": {"trade_id", "order_id", "symbol", "side", "position_side", "qty", "price", "fee", "realized_pnl", "timestamp_ms"},
    "bingx_income": {"income_key", "symbol", "income_type", "amount", "asset", "timestamp_ms", "trade_id"},
    "processed_targets": {"event_id", "target", "payload"},
}
CHILD_TYPES = {"sl": "STOP_MARKET", "tp": "TAKE_PROFIT_MARKET", "trailing": "TRAILING_STOP_MARKET"}
RESULT_CODES = ("ENTRY_UNRESOLVED", "EXIT_UNRESOLVED", "MERGED_OR_SHARED_UNRESOLVED",
                "EXIT_FILL_SUPPORTED_FINANCE_INCONCLUSIVE", "EXIT_FILL_SUPPORTED_ESTIMATED")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def money(value: object) -> Decimal | None:
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def stamp(value: str) -> int | None:
    try:
        x = int(value)
        return x if 0 < x < 4102444800000 else None
    except (ValueError, TypeError):
        return None


def _read(root: Path, name: str) -> list[dict]:
    path = root / (name + ".csv")
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"required sanitized CSV missing/unsafe: {name}.csv")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or [])
        if not REQUIRED[name] <= fields:
            raise ValueError(f"{name}: missing columns {sorted(REQUIRED[name]-fields)}")
        records = list(reader)
        if any(None in x for x in records):
            raise ValueError(f"{name}: malformed record")
        return records


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def seal(root: Path, exported_at_utc: str) -> dict:
    dt = datetime.fromisoformat(exported_at_utc.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
        raise ValueError("exported_at_utc requires ISO-8601 UTC, e.g. 2026-11-10T04:30:00Z")
    files = {}
    for name in REQUIRED:
        records = _read(root, name)
        files[name] = {"rows": len(records), "sha256": sha256(root / (name + ".csv"))}
    m = {"schema_version": SCHEMA, "exported_at_utc": exported_at_utc,
         "read_only": True, "files": files}
    _write_json(root / "manifest-v3-04.json", m)
    return m


def load(root: Path) -> tuple[dict, dict]:
    p = root / "manifest-v3-04.json"
    if p.is_symlink() or not p.is_file():
        raise ValueError("manifest-v3-04.json missing; seal the sanitized offline export first")
    m = json.loads(p.read_text(encoding="utf-8"))
    if m.get("schema_version") != SCHEMA or m.get("read_only") is not True or set(m.get("files", {})) != set(REQUIRED):
        raise ValueError("manifest version or expected table set mismatch")
    rows = {}
    for name, meta in m["files"].items():
        if sha256(root / (name + ".csv")) != meta.get("sha256"):
            raise ValueError(f"export checksum mismatch: {name}")
        rows[name] = _read(root, name)
        if len(rows[name]) != meta.get("rows"):
            raise ValueError(f"export row count mismatch: {name}")
    return m, rows


def _payload(s: str) -> dict:
    try:
        obj = json.loads(s or "{}")
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _compatible_states(states: list[dict]) -> tuple[dict, list[str]]:
    """A missing key is not contradictory. Two different nonempty values are."""
    if not states:
        return {}, ["STATE_NOT_EXPORTED"]
    ordered = sorted(states, key=lambda x: x[0] != "bingx_state")
    primary = dict(ordered[0][1])
    # If the preferred bingx_state snapshot lacks a field but the account
    # record has it, import only that missing field. Never overwrite a value.
    for _, snapshot in ordered[1:]:
        for field in ("exit_mode", "position_id", "shared_position", "merged_reconcile"):
            if primary.get(field) in (None, "") and snapshot.get(field) not in (None, ""):
                primary[field] = snapshot[field]
        if not isinstance(primary.get("protection"), dict):
            primary["protection"] = snapshot.get("protection")
    reasons = []
    for name in ("exit_mode", "position_id", "shared_position"):
        values = {json.dumps(s.get(name), sort_keys=True) for _, s in states if s.get(name) not in (None, "")}
        if len(values) > 1:
            reasons.append("STATE_CONFLICT_" + name.upper())
    # Child IDs are authoritative only if all nonempty copies agree.
    for leg in CHILD_TYPES:
        values = {str(s["protection"][leg]["order_id"])
                  for _, s in states if isinstance(s.get("protection"), dict)
                  and isinstance(s["protection"].get(leg), dict)
                  and s["protection"][leg].get("order_id") not in (None, "")}
        if len(values) > 1:
            reasons.append("STATE_CONFLICT_CHILD_" + leg.upper())
    return primary, reasons


def _index_unique(rows: list[dict], key) -> tuple[dict, set]:
    index, dup = {}, set()
    for row in rows:
        k = key(row)
        if k in index:
            dup.add(k)
        else:
            index[k] = row
    return index, dup


def _group(rows: list[dict], key) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return groups


def _fill_match(fills: list[dict], order: dict) -> tuple[Decimal, list[str]]:
    if not fills:
        return Decimal(0), ["EXACT_FILL_MISSING"]
    reasons = []
    t_ids = [f["trade_id"] for f in fills]
    if len(t_ids) != len(set(t_ids)):
        reasons.append("DUPLICATE_TRADE_ID")
    qty = Decimal(0)
    for f in fills:
        q = money(f.get("qty"))
        p = money(f.get("price"))
        if q is None or q <= 0 or p is None or p <= 0:
            reasons.append("INVALID_FILL_QTY_OR_PRICE")
            continue
        qty += q
        if f.get("side") != order.get("side") or f.get("position_side") != order.get("position_side"):
            reasons.append("FILL_SIDE_POSITION_MISMATCH")
        if stamp(f.get("timestamp_ms", "")) is None:
            reasons.append("FILL_TIME_INVALID")
    requested = money(order.get("filled_qty"))
    if requested is None or requested <= 0:
        reasons.append("ORDER_FILLED_QTY_INVALID")
    elif abs(qty - requested) > max(Decimal("0.00000001"), requested * Decimal("0.000001")):
        reasons.append("FILL_QTY_DIFFERS_FROM_ORDER")
    if order.get("status") != "FILLED":
        reasons.append("ORDER_STATUS_NOT_FILLED")
    return qty, sorted(set(reasons))


def _date(ms: str) -> str:
    x = stamp(ms)
    return datetime.fromtimestamp(x / 1000, tz=timezone.utc).strftime("%Y-%m") if x else "UNKNOWN"


def wilson95(successes: int, count: int) -> list[float] | None:
    """Descriptive Wilson binomial interval, not independent-trade inference."""
    if not count:
        return None
    z = 1.959963984540054
    p = successes / count
    denom = 1 + z*z/count
    centre = (p + z*z/(2*count)) / denom
    span = z * math.sqrt(p*(1-p)/count + z*z/(4*count*count))/denom
    return [round(max(0,centre-span),6), round(min(1,centre+span),6)]


def _load_sim(path: Path | None) -> tuple[dict, int]:
    if path is None:
        return {}, 0
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing/unsafe V3-03 scenario file")
    found, ambiguous = {}, set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            k = (obj.get("symbol"), obj.get("combo"), obj.get("side"), stamp(str(obj.get("signal_close_ms", ""))))
            if k in found:
                ambiguous.add(k)
            found[k] = obj
    for k in ambiguous:
        del found[k]
    return found, len(ambiguous)


def reconcile(root: Path, output: Path, *, scenario_split: Path | None = None,
              scenario_hard: Path | None = None, holdout_start: str = "2026-10-15") -> dict:
    manifest, tables = load(root)
    if output.resolve().is_relative_to(root.resolve()):
        # writing report inside raw export would break its read-only archive boundary
        raise ValueError("report directory must be outside read-only export")
    try:
        holdout = int(datetime.strptime(holdout_start, "%Y-%m-%d")
                      .replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError as exc:
        raise ValueError("invalid holdout date") from exc
    links = tables["signal_links"]
    orders, dup_orders = _index_unique(tables["bingx_orders"], lambda r: (r["symbol"],r["order_id"]))
    fills = _group(tables["bingx_fills"], lambda r: (r["symbol"], r["order_id"]))
    states = _group(tables["processed_targets"], lambda r: r["event_id"])
    income = tables["bingx_income"]
    sig_dup = Counter((r["symbol"], r["signal_id"]) for r in links)
    protection_by_event = {}
    child_users = defaultdict(set)
    for link in links:
        event = link["signal_id"]
        state, _ = _compatible_states([(r["target"], _payload(r["payload"])) for r in states[event]])
        child = {}
        protection = state.get("protection")
        if isinstance(protection, dict):
            for name in CHILD_TYPES:
                part = protection.get(name)
                if isinstance(part, dict):
                    order_id = str(part.get("order_id") or "")
                    if order_id:
                        child[name] = order_id
                        child_users[(link["symbol"], order_id)].add((link["symbol"], event))
        protection_by_event[(link["symbol"],event)] = child
    sim_split, dup_split = _load_sim(scenario_split)
    sim_hard, dup_hard = _load_sim(scenario_hard)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    live_intervals = []
    for link in links:
        symbol = link["symbol"]
        event_id = link["signal_id"]
        event_key = hashlib.sha256(f"{symbol}|{event_id}|{link['entry_order_id']}".encode()).hexdigest()[:18]
        reasons = set()
        order_key = (symbol, link["entry_order_id"])
        order = orders.get(order_key)
        if order_key in dup_orders or not order:
            reasons.add("ENTRY_ORDER_ID_MISSING_OR_DUPLICATE")
        if sig_dup[(symbol, event_id)] != 1:
            reasons.add("MULTIPLE_ENTRIES_PER_SIGNAL")
        if order and (order["side"] != link["side"] or order["type"] not in ("MARKET","LIMIT")
                      or order["position_side"] != ("LONG" if link["side"]=="BUY" else "SHORT")):
            reasons.add("ENTRY_ORDER_IDENTITY_INVALID")
        if order and not order.get("position_id"):
            reasons.add("ENTRY_POSITION_ID_MISSING")
        entry_fills = fills.get(order_key, [])
        entry_qty = Decimal(0)
        if order:
            entry_qty, errors = _fill_match(entry_fills, order)
            reasons.update("ENTRY_"+e for e in errors)
        snapshots = [(s["target"],_payload(s["payload"])) for s in states[event_id]]
        state, errors = _compatible_states(snapshots)
        reasons.update(errors)
        if state.get("merged_reconcile") or state.get("shared_position"):
            reasons.add("MERGED_OR_SHARED_POSITION")
        mode = state.get("exit_mode")
        if mode not in ("split_trailing", "hard_tp"):
            reasons.add("EXIT_MODE_NOT_VERIFIED")
        child_map = protection_by_event.get((symbol,event_id),{})
        mandatory = {"sl", "tp", "trailing"} if mode=="split_trailing" else {"sl", "tp"}
        if not mandatory <= set(child_map):
            reasons.add("PROTECTION_CHILD_IDS_INCOMPLETE")
        if len(child_map.values()) != len(set(child_map.values())):
            reasons.add("EXIT_PROTECTION_ORDER_REUSED_FOR_MULTIPLE_LEGS")
        exit_fills = []
        for name, child_id in child_map.items():
            k=(symbol,child_id)
            if len(child_users[k])>1:
                reasons.add("EXIT_ORDER_SHARED_BETWEEN_COMBOS")
            if k in dup_orders or k not in orders:
                reasons.add("EXIT_ORDER_ID_MISSING_OR_DUPLICATE")
                continue
            ch = orders[k]
            if (ch["type"] != CHILD_TYPES[name]
                or ch["side"] == link["side"] or ch["symbol"]!=symbol
                or ch["position_side"] != ("LONG" if link["side"]=="BUY" else "SHORT")):
                reasons.add("EXIT_ORDER_IDENTITY_INVALID")
                continue
            if not ch.get("position_id") or (order and ch["position_id"] != order.get("position_id")):
                reasons.add("EXIT_POSITION_ID_MISMATCH")
                continue
            leg_fills = fills.get(k,[])
            if leg_fills:
                q, errs = _fill_match(leg_fills,ch)
                # Old CANCELLED records can contain partial fills, but that is not
                # a clean enough record to prove per-combo ownership.
                reasons.update("EXIT_"+e for e in errs)
                exit_fills.extend(leg_fills)
            elif ch["status"] == "FILLED":
                reasons.add("EXIT_FILLED_ORDER_HAS_NO_TRADE_FILL")
        exit_qty = sum((money(f["qty"]) or Decimal(0) for f in exit_fills),Decimal(0))
        if not exit_fills:
            reasons.add("NO_EXIT_FILLS_FROM_EXACT_CHILD_IDS")
        elif abs(exit_qty-entry_qty)>max(Decimal("0.00000001"),entry_qty*Decimal("0.000001")):
            reasons.add("EXIT_SUM_QTY_NOT_EQUAL_ENTRY")
        # All fee/pnl amounts are exchange-derived fill details, not necessarily
        # venue-net due to unallocated funding and position merges.
        financials = [(money(f.get("fee")),money(f.get("realized_pnl"))) for f in entry_fills+exit_fills]
        if any(a is None or b is None for a,b in financials):
            reasons.add("FILL_FEE_OR_PNL_UNKNOWN")
        exit_times = [stamp(f["timestamp_ms"]) for f in exit_fills]
        entry_times = [stamp(f["timestamp_ms"]) for f in entry_fills]
        if entry_times and exit_times and any(e is None or e<min(entry_times) for e in exit_times):
            reasons.add("EXIT_TIME_BEFORE_ENTRY")
        # Current V2 incomes have zero exact fill trade_id joins in production.
        # Never attribute them by symbol + timestamp/nearby price.
        income_matches = [f for f in income if f["symbol"]==symbol and
                          f["trade_id"] in {x["trade_id"] for x in entry_fills+exit_fills}]
        if not income_matches:
            reasons.add("INCOME_CANNOT_BE_LINKED_BY_TRADE_ID")
        t=stamp(link.get("signal_time_ms", ""))
        if t is None:
            reasons.add("SIGNAL_TIMESTAMP_INVALID")
        window="IN_V3_M1" if t is not None and M1_START<=t<M1_END else "OUTSIDE_V3_M1"
        if window!="IN_V3_M1":
            reasons.add("OUTSIDE_V3_M1_WINDOW")
        # In the last-price dataset only 2026-10-28 is BingX native, all other
        # live signal windows use Binance or are entirely outside archive.
        venue="BINGX" if t and 1793145600000<=t<1793232000000 else ("BINANCE" if window=="IN_V3_M1" else "NO_M1")
        if venue=="BINANCE":
            reasons.add("CROSS_VENUE_PRICE_SERIES")
        if venue=="BINGX":
            reasons.add("NATIVE_MARK_OR_TICK_TRIGGER_UNVERIFIED")
        # Even perfectly matched fills cannot establish combo allocation for a
        # shared hedge container without exact owner/child ledger and funding.
        hard = any(x.startswith("ENTRY_") or x.startswith("STATE_CONFLICT") for x in reasons)
        if hard:
            status="ENTRY_UNRESOLVED"
        elif any("MERGED" in x or "SHARED" in x for x in reasons):
            status="MERGED_OR_SHARED_UNRESOLVED"
        elif any(x.startswith("EXIT_") or x.startswith("NO_EXIT_") or x.startswith("PROTECTION_")
                 for x in reasons):
            status="EXIT_UNRESOLVED"
        else:
            status="EXIT_FILL_SUPPORTED_FINANCE_INCONCLUSIVE"
        # Maintain the evidence hierarchy explicitly. None is EXCHANGE_VERIFIED
        # without funding/position allocation and full exact exit evidence.
        comparable = (status=="EXIT_FILL_SUPPORTED_FINANCE_INCONCLUSIVE"
                      and venue=="BINGX" and not reasons.intersection(
                          {"INCOME_CANNOT_BE_LINKED_BY_TRADE_ID","NATIVE_MARK_OR_TICK_TRIGGER_UNVERIFIED"}))
        mode_candidate = sim_split if mode=="split_trailing" else sim_hard if mode=="hard_tp" else {}
        lookup=(symbol,link["combo"],link["side"],t)
        sim=mode_candidate.get(lookup)
        if sim is None:
            reasons.add("EXACT_SIM_SIGNAL_NOT_MATCHED" if (scenario_split or scenario_hard)
                        else "SIMULATION_FILES_NOT_PROVIDED")
        else:
            if sim.get("scenario_quality")!="SIMULATED" or not sim.get("closed_in_both"):
                reasons.add("SIMULATION_PATH_OR_LIFECYCLE_UNCERTAIN")
            if (sim.get("high_first", {}).get("provider_switches", 0) or
                    sim.get("low_first", {}).get("provider_switches", 0)):
                reasons.add("MIXED_PROVIDER_DURING_SIM_POSITION")
        live_intervals.append((symbol, link["side"], min(entry_times) if entry_times else None,
                               max(exit_times) if exit_times else None, len(rows)))
        rows.append({"event_hash":event_key,"symbol":symbol,"combo":link["combo"],
                     "month_utc":_date(link["signal_time_ms"]),
                     "evaluation_set":("OUTSIDE_V3_M1" if window=="OUTSIDE_V3_M1"
                                       else "HOLDOUT" if t>=holdout else "CALIBRATION"),
                     "side":link["side"],"entry_fill_count":len(entry_fills),
                     "exit_fill_count":len(exit_fills),"mode":mode if mode in ("split_trailing","hard_tp") else "UNKNOWN",
                     "source_venue":venue,"window":window,"evidence_status":status,
                     "exact_sim_match":sim is not None,"eligible_paired":comparable and sim is not None,
                     "reason_codes":sorted(reasons),
                     "entry_fill_qty":str(entry_qty),"exit_fill_qty":str(exit_qty)})
    # Even distinct child-order IDs do not prove position separation if same
    # venue+side entries overlap in time. Merge risk requires native evidence.
    for i,(symbol,side,start,end,index) in enumerate(live_intervals):
        if start is None:
            continue
        end_bound = end if end is not None else 4102444800000
        for j,(other_symbol,other_side,other_start,other_end,other_index) in enumerate(live_intervals):
            if j <= i or other_symbol != symbol or other_side != side or other_start is None:
                continue
            other_bound = other_end if other_end is not None else 4102444800000
            if start < other_bound and other_start < end_bound:
                for k in (index,other_index):
                    row = rows[k]
                    row["reason_codes"] = sorted(set(row["reason_codes"]) | {"POTENTIAL_MERGED_POSITION_OVERLAP"})
                    row["evidence_status"] = "MERGED_OR_SHARED_UNRESOLVED"
                    row["eligible_paired"] = False
    # Pairing is an exact join, not a holdout prediction. Count eligibility by
    # month/mode. The live export is a point-in-time sample; no confidence claims
    # are valid with fewer than 30 VERIFIED paired independent holdout exits.
    summaries=defaultdict(lambda: Counter())
    for item in rows:
        k=(item["symbol"],item["month_utc"],item["mode"])
        summaries[k][item["evidence_status"]]+=1
        summaries[k]["total"]+=1
        if item["window"]=="IN_V3_M1":summaries[k]["in_data"]+=1
        if item["exact_sim_match"]:summaries[k]["sim_exact_join"]+=1
        if item["eligible_paired"]:summaries[k]["holdout_eligible"]+=1
    details=output/"reconciled_evidence.jsonl"
    with details.open("w",encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row,sort_keys=True)+"\n")
    paired = sum(row["eligible_paired"] for row in rows)
    holdout_rows = [r for r in rows if r["evaluation_set"]=="HOLDOUT"]
    holdout_pairs = sum(r["eligible_paired"] for r in holdout_rows)
    report={"gate":"C2","status":"INCONCLUSIVE",
            "reason":"NO_30_EXCHANGE_VERIFIED_PAIRED_HOLDOUT_TRADES_AND_NO_EXACT_FUNDING_ALLOCATION",
            "exported_at_utc":manifest["exported_at_utc"],
            "manifest_sha256":sha256(root/"manifest-v3-04.json"),
            "source_rows":{name:meta["rows"] for name,meta in manifest["files"].items()},
            "total_signals":len(rows),"outside_historical_M1":sum(x["window"]=="OUTSIDE_V3_M1" for x in rows),
            "in_historical_M1":sum(x["window"]=="IN_V3_M1" for x in rows),
            "has_entry_fill":sum(x["entry_fill_count"]>0 for x in rows),
            "explicit_exit_mode":sum(x["mode"]!="UNKNOWN" for x in rows),
            "exact_sim_match":sum(x["exact_sim_match"] for x in rows),
            "verified_net_pnl_pair_count":0,
            "exact_exit_fills_per_signal":sum(r["exit_fill_count"]>0 for r in rows),
            "paired_eligible_rate":round(paired/len(rows),6) if rows else None,
            "paired_eligible_wilson95_descriptive":wilson95(paired,len(rows)),
            "holdout_total":len(holdout_rows),"holdout_eligible_paired":holdout_pairs,
            "holdout_wilson95_descriptive":wilson95(holdout_pairs,len(holdout_rows)),
            "gate_c2_a_target":"80% hard / 60% split eligible evidence (not yet satisfied)",
            "gate_c2_b_target":"at least 30 exchange-verified paired holdout trades per exit profile",
            "unattributed_income_count":len(income)-sum(1 for inc in income if any(inc["trade_id"]==f["trade_id"] and inc["symbol"]==f["symbol"] for f in tables["bingx_fills"])),
            "duplicate_sim_records":dup_split+dup_hard,
            "status_counts":dict(Counter(x["evidence_status"] for x in rows)),
            "reason_counts":dict(Counter(reason for row in rows for reason in row["reason_codes"])),
            "per_symbol_month_mode":[dict(symbol=s,month_utc=m,mode=mode,**dict(c))
                                      for (s,m,mode),c in sorted(summaries.items())],
            "holdout_start_utc":holdout_start,
            "finance_note":"No PnL/ROI calculated: income trade IDs do not establish attributable funding or execution net on shared BingX containers.",
            "safety":"READ_ONLY_LOCAL_OFFLINE. NO TRADE/ORDER ENDPOINT OR DB UPDATE."}
    _write_json(output/"gate_c2_report.json",report)
    return report


def main() -> int:
    p=argparse.ArgumentParser(description="AutoTrade V3-04 offline live evidence audit")
    s=p.add_subparsers(dest="cmd",required=True)
    a=s.add_parser("seal")
    a.add_argument("--input-dir",type=Path,required=True)
    a.add_argument("--exported-at-utc",required=True)
    b=s.add_parser("reconcile")
    b.add_argument("--input-dir",type=Path,required=True)
    b.add_argument("--output-dir",type=Path,required=True)
    b.add_argument("--scenario-split",type=Path)
    b.add_argument("--scenario-hard",type=Path)
    b.add_argument("--holdout-start",default="2026-10-15")
    args=p.parse_args()
    try:
        if args.cmd=="seal":
            result=seal(args.input_dir,args.exported_at_utc)
            print(json.dumps({"schema_version":result["schema_version"],"file_counts":{k:v["rows"] for k,v in result["files"].items()}}))
        else:
            result=reconcile(args.input_dir,args.output_dir,scenario_split=args.scenario_split,
                             scenario_hard=args.scenario_hard,holdout_start=args.holdout_start)
            print(json.dumps({k:result[k] for k in ("status","total_signals","explicit_exit_mode","has_entry_fill","exact_sim_match","verified_net_pnl_pair_count")}))
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as exc:
        p.exit(2,f"V3-04 offline error: {exc}\n")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
