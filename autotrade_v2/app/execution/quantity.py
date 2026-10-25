"""Exchange-authoritative quantity checks for partial exits. No float arithmetic."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN


class PositionQuantityMismatch(ValueError):
    """Do not split an aggregated or changed position into strategy-level exits."""


def _amount(value, label:str)->Decimal:
    try:
        result=Decimal(str(value))
    except (InvalidOperation,TypeError) as exc:
        raise ValueError(f"invalid {label} from BingX") from exc
    if not result.is_finite() or result<0:
        raise ValueError(f"invalid {label} from BingX")
    return result


def confirmed_position_qty(
    positions:list[dict],symbol:str,entry_side:str,executed_qty
)->tuple[str,str|None]:
    """Read actual positionAmt; reject aggregated/shared positions and timing races.

    executedQty identifies this trade's fill; positionAmt identifies what is
    actually open NOW. If they differ, there is no safe per-trade allocation.
    """
    side="LONG" if entry_side.upper()=="BUY" else "SHORT"
    candidates=[]
    for row in positions:
        if row.get("symbol")!=symbol or str(row.get("positionSide") or "").upper()!=side:
            continue
        amount=_amount(row.get("positionAmt"),"positionAmt")
        if amount>0:
            candidates.append((row,amount))
    if len(candidates)!=1:
        raise PositionQuantityMismatch(
            f"BingX returned {len(candidates)} active {symbol} {side} positions; cannot allocate partial exits"
        )
    position,remaining=candidates[0]
    filled=_amount(executed_qty,"executedQty")
    if filled<=0 or remaining!=filled:
        raise PositionQuantityMismatch(
            f"BingX positionAmt={remaining} differs from executedQty={filled}; "
            "another trade or partial exit may share the position"
        )
    return format(remaining,"f"),str(position.get("positionId") or "") or None


def remaining_position_qty(
    positions:list[dict],symbol:str,entry_side:str,expected_position_id:str|None=None
)->str:
    """Fresh BingX positionAmt for SL resizing; never subtract hypothetical TP fills.

    No position of this symbol/side means fully closed. If the tracked position
    ID disappears while another one exists, refuse to confuse their sizes.
    """
    side="LONG" if entry_side.upper()=="BUY" else "SHORT"
    candidates=[row for row in positions if row.get("symbol")==symbol
                and str(row.get("positionSide") or "").upper()==side]
    if expected_position_id:
        selected=[row for row in candidates if str(row.get("positionId") or "")==str(expected_position_id)]
        if not selected and candidates:
            raise PositionQuantityMismatch("tracked positionId no longer matches BingX position")
    else:
        selected=candidates
    if not selected:
        return "0"
    if len(selected)!=1:
        raise PositionQuantityMismatch("multiple positions share this symbol/side")
    return format(_amount(selected[0].get("positionAmt"),"positionAmt"),"f")


def allocate_exit_qty(
    actual_qty,contract_rules:dict,reference_price
)->dict:
    """Split exact exchange quantity into valid leg sizes or use 100% hard TP.

    Never use float math, silently round away units, or invent a remaining SL
    size by subtracting expected fills. SL reconciliation uses a fresh positionAmt.
    """
    qty=_amount(actual_qty,"positionAmt")
    price=_amount(reference_price,"reference price")
    precision=contract_rules.get("quantityPrecision")
    if isinstance(precision,bool) or not isinstance(precision,int) or not 0<=precision<=12:
        raise ValueError("invalid BingX quantityPrecision")
    step=Decimal(1).scaleb(-precision)
    minimum=_amount(contract_rules.get("tradeMinQuantity"),"tradeMinQuantity")
    min_usdt=_amount(contract_rules.get("tradeMinUSDT",0),"tradeMinUSDT")
    if qty<=0 or price<=0 or minimum<=0 or qty!=qty.quantize(step):
        raise ValueError("BingX positionAmt/contract step or minimum is invalid")
    half=(qty/2).quantize(step,rounding=ROUND_DOWN)
    other=qty-half
    if half+other!=qty:
        raise ValueError("exit quantities do not equal the exchange positionAmt")
    valid=(
        half>=minimum and other>=minimum and
        half*price>=min_usdt and other*price>=min_usdt
    )
    return {
        "mode":"split_trailing" if valid else "hard_tp",
        "reason":None if valid else "HALF_BELOW_BINGX_MINIMUM",
        "position_qty":format(qty,"f"),
        "tp_qty":format(other if valid else qty,"f"),
        "trailing_qty":format(half if valid else Decimal(0),"f"),
        "quantity_precision":precision,
        "min_order_qty":format(minimum,"f"),
        "min_order_usdt":format(min_usdt,"f"),
    }
