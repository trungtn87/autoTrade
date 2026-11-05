"""Read-only combo ledger over existing execution states and exact exchange orders.

A position ID identifies an exchange container, never a combo. Quantities are
attributed only through an entry client ID and owned exit/trigger IDs. No writes
or exchange mutations occur here; a disagreement locks the entire side.
"""
from __future__ import annotations

from decimal import Decimal

from .identity import position_id
from .exit_cleanup import (CleanupUncertain, inspect_one, positive, oid, ACTIVE)


def entry_proof(state, row, client_id):
    intent=state["intent"]
    cid=str(row.get("clientOrderId") or row.get("clientOrderID") or "")
    side=str(intent["side"]).upper()
    if (not oid(row) or cid != client_id
            or row.get("symbol") != intent["symbol"]
            or str(row.get("side") or "").upper() != side
            or str(row.get("positionSide") or "").upper() != ("SHORT" if side=="SELL" else "LONG")
            or str(row.get("type") or "").upper() != "MARKET"
            or str(row.get("status") or "").upper() != "FILLED"):
        raise CleanupUncertain("entry identity/fill not verified")
    filled=positive(row.get("executedQty"), "entry executedQty")
    if filled<=0 or filled>positive(row.get("origQty") or row.get("quantity"), "entry origQty"):
        raise CleanupUncertain("entry quantity not verified")
    if state.get("order_id") and str(state["order_id"]) != oid(row):
        raise CleanupUncertain("entry order ID changed")
    return filled, position_id(row)


def reconcile_side(states, positions, open_by_id, history_by_id):
    """Return per-event ledgers and group risk; never assign a size delta.

    states must contain ALL known events of this symbol/side, including pending
    and completed events. Historical evidence must be a non-ambiguous ID map.
    """
    if not states:
        raise CleanupUncertain("no tracked combos")
    first=states[0][1]["intent"]
    symbol,side=first["symbol"],str(first["side"]).upper()
    wanted="SHORT" if side=="SELL" else "LONG"
    actual=Decimal(0); containers={}
    for row in positions:
        if row.get("symbol")!=symbol or str(row.get("positionSide") or "").upper()!=wanted:
            continue
        pid=position_id(row)
        if not pid or pid in containers:
            raise CleanupUncertain("position identity missing/duplicated")
        amount=positive(row.get("positionAmt"),"positionAmt")
        containers[pid]=amount
        actual+=amount
    owners={}; ledgers={}; expected=Decimal(0); risks=[]
    for event_id,state in states:
        if state["intent"]["symbol"]!=symbol or str(state["intent"]["side"]).upper()!=side:
            raise CleanupUncertain("mixed side ledger")
        # Rejected/absent entries have no quantity. Unresolved submissions do.
        if not state.get("entry_filled"):
            if not state.get("terminal"):
                risks.append(f"{event_id}:ENTRY_UNVERIFIED")
            continue
        initial=positive(state.get("executed_qty"),"entry executedQty")
        if initial<=0:
            raise CleanupUncertain("nonpositive entry fill")
        for kind,item in (state.get("protection") or {}).items():
            order_id=str((item or {}).get("order_id") or "")
            if not order_id:
                continue
            if order_id in owners:
                raise CleanupUncertain("protection order claimed by multiple combos")
            owners[order_id]=(event_id,kind)
        if not state.get("protection"):
            remaining=initial; monitor={"fills_verified":False}
            risks.append(f"{event_id}:PROTECTION_UNVERIFIED")
        else:
            # Do not trust 'done' as a quantity proof; recalculate owned fills.
            check={**state,"exit_cleanup":{**(state.get("exit_cleanup") or {}),"status":"watching"}}
            monitor,_,_=inspect_one(None,check,open_by_id,positions,historical_by_id=history_by_id)
            remaining=positive(monitor.get("remaining_qty"),"remaining_qty")
            if not monitor.get("fills_verified"):
                risks.append(f"{event_id}:EXIT_HISTORY_UNVERIFIED")
        expected+=remaining
        quantities={kind:(monitor.get("orders",{}).get(str(item.get("order_id") or "")) or {}).get("executed_qty")
                    for kind,item in (state.get("protection") or {}).items() if isinstance(item,dict)}
        ledgers[event_id]={"entry_qty":str(initial),"remaining_qty":str(remaining),
                           "exit_filled_qty_by_kind":quantities,
                           "monitor":monitor,"position_id":state.get("position_id")}
        if remaining:
            sl=(state.get("protection") or {}).get("sl") or {}
            slrow=open_by_id.get(str(sl.get("order_id") or ""))
            if slrow is None:
                risks.append(f"{event_id}:NO_VERIFIED_ACTIVE_SL")
            elif positive(slrow.get("origQty") or slrow.get("quantity"),"SL qty")-positive(slrow.get("executedQty"),"SL executedQty")!=remaining:
                risks.append(f"{event_id}:SL_QUANTITY_MISMATCH")
    if expected!=actual:
        risks.append("POSITION_LEDGER_MISMATCH")
    executable=Decimal(0)
    for order_id,row in open_by_id.items():
        if row.get("symbol")!=symbol or str(row.get("positionSide") or "").upper()!=wanted:
            continue
        if str(row.get("side") or "").upper()==side:
            risks.append("UNTRACKED_ENTRY_ORDER")
            continue
        if order_id not in owners:
            risks.append("UNOWNED_EXIT_ORDER")
        if str(row.get("status") or "").upper() not in ACTIVE:
            raise CleanupUncertain("inactive order in open snapshot")
        executable+=positive(row.get("origQty") or row.get("quantity"),"exit origQty")-positive(row.get("executedQty"),"exit executedQty")
    # No assumed OCO or side-wide reduce-only guarantee. An overcommitted
    # container cannot be declared safe just because every order is a close.
    if executable>actual:
        risks.append("EXECUTABLE_EXIT_BUDGET_EXCEEDED")
    return {"status":"RECONCILE_REQUIRED" if risks else "VERIFIED",
            "actual_qty":str(actual),"tracked_qty":str(expected),
            "executable_exit_qty":str(executable),"risks":sorted(set(risks)),
            "shared":sum(1 for x in ledgers.values() if Decimal(x["remaining_qty"])>0)>1,
            "combos":ledgers}
