from __future__ import annotations

import time

import pandas as pd

from ..contracts import MarketSnapshot
from ..control.errors import DataLayerError
from .market_client import PublicMarketClient, closed_only
from .store import CandleStore
from .validator import DataValidationError, validate_15m_candles


class DataService:
    """Layer 1: network -> validated persistent market snapshot."""

    def __init__(
        self,
        market: PublicMarketClient,
        store: CandleStore,
        *,
        fetch_limit: int = 1000,
        keep: int = 3400,
        required: int = 3400,
    ):
        self.market = market
        self.store = store
        self.fetch_limit = int(fetch_limit)
        self.keep = int(keep)
        self.required = int(required)

    def seed(self, symbol: str, candles: pd.DataFrame) -> int:
        """Offline/bootstrap path. Never performs network recovery."""
        return self.store.upsert(symbol.upper(), "15m", candles)

    def refresh(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)

        # Exactly one market request per refresh.
        latest = self.market.klines(symbol, "15m", self.fetch_limit)
        latest = closed_only(latest, now_ms)
        self.store.upsert(symbol, "15m", latest)
        self.store.trim(symbol, "15m", self.keep)

        candles = self.store.load(symbol, "15m", self.keep)
        if len(candles) < self.required:
            raise DataLayerError(
                f"{symbol} warmup incomplete: have={len(candles)} required={self.required}; "
                "seed/bootstrap is required before strategy may run"
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

    def snapshot_from_store(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        """No-network snapshot, useful for parity tests and deterministic replay."""
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
