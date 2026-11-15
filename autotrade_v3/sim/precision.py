"""BingX contract precision and minimum order validation helpers.

Mirrors the verified Live rounding rules in `autotrade_v2/app/execution/final14_executor.py`
and `autotrade_v2/app/execution/quantity.py` using Decimal arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


@dataclass(frozen=True)
class BingXContractRules:
    """BingX perpetual swap contract precision and minimum order rules."""

    symbol: str
    quantity_precision: int
    price_precision: int
    trade_min_quantity: float
    trade_min_usdt: float = 0.0
    source_status: str = "UNVERIFIED_OFFLINE"


def round_price_half_up(price: float, price_precision: int) -> float:
    """Round trigger/order price to exchange `pricePrecision` using ROUND_HALF_UP."""
    if isinstance(price_precision, bool) or not 0 <= price_precision <= 12:
        raise ValueError(f"invalid BingX pricePrecision: {price_precision}")
    step = Decimal(1).scaleb(-price_precision)
    return float(Decimal(str(price)).quantize(step, rounding=ROUND_HALF_UP))


def round_qty_down(qty: float, quantity_precision: int) -> float:
    """Round order quantity down to exchange `quantityPrecision` using ROUND_DOWN."""
    if isinstance(quantity_precision, bool) or not 0 <= quantity_precision <= 12:
        raise ValueError(f"invalid BingX quantityPrecision: {quantity_precision}")
    step = Decimal(1).scaleb(-quantity_precision)
    return float(Decimal(str(qty)).quantize(step, rounding=ROUND_DOWN))


def validate_min_order(qty: float, price: float, rules: BingXContractRules) -> tuple[bool, str]:
    """Validate rounded order quantity against `tradeMinQuantity` and `tradeMinUSDT`."""
    q = Decimal(str(qty))
    p = Decimal(str(price))
    min_q = Decimal(str(rules.trade_min_quantity))
    min_usdt = Decimal(str(rules.trade_min_usdt))
    if q <= 0 or q < min_q:
        return False, "BELOW_TRADE_MIN_QUANTITY"
    if min_usdt > 0 and (q * p) < min_usdt:
        return False, "BELOW_TRADE_MIN_USDT"
    return True, "OK"
