"""Historical Funding Rate module for V3 Lab backtest engine (Mode C / BINGX_PORTFOLIO)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FundingEventRecord:
    """Single realized funding settlement cashflow for an open leg."""

    funding_time_ms: int
    funding_time_utc: str
    symbol: str
    side: Literal["BUY", "SELL"]
    trade_id: str
    leg_id: str
    combo_id: int
    combo_name: str
    leg_qty: float
    settlement_price: float
    position_notional: float
    funding_rate: float
    direction: int
    funding_cashflow: float
    source: str


@dataclass
class FundingTable:
    """Immutable-style lookup table of funding rates keyed by (symbol, funding_time_ms)."""

    rates_by_sym: dict[str, dict[int, float]] = field(default_factory=dict)
    mark_px_by_sym: dict[str, dict[int, float]] = field(default_factory=dict)
    source_by_sym: dict[str, dict[int, str]] = field(default_factory=dict)
    all_timestamps: set[int] = field(default_factory=set)

    @classmethod
    def from_dataframes(cls, dfs_by_symbol: dict[str, pd.DataFrame]) -> FundingTable:
        rates_by_sym: dict[str, dict[int, float]] = {}
        mark_px_by_sym: dict[str, dict[int, float]] = {}
        source_by_sym: dict[str, dict[int, str]] = {}
        all_ts: set[int] = set()

        for sym, df in dfs_by_symbol.items():
            if df is None or df.empty:
                continue
            r_map: dict[int, float] = {}
            m_map: dict[int, float] = {}
            s_map: dict[int, str] = {}
            # Deduplicate by funding_time_ms keeping last if any
            dedup = df.drop_duplicates(subset=["funding_time_ms"], keep="last")
            for row in dedup.itertuples(index=False):
                ts = int(row.funding_time_ms)
                rate = float(row.funding_rate)
                if not np.isfinite(rate):
                    continue
                r_map[ts] = rate
                mpx = getattr(row, "mark_price", np.nan)
                if mpx is not None and np.isfinite(mpx) and float(mpx) > 0.0:
                    m_map[ts] = float(mpx)
                s_map[ts] = str(getattr(row, "source", "unknown"))
                all_ts.add(ts)
            rates_by_sym[sym] = r_map
            mark_px_by_sym[sym] = m_map
            source_by_sym[sym] = s_map

        return cls(
            rates_by_sym=rates_by_sym,
            mark_px_by_sym=mark_px_by_sym,
            source_by_sym=source_by_sym,
            all_timestamps=all_ts,
        )

    @classmethod
    def from_parquets(cls, paths_by_symbol: dict[str, Path]) -> FundingTable:
        dfs = {sym: pd.read_parquet(p) for sym, p in paths_by_symbol.items()}
        return cls.from_dataframes(dfs)

    def get(self, symbol: str, funding_time_ms: int) -> tuple[float, float | None, str] | None:
        r_map = self.rates_by_sym.get(symbol)
        if not r_map or funding_time_ms not in r_map:
            return None
        rate = r_map[funding_time_ms]
        mpx = self.mark_px_by_sym.get(symbol, {}).get(funding_time_ms)
        src = self.source_by_sym.get(symbol, {}).get(funding_time_ms, "unknown")
        return rate, mpx, src


def compute_funding_cashflow(
    *,
    position_notional: float,
    funding_rate: float,
    side: Literal["BUY", "SELL"],
) -> float:
    """Compute Funding Cashflow = - Position Notional * Funding Rate * Position Direction.

    - BUY (LONG): direction = +1 (positive rate => negative cashflow / paid)
    - SELL (SHORT): direction = -1 (positive rate => positive cashflow / received)
    """
    direction = 1 if side == "BUY" else -1
    return -float(position_notional) * float(funding_rate) * direction
