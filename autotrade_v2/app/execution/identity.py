"""Read-only identification of the position created by a confirmed entry.

Do not confuse positionSide (LONG/SHORT) with a unique Separate Isolated
positionId. The pre-entry snapshot is persisted before any MARKET POST.
"""
from __future__ import annotations

from decimal import Decimal

from .quantity import PositionQuantityMismatch, _amount


def _same_side(positions:list[dict],symbol:str,entry_side:str)->dict[str,Decimal]:
    wanted="LONG" if entry_side.upper()=="BUY" else "SHORT"
    result={}
    for row in positions:
        if row.get("symbol")!=symbol or str(row.get("positionSide") or "").upper()!=wanted:
            continue
        amt=_amount(row.get("positionAmt"),"positionAmt")
        if amt<=0:
            continue
        pid=str(row.get("positionId") or "")
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
)->str:
    """Resolve one ID, never by arbitrary list ordering or side alone.

    Baseline-based proof: exactly one NEW position with its quantity equal to
    the fill (Separate Isolated); OR exactly one old ID whose amount grew by
    exactly the fill (aggregated Hedge). Unrelated other IDs must be unchanged.
    If the exchange explicitly reports an ID on the entry itself, it must also
    exist in the fresh position snapshot.
    """
    current=_same_side(positions,symbol,entry_side)
    filled=_amount(executed_qty,"entry executedQty")
    if filled<=0:
        raise PositionQuantityMismatch("entry executedQty must be positive")
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
