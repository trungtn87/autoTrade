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
        # L1 snapshots read the full coin capacity (BTC 13440, ETH 25920) so L2
        # can slice per combo. REST remains limited to recent-candle repair.
        self.services = {
            symbol: DataService(
                self.historical,
                candle_store,
                retention=settings.candle_retention_for(symbol),
                signal_window=settings.snapshot_window_for(symbol),
                required=settings.required_15m_for(symbol),
            )
            for symbol in settings.symbols
        }
        self.health: dict[str, dict] = {}
        self._stop = threading.Event()
        self._attempts: dict[tuple[str, int], dict] = {}
        # Kept for compatibility with explicit/manual history review. Startup no
        # longer launches a deep-history producer that could invoke this callback.
        self.on_backfilled = on_backfilled

    def ingest(self, symbol: str, frame):
        return self.services[symbol.upper()].ingest_closed_candle(symbol, frame)

    def recover(self, symbol: str, now_ms: int | None = None):
        return self.services[symbol.upper()].recover_missing_closed(symbol, now_ms)

    def set_health(self, symbol: str, ok: bool, **details) -> None:
        self.health[symbol] = {"ok": bool(ok), "checked_at_ms": int(time.time() * 1000), **details}

    def startup_sync(self) -> None:
        """Repair only the recent live window; never backfill toward DB retention."""
        for symbol in self.settings.symbols:
            try:
                snap, missing = self.recover(symbol)
                self.set_health(symbol, True, latest_open_time=snap.latest_open_time, recovered=len(missing),
                                source="startup")
            except Exception as exc:
                self.set_health(symbol, False, error=str(exc), source="startup")
                log.warning("V3_L1_STARTUP_WAIT symbol=%s error=%s", symbol, exc)
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
                self._attempts[key] = {**state, "count": state["count"] + 1, "last_ms": now_ms}
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
