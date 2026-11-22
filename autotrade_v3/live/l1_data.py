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

from .candles.historical import HistoricalKlineClient
from .candles.service import DataService

log = logging.getLogger("autotrade_v3.l1")

STEP_15M_MS = 15 * 60_000
CANDLE_GRACE_MS = 30_000
WATCHDOG_POLL_SEC = 5.0
RECOVERY_RETRY_MS = 60_000
RECOVERY_MAX_ATTEMPTS = 2
BACKFILL_RETRY_SEC = 15 * 60.0  # one persisted page per 15-minute backfill cycle


def expected_closed_open(now_ms: int) -> int:
    return (now_ms // STEP_15M_MS) * STEP_15M_MS - STEP_15M_MS


class L1Data:
    def __init__(self, settings, candle_store, control, historical: HistoricalKlineClient | None = None,
                 on_backfilled: Callable[[str], None] | None = None):
        self.settings = settings
        self.store = candle_store
        self.control = control
        self.historical = historical or HistoricalKlineClient(
            base_url=settings.bingx_base_url, min_interval_sec=settings.historical_min_interval_ms / 1000.0,
            request_limit=settings.historical_request_limit)
        # One V3-owned service per symbol: ETH keeps 12000 bars (ETH C1), BTC keeps 3400.
        self.services = {
            symbol: DataService(self.historical, candle_store, keep=settings.candle_keep_for(symbol),
                                required=settings.required_15m, recover_window=settings.candle_keep_for(symbol))
            for symbol in settings.symbols
        }
        self.health: dict[str, dict] = {}
        self._stop = threading.Event()
        self._attempts: dict[tuple[str, int], dict] = {}
        self.on_backfilled = on_backfilled

    def ingest(self, symbol: str, frame):
        return self.services[symbol.upper()].ingest_closed_candle(symbol, frame)

    def recover(self, symbol: str, now_ms: int | None = None):
        return self.services[symbol.upper()].recover_missing_closed(symbol, now_ms)

    def set_health(self, symbol: str, ok: bool, **details) -> None:
        self.health[symbol] = {"ok": bool(ok), "checked_at_ms": int(time.time() * 1000), **details}

    def startup_sync(self) -> None:
        """Fill the gap between the stored candles and now (bounded REST), then keep backfilling."""
        for symbol in self.settings.symbols:
            try:
                snap, missing = self.recover(symbol)
                self.set_health(symbol, True, latest_open_time=snap.latest_open_time, recovered=len(missing),
                                source="startup")
            except Exception as exc:
                self.set_health(symbol, False, error=str(exc), source="startup")
                log.warning("V3_L1_STARTUP_WAIT symbol=%s error=%s", symbol, exc)
            if not self.services[symbol].backfill_complete(symbol):
                threading.Thread(target=self.backfill_loop, args=(symbol,),
                                 name=f"v3-candle-backfill-{symbol}", daemon=True).start()

    def _stats_count(self, symbol: str) -> int:
        return int(self.store.stats(symbol, "15m").get("count", 0))

    def backfill_loop(self, symbol: str) -> None:
        """Fetch at most one page, persist, then wait 15 minutes; resume after restart."""
        keep = self.settings.candle_keep_for(symbol)
        service = self.services[symbol]
        while not self._stop.is_set():
            try:
                if service.backfill_complete(symbol):
                    log.info("V3_L1_BACKFILL_DONE symbol=%s bars=%s", symbol, keep)
                    return
                saved = service.backfill_one_page(symbol)
                total = self._stats_count(symbol)
                log.info("V3_L1_BACKFILL_SAVED symbol=%s page_bars=%s total=%s target=%s",
                         symbol, saved, total, keep)
                # Notify only after validated data is persisted; never re-save a page
                # just because the optional L2 callback failed.
                callback = getattr(self, "on_backfilled", None)
                if saved > 0 and callback is not None:
                    try:
                        callback(symbol)
                    except Exception:
                        log.exception("V3_L1_BACKFILL_CALLBACK_FAILED symbol=%s", symbol)
            except Exception as exc:  # API/cooldown/validation: keep all prior pages, retry next cycle
                log.warning("V3_L1_BACKFILL_RETRY symbol=%s error=%s", symbol, exc)
            self._stop.wait(BACKFILL_RETRY_SEC)

    def _safe_stats(self, symbol: str) -> dict:
        try:
            return self.store.stats(symbol, "15m")
        except Exception as exc:
            return {"error": str(exc)}

    def watchdog(self, processed_open: Callable[[str], int], on_snapshot: Callable[[str, object], None],
                 retry_pending: Callable[[str], None] | None = None) -> None:
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
                if state["count"] == 0:
                    # First miss for this candle: alert once, before REST recovery runs.
                    self.control.report("L1", "candle_stale",
                                        f"Thiếu nến 15m {symbol} open={expected}, đang khôi phục từ REST",
                                        symbol=symbol, details={"expected_open_time": expected})
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
            # The L1 cursor can be current even when the L2 evaluation failed.
            # Retry L2 separately: no redundant REST requests or cross-coin gate.
            if retry_pending is not None:
                for symbol in self.settings.symbols:
                    try:
                        retry_pending(symbol)
                    except Exception:
                        log.exception("V3_L2_RETRY_CALLBACK_FAILED symbol=%s", symbol)
            for key in [k for k in self._attempts if k[1] < expected - STEP_15M_MS]:
                self._attempts.pop(key, None)

    def stop(self) -> None:
        self._stop.set()
