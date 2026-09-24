from __future__ import annotations

import time

import pandas as pd

from ..contracts import MarketSnapshot
from ..control.errors import DataLayerError, StrategyInsufficientCandlesError
from .historical import HistoricalKlineClient
from .store import CandleStore
from .validator import DataValidationError, validate_15m_candles


STEP_15M_MS=15*60_000


class DataService:
    """Layer 1: historical REST/bootstrap + WebSocket closed candles -> DB.

    Strategy never receives network data directly. The database is the only
    source used to construct MarketSnapshot.
    """

    def __init__(
        self,
        historical: HistoricalKlineClient,
        store: CandleStore,
        *,
        keep: int = 3400,
        required: int = 3400,
    ):
        self.historical = historical
        self.store = store
        self.keep = int(keep)
        self.required = int(required)

    def bootstrap(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        """Use a valid stored window or rebuild the required window from REST."""
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)
        try:
            return self.snapshot_from_store(symbol, now_ms)
        except (DataLayerError, StrategyInsufficientCandlesError):
            pass

        candles = self.historical.fetch_history(symbol, self.required, now_ms)
        self.store.upsert(symbol, "15m", candles)
        self.store.trim(symbol, "15m", self.keep)
        return self.snapshot_from_store(symbol, now_ms)

    def ingest_closed_candle(
        self,
        symbol: str,
        candle: pd.DataFrame,
        now_ms: int | None = None,
    ) -> MarketSnapshot:
        """Persist one closed WS candle and return a validated strategy snapshot."""
        symbol = symbol.upper()
        if candle is None or len(candle) != 1:
            raise DataLayerError("WebSocket ingest requires exactly one closed candle")
        self.store.upsert(symbol, "15m", candle)
        self.store.trim(symbol, "15m", self.keep)
        now_ms = int(now_ms or max(time.time() * 1000, int(candle.iloc[-1]["close_time"]) + 1))
        return self.snapshot_from_store(symbol, now_ms)

    def recover_missing_closed(
        self,
        symbol: str,
        now_ms: int | None = None,
    ) -> tuple[MarketSnapshot,list[int]]:
        """Repair only missing candles in the latest required 15m window."""
        symbol=symbol.upper()
        now_ms=int(now_ms or time.time()*1000)
        expected_latest=(now_ms//STEP_15M_MS)*STEP_15M_MS-STEP_15M_MS
        first_required=expected_latest-(self.required-1)*STEP_15M_MS

        current=self.store.load(symbol,"15m",None)
        existing={
            int(x)
            for x in current["open_time"].tolist()
            if first_required<=int(x)<=expected_latest
        } if not current.empty else set()
        required_times=list(range(first_required,expected_latest+STEP_15M_MS,STEP_15M_MS))
        missing=[x for x in required_times if x not in existing]

        if not missing:
            return self.snapshot_from_store(symbol,now_ms),[]

        recovered_frames=[]
        cursor_idx=0
        while cursor_idx<len(missing):
            start=missing[cursor_idx]
            end=start
            cursor_idx+=1
            while cursor_idx<len(missing) and missing[cursor_idx]==end+STEP_15M_MS:
                end=missing[cursor_idx]
                cursor_idx+=1

            chunk_start=start
            while chunk_start<=end:
                remaining=((end-chunk_start)//STEP_15M_MS)+1
                take=min(self.historical.request_limit,int(remaining))
                chunk_end=chunk_start+(take-1)*STEP_15M_MS
                frame=self.historical.fetch_window(
                    symbol,
                    chunk_start,
                    chunk_end+STEP_15M_MS-1,
                    take,
                )
                expected=list(range(chunk_start,chunk_end+STEP_15M_MS,STEP_15M_MS))
                got=[int(x) for x in frame["open_time"].tolist()] if not frame.empty else []
                if got!=expected:
                    raise DataLayerError(
                        f"{symbol} recovery mismatch expected={expected[0]}..{expected[-1]} "
                        f"count={len(expected)} got_count={len(got)}"
                    )
                recovered_frames.append(frame)
                chunk_start=chunk_end+STEP_15M_MS

        recovered=pd.concat(recovered_frames,ignore_index=True)
        self.store.upsert(symbol,"15m",recovered)
        self.store.trim(symbol,"15m",self.keep)

        snapshot=self.snapshot_from_store(symbol,now_ms)
        return snapshot,missing

    def snapshot_from_store(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)
        candles = self.store.load(symbol, "15m", self.keep)
        if len(candles) < self.required:
            raise StrategyInsufficientCandlesError(
                symbol,
                available=len(candles),
                required=self.required,
            )
        try:
            validation = validate_15m_candles(candles, now_ms)
        except DataValidationError as exc:
            raise DataLayerError(
                f"{symbol} candle validation failed: {exc}; details={exc.details}"
            ) from exc
        return MarketSnapshot(
            symbol=symbol,
            timeframe="15m",
            candles=candles,
            latest_open_time=int(candles.iloc[-1]["open_time"]),
            latest_close_time=int(candles.iloc[-1]["close_time"]),
            validation=validation,
        )
