"""Normalize *already exported* BingX GET response JSON to local V3-04B CSV.

Never makes a network request. JSON may contain private account metadata: only
allowlisted order/fill fields reach CSV. Paginated response completeness is NOT
inferred from file count; review coverage against BingX separately.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from decimal import Decimal
from pathlib import Path

from .verify_bingx_exits import EXCHANGE_FILES


def _field(obj, *names):
    for name in names:
        if name in obj and obj[name] is not None:
            x = obj[name]
            if isinstance(x, (dict, list)):
                raise ValueError(f"nested value in scalar field: {name}")
            if isinstance(x, bool):
                return str(x).lower()
            return str(x)
    return ""


def _records(path: Path, mode: str):
    """Single BingX GET response or an array of saved paginated GET responses."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or unsafe historical API JSON export")
    root = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    pages = root if isinstance(root, list) else [root]
    result=[]
    for page in pages:
        if not isinstance(page, dict) or "code" not in page or str(page["code"])!="0":
            raise ValueError("BingX GET response must have code 0; no error/unknown response accepted")
        data = page.get("data")
        if isinstance(data, list):
            found=data
        elif isinstance(data, dict):
            keys=("orders", "list") if mode=="orders" else ("fill_orders", "fills", "list")
            matches=[data[k] for k in keys if isinstance(data.get(k),list)]
            if len(matches)!=1:
                raise ValueError("BingX response data has ambiguous or no list")
            found=matches[0]
        else:
            raise ValueError("BingX response data is not a list/container")
        if not all(isinstance(x,dict) for x in found):
            raise ValueError("BingX order/fill row must be object")
        result+=found
    return result


def _normalize_order(x):
    return dict(
        symbol=_field(x,"symbol"),
        order_id=_field(x,"orderID","orderId"),
        side=_field(x,"side"),
        position_side=_field(x,"positionSide"),
        type=_field(x,"type","orderType"),
        status=_field(x,"status"),
        position_id=_field(x,"positionID","positionId"),
        filled_qty=_field(x,"executedQty","filledQty","filledQuantity"),
        trigger_order_id=_field(x,"triggerOrderId"),
        main_order_id=_field(x,"mainOrderId"),
        working_type=_field(x,"workingType"))


def _normalize_fill(x):
    return dict(
        symbol=_field(x,"symbol"),
        trade_id=_field(x,"tradeId","tradeID"),
        order_id=_field(x,"orderID","orderId"),
        side=_field(x,"side"),
        position_side=_field(x,"positionSide"),
        qty=_field(x,"qty","quantity","executedQty"),
        price=_field(x,"price","tradePrice"),
        fee=_field(x,"fee","commission"),
        realized_pnl=_field(x,"realizedPnl","realizedPNL"),
        timestamp_ms=_field(x,"time","timestamp","updateTime"))


def _atomic_csv(path: Path, headers: set[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(".csv.tmp")
    try:
        with tmp.open("w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=sorted(headers))
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp,path)
    finally:
        if tmp.exists():
            tmp.unlink()


def normalize(orders_json: Path, fills_json: Path, out: Path) -> dict:
    out=out.resolve()
    if out in (orders_json.resolve().parent, fills_json.resolve().parent):
        raise ValueError("destination must be separate from raw JSON exports")
    if any(out == base or base in out.parents for base in (orders_json.resolve(),fills_json.resolve())):
        raise ValueError("destination must not be beneath source file")
    orders=[_normalize_order(x) for x in _records(orders_json,"orders")]
    fills=[_normalize_fill(x) for x in _records(fills_json,"fills")]
    def validate(rows,kind,id_key):
        seen=set()
        for r in rows:
            ident=(r["symbol"],r[id_key])
            if not r["symbol"] or not r[id_key] or ident in seen:
                raise ValueError(f"{kind}: missing or duplicate exact identity")
            if any(ch in r[id_key] for ch in ".Ee+") or not r[id_key].isdigit():
                raise ValueError(f"{kind}: order/trade ID must be exact decimal string")
            seen.add(ident)
    validate(orders,"orders","order_id")
    validate(fills,"fills","trade_id")
    if any(not f["order_id"] or not f["order_id"].isdigit() for f in fills):
        raise ValueError("fills: missing exact order ID")
    _atomic_csv(out/"orders.csv",EXCHANGE_FILES["orders"],orders)
    _atomic_csv(out/"fills.csv",EXCHANGE_FILES["fills"],fills)
    return {"orders":len(orders),"fills":len(fills),
            "source":"BINGX_GET_JSON_OFFLINE_NORMALIZED",
            "pagination_completeness":"NOT_ATTESTED",
            "parent_child_links_verified":0}


def main():
    p=argparse.ArgumentParser(description="Normalize saved BingX GET order/fill JSON offline")
    p.add_argument("--orders-json",type=Path,required=True)
    p.add_argument("--fills-json",type=Path,required=True)
    p.add_argument("--out-dir",type=Path,required=True)
    a=p.parse_args()
    try:
        r=normalize(a.orders_json,a.fills_json,a.out_dir)
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        p.exit(2,f"V3-04B normalization failed: {exc}\n")
    print(json.dumps(r,sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(main())