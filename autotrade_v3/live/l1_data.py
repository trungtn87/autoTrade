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
RECOVERY_ALERT_DELAY_MS = 30_000  # let late WebSocket candles land before Discord alerts
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

    def _latest_candle_is(self, symbol: str, expected_open: int) -> bool:
        """Recheck DB; a failed REST call does not prove a candle is missing."""
        try:
            latest = self.store.load(symbol, "15m", 1)
            return not latest.empty and int(latest.iloc[-1]["open_time"]) == expected_open
        except Exception:
            log.exception("V3_L1_CANDLE_ALERT_DB_CHECK_FAILED symbol=%s open=%s", symbol, expected_open)
            return False

    def _check_delayed_recovery_alert(self, symbol: str, expected: int, state: dict,
                                      processed_open: Callable[[str], int], now_ms: int) -> None:
        """Send one error only if a candle remains missing after the WS catch-up window."""
        failed_ms = state.get("first_failed_ms")
        if failed_ms is None or state.get("alert_sent"):
            return
        if processed_open(symbol) >= expected or self._latest_candle_is(symbol, expected):
            state.pop("first_failed_ms", None)
            state.pop("last_error", None)
            log.info("V3_L1_REST_ERROR_RESOLVED symbol=%s open=%s", symbol, expected)
            return
        if now_ms - failed_ms < RECOVERY_ALERT_DELAY_MS:
            return
        state["alert_sent"] = True
        self.control.report(
            "L1", "candle_recovery",
            f"Thiếu nến 15m {symbol} open={expected} sau {RECOVERY_ALERT_DELAY_MS // 1000}s xác minh; "
            f"REST khôi phục lỗi: {state.get('last_error', 'unknown')}",
            symbol=symbol,
            details={"expected_open_time": expected, "stats": self._safe_stats(symbol),
                     "rest_attempts": state["count"], "alert_delay_ms": RECOVERY_ALERT_DELAY_MS},
        )

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
                key = (symbol, expected)
                state = self._attempts.get(key, {"count": 0, "last_ms": 0})
                self._check_delayed_recovery_alert(symbol, expected, state, processed_open, now_ms)
                if processed_open(symbol) >= expected:
                    continue
                if state["count"] >= RECOVERY_MAX_ATTEMPTS:
                    continue
                if state["count"] and now_ms - state["last_ms"] < RECOVERY_RETRY_MS:
                    continue
                if state["count"] == 0:
                    # WS may simply be late. Log only; no Discord alert unless REST repair fails.
                    log.info("V3_L1_CANDLE_LATE symbol=%s open=%s, REST recovery started", symbol, expected)
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
                    if processed_open(symbol) >= expected:
                        # WS delivered the candle while REST was failing: not an error.
                        log.info("V3_L1_CANDLE_LATE_RESOLVED_BY_WS symbol=%s open=%s rest_error=%s",
                                 symbol, expected, exc)
                        continue
                    pending = self._attempts[key]
                    pending.setdefault("first_failed_ms", now_ms)
                    pending["last_error"] = str(exc)
                    log.warning("V3_L1_REST_RECOVERY_FAILED_WAITING_FOR_WS symbol=%s open=%s attempt=%s error=%s",
                                symbol, expected, pending["count"], exc)
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
