"""Read-only confirmation of a specific BingX Separate Isolated position.

A FILLED close order does not establish that the entire position is flat.
Never sum other same-side positions or infer ownership from LONG/SHORT alone.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

class PositionConfirmationError(RuntimeError):
    pass

def position_remaining(client, symbol: str, entry_side: str, position_id: str) -> Decimal:
    pid = str(position_id or "")
    if not pid.isdecimal() or int(pid) <= 0:
        raise PositionConfirmationError("confirmed numeric positionId required")
    rows = client.get_positions(symbol)
    if not isinstance(rows, list):
        raise PositionConfirmationError("BingX positions response malformed")
    side = "LONG" if entry_side.upper() == "BUY" else "SHORT"
    matches = [
        row for row in rows if row.get("symbol") == symbol
        and str(row.get("positionSide") or "").upper() == side
        and str(row.get("positionId") or "") == pid
    ]
    if len(matches) > 1:
        raise PositionConfirmationError("BingX returned duplicate positionId")
    if not matches:
        return Decimal("0")
    try:
        qty = Decimal(str(matches[0].get("positionAmt")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PositionConfirmationError("BingX positionAmt missing/invalid") from exc
    if not qty.is_finite() or qty < 0:
        raise PositionConfirmationError("BingX positionAmt invalid")
    return qty
