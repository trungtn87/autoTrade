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



def _structurally_present_pending_trailing(state, open_by_id):
    """Exact-ID proof of a live trailing order, NOT proof of callback/activation.

    Historical BingX order snapshots may omit activationPrice and priceRate.
    Only use this proof to classify a parser/parameter issue as a warning,
    never to mark the pending protection journal VERIFIED or resend the POST.
    A stale/closed/wrong order, numeric mismatch, or missing position ID fails
    closed and retains the ordinary side-level reconciliation blockers.
    """
    if state.get("exit_mode")!="split_trailing" or (state.get("protection") or {}).get("trailing"):
        return ""
    pending=(state.get("protection_journal") or {}).get("trailing") or {}
    if pending.get("phase") not in ("prepared", "acknowledged", None):
        return ""
    acknowledged=str(pending.get("acknowledged_order_id") or "")
    spec=pending.get("spec") or {}
    row=open_by_id.get(acknowledged)
    if not acknowledged or not isinstance(row,dict) or oid(row)!=acknowledged:
        return ""
    intent=state.get("intent") or {}
    side="BUY" if intent.get("side")=="SELL" else "SELL"
    position_side="SHORT" if intent.get("side")=="SELL" else "LONG"
    pid=str(state.get("position_id") or "")
    if (spec.get("kind")!="trailing"
            or spec.get("symbol")!=intent.get("symbol")
            or spec.get("side")!=side
            or spec.get("position_side")!=position_side
            or str(spec.get("position_id") or "")!=pid
            or not pid
            or row.get("symbol")!=intent.get("symbol")
            or str(row.get("type") or "").upper()!="TRAILING_STOP_MARKET"
            or str(row.get("side") or "").upper()!=side
            or str(row.get("positionSide") or "").upper()!=position_side
            or str(row.get("status") or "").upper()!="NEW"):
        return ""
    try:
        if position_id(row)!=pid:
            return ""
        qty=positive(row.get("origQty") or row.get("quantity"),"trailing origQty")
        executed=positive(row.get("executedQty"),"trailing executedQty")
        requested=positive(spec.get("quantity"),"trailing requested quantity")
        trigger=positive(spec.get("trigger"),"trailing requested activation")
        callback=positive(spec.get("callback_rate"),"trailing requested callback")
        if (qty<=0 or qty!=requested or executed!=0 or trigger<=0 or callback<=0):
            return ""
        # Explicit exchange values cannot be overridden by an absent-field
        # compatibility rule. A value disagreeing with the POST spec BLOCKS.
        for key,wanted in (("activationPrice",trigger),("priceRate",callback)):
            reported=row.get(key)
            if reported not in (None,"") and positive(reported,key)!=wanted:
                return ""
    except CleanupUncertain:
        return ""
    return acknowledged

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
    owners={}; ledgers={}; expected=Decimal(0); risks=[];warnings=[]
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
        for kind,prepared in (state.get("protection_journal") or {}).items():
            acknowledged=str(prepared.get("acknowledged_order_id") or "")
            if acknowledged:
                if acknowledged in owners and owners[acknowledged]!=(event_id,kind):
                    raise CleanupUncertain("acknowledged protection ID claimed by another combo")
                # Exact durable POST acknowledgement proves ownership, not
                # activation/parameters. The journal must still verify those.
                owners[acknowledged]=(event_id,kind)
        abort=state.get("abort") or {}
        if abort and abort.get("phase")!="closed":
            risks.append(f"{event_id}:ABORT_IN_PROGRESS")
        # An exact acknowledged trailing may be visible on BingX even when
        # GET omits activation/callback. Count its *observed* zero-fill order
        # in this READ-ONLY ledger; never persist it as verified protection.
        pending_trailing=_structurally_present_pending_trailing(state,open_by_id)
        observed_protection=dict(state.get("protection") or {})
        if pending_trailing:
            observed_protection["trailing"]={"order_id":pending_trailing}
            warnings.append(f"{event_id}:TRAILING_PARAMS_UNVERIFIED")
        if not observed_protection and not abort.get("close_order") and not state.get("force_close_allocation"):
            remaining=initial; monitor={"fills_verified":False}
            risks.append(f"{event_id}:PROTECTION_UNVERIFIED")
        else:
            # Do not trust 'done' as a quantity proof; recalculate owned fills.
            check={**state,"protection":observed_protection,"exit_cleanup":{**(state.get("exit_cleanup") or {}),"status":"watching"}}
            monitor,_,_=inspect_one(None,check,open_by_id,positions,historical_by_id=history_by_id,allow_incomplete=True)
            remaining=positive(monitor.get("remaining_qty"),"remaining_qty")
            if not monitor.get("fills_verified"):
                risks.append(f"{event_id}:EXIT_HISTORY_UNVERIFIED")
        expected+=remaining
        quantities={kind:(monitor.get("orders",{}).get(str(item.get("order_id") or "")) or {}).get("executed_qty")
                    for kind,item in (state.get("protection") or {}).items() if isinstance(item,dict)}
        ledgers[event_id]={"entry_qty":str(initial),"remaining_qty":str(remaining),
                           "exit_filled_qty_by_kind":quantities,
                           "monitor":monitor,"position_id":state.get("position_id")}
        if any(
            item.get("phase")!="rejected" and
            not (kind=="trailing" and pending_trailing)
            for kind,item in (state.get("protection_journal") or {}).items()
        ):
            risks.append(f"{event_id}:PROTECTION_SUBMISSION_UNRESOLVED")
        if remaining:
            required={"sl","tp"}|({"trailing"} if state.get("exit_mode")=="split_trailing" else set())
            for kind in required:
                item=observed_protection.get(kind) or {}
                order_id=str(item.get("order_id") or "")
                observed=(monitor.get("orders") or {}).get(order_id) or {}
                if order_id not in open_by_id and positive(observed.get("executed_qty",0),"exit fill")==0:
                    risks.append(f"{event_id}:NO_VERIFIED_ACTIVE_{kind.upper()}")
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
        original=positive(row.get("origQty") or row.get("quantity"),"exit origQty")
        executed=positive(row.get("executedQty"),"exit executedQty")
        if executed>original:
            raise CleanupUncertain("exit executed quantity exceeds original")
        if order_id in owners:
            owner,_=owners[order_id]
            if positive(ledgers[owner]["remaining_qty"],"owned remaining")==0 and original>executed:
                risks.append(f"{owner}:CLOSED_COMBO_ACTIVE_EXIT")
        executable+=original-executed
    # No assumed OCO or side-wide reduce-only guarantee. An overcommitted
    # container cannot be declared safe just because every order is a close.
    if executable>actual:
        risks.append("EXECUTABLE_EXIT_BUDGET_EXCEEDED")
    # Exit-budget-only is an existing diagnostic, not an admission blocker.
    # Do not let that diagnostic turn a structurally-proven trailing warning
    # into the same RECONCILE_REQUIRED status as genuinely missing SL/exposure.
    material_risks=[risk for risk in risks if risk!="EXECUTABLE_EXIT_BUDGET_EXCEEDED"]
    return {"status":("RECONCILE_REQUIRED" if material_risks else
                      "ADMISSION_SAFE_TRAILING_PARAMS_UNVERIFIED" if warnings else
                      "RECONCILE_REQUIRED" if risks else "VERIFIED"),
            "actual_qty":str(actual),"tracked_qty":str(expected),
            "executable_exit_qty":str(executable),"risks":sorted(set(risks)),
            "warnings":sorted(set(warnings)),
            "shared":sum(1 for x in ledgers.values() if Decimal(x["remaining_qty"])>0)>1,
            "combos":ledgers}
