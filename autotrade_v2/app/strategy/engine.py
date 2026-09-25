from __future__ import annotations

from ..contracts import MarketSnapshot, TradeIntent
from ..control.errors import StrategyInsufficientCandlesError
from .final14_exact_strategy import MIN_LIVE_15M_BARS, scan_latest


class StrategyEngine:
    """Pure FINAL14 + NEW6 adapter. No network, database, logging side effects, or execution."""

    def calculate(self, snapshot: MarketSnapshot) -> list[TradeIntent]:
        available = int(len(snapshot.candles))
        if available < MIN_LIVE_15M_BARS:
            raise StrategyInsufficientCandlesError(
                snapshot.symbol,
                available=available,
                required=MIN_LIVE_15M_BARS,
            )

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
