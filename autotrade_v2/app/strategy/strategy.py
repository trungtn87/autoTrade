from __future__ import annotations

from dataclasses import dataclass


def _case_token(combo:int)->str:
    combo=int(combo)
    if 101 <= combo <= 106:
        return f"C{combo-90}"
    return "TIER" if combo==11 else f"C{combo}"


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
        return f"{self.symbol}|{self.timeframe}|{_case_token(self.combo)}|{self.side}|{self.close_time}"
