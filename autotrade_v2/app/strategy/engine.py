from __future__ import annotations

from ..contracts import MarketSnapshot, TradeIntent
from .final14_exact_strategy import scan_latest


class StrategyEngine:
    """Pure FINAL14 adapter. No network, database, logging side effects, or execution."""

    def calculate(self, snapshot: MarketSnapshot) -> list[TradeIntent]:
        signals = scan_latest(snapshot.symbol, snapshot.candles)
        return [
            TradeIntent(
                event_id=s.event_id,
                symbol=s.symbol,
                combo=s.combo,
                side=s.side,
                timeframe=s.timeframe,
                close_time=s.close_time,
                entry=s.entry,
                tp=s.tp,
                sl=s.sl,
                smc_dir=s.smc_dir,
            )
            for s in signals
        ]
