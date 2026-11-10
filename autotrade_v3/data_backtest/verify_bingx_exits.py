"""AutoTrade V3-04B — OFFLINE historical BingX exit lineage evidence.

Never calls private/public exchange APIs or mutates V2. Uses signed-off local
CSV exports (strings, especially order IDs > 2**53) and exact order identities.
C2 cannot pass from this evidence alone: funding and merged positions remain
explicitly unresolved. Input proof references identify locally retained BingX
order details; their mere presence is not cryptographic proof of authenticity.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = 1
PROTECTION_TYPES = {"sl": "STOP_MARKET", "tp": "TAKE_PROFIT_MARKET", "trailing": "TRAILING_STOP_MARKET"}
V2_FILES = {
    "signal_links": {"entry_order_id", "signal_id", "symbol", "side", "combo", "signal_time_ms"},
    "bingx_orders": {"order_id", "symbol", "side", "position_side", "type", "status", "filled_qty", "position_id"},
    "bingx_fills": {"trade_id", "order_id", "symbol", "side", "position_side", "qty", "price", "fee", "realized_pnl", "timestamp_ms"},
    "bingx_income": {"income_key", "symbol", "income_type", "amount", "asset", "timestamp_ms", "trade_id"},
    "processed_targets": {"event_id", "target", "payload"},
}
EXCHANGE_FILES = {
    "orders": {"symbol", "order_id", "side", "position_side", "type", "status", "position_id", "filled_qty", "trigger_order_id", "main_order_id", "working_type"},
    "fills": {"symbol", "trade_id", "order_id", "side", "position_side", "qty", "price", "fee", "realized_pnl", "timestamp_ms"},
}
OPTIONAL_EXCHANGE = {
    "explicit_links": {"symbol", "parent_order_id", "child_order_id", "proof_source", "proof_sha256", "proof_path"},
    "position_history": {"symbol", "position_id", "position_side", "opened_ms", "closed_ms"},
    "income": {"symbol", "income_key", "income_type", "amount", "position_id", "trade_id", "timestamp_ms"},
}
MAX_MS = 4102444800000


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for blob in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blob)
    return h.hexdigest()


def _csv(root: Path, name: str, fields: set[str]) -> list[dict]:
    path = root / f"{name}.csv"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing/unsafe {name}.csv")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not fields <= set(reader.fieldnames or ()):
            raise ValueError(f"{name}: missing {sorted(fields - set(reader.fieldnames or ()))}")
        out = list(reader)
        if any(None in item for item in out):
            raise ValueError(f"{name}: malformed CSV row")
    return out


def _atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _proof_content(root: Path, row: dict) -> tuple[str, bytes]:
    """Read an immutable local provider response, never trust a self-asserted link."""
    rel = row.get("proof_path", "")
    relpath = Path(rel)
    if (not rel or relpath.is_absolute() or ".." in relpath.parts
            or len(relpath.parts) != 2 or relpath.parts[0] != "proofs"
            or relpath.suffix != ".json"):
        raise ValueError("proof_path must be under proofs/<file>.json")
    file = root / relpath
    if file.is_symlink() or not file.is_file():
        raise ValueError("BingX original order detail proof missing or unsafe")
    raw = file.read_bytes()
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("BingX order detail JSON invalid") from exc
    if not isinstance(obj, dict):
        raise ValueError("BingX proof must be a JSON object")
    if "code" in obj and str(obj["code"]) != "0":
        raise ValueError("BingX order detail response is not successful")
    data = obj.get("data", obj)
    if not isinstance(data, dict):
        raise ValueError("BingX proof missing order detail data")
    oid = data.get("orderID", data.get("orderId"))
    triggered = data.get("triggerOrderId")
    if str(oid) != row.get("parent_order_id") or str(triggered) != row.get("child_order_id"):
        raise ValueError("BingX order detail parent/trigger fields disagree with CSV")
    return relpath.as_posix(), raw


def _utc(value: str) -> str:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset().total_seconds() != 0:
        raise ValueError("requires UTC ISO timestamp")
    return d.isoformat().replace("+00:00", "Z")


def seal_exchange(root: Path, exported_at_utc: str) -> dict:
    """Seal existing exchange exports; never interpret 'present' as complete."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("exchange export folder missing")
    files = {}
    for name, columns in {**EXCHANGE_FILES, **OPTIONAL_EXCHANGE}.items():
        path = root / f"{name}.csv"
        if not path.exists() and name in OPTIONAL_EXCHANGE:
            continue
        rows = _csv(root, name, columns)
        files[name] = {"sha256": _sha_file(path), "rows": len(rows)}
    proofs = {}
    for row in _csv(root, "explicit_links", OPTIONAL_EXCHANGE["explicit_links"]) if "explicit_links" in files else []:
        rel, payload = _proof_content(root, row)
        proofs[rel] = hashlib.sha256(payload).hexdigest()
        if row["proof_sha256"].lower() != proofs[rel]:
            raise ValueError("BingX order detail proof checksum mismatch")
    manifest = {
        "schema_version": SCHEMA,
        "source": "BINGX_USDT_PERP_OFFLINE_READ_ONLY_EXPORT",
        "exported_at_utc": _utc(exported_at_utc),
        "complete_history_attested": False,
        "proof_files_sha256": proofs,
        "files": files,
        "note": "SHA-256 proves export immutability, not provider authenticity or pagination completeness.",
    }
    _atomic_json(root / "manifest-v3-04b.json", manifest)
    return manifest


def _load_manifest(root: Path, manifest_name: str, required: dict, optional: dict | None = None):
    path = root / manifest_name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing/unsafe {manifest_name}")
    m = json.loads(path.read_text(encoding="utf-8"))
    if m.get("schema_version") != SCHEMA or not isinstance(m.get("files"), dict):
        raise ValueError("unsupported manifest schema")
    files = m["files"]
    contract = {**required, **(optional or {})}
    if not set(required) <= set(files) or not set(files) <= set(contract):
        raise ValueError("manifest file set mismatch")
    rows = {}
    for name, meta in files.items():
        path = root / f"{name}.csv"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"unsafe/missing {name}.csv")
        if meta.get("sha256") != _sha_file(path):
            raise ValueError(f"checksum mismatch for {name}")
        values = _csv(root, name, contract[name])
        if meta.get("rows") != len(values):
            raise ValueError(f"row count mismatch for {name}")
        rows[name] = values
    return m, rows


def _load_v2(root: Path):
    m, rows = _load_manifest(root, "manifest-v3-04.json", V2_FILES)
    if m.get("read_only") is not True:
        raise ValueError("V2 export is not sealed as read-only")
    return m, rows


def _load_exchange(root: Path):
    m, rows = _load_manifest(root, "manifest-v3-04b.json", EXCHANGE_FILES, OPTIONAL_EXCHANGE)
    if m.get("source") != "BINGX_USDT_PERP_OFFLINE_READ_ONLY_EXPORT":
        raise ValueError("exchange source mismatch")
    verified = {}
    for row in rows.get("explicit_links", []):
        rel, proof = _proof_content(root, row)
        digest = hashlib.sha256(proof).hexdigest()
        if digest != row["proof_sha256"].lower():
            raise ValueError("BingX detail proof checksum mismatch")
        verified[rel] = digest
    if m.get("proof_files_sha256", {}) != verified:
        raise ValueError("BingX proof manifest mismatched")
    return m, rows


def _amount(value):
    try:
        d = Decimal(str(value))
        return d if d.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _millis(x):
    try:
        value = int(x)
        return value if 0 < value < MAX_MS else None
    except (ValueError, TypeError):
        return None


def _payload(value):
    try:
        val = json.loads(value or "{}")
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def _nonempty(x) -> bool:
    return str(x or "").strip().lower() not in ("", "null", "none", "0")


def _index_unique(rows, keys):
    index, dup = {}, set()
    for row in rows:
        k = tuple(row.get(key, "") for key in keys)
        if k in index:
            dup.add(k)
        else:
            index[k] = row
    return index, dup


def _group(rows, keys):
    out = defaultdict(list)
    for row in rows:
        out[tuple(row.get(k, "") for k in keys)].append(row)
    return out


def _validate_fills(rows, order, *, expected_side, expected_position, earliest_ms=None):
    errors = set()
    if not rows:
        return None, ["NO_EXACT_EXECUTION_FILL"]
    seen, qty = set(), Decimal(0)
    for row in rows:
        ident = (row["symbol"], row["trade_id"])
        if not _nonempty(row["trade_id"]) or ident in seen:
            errors.add("DUPLICATE_OR_EMPTY_TRADE_ID")
        seen.add(ident)
        if row["symbol"] != order["symbol"] or row["order_id"] != order["order_id"]:
            errors.add("FILL_ORDER_IDENTITY_MISMATCH")
        if row["side"] != expected_side or row["position_side"] != expected_position:
            errors.add("FILL_SIDE_POSITION_MISMATCH")
        q, px = _amount(row["qty"]), _amount(row["price"])
        fee, realized = _amount(row["fee"]), _amount(row["realized_pnl"])
        if q is None or q <= 0 or px is None or px <= 0 or fee is None or realized is None:
            errors.add("INVALID_FILL_QUANTITY_PRICE_OR_FINANCE")
        else:
            qty += q
        when = _millis(row["timestamp_ms"])
        if when is None or (earliest_ms is not None and when < earliest_ms):
            errors.add("EXIT_FILL_BEFORE_ENTRY_OR_INVALID_TIME")
    expected = _amount(order.get("filled_qty"))
    if expected is None or expected <= 0:
        errors.add("ORDER_FILLED_QUANTITY_UNKNOWN")
    elif abs(qty - expected) > max(Decimal("0.00000001"), expected * Decimal("0.000001")):
        errors.add("FILL_QUANTITY_MISMATCH")
    if order.get("status") != "FILLED":
        errors.add("ORDER_STATUS_NOT_FILLED")
    return qty, sorted(errors)


def _state_for(event_id, state_rows):
    snapshots = sorted(state_rows.get((event_id,), []), key=lambda x: x.get("target") != "bingx_state")
    if not snapshots:
        return {}, ["NO_V2_STATE"]
    obj = _payload(snapshots[0]["payload"])
    errors = set()
    for s in snapshots[1:]:
        alt = _payload(s["payload"])
        for field in ("exit_mode", "position_id", "shared_position"):
            if _nonempty(obj.get(field)) and _nonempty(alt.get(field)) and obj[field] != alt[field]:
                errors.add("V2_STATE_CONFLICT_" + field.upper())
            elif not _nonempty(obj.get(field)) and _nonempty(alt.get(field)):
                obj[field] = alt[field]
        if not isinstance(obj.get("protection"), dict):
            obj["protection"] = alt.get("protection")
        elif isinstance(alt.get("protection"), dict):
            for leg in PROTECTION_TYPES:
                old = (obj["protection"].get(leg) or {}).get("order_id") if isinstance(obj["protection"].get(leg), dict) else ""
                new = (alt["protection"].get(leg) or {}).get("order_id") if isinstance(alt["protection"].get(leg), dict) else ""
                if _nonempty(old) and _nonempty(new) and str(old) != str(new):
                    errors.add("V2_STATE_CONFLICT_PROTECTION_" + leg.upper())
                elif not _nonempty(old) and _nonempty(new):
                    obj["protection"][leg] = alt["protection"][leg]
    return obj, sorted(errors)


def reconcile(v2_dir: Path, exchange_dir: Path | None, output: Path) -> dict:
    v2_dir = v2_dir.resolve()
    if output.resolve().is_relative_to(v2_dir):
        raise ValueError("output must be outside source export folders")
    _, v2 = _load_v2(v2_dir)
    if exchange_dir is None:
        ex_manifest, ex = {}, {"orders": [], "fills": []}
    else:
        exchange_dir = exchange_dir.resolve()
        if output.resolve().is_relative_to(exchange_dir):
            raise ValueError("output must be outside exchange evidence folder")
        ex_manifest, ex = _load_exchange(exchange_dir)
    v2_orders, duplicated_v2 = _index_unique(v2["bingx_orders"], ("symbol", "order_id"))
    ex_orders, duplicated_ex = _index_unique(ex["orders"], ("symbol", "order_id"))
    ex_fills = _group(ex["fills"], ("symbol", "order_id"))
    states = _group(v2["processed_targets"], ("event_id",))
    explicit = _group(ex.get("explicit_links", []), ("symbol", "parent_order_id"))
    linked_entries = _group(v2["signal_links"], ("symbol", "signal_id"))
    sig_per_container = defaultdict(set)
    child_owner = defaultdict(set)
    all_trades = _group(ex["fills"], ("symbol", "trade_id"))
    for link in v2["signal_links"]:
        ord0 = v2_orders.get((link["symbol"], link["entry_order_id"]), {})
        if _nonempty(ord0.get("position_id")):
            sig_per_container[(link["symbol"], ord0["position_id"], ord0.get("position_side"))].add(link["signal_id"])
        state, _ = _state_for(link["signal_id"], states)
        protect = state.get("protection", {})
        if isinstance(protect, dict):
            for leg in PROTECTION_TYPES:
                rec = protect.get(leg, {})
                order_id = rec.get("order_id") if isinstance(rec, dict) else None
                if _nonempty(order_id):
                    child_owner[(link["symbol"], str(order_id))].add(link["signal_id"])
    # A triggered execution child cannot be assigned to two independent
    # signals, even when their parent protective IDs differ.
    triggered_child_owners = defaultdict(set)
    for link in v2["signal_links"]:
        st,_ = _state_for(link["signal_id"],states)
        pp=st.get("protection",{})
        if not isinstance(pp,dict):
            continue
        for part in pp.values():
            if not isinstance(part,dict) or not _nonempty(part.get("order_id")):
                continue
            parent_order=ex_orders.get((link["symbol"],str(part["order_id"])))
            if parent_order and _nonempty(parent_order.get("trigger_order_id")) and parent_order["trigger_order_id"]!=parent_order["order_id"]:
                triggered_child_owners[(link["symbol"],parent_order["trigger_order_id"])].add(link["signal_id"])
    rows=[]
    for link in v2["signal_links"]:
        symbol=link["symbol"]
        identity=(symbol,link["signal_id"])
        stamp=_millis(link.get("signal_time_ms"))
        event_hash=hashlib.sha256((symbol+"|"+link["signal_id"]+"|"+link["entry_order_id"]).encode()).hexdigest()[:18]
        issues=set()
        if stamp is None:
            issues.add("INVALID_SIGNAL_TIME")
        if len(linked_entries[identity]) != 1:
            issues.add("MULTIPLE_V2_ENTRY_LINKS")
        key=(symbol,link["entry_order_id"])
        old_order = v2_orders.get(key)
        entry = ex_orders.get(key)
        if key in duplicated_v2 or not old_order:
            issues.add("V2_ENTRY_ORDER_UNRESOLVED")
        if entry is None:
            issues.add("EXCHANGE_ENTRY_ORDER_MISSING")
        if key in duplicated_ex:
            issues.add("DUPLICATED_EXCHANGE_ENTRY_ORDER")
        if entry and old_order:
            for field in ("side", "position_side", "position_id", "type"):
                if entry.get(field) != old_order.get(field):
                    issues.add("ENTRY_V2_EXCHANGE_CONFLICT_"+field.upper())
            a, b = _amount(entry.get("filled_qty")), _amount(old_order.get("filled_qty"))
            if a is None or b is None or abs(a-b) > max(Decimal("0.00000001"),abs(b)*Decimal("0.000001")):
                issues.add("ENTRY_V2_EXCHANGE_FILLED_QTY_CONFLICT")
        intended_side=link["side"]
        expected_pos="LONG" if intended_side=="BUY" else "SHORT"
        if old_order and (old_order["side"]!=intended_side or old_order["position_side"]!=expected_pos or old_order["type"] not in ("MARKET","LIMIT")):
            issues.add("ENTRY_V2_IDENTITY_CONFLICT")
        entry_qty = None
        first_entry_ms = None
        if entry:
            fills = ex_fills.get(key,[])
            entry_qty, errs = _validate_fills(fills,entry,expected_side=intended_side,expected_position=expected_pos)
            issues.update("ENTRY_"+e for e in errs)
            if fills and all(_millis(f.get("timestamp_ms")) for f in fills):
                first_entry_ms=min(_millis(f["timestamp_ms"]) for f in fills)
        if old_order and _nonempty(old_order.get("position_id")) and len(sig_per_container[(symbol,old_order["position_id"],expected_pos)])>1:
            issues.add("POSITION_CONTAINER_REUSED_BY_SIGNALS")
        state,err = _state_for(link["signal_id"],states)
        issues.update(err)
        merged = state.get("merged_reconcile")
        shared = state.get("shared_position")
        # Sanitized V3-04 state emits {"shared": null} for absent merge evidence.
        # The mere presence of this container does NOT establish shared position.
        if shared is True or (isinstance(shared, str) and shared.lower()=="true") or (
            isinstance(merged,dict) and (merged.get("shared") is True or
                                         str(merged.get("shared")).lower()=="true")
        ):
            issues.add("V2_SHARED_OR_MERGED_POSITION")
        exit_mode=state.get("exit_mode")
        if exit_mode not in ("hard_tp", "split_trailing"):
            issues.add("EXIT_MODE_NOT_PROVEN")
        protection=state.get("protection") if isinstance(state.get("protection"),dict) else {}
        all_protective_ids = [str(protection[leg]["order_id"]) for leg in PROTECTION_TYPES
                              if isinstance(protection.get(leg),dict)
                              and _nonempty(protection[leg].get("order_id"))]
        if len(all_protective_ids) != len(set(all_protective_ids)):
            issues.add("PROTECTIVE_ORDER_ID_REUSED_BETWEEN_LEGS")
        executed_trade_ids=set()
        total_exit_qty=Decimal(0)
        proven_legs=0
        for leg,typ in PROTECTION_TYPES.items():
            node=protection.get(leg)
            parent_id=(str(node.get("order_id")) if isinstance(node,dict) and _nonempty(node.get("order_id")) else "")
            if not parent_id:
                if leg in (("sl","tp","trailing") if exit_mode=="split_trailing" else ("sl","tp")):
                    issues.add("MISSING_PROTECTIVE_"+leg.upper())
                continue
            pk=(symbol,parent_id)
            if len(child_owner[pk])>1:
                issues.add("CHILD_ORDER_SHARED_BETWEEN_SIGNALS")
            parent=ex_orders.get(pk)
            if not parent:
                issues.add("EXCHANGE_PROTECTIVE_ORDER_MISSING")
                continue
            if pk in duplicated_ex:
                issues.add("DUPLICATE_PROTECTIVE_ORDER_ID")
                continue
            if parent.get("type")!=typ or parent.get("side")==intended_side or parent.get("position_side")!=expected_pos:
                issues.add("PROTECTIVE_ORDER_IDENTITY_CONFLICT")
                continue
            if old_order and (not _nonempty(parent.get("position_id")) or parent["position_id"]!=old_order.get("position_id")):
                issues.add("PROTECTIVE_POSITION_ID_CONFLICT")
                continue
            if parent.get("working_type") not in ("MARK_PRICE","CONTRACT_PRICE","INDEX_PRICE"):
                issues.add("TRIGGER_PRICE_BASIS_UNKNOWN")
            matched_orders=[]
            if ex_fills.get(pk):
                matched_orders=[parent]
            elif parent.get("status") in ("CANCELLED","CANCELED","EXPIRED"):
                # A cancelled stop or TP can legitimately have no fill. If
                # filled_qty > 0, partial executions must be evidenced.
                filled = _amount(parent.get("filled_qty"))
                if filled is None or filled > 0:
                    issues.add("CANCELLED_PROTECTION_FILL_STATUS_UNVERIFIED")
                continue
            else:
                trigger=str(parent.get("trigger_order_id") or "")
                if not _nonempty(trigger) or trigger==parent_id:
                    issues.add("PARENT_HAS_NO_DISTINCT_TRIGGER_ORDER_ID")
                    continue
                if len(triggered_child_owners[(symbol,trigger)])>1:
                    issues.add("CHILD_EXECUTION_ORDER_SHARED_BETWEEN_SIGNALS")
                    continue
                child=ex_orders.get((symbol,trigger))
                if not child or (symbol,trigger) in duplicated_ex:
                    issues.add("TRIGGER_CHILD_ORDER_NOT_EXPORTED")
                    continue
                # An unidirectional field is merely a candidate; require an
                # explicit audited relation to avoid assigning another combo's fill.
                relations=[rel for rel in explicit.get(pk,[]) if rel.get("child_order_id")==trigger]
                if (len(relations)!=1 or relations[0].get("proof_source")!="BINGX_ORDER_DETAIL"
                        or not isinstance(relations[0].get("proof_sha256"),str)
                        or len(relations[0]["proof_sha256"])!=64
                        or any(c not in "0123456789abcdefABCDEF" for c in relations[0]["proof_sha256"])):
                    issues.add("TRIGGER_PARENT_CHILD_PROOF_MISSING")
                    continue
                if child.get("side")!=parent["side"] or child.get("position_side")!=expected_pos or not _nonempty(child.get("position_id")) or child["position_id"]!=parent["position_id"]:
                    issues.add("TRIGGER_CHILD_IDENTITY_CONFLICT")
                    continue
                if child.get("type") not in ("MARKET",typ,"LIMIT"):
                    issues.add("TRIGGER_CHILD_TYPE_CONFLICT")
                    continue
                matched_orders=[child]
            for fired in matched_orders:
                actual=ex_fills.get((symbol,fired["order_id"]),[])
                qty, err=_validate_fills(actual,fired,expected_side=parent["side"],expected_position=expected_pos,earliest_ms=first_entry_ms)
                if err:
                    issues.update("EXIT_"+e for e in err)
                else:
                    ids={(f["symbol"],f["trade_id"]) for f in actual}
                    if ids & executed_trade_ids or any(len(all_trades[i])>1 for i in ids):
                        issues.add("REUSED_OR_DUPLICATED_EXIT_TRADE_ID")
                    else:
                        executed_trade_ids|=ids
                        total_exit_qty+=qty
                        proven_legs+=1
        if not executed_trade_ids:
            issues.add("NO_EXACT_EXIT_TRADE_FILLS")
        if entry_qty is None or entry_qty<=0 or not executed_trade_ids or abs(total_exit_qty-entry_qty)>max(Decimal("0.00000001"), (entry_qty or Decimal(0))*Decimal("0.000001")):
            issues.add("EXIT_QUANTITY_CANNOT_PROVE_FULL_CLOSE")
        # Funding via positionId is not combo attribution when a hedge container
        # is shared; no net PnL/ROI is represented as verified here.
        if ("POSITION_CONTAINER_REUSED_BY_SIGNALS" in issues
                or "V2_SHARED_OR_MERGED_POSITION" in issues
                or "CHILD_ORDER_SHARED_BETWEEN_SIGNALS" in issues
                or "CHILD_EXECUTION_ORDER_SHARED_BETWEEN_SIGNALS" in issues):
            status="MERGED_POSITION_UNRESOLVED"
        elif any(i.startswith("ENTRY_") or i=="V2_ENTRY_ORDER_UNRESOLVED" for i in issues):
            status="ENTRY_EVIDENCE_UNRESOLVED"
        elif not executed_trade_ids or any(i.startswith(("TRIGGER_","PROTECTIVE_","MISSING_PROTECTIVE_","EXCHANGE_PROTECTIVE_","CHILD_ORDER_")) for i in issues):
            status="EXIT_LINEAGE_UNRESOLVED"
        elif "EXIT_QUANTITY_CANNOT_PROVE_FULL_CLOSE" in issues or any(i.startswith("EXIT_") for i in issues):
            status="EXIT_FILL_INCOMPLETE"
        elif issues:
            status="EXIT_FILL_SUPPORTED_FINANCE_UNRESOLVED"
        else:
            status="EXIT_FILL_SUPPORTED_FINANCE_UNRESOLVED"  # no verified funding allocation
        entry_is_proven = (entry is not None and old_order is not None and entry_qty is not None
                           and entry_qty > 0 and not any(x.startswith("ENTRY_") or
                           x in ("V2_ENTRY_ORDER_UNRESOLVED","MULTIPLE_V2_ENTRY_LINKS",
                                 "DUPLICATED_EXCHANGE_ENTRY_ORDER") for x in issues))
        exit_is_proven = (bool(executed_trade_ids) and entry_is_proven
                          and abs(total_exit_qty-entry_qty) <= max(Decimal("0.00000001"),entry_qty*Decimal("0.000001"))
                          and not any(x.startswith(("EXIT_","PROTECTIVE_","TRIGGER_","MISSING_PROTECTIVE_"))
                              or x in ("CHILD_ORDER_SHARED_BETWEEN_SIGNALS","POSITION_CONTAINER_REUSED_BY_SIGNALS",
                                       "V2_SHARED_OR_MERGED_POSITION","CANCELLED_PROTECTION_FILL_STATUS_UNVERIFIED",
                                       "REUSED_OR_DUPLICATED_EXIT_TRADE_ID",
                                       "PROTECTIVE_ORDER_ID_REUSED_BETWEEN_LEGS",
                                       "CHILD_EXECUTION_ORDER_SHARED_BETWEEN_SIGNALS") for x in issues))
        rows.append({
            "event_hash":event_hash,"symbol":symbol,"combo":link["combo"],
            "month_utc":datetime.fromtimestamp(stamp/1000,tz=timezone.utc).strftime("%Y-%m") if stamp else "UNKNOWN",
            "status":status,"reason_codes":sorted(issues),"matched_exit_legs":proven_legs,
            "matched_exit_fill_trades":len(executed_trade_ids),
            "full_exit_qty_supported":exit_is_proven,
            "net_pnl_verified":None,"roi_verified":None,
            "source": "OFFLINE_BINGX_EXPORT" if exchange_dir else "NO_BINGX_EXPORT",
        })
    counts=Counter(row["status"] for row in rows)
    by_month=Counter((row["symbol"],row["month_utc"],row["status"]) for row in rows)
    report={"gate":"C2","status":"INCONCLUSIVE",
            "source":"V3_04B_READONLY_OFFLINE",
            "exchange_export_present":exchange_dir is not None,
            "exchange_complete_history_attested":bool(ex_manifest.get("complete_history_attested",False)),
            "exchange_export_sha256":_sha_file(exchange_dir/"manifest-v3-04b.json") if exchange_dir else None,
            "v2_export_sha256":_sha_file(v2_dir/"manifest-v3-04.json"),
            "signal_count":len(rows),"status_counts":dict(sorted(counts.items())),
            "full_close_qty_candidates":sum(row["full_exit_qty_supported"] for row in rows),
            "verified_net_pnl_pairs":0,
            "by_month":[{"symbol":s,"month_utc":m,"status":st,"count":n} for (s,m,st),n in sorted(by_month.items())],
            "notes":["Exact order IDs only; triggerOrderId alone cannot prove parent-child ownership.",
                     "Even a fully matched exit fill is not a verified net ROI without independent funding and merged-position allocation.",
                     "Manifest hashes do not prove BingX API export pagination completeness."]}
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    _atomic_json(output/"gate_c2b_report.json",report)
    with (output/"exit_lineage.jsonl").open("w",encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row,sort_keys=True,ensure_ascii=False)+"\n")
    return report


def main() -> int:
    p=argparse.ArgumentParser(description="Offline BingX native trigger→child→fill evidence; no trading API")
    sub=p.add_subparsers(dest="command",required=True)
    s=sub.add_parser("seal",help="checksum an existing read-only BingX CSV export")
    s.add_argument("--bingx-dir",type=Path,required=True)
    s.add_argument("--exported-at-utc",required=True)
    a=sub.add_parser("audit",help="compare sealed V2 export and optional sealed BingX history")
    a.add_argument("--v2-dir",type=Path,required=True)
    a.add_argument("--bingx-dir",type=Path)
    a.add_argument("--out-dir",type=Path,required=True)
    args=p.parse_args()
    try:
        if args.command=="seal":
            result=seal_exchange(args.bingx_dir,args.exported_at_utc)
            print(json.dumps({"status":"SEALED","files":list(result["files"])},sort_keys=True))
        else:
            result=reconcile(args.v2_dir,args.bingx_dir,args.out_dir)
            print(json.dumps({"status":result["status"],"status_counts":result["status_counts"],
                              "verified_net_pnl_pairs":result["verified_net_pnl_pairs"]},sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        p.exit(2,f"V3-04B audit failed: {exc}\n")
    return 0

if __name__=="__main__":
    raise SystemExit(main())