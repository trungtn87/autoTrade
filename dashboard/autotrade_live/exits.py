"""Read-only reconciliation of BingX exit fills against V2 order IDs.

This module never calls BingX, never writes to execution tables and never
attributes a shared exchange position's total PnL to a particular Combo.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from .bridge import case_name

LOG = logging.getLogger(__name__)
EXIT_KINDS = {
    "STOP_MARKET": "SL",
    "TAKE_PROFIT_MARKET": "TP",
    "TRAILING_STOP_MARKET": "TRAILING",
}
MAX_FALLBACK_DELAY_MS = 48 * 60 * 60 * 1000


def number(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError, OverflowError):
        return default


def _id(value):
    value = str(value or "").strip()
    return value if re.fullmatch(r"\d+", value) else ""


def extract_exit_spec(payload, event_id=""):
    """Use confirmed V2 execution state (not guessed candle trades)."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(data, dict) or not data.get("entry_filled"):
            return None
        intent = data.get("intent") or {}
        if not isinstance(intent, dict):
            return None
        symbol = str(intent["symbol"]).upper()
        side = str(intent["side"]).upper()
        entry_id = _id(data.get("order_id"))
        if not re.fullmatch(r"[A-Z0-9]+-[A-Z]+", symbol) or side not in {"BUY", "SELL"} or not entry_id:
            return None
        protection = data.get("protection") or {}
        ids = {}
        for key,kind in (("sl","SL"),("tp","TP"),("trailing","TRAILING")):
            value = protection.get(key) or {}
            oid = _id(value.get("order_id"))
            if oid:
                ids[kind] = oid
        pending_sl = (data.get("lifecycle") or {}).get("pending_sl_replace") or {}
        client_id = str(pending_sl.get("client_order_id") or "").strip()
        if not re.fullmatch(r"atv2sl-[a-zA-Z0-9-]{12,80}", client_id):
            client_id = ""
        return {
            "event_id": str(event_id),
            "entry_order_id": entry_id,
            "symbol": symbol,
            "side": side,
            "combo": case_name(intent["combo"]),
            "signal_time_ms": int(intent.get("close_time") or 0),
            "tp": number(intent.get("tp")),
            "sl": number(intent.get("sl")),
            "trailing_activation": number(data.get("trailing_activation_price")),
            "trailing_callback_rate": number(data.get("trailing_callback_rate")),
            "entry_qty": number(data.get("executed_qty")),
            "position_id": str(data.get("position_id") or ""),
            "exit_ids": ids,
            "cleanup_status": str((data.get("exit_cleanup") or {}).get("status") or ""),
            "cleanup_warnings": list((data.get("exit_cleanup") or {}).get("warnings") or []),
            "legacy_sl_pending": bool((data.get("lifecycle") or {}).get("pending_sl_replace")),
            "legacy_sl_client_order_id": client_id,
            "legacy_sl_qty": number(pending_sl.get("quantity")),
            "legacy_sl_old_order_id": _id(pending_sl.get("old_order_id")),
        }
    except (ValueError, TypeError, OverflowError, KeyError, AttributeError):
        return None


def load_exit_specs(engine):
    """No exchange API calls; gracefully degrade if private bridge access is absent."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT event_id, payload FROM public.processed_targets "
                "WHERE target = 'bingx_state' AND payload IS NOT NULL"
            )).all()
    except SQLAlchemyError as exc:
        LOG.warning("Read-only exit mapping unavailable: %s", type(exc).__name__)
        return []
    specs = {}
    for event_id, payload in rows:
        spec = extract_exit_spec(payload, event_id)
        if spec:
            specs[spec["entry_order_id"]] = spec
    return list(specs.values())


def _same_price(a,b):
    if a <= 0 or b <= 0:
        return False
    return abs(a-b) <= max(0.01, abs(b)*0.000002)


def _compatible(order,spec):
    if getattr(order,"symbol","") != spec["symbol"]:
        return False
    side = str(getattr(order,"side","") or "").upper()
    if side and side != ("BUY" if spec["side"]=="SELL" else "SELL"):
        return False
    ps = str(getattr(order,"position_side","") or "").upper()
    if ps and ps != ("SHORT" if spec["side"]=="SELL" else "LONG"):
        return False
    raw = getattr(order,"raw",{}) or {}
    if isinstance(raw,dict):
        oid = str(raw.get("positionID") or raw.get("positionId") or "")
        if oid and oid != "0" and spec["position_id"] and oid != spec["position_id"]:
            return False
    qty = number(getattr(order,"quantity",0))
    if qty > 0 and spec["entry_qty"] > 0 and qty > spec["entry_qty"] + 1e-9:
        return False
    return True


def _target_matches(order,spec,kind):
    raw = getattr(order,"raw",{}) or {}
    if not isinstance(raw,dict):
        return False
    if kind == "SL":
        return _same_price(number(raw.get("stopPrice")),spec["sl"])
    if kind == "TP":
        return _same_price(number(raw.get("stopPrice")),spec["tp"])
    if kind == "TRAILING":
        if not _same_price(number(raw.get("activationPrice")),spec["trailing_activation"]):
            return False
        rate = number(raw.get("priceRate"))
        expected = spec["trailing_callback_rate"]
        return expected > 0 and rate > 0 and abs(rate-expected) <= 1e-9
    return False


def identify_exit(order_id,order,specs):
    """Exact stored orderID wins; price+time is only a labeled candidate."""
    kind = EXIT_KINDS.get(str(getattr(order,"type","") or "").upper()) if order else None
    exact = [(spec, k) for spec in specs for k,oid in spec["exit_ids"].items() if oid==str(order_id)]
    if len(exact)>1:
        return None,"AMBIGUOUS",None

    # BingX uses a NEW orderId for a triggered conditional exit. The filled
    # order carries the original conditional orderId in triggerOrderId.
    # This is an exact linkage, unlike stopPrice/creation-time heuristics.
    raw = getattr(order,"raw",{}) if order else {}
    raw = raw if isinstance(raw,dict) else {}
    trigger_raw = raw.get("triggerOrderId") or raw.get("triggerOrderID")
    trigger_id = _id(trigger_raw)
    linked = [(spec,k) for spec in specs for k,oid in spec["exit_ids"].items()
              if trigger_id and oid==trigger_id]
    if len(linked)>1:
        return None,"AMBIGUOUS",kind
    if exact and linked and (exact[0][0]["entry_order_id"],exact[0][1]) != (
        linked[0][0]["entry_order_id"],linked[0][1]):
        return None,"CONFLICT",kind
    matches = exact or linked
    if matches:
        spec,k = matches[0]
        if order and (not _compatible(order,spec) or (kind and kind!=k)):
            return None,"CONFLICT",kind
        return spec,("ORDER_ID" if exact else "TRIGGER_ORDER_ID"),k
    # Legacy SL cancelReplace: BingX's final execution order can carry the
    # bot's exact deterministic clientOrderId even when its parent triggerOrderId
    # was never persisted in the old lifecycle state. This is a real order
    # identity proof, NOT a price/time/position-side guess.
    client_id = str(raw.get("clientOrderId") or raw.get("clientOrderID") or "").strip()
    if kind == "SL" and client_id:
        legacy = [spec for spec in specs
                  if spec.get("legacy_sl_client_order_id")
                  and spec["legacy_sl_client_order_id"] == client_id]
        if len(legacy) > 1:
            return None,"AMBIGUOUS",kind
        if legacy:
            spec=legacy[0]
            position_id=str(raw.get("positionID") or raw.get("positionId") or "")
            qty=number(getattr(order,"quantity",0))
            expected=spec.get("legacy_sl_qty",0)
            age=int(getattr(order,"created_ms",0) or 0)-spec["signal_time_ms"]
            if not (
                spec.get("legacy_sl_old_order_id")==spec["exit_ids"].get("SL")
                and spec["position_id"] and position_id==spec["position_id"]
                and 0<=age<=MAX_FALLBACK_DELAY_MS
                and qty>0 and expected>0 and abs(qty-expected)<=1e-9
                and _compatible(order,spec)
                and _target_matches(order,spec,"SL")
            ):
                return None,"CONFLICT",kind
            return spec,"LEGACY_SL_CLIENT_ID",kind
    # An explicit trigger ID that is not in our state must NOT be attributed
    # by coincidentally equal stop prices or shared exchange positions.
    if trigger_raw not in (None,"",0,"0"):
        return None,"UNMAPPED",kind
    if not order or kind is None:
        return None,"UNMAPPED",kind
    order_time = int(getattr(order,"created_ms",0) or 0)
    if order_time<=0:
        return None,"UNMAPPED",kind
    candidates = [
        spec for spec in specs
        if spec["signal_time_ms"] > 0
        and 0 <= order_time-spec["signal_time_ms"] <= MAX_FALLBACK_DELAY_MS
        and _compatible(order,spec)
        and _target_matches(order,spec,kind)
    ]
    if len(candidates)==1:
        return candidates[0],"PRICE_TIME_CANDIDATE",kind
    return None,("AMBIGUOUS" if len(candidates)>1 else "UNMAPPED"),kind


def build_exit_events(orders,fills,specs,forced=()):
    """Exchange fill PnL is per exit order and may use a shared position avg.

    It is intentionally NOT described as independent Combo strategy PnL.
    Entry fees, funding and other exit orders are not apportioned here.
    """
    order_by_id = {str(o.order_id):o for o in orders}
    forced_ids = {str(getattr(x,"order_id","")) for x in forced if getattr(x,"order_id","")}
    groups = defaultdict(list)
    for f in fills:
        if number(getattr(f,"qty",0))>0:
            groups[(str(f.order_id),str(f.symbol))].append(f)
    events=[]
    for (order_id,symbol),rows in groups.items():
        order=order_by_id.get(order_id)
        spec,confidence,kind=identify_exit(order_id,order,specs)
        if spec is not None and spec["symbol"] != symbol:
            spec,confidence=None,"CONFLICT"
        if order_id in forced_ids:
            kind="LIQUIDATION"
        elif kind is None or kind not in {"TP","SL","TRAILING"}:
            # Entry fills and unrelated market transactions are not exits.
            continue
        rows.sort(key=lambda f:(int(getattr(f,"timestamp_ms",0) or 0),str(f.trade_id)))
        qty=sum(number(f.qty) for f in rows)
        proceeds=sum(number(f.price)*number(f.qty) for f in rows)
        events.append({
            "symbol":symbol,
            "combo":spec["combo"] if spec else None,
            "entry_order_id":spec["entry_order_id"] if spec else None,
            "order_id":order_id,
            "type":kind,
            "confidence":confidence,
            "time_ms":max(int(getattr(f,"timestamp_ms",0) or 0) for f in rows),
            "qty":round(qty,9),
            "exit_price":round(proceeds/qty,8),
            "bingx_realized_pnl":round(sum(number(f.realized_pnl) for f in rows),8),
            "exit_fees":round(sum(number(f.fee) for f in rows),8),
            "fill_count":len(rows),
        })
    return sorted(events,key=lambda x:(x["time_ms"],x["order_id"]),reverse=True)
