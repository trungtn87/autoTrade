"""Risk, leverage, fee, and sizing models for AutoTrade V3."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RiskConfig:
    initial_equity: float = 1000.0
    notional_per_trade: float = 100.0
    leverage_btc: float = 100.0
    leverage_eth: float = 50.0
    taker_fee_rate: float = 0.0005  # 0.05% taker fee per side
    maker_fee_rate: float = 0.0002  # 0.02% maker fee per side
    slippage_rate: float = 0.0      # Configurable slippage fraction
    fee_mode: Literal["taker", "maker"] = "taker"

    def get_leverage(self, symbol: str) -> float:
        sym = symbol.upper()
        if "BTC" in sym:
            return self.leverage_btc
        if "ETH" in sym:
            return self.leverage_eth
        return 50.0

    def calc_margin(self, symbol: str, notional: float | None = None) -> float:
        n = notional if notional is not None else self.notional_per_trade
        return n / self.get_leverage(symbol)

    def calc_fee(self, notional: float, is_taker: bool = True) -> float:
        rate = self.taker_fee_rate if is_taker else self.maker_fee_rate
        return notional * rate

    def apply_slippage(self, price: float, side: str, is_entry: bool) -> float:
        """Apply slippage conservatively against the trade direction.

        - For BUY entry: executed at a higher price
        - For BUY exit (closing short): executed at a higher price
        - For SELL entry: executed at a lower price
        - For SELL exit (closing long): executed at a lower price
        """
        if self.slippage_rate <= 0:
            return price
        if side.upper() in ("BUY", "L"):
            return price * (1.0 + self.slippage_rate) if is_entry else price * (1.0 - self.slippage_rate)
        else:  # SELL / S
            return price * (1.0 - self.slippage_rate) if is_entry else price * (1.0 + self.slippage_rate)
