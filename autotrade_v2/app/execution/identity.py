"""Read-only identification of the position created by a confirmed entry.

Do not confuse positionSide (LONG/SHORT) with a unique Separate Isolated
positionId. The pre-entry snapshot is persisted before any MARKET POST.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .quantity import PositionQuantityMismatch, _amount

def position_id(row: dict) -> str:
    """BingX uses both spellings. Conflicting aliases are never evidence."""
    values={str(row[k]) for k in ("positionId", "positionID") if row.get(k) not in (None, "", 0, "0")}
    if len(values)>1:
        raise PositionQuantityMismatch("conflicting BingX position ID aliases")
    return next(iter(values), "")



def verified_entry_order(order:dict, symbol:str, entry_side:str, client_id:str,
                         expected_order_id:str|None=None,*,require_filled=True)->tuple[Decimal,str]:
    """Prove the combo fill from its exact MARKET order, not a position delta."""
    ids={str(order[k]) for k in ("orderID","orderId") if order.get(k) not in (None, "")}
    clients={str(order[k]) for k in ("clientOrderId","clientOrderID") if order.get(k) not in (None, "")}
    oid=next(iter(ids), "")
    side=entry_side.upper()
    if (len(ids)!=1 or not re.fullmatch(r"[1-9][0-9]*",oid)
            or clients!={client_id} or (expected_order_id and oid!=str(expected_order_id))
            or order.get("symbol")!=symbol
            or str(order.get("side") or "").upper()!=side
            or str(order.get("positionSide") or "").upper()!=("LONG" if side=="BUY" else "SHORT")
            or str(order.get("type") or "").upper()!="MARKET"
            or str(order.get("status") or "").upper() not in (
                {"FILLED"} if require_filled else {"NEW","PARTIALLY_FILLED","FILLED","CANCELED","CANCELLED","EXPIRED","REJECTED"})):
        raise PositionQuantityMismatch("entry client/order/symbol/side/type/fill identity unverified")
    filled=_amount(order.get("executedQty"),"entry executedQty")
    original=_amount(order.get("origQty") or order.get("quantity"),"entry origQty")
    if original<=0 or (require_filled and filled<=0) or filled>original:
        raise PositionQuantityMismatch("entry fill quantity unverified")
    return filled,position_id(order)


def _same_side(positions:list[dict],symbol:str,entry_side:str)->dict[str,Decimal]:
    wanted="LONG" if entry_side.upper()=="BUY" else "SHORT"
    result={}
    for row in positions:
        if row.get("symbol")!=symbol or str(row.get("positionSide") or "").upper()!=wanted:
            continue
        amt=_amount(row.get("positionAmt"),"positionAmt")
        if amt<=0:
            continue
        pid=position_id(row)
        if not pid or pid in result:
            raise PositionQuantityMismatch("BingX positionId missing or duplicated")
        result[pid]=amt
    return result


def entry_baseline(positions:list[dict],symbol:str,entry_side:str)->dict[str,str]:
    """Call and persist BEFORE sending a new MARKET entry."""
    if not isinstance(positions,list):
        raise PositionQuantityMismatch("BingX pre-entry position list missing")
    return {pid:format(qty,"f") for pid,qty in _same_side(positions,symbol,entry_side).items()}


def identify_entry_position(
    positions:list[dict],symbol:str,entry_side:str,executed_qty,
    baseline:dict[str,str]|None=None,entry_position_id:str|None=None,
    *,entry_order:dict|None=None,client_order_id:str|None=None,
    expected_order_id:str|None=None,
)->str:
    """Resolve one ID, never by arbitrary list ordering or side alone.

    A validated exact MARKET/client ID with an explicit container ID proves
    routing independently of concurrent peer exits. The filled quantity remains
    owned by the combo's order, not by this shared container.

    Legacy baseline-based proof: exactly one NEW position with its quantity equal to
    the fill (Separate Isolated); OR exactly one old ID whose amount grew by
    exactly the fill (aggregated Hedge). Unrelated other IDs must be unchanged.
    If the exchange explicitly reports an ID on the entry itself, it must also
    exist in the fresh position snapshot.
    """
    current=_same_side(positions,symbol,entry_side)
    filled=_amount(executed_qty,"entry executedQty")
    if filled<=0:
        raise PositionQuantityMismatch("entry executedQty must be positive")
    if entry_order is not None:
        proven,pid=verified_entry_order(entry_order,symbol,entry_side,
                                        str(client_order_id or ""),expected_order_id)
        if proven!=filled:
            raise PositionQuantityMismatch("entry fill disagrees with requested combo quantity")
        if entry_position_id and pid and str(entry_position_id)!=pid:
            raise PositionQuantityMismatch("entry position ID aliases disagree")
        if not pid and baseline is None:
            raise PositionQuantityMismatch("entry lacks an explicit container ID and durable baseline")
        if pid:
            # An exchange order ties this exact fill to the container. A peer
            # exit changes container quantity, never the entry's own fill.
            # Do not reassign missing/rotated IDs from side or average price.
            if pid not in current or current[pid]<filled:
                raise PositionQuantityMismatch("filled entry container not confirmed in current snapshot")
            return pid
    if entry_position_id:
        pid=str(entry_position_id)
        if pid not in current or current[pid]<filled:
            raise PositionQuantityMismatch("entry positionId not confirmed in BingX position snapshot")
        # An entry's positionId is useful but must NOT bypass the durable
        # before/after delta proof: concurrent new same-side positions can
        # otherwise be silently attributed to the tracked trade.
        if baseline is None:
            return pid
    if baseline is None:
        if len(current)==1 and next(iter(current.values()))==filled:
            return next(iter(current))
        raise PositionQuantityMismatch("no pre-entry baseline; positionId cannot be assigned to this trade")
    previous={str(pid):_amount(qty,"baseline positionAmt") for pid,qty in baseline.items()}
    if not set(previous).issubset(current):
        raise PositionQuantityMismatch("a pre-existing position disappeared during entry")
    changed=[]
    for pid,qty in current.items():
        delta=qty-previous.get(pid,Decimal(0))
        if delta<0:
            raise PositionQuantityMismatch("unrelated position size decreased during entry")
        if delta:
            changed.append((pid,delta))
    if len(changed)!=1 or changed[0][1]!=filled:
        raise PositionQuantityMismatch("unable to attribute exactly one positionId to this entry")
    if entry_position_id and changed[0][0]!=str(entry_position_id):
        raise PositionQuantityMismatch("entry positionId disagrees with pre-entry delta")
    return changed[0][0]
