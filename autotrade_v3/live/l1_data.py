"""L1 DATA: BTC/ETH 15m candles -> autotrade_v3.candles -> L2.

Reuses the existing connections unchanged: BingX kline WebSocket, BingX public
historical REST and the strict 15m validator (V2 DataService). Only storage is
new (V3 table). L1 has no trading authority and its REST candle reads are not
part of L4's three BingX read mechanisms.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from autotrade_v2.app.data.historical import HistoricalKlineClient
from autotrade_v2.app.data.service import DataService

log = logging.getLogger("autotrade_v3.l1")

STEP_15M_MS = 15 * 60_000
CANDLE_GRACE_MS = 30_000
WATCHDOG_POLL_SEC = 5.0
RECOVERY_RETRY_MS = 60_000
RECOVERY_MAX_ATTEMPTS = 2


def expected_closed_open(now_ms: int) -> int:
    return (now_ms // STEP_15M_MS) * STEP_15M_MS - STEP_15M_MS


class L1Data:
    def __init__(self, settings, candle_store, control, historical: HistoricalKlineClient | None = None):
        self.settings = settings
        self.store = candle_store
        self.control = control
        self.historical = historical or HistoricalKlineClient(
            base_url=settings.bingx_base_url, min_interval_sec=settings.historical_min_interval_ms / 1000.0,
            request_limit=settings.historical_request_limit)
        self.service = DataService(self.historical, candle_store, keep=settings.candle_keep_15m,
                                   required=settings.required_15m)
        self.health: dict[str, dict] = {}
        self._stop = threading.Event()
        self._attempts: dict[tuple[str, int], dict] = {}

    def ingest(self, symbol: str, frame):
        return self.service.ingest_closed_candle(symbol, frame)

    def recover(self, symbol: str, now_ms: int | None = None):
        return self.service.recover_missing_closed(symbol, now_ms)

    def set_health(self, symbol: str, ok: bool, **details) -> None:
        self.health[symbol] = {"ok": bool(ok), "checked_at_ms": int(time.time() * 1000), **details}

    def startup_sync(self) -> None:
        """Fill the small gap between the copied candles and now (bounded REST)."""
        for symbol in self.settings.symbols:
            try:
                snap, missing = self.recover(symbol)
                self.set_health(symbol, True, latest_open_time=snap.latest_open_time, recovered=len(missing),
                                source="startup")
            except Exception as exc:
                self.set_health(symbol, False, error=str(exc), source="startup")
                self.control.report("L1", "startup_candles", f"Nến {symbol} chưa sẵn sàng khi khởi động: {exc}",
                                    symbol=symbol, details={"stats": self._safe_stats(symbol)})

    def _safe_stats(self, symbol: str) -> dict:
        try:
            return self.store.stats(symbol, "15m")
        except Exception as exc:
            return {"error": str(exc)}

    def watchdog(self, processed_open: Callable[[str], int], on_snapshot: Callable[[str, object], None]) -> None:
        """If the WebSocket missed a closed candle, repair it from REST and run the pipeline."""
        while not self._stop.wait(WATCHDOG_POLL_SEC):
            now_ms = int(time.time() * 1000)
            boundary = (now_ms // STEP_15M_MS) * STEP_15M_MS
            if now_ms - boundary < CANDLE_GRACE_MS:
                continue
            expected = boundary - STEP_15M_MS
            for symbol in self.settings.symbols:
                if processed_open(symbol) >= expected:
                    continue
                key = (symbol, expected)
                state = self._attempts.get(key, {"count": 0, "last_ms": 0})
                if state["count"] >= RECOVERY_MAX_ATTEMPTS:
                    continue
                if state["count"] and now_ms - state["last_ms"] < RECOVERY_RETRY_MS:
                    continue
                self._attempts[key] = {"count": state["count"] + 1, "last_ms": now_ms}
                try:
                    snap, missing = self.recover(symbol, now_ms)
                    if int(snap.latest_open_time) != expected:
                        raise RuntimeError(f"nến mới nhất {snap.latest_open_time} khác nến cần có {expected}")
                    self.set_health(symbol, True, latest_open_time=snap.latest_open_time, recovered=len(missing),
                                    source="watchdog")
                    log.warning("V3_L1_CANDLE_RECOVERED symbol=%s open=%s missing=%s", symbol, expected, len(missing))
                    on_snapshot(symbol, snap)
                except Exception as exc:
                    self.set_health(symbol, False, error=str(exc), expected_open_time=expected, source="watchdog")
                    self.control.report("L1", "candle_recovery",
                                        f"Thiếu nến 15m {symbol} open={expected}, khôi phục lần {state['count'] + 1} thất bại: {exc}",
                                        symbol=symbol, details={"expected_open_time": expected,
                                                                "stats": self._safe_stats(symbol)})
            for key in [k for k in self._attempts if k[1] < expected - STEP_15M_MS]:
                self._attempts.pop(key, None)

    def stop(self) -> None:
        self._stop.set()
