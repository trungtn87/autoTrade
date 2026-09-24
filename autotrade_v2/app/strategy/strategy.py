from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    symbol: str
    combo: int
    side: str
    timeframe: str
    close_time: int
    entry: float
    tp: float
    sl: float
    smc_dir: int

    @property
    def event_id(self) -> str:
        return f"{self.symbol}|{self.timeframe}|C{self.combo}|{self.side}|{self.close_time}"
