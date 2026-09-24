from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    timeframe: str
    candles: pd.DataFrame
    latest_open_time: int
    latest_close_time: int
    validation: dict[str, Any]


@dataclass(frozen=True)
class TradeIntent:
    event_id: str
    symbol: str
    combo: int
    side: str
    timeframe: str
    close_time: int
    entry: float
    tp: float
    sl: float
    smc_dir: int


@dataclass(frozen=True)
class ExecutionResult:
    event_id: str
    accepted: bool
    status: str
    order_id: str | None = None
    details: dict[str, Any] | None = None
