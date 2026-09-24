from __future__ import annotations

import time

import pandas as pd

from ..contracts import MarketSnapshot
from ..control.errors import DataLayerError
from .historical import HistoricalKlineClient
from .store import CandleStore
from .validator import DataValidationError, validate_15m_candles


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

    def seed(self, symbol: str, candles: pd.DataFrame) -> int:
        return self.store.upsert(symbol.upper(), "15m", candles)

    def bootstrap(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        """Explicit bootstrap/backfill path.

        It may call REST multiple times, but only here. Live WebSocket ingestion
        never invokes REST automatically.
        """
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)
        try:
            return self.snapshot_from_store(symbol, now_ms)
        except DataLayerError:
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
        """Live path: one already-closed WS candle -> DB -> validated snapshot.

        No REST recovery is permitted here. A gap fails closed.
        """
        symbol = symbol.upper()
        if candle is None or len(candle) != 1:
            raise DataLayerError("WebSocket ingest requires exactly one closed candle")
        self.store.upsert(symbol, "15m", candle)
        self.store.trim(symbol, "15m", self.keep)
        now_ms = int(now_ms or max(time.time() * 1000, int(candle.iloc[-1]["close_time"]) + 1))
        return self.snapshot_from_store(symbol, now_ms)

    def snapshot_from_store(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)
        candles = self.store.load(symbol, "15m", self.keep)
        if len(candles) < self.required:
            raise DataLayerError(
                f"{symbol} warmup incomplete: have={len(candles)} required={self.required}"
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
