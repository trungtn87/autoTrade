"""V3-00: offline, conservative inventory of exported V2 execution evidence.

Does not connect to Supabase, BingX, Gemini or a trading executor.
This module deliberately does NOT verify exit ownership or compute PnL.
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

SCHEMA_VERSION = 1
REQUIRED = {
    "signal_links": {"entry_order_id", "signal_id", "symbol", "side", "combo", "signal_time_ms"},
    "bingx_orders": {"order_id", "symbol", "side", "position_side", "type", "filled_qty"},
    "bingx_fills": {"trade_id", "order_id", "symbol", "side", "position_side", "qty"},
}
OPTIONAL = {
    "trade_results": {"event_id", "payload"},
    "v2_states": {"event_id", "payload"},
    "ai_reviews": {"event_id", "symbol"},
    "processed_targets": {"event_id", "target", "payload"},
}
TABLES = {**REQUIRED, **OPTIONAL}
ENTRY_TYPES = {"MARKET", "LIMIT"}


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_table(root: Path, name: str) -> list[dict]:
    path = root / (name + ".csv")
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe export: {name}.csv")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or ())
        if not TABLES[name].issubset(columns):
            raise ValueError(f"{name}: missing columns {sorted(TABLES[name] - columns)}")
        rows = list(reader)
    if any(None in row for row in rows):
        raise ValueError(f"{name}: malformed CSV row")
    return rows


def seal(root: Path, exported_at_utc: str) -> dict:
    """Write ONLY a local manifest; never export/query a remote database."""
    root = root.resolve()
    try:
        dt = datetime.fromisoformat(exported_at_utc.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("exported_at_utc must be a UTC ISO-8601 timestamp") from exc
    files = {}
    for name in TABLES:
        path = root / (name + ".csv")
        if not path.exists():
            if name in REQUIRED:
                raise ValueError(f"required export missing: {name}.csv")
            continue
        rows = _read_table(root, name)
        files[name] = {"sha256": _file_hash(path), "rows": len(rows)}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "exported_at_utc": dt.isoformat().replace("+00:00", "Z"),
        "files": files,
    }
    _atomic_json(root / "manifest.json", manifest)
    return manifest


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    try:
        staging.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


def _load(root: Path) -> tuple[dict, dict[str, list[dict]]]:
    p = root / "manifest.json"
    if p.is_symlink() or not p.is_file():
        raise ValueError("manifest.json missing or unsafe; run 'seal' after read-only export")
    manifest = json.loads(p.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION or not isinstance(manifest.get("files"), dict):
        raise ValueError("unsupported manifest schema")
    if not isinstance(manifest.get("exported_at_utc"), str):
        raise ValueError("export timestamp missing")
    files = manifest["files"]
    if not set(REQUIRED).issubset(files) or set(files) - set(TABLES):
        raise ValueError("missing required table or unknown table in manifest")
    rows = {}
    for name, meta in files.items():
        path = root / (name + ".csv")
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing or unsafe export: {name}.csv")
        if _file_hash(path) != meta.get("sha256"):
            raise ValueError(f"checksum mismatch: {name}.csv")
        records = _read_table(root, name)
        if len(records) != meta.get("rows"):
            raise ValueError(f"row count mismatch: {name}.csv")
        rows[name] = records
    return manifest, rows


def _decimal(value: object) -> Decimal | None:
    try:
        d = Decimal(str(value))
        return d if d.is_finite() and d >= 0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def _payload(row: dict) -> dict:
    obj = row.get("payload")
    if isinstance(obj, dict):
        return obj
    try:
        decoded = json.loads(obj or "{}")
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _month(ms: str) -> str:
    try:
        n = int(ms)
        if not (0 <= n < 4102444800000):
            return "UNKNOWN"
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).strftime("%Y-%m")
    except (ValueError, TypeError, OverflowError, OSError):
        return "UNKNOWN"


def _fill_status(order: dict, fills: list[dict]) -> str:
    expected = _decimal(order.get("filled_qty"))
    if expected is None or expected == 0:
        return "ENTRY_UNFILLED_OR_INVALID_QTY"
    if not fills:
        return "ENTRY_FILL_MISSING"
    side, position = order.get("side"), order.get("position_side")
    if any(f.get("side") != side or f.get("position_side") != position for f in fills):
        return "ENTRY_FILL_IDENTITY_CONFLICT"
    quantities = [_decimal(f.get("qty")) for f in fills]
    if any(q is None or q == 0 for q in quantities):
        return "ENTRY_FILL_INVALID_QTY"
    actual = sum(quantities, Decimal(0))
    tolerance = max(Decimal("0.00000001"), expected * Decimal("0.000001"))
    if abs(actual - expected) > tolerance:
        return "ENTRY_FILL_QTY_MISMATCH"
    if len({f.get("trade_id") for f in fills}) != len(fills):
        return "DUPLICATE_FILL_ID"
    return "ENTRY_FILL_SUPPORTED_NOT_EXIT_VERIFIED"


def audit(root: Path) -> dict:
    """Return aggregate counts only, never raw order IDs, payloads or API secrets."""
    root = root.resolve()
    manifest, tables = _load(root)
    links = tables["signal_links"]
    orders = defaultdict(list)
    for order in tables["bingx_orders"]:
        orders[str(order.get("order_id", ""))].append(order)
    fills = defaultdict(list)
    for fill in tables["bingx_fills"]:
        fills[(str(fill.get("symbol", "")), str(fill.get("order_id", "")))].append(fill)
    signals = Counter((r.get("symbol"), r.get("signal_id")) for r in links)
    results = defaultdict(list)
    for name in ("trade_results", "v2_states", "processed_targets"):
        for row in tables.get(name, []):
            results[row.get("event_id")].append(_payload(row))
    result_exists = "trade_results" in tables
    missing_tables = sorted(set(OPTIONAL) - set(tables))
    statuses, per_group, exit_mode_missing = Counter(), defaultdict(Counter), 0
    exit_evidence_candidates = 0
    supported_entry_ids = set()
    for link in links:
        symbol, side = link.get("symbol", ""), link.get("side", "")
        order_id = str(link.get("entry_order_id", ""))
        signal_id = link.get("signal_id", "")
        candidates = orders.get(order_id, [])
        if not order_id or not symbol or not signal_id or not side:
            status = "INVALID_SIGNAL_LINK"
        elif signals[(symbol, signal_id)] > 1:
            status = "MULTIPLE_LINKED_ENTRIES_UNRESOLVED"
        elif len(candidates) != 1:
            status = "ENTRY_ORDER_MISSING_OR_DUPLICATE"
        elif candidates[0].get("symbol") != symbol:
            status = "ENTRY_SYMBOL_MISMATCH"
        elif candidates[0].get("side") != side:
            status = "ENTRY_SIDE_MISMATCH"
        elif candidates[0].get("type") not in ENTRY_TYPES:
            status = "ENTRY_TYPE_NOT_ENTRY"
        elif candidates[0].get("position_side") != ("LONG" if side == "BUY" else "SHORT"):
            status = "ENTRY_POSITION_SIDE_MISMATCH"
        else:
            status = _fill_status(candidates[0], fills.get((symbol, order_id), []))
            if status == "ENTRY_FILL_SUPPORTED_NOT_EXIT_VERIFIED":
                supported_entry_ids.add((symbol, order_id))
        statuses[status] += 1
        group = (symbol or "UNKNOWN", link.get("combo") or "UNKNOWN", _month(link.get("signal_time_ms", "")))
        per_group[group][status] += 1
        payloads = results.get(signal_id, [])
        if not any(p.get("exit_mode") for p in payloads):
            exit_mode_missing += 1
        if any(p.get("status") == "CLOSED" for p in payloads):
            exit_evidence_candidates += 1

    linked_signal_ids = {(r.get("symbol"), r.get("signal_id")) for r in links}
    no_link_reviews = sum((r.get("symbol"), r.get("event_id")) not in linked_signal_ids
                          for r in tables.get("ai_reviews", []))
    return {
        "gate": "G0",
        "status": "INCONCLUSIVE",  # no exit verification in an inventory pass
        "reason": "EXIT_ORDER_PARENT_CHILD_AND_MONEY_NOT_VALIDATED",
        "exported_at_utc": manifest["exported_at_utc"],
        "manifest_sha256": _file_hash(root / "manifest.json"),
        "source_table_rows": {name: meta["rows"] for name, meta in sorted(manifest["files"].items())},
        "missing_optional_tables": missing_tables,
        "signal_link_rows": len(links),
        "entry_status_counts": dict(sorted(statuses.items())),
        "entry_fill_supported": len(supported_entry_ids),
        "exit_mode_missing_rows": exit_mode_missing,
        "closed_result_candidates_unverified": exit_evidence_candidates,
        "ai_reviews_without_confirmed_entry_link": no_link_reviews,
        "exits_verified": None,  # not measured, NOT proof there were zero verified exits
        "exit_verification_status": "NOT_EVALUATED_IN_G0"
        "per_symbol_combo_month": [
            {"symbol": symbol, "combo": combo, "month_utc": month,
             "total": sum(c.values()), "entry_status_counts": dict(sorted(c.items()))}
            for (symbol, combo, month), c in sorted(per_group.items())
        ],
        "read_only_scope": "OFFLINE_CSV_ONLY_NO_NETWORK_NO_EXCHANGE_CALLS",
        "note": "Entry fill evidence is not exit ownership or verified PnL. Missing link is not proof of no trade.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V3-00 local-only V2 evidence export inventory")
    sub = parser.add_subparsers(dest="command", required=True)
    seal_cmd = sub.add_parser("seal", help="hash existing read-only CSV exports")
    seal_cmd.add_argument("--input-dir", type=Path, required=True)
    seal_cmd.add_argument("--exported-at-utc", required=True)
    audit_cmd = sub.add_parser("audit", help="validate manifest and report evidence gaps")
    audit_cmd.add_argument("--input-dir", type=Path, required=True)
    audit_cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "seal":
            report = seal(args.input_dir, args.exported_at_utc)
        else:
            report = audit(args.input_dir)
            _atomic_json(args.output, report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"V3-00 audit error: {exc}\n")
    print(json.dumps({k: v for k, v in report.items()
                      if k in ("status", "signal_link_rows", "entry_fill_supported",
                               "exit_mode_missing_rows", "schema_version", "exported_at_utc")},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
