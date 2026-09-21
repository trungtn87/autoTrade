from __future__ import annotations

import json
import logging
from typing import Any

from .final14_config import case_name

log=logging.getLogger(__name__)
STATE_KEY="final14_active_cases_v1"


def _load(state)->list[dict[str,Any]]:
    raw=state.get_runtime_value(STATE_KEY,"[]")
    try:
        data=json.loads(raw or "[]")
    except Exception:
        data=[]
    return [x for x in data if isinstance(x,dict)]


def _save(state,rows:list[dict[str,Any]])->None:
    state.set_runtime_value(
        STATE_KEY,
        json.dumps(rows,ensure_ascii=False,separators=(",",":")),
    )


def case_id(symbol:str,combo:int)->str:
    return f"{symbol.upper()}|{case_name(combo)}"


def is_case_active(state,symbol:str,combo:int)->bool:
    cid=case_id(symbol,combo)
    return any(x.get("case_id")==cid for x in _load(state))


def register_execution(state,signal,result:dict)->dict:
    cid=case_id(signal.symbol,signal.combo)
    rows=[x for x in _load(state) if x.get("case_id")!=cid]
    rec={
        "case_id":cid,
        "symbol":signal.symbol,
        "combo":int(signal.combo),
        "side":signal.side,
        "position_side":"LONG" if signal.side=="BUY" else "SHORT",
        "event_id":signal.event_id,
        "entry_close_time":int(signal.close_time),
        "entry_order_id":str(result.get("order_id") or ""),
        "position_id":str(result.get("position_id") or ""),
        "avg_price":float(result.get("avg_price") or 0),
        "qty":float(result.get("executed_qty") or 0),
        "tp":float(result.get("tp") or signal.tp),
        "sl":float(result.get("sl") or signal.sl),
        "rr":float(result.get("rr") or 0),
    }
    rows.append(rec)
    _save(state,rows)
    log.info(
        "FINAL14_STATE_REGISTER case_id=%s position_id=%s order_id=%s",
        cid,rec["position_id"],rec["entry_order_id"],
    )
    return rec


def _active_positions(executor,symbol:str)->list[dict]:
    rows=executor._positions(symbol)
    out=[]
    for row in rows:
        try:
            amt=abs(float(row.get("positionAmt") or 0))
        except Exception:
            amt=0.0
        if amt>0:
            out.append(row)
    return out


def _resolve_unassigned(rec:dict,positions:list[dict],claimed:set[str])->str:
    candidates=[]
    for pos in positions:
        pid=str(pos.get("positionId") or "")
        if not pid or pid in claimed:
            continue
        if str(pos.get("positionSide") or "").upper()!=str(rec.get("position_side") or "").upper():
            continue
        try:
            qty=abs(float(pos.get("positionAmt") or 0))
            px=float(pos.get("avgPrice") or 0)
        except Exception:
            continue
        qty0=max(float(rec.get("qty") or 0),1e-12)
        px0=max(float(rec.get("avg_price") or 0),1e-12)
        qty_err=abs(qty-qty0)/qty0
        px_err=abs(px-px0)/px0
        candidates.append((qty_err+px_err,qty_err,px_err,pid))
    if not candidates:
        return ""
    candidates.sort()
    # Separate-isolated normally yields one obvious new position. Keep the
    # record unresolved if matching is materially ambiguous.
    best=candidates[0]
    if best[1]>0.05 or best[2]>0.01:
        return ""
    if len(candidates)>1 and abs(candidates[1][0]-best[0])<1e-6:
        return ""
    return best[3]


def refresh_symbol(state,executor,symbol:str)->dict:
    rows=_load(state)
    symbol_rows=[r for r in rows if r.get("symbol")==symbol]
    if not symbol_rows:
        return {"symbol":symbol,"active":0,"closed":[],"resolved":[]}

    positions=_active_positions(executor,symbol)
    pos_by_id={str(p.get("positionId") or ""):p for p in positions if p.get("positionId")}
    claimed={str(r.get("position_id") or "") for r in symbol_rows if r.get("position_id")}
    closed=[];resolved=[];kept=[]

    for rec in rows:
        if rec.get("symbol")!=symbol:
            kept.append(rec)
            continue
        pid=str(rec.get("position_id") or "")
        if pid:
            if pid in pos_by_id:
                kept.append(rec)
            else:
                closed.append(rec["case_id"])
                log.info(
                    "FINAL14_POSITION_CLOSED case_id=%s position_id=%s",
                    rec["case_id"],pid,
                )
            continue

        new_pid=_resolve_unassigned(rec,positions,claimed)
        if new_pid:
            rec["position_id"]=new_pid
            claimed.add(new_pid)
            resolved.append({"case_id":rec["case_id"],"position_id":new_pid})
            log.warning(
                "FINAL14_POSITION_ID_RESOLVED case_id=%s position_id=%s",
                rec["case_id"],new_pid,
            )
        kept.append(rec)

    _save(state,kept)
    active=sum(1 for r in kept if r.get("symbol")==symbol)
    return {
        "symbol":symbol,
        "active":active,
        "closed":closed,
        "resolved":resolved,
    }


def snapshot(state)->list[dict]:
    return _load(state)
