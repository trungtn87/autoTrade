from __future__ import annotations

import time
import threading
import math
from collections import deque

import pandas as pd

from .contracts import MarketSnapshot
from .errors import DataLayerError, StrategyInsufficientCandlesError
from .historical import HistoricalKlineClient
from ..store import CandleStore
from .validator import DataValidationError, validate_15m_candles


STEP_15M_MS=15*60_000
RECOVERY_REST_WINDOW_SEC=15*60
RECOVERY_REST_MAX_REQUESTS=6
RECOVERY_REST_COOLDOWN_SEC=15*60


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
        recover_window: int | None = None,
    ):
        self.historical = historical
        self.store = store
        self.keep = int(keep)
        self.required = int(required)
        # Window repaired/bootstrapped from REST. ETH C1 uses 12000 bars; other combos keep `required`.
        self.recover_window = int(recover_window or required)
        if self.recover_window > self.keep:
            raise ValueError("recover_window must not exceed keep")
        # Shared per-service limiter across BTC/ETH recovery calls.
        self._recovery_rest_lock=threading.Lock()
        self._recovery_rest_calls=deque()
        self._recovery_rest_cooldown_until=0.0

    def bootstrap(self, symbol: str, now_ms: int | None = None) -> MarketSnapshot:
        """Use a valid stored window or rebuild the required window from REST."""
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)
        try:
            return self.snapshot_from_store(symbol, now_ms)
        except (DataLayerError, StrategyInsufficientCandlesError):
            pass

        candles = self.historical.fetch_history(symbol, self.recover_window, now_ms)
        self.store.upsert(symbol, "15m", candles)
        self.store.trim(symbol, "15m", self.keep)
        return self.snapshot_from_store(symbol, now_ms)

    def _recovery_fetch_window(
        self,
        symbol:str,
        start_time:int,
        end_time:int,
        limit:int,
    )->pd.DataFrame:
        """Rate-limited REST used only for repairing missing closed candles."""
        now=time.monotonic()
        with self._recovery_rest_lock:
            if now<self._recovery_rest_cooldown_until:
                remaining=int(self._recovery_rest_cooldown_until-now)
                raise DataLayerError(
                    f"REST recovery cooldown active after BingX rate-limit; "
                    f"retry_after_sec={remaining}"
                )
            cutoff=now-RECOVERY_REST_WINDOW_SEC
            while self._recovery_rest_calls and self._recovery_rest_calls[0]<cutoff:
                self._recovery_rest_calls.popleft()
            if len(self._recovery_rest_calls)>=RECOVERY_REST_MAX_REQUESTS:
                oldest=self._recovery_rest_calls[0]
                retry=max(1,int(RECOVERY_REST_WINDOW_SEC-(now-oldest)))
                raise DataLayerError(
                    f"REST recovery budget exhausted: "
                    f"{RECOVERY_REST_MAX_REQUESTS}/{RECOVERY_REST_WINDOW_SEC}s; "
                    f"retry_after_sec={retry}"
                )
            # Reserve the slot before transport so concurrent BTC/ETH recovery
            # cannot overshoot the global budget.
            self._recovery_rest_calls.append(now)

        try:
            return self.historical.fetch_window(
                symbol,start_time,end_time,limit
            )
        except DataLayerError as exc:
            msg=str(exc)
            if any(f"code={code}" in msg for code in ("100410", "109425", "109429")):
                with self._recovery_rest_lock:
                    self._recovery_rest_cooldown_until=max(
                        self._recovery_rest_cooldown_until,
                        time.monotonic()+RECOVERY_REST_COOLDOWN_SEC,
                    )
            raise

    def recovery_rest_status(self)->dict:
        now=time.monotonic()
        with self._recovery_rest_lock:
            cutoff=now-RECOVERY_REST_WINDOW_SEC
            while self._recovery_rest_calls and self._recovery_rest_calls[0]<cutoff:
                self._recovery_rest_calls.popleft()
            return {
                "window_sec":RECOVERY_REST_WINDOW_SEC,
                "max_requests":RECOVERY_REST_MAX_REQUESTS,
                "used_requests":len(self._recovery_rest_calls),
                "cooldown_remaining_sec":max(
                    0,int(self._recovery_rest_cooldown_until-now)
                ),
            }


    @staticmethod
    def _validate_recovery_page(symbol: str, frame: pd.DataFrame, expected: list[int]) -> None:
        """Reject empty, malformed or shifted REST pages before any DB write."""
        got = [int(x) for x in frame["open_time"].tolist()] if frame is not None and not frame.empty else []
        if got != expected:
            raise DataLayerError(
                f"{symbol} recovery mismatch expected={expected[0]}..{expected[-1]} "
                f"count={len(expected)} got_count={len(got)}"
            )
        for row in frame.itertuples(index=False):
            open_ms = int(row.open_time)
            if open_ms % STEP_15M_MS or int(row.close_time) != open_ms + STEP_15M_MS - 1:
                raise DataLayerError(f"{symbol} REST candle timestamps invalid: open={open_ms}")
            o, h, l, c, v = (float(row.open), float(row.high), float(row.low),
                             float(row.close), float(row.volume))
            if (not all(math.isfinite(x) for x in (o, h, l, c, v))
                    or min(o, h, l, c) <= 0 or v < 0
                    or h < max(o, c, l) or l > min(o, c)):
                raise DataLayerError(f"{symbol} REST candle OHLCV invalid: open={open_ms}")

    def backfill_complete(self, symbol: str, now_ms: int | None = None) -> bool:
        """A full, strictly contiguous and current keep-window is present in DB."""
        try:
            return len(self.snapshot_from_store(symbol, now_ms).candles) == self.keep
        except (DataLayerError, StrategyInsufficientCandlesError):
            return False

    def backfill_one_page(self, symbol: str, now_ms: int | None = None) -> int:
        """Fetch and durably save at most ONE 1000-bar REST page per invocation.

        Work backward from the newest missing bar so each successful page extends
        the contiguous live tail. Partial progress survives rate limits, crashes
        and process restarts; L2 continues to use the valid latest window.
        """
        symbol = symbol.upper()
        now_ms = int(now_ms or time.time() * 1000)
        latest = (now_ms // STEP_15M_MS) * STEP_15M_MS - STEP_15M_MS
        oldest = latest - (self.keep - 1) * STEP_15M_MS
        current = self.store.load(symbol, "15m", None)
        existing = set(int(x) for x in current["open_time"].tolist()) if not current.empty else set()
        missing_end = next(
            (t for t in range(latest, oldest - STEP_15M_MS, -STEP_15M_MS) if t not in existing),
            None,
        )
        if missing_end is None:
            return 0

        start = missing_end
        take = 1
        while (take < self.historical.request_limit and start > oldest
               and start - STEP_15M_MS not in existing):
            start -= STEP_15M_MS
            take += 1
        frame = self._recovery_fetch_window(
            symbol, start, missing_end + STEP_15M_MS - 1, take,
        )
        expected = list(range(start, missing_end + STEP_15M_MS, STEP_15M_MS))
        self._validate_recovery_page(symbol, frame, expected)
        self.store.upsert(symbol, "15m", frame)
        self.store.trim(symbol, "15m", self.keep)
        return len(frame)

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
        # Urgent WebSocket/watchdog repair only needs the live 3400-bar window.
        # Older ETH history is loaded separately, ONE persisted page per backfill cycle.
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
                frame=self._recovery_fetch_window(
                    symbol,
                    chunk_start,
                    chunk_end+STEP_15M_MS-1,
                    take,
                )
                expected=list(range(chunk_start,chunk_end+STEP_15M_MS,STEP_15M_MS))
                self._validate_recovery_page(symbol, frame, expected)
                # Do not discard earlier successfully repaired pages on later errors.
                self.store.upsert(symbol,"15m",frame)
                self.store.trim(symbol,"15m",self.keep)
                chunk_start=chunk_end+STEP_15M_MS

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
