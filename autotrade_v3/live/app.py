"""AutoTrade V3 live engine: wires L1..L5 and exposes /health and /status.

Mounted by ``unified_app`` under ``/engine`` (same URL as before). Nothing
starts at import time; the FastAPI lifespan starts the runtime.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import V3Settings
from .l1_data import STEP_15M_MS, L1Data, expected_closed_open
from .l2_strategy import L2Strategy
from .l3_execution import L3Executor
from .l4_leftover import LeftoverCleaner, v3_threads_busy
from .l4_sl_resize import SLResizePoller
from .l4_control import ControlLog, HourlySync, L4Reader
from .l5_alert import L5Alert
from .sanitize import clean
from .store import CandleStore, make_store
from .transport import V3BingXClient

log = logging.getLogger("autotrade_v3")
ENGINE_NAME = "autotrade-v3"
L2_RETRY_INTERVAL_MS = 60_000


class Runtime:
    def __init__(self, settings: V3Settings | None = None, *, store=None, candle_store=None, client=None,
                 alert: L5Alert | None = None):
        self.settings = settings or V3Settings()
        s = self.settings
        self.started_ms = int(time.time() * 1000)
        self.store = store if store is not None else make_store(s.database_url)
        self.candles = candle_store if candle_store is not None else CandleStore(s.database_url)
        self.alert = alert or L5Alert(enabled=s.discord_enabled, webhook_btc=s.discord_webhook_btc,
                                      webhook_eth=s.discord_webhook_eth, webhook_error=s.discord_webhook_error,
                                      store=self.store)
        self.control = ControlLog(self.store, self.alert)
        self.client = client or V3BingXClient(s)
        self.reader = L4Reader(self.client, self.store, self.control)
        self.executor = L3Executor(s, self.client, self.store, self.reader, self.control, self.alert)
        self.l1 = L1Data(s, self.candles, self.control, on_backfilled=self.on_backfilled)
        self.l2 = L2Strategy(self.store, self.control, self.alert, self.executor if s.execution_enabled else None,
                             registry=s.strategy_registry)
        self.leftover = LeftoverCleaner(s, self.client, self.store, self.reader, self.control, self.alert,
                                        busy=v3_threads_busy)
        self.executor.on_settled = self.leftover.request
        self.sl_resize_poll = SLResizePoller(self.leftover, self.store, admit=self._archive_ready,
                                             interval_sec=s.sl_resize_poll_sec)
        self.hourly = HourlySync(self.store, self.control, admit=self._archive_ready, client=self.client, after_sync=self.leftover.request)
        self.stream = None
        self._processed: dict[str, int] = {}  # latest candle persisted by L1
        self._l2_last_success: dict[str, int] = {}  # L2 completed calculation and dispatch
        self._l2_pending: dict[str, object] = {}
        self._l2_pending_signals: dict[str, list] = {}
        self._l2_retry_after: dict[str, int] = {}
        self._history_preview: dict[str, dict] = {}
        self._symbol_locks = {sym: threading.Lock() for sym in s.symbols}
        self.last_run: dict = {"status": "not_started"}
        self.status = "idle"

    # ---- pipeline -----------------------------------------------------
    def processed_open(self, symbol: str) -> int:
        return int(self._processed.get(symbol, 0))

    def on_closed(self, symbol, frame) -> None:
        """WebSocket closed 15m candle -> L1 -> L2 -> L3/L5."""
        symbol = str(symbol).upper()
        open_time = int(frame.iloc[-1]["open_time"])
        lock = self._symbol_locks.setdefault(symbol, threading.Lock())
        with lock:
            if open_time <= self.processed_open(symbol):
                return
            try:
                snapshot = self.l1.ingest(symbol, frame)
            except Exception as exc:
                self.l1.set_health(symbol, False, error=str(exc), source="pipeline")
                self.control.report("L1", "candle_ingest", f"Không lưu/kiểm tra được nến {symbol} open={open_time}: {exc}",
                                    symbol=symbol, details={"open_time": open_time})
                self.last_run = {"ok": False, "symbol": symbol, "stage": "data", "error": str(exc)}
                return
            self._processed[symbol] = open_time
            self.l1.set_health(symbol, True, latest_open_time=int(snapshot.latest_open_time), source="pipeline")
            # A delayed WebSocket event cannot replay a candle from before
            # this runtime started, or reprocess an older candle already in DB.
            if (int(snapshot.latest_open_time) != open_time
                    or int(snapshot.latest_close_time) < self.started_ms):
                return
            self._run_strategy(symbol, snapshot)

    def on_recovered(self, symbol: str, snapshot) -> None:
        """Watchdog-repaired candle. Only candles closed after this process started are traded."""
        lock = self._symbol_locks.setdefault(symbol, threading.Lock())
        with lock:
            open_time = int(snapshot.latest_open_time)
            if open_time <= self.processed_open(symbol):
                return
            self._processed[symbol] = open_time
            if int(snapshot.latest_close_time) < self.started_ms:
                return  # never replay candles that closed before this process existed
            self._run_strategy(symbol, snapshot)

    def _run_strategy(self, symbol: str, snapshot) -> None:
        """Retain failed L2 evaluation separately from the L1 persisted cursor.

        A calculation that succeeded is never recomputed after a dispatch error:
        the exact original signals are cached for a safe, idempotent retry.
        """
        open_time = int(snapshot.latest_open_time)
        now_ms = int(time.time() * 1000)
        if open_time <= self._l2_last_success.get(symbol, -1):
            return
        if (open_time != expected_closed_open(now_ms)
                or int(snapshot.latest_close_time) < self.started_ms):
            return  # no historical or superseded Entries
        previous = self._l2_pending.get(symbol)
        if previous is not None and int(previous.latest_open_time) != open_time:
            self._l2_pending_signals.pop(symbol, None)
        self._l2_pending[symbol] = snapshot
        try:
            signals = self._l2_pending_signals.get(symbol)
            if signals is None:
                signals = self.l2.calculate(snapshot)
                self._l2_pending_signals[symbol] = signals
            self.l2.dispatch(signals)
        except Exception as exc:
            self._l2_retry_after[symbol] = now_ms + L2_RETRY_INTERVAL_MS
            version = self.l2.registry.version if self.l2.registry else "registry"
            self.control.report(
                "L2", "strategy_calculate", f"{version} không xử lý được cho {symbol}: {exc}",
                symbol=symbol, details={"latest_open_time": open_time,
                                        "retry_after_ms": self._l2_retry_after[symbol]})
            self.last_run = {"ok": False, "symbol": symbol, "stage": "strategy", "error": str(exc)}
            return
        self._l2_last_success[symbol] = open_time
        self._l2_pending.pop(symbol, None)
        self._l2_pending_signals.pop(symbol, None)
        self._l2_retry_after.pop(symbol, None)
        self.last_run = {"ok": True, "symbol": symbol, "latest_open_time": open_time,
                         "signals": [s.event_id for s in signals], "at_ms": int(time.time() * 1000),
                         "execution_enabled": self.settings.execution_enabled}

    def retry_pending(self, symbol: str, *, force: bool = False) -> None:
        """Retry L2 without REST; discard old bars when a newer M15 has closed."""
        symbol = str(symbol).upper()
        lock = self._symbol_locks.setdefault(symbol, threading.Lock())
        with lock:
            snapshot = self._l2_pending.get(symbol)
            if snapshot is None:
                return
            now_ms = int(time.time() * 1000)
            open_time = int(snapshot.latest_open_time)
            if open_time != expected_closed_open(now_ms):
                self._l2_pending.pop(symbol, None)
                self._l2_pending_signals.pop(symbol, None)
                self._l2_retry_after.pop(symbol, None)
                self.control.report(
                    "L2", "strategy_retry_expired",
                    f"Bỏ qua tín hiệu quá hạn {symbol} open={open_time}; không đặt lệnh nến cũ",
                    symbol=symbol, details={"latest_open_time": open_time})
                return
            if not force and now_ms < self._l2_retry_after.get(symbol, 0):
                return
            if symbol not in self._l2_pending_signals:
                try:
                    updated = self.l1.services[symbol].snapshot_from_store(symbol, now_ms)
                    if int(updated.latest_open_time) == open_time:
                        snapshot = updated
                except Exception:
                    pass  # the last validated L1 snapshot remains safe to retry
            self._run_strategy(symbol, snapshot)

    def on_backfilled(self, symbol: str) -> None:
        """After REST backfill, check historical signals without placing orders."""
        symbol = str(symbol).upper()
        now_ms = int(time.time() * 1000)
        try:
            snapshot = self.l1.services[symbol].snapshot_from_store(symbol, now_ms)
        except Exception as exc:
            log.info("V3_L1_BACKFILL_PREVIEW_WAIT symbol=%s error=%s", symbol, exc)
            return
        # A newly repaired CURRENT bar can still run the live pipeline once.
        if (int(snapshot.latest_open_time) == expected_closed_open(now_ms)
                and int(snapshot.latest_close_time) >= self.started_ms):
            if int(snapshot.latest_open_time) > self.processed_open(symbol):
                self.on_recovered(symbol, snapshot)
            else:
                self.retry_pending(symbol, force=True)
        try:
            signals = self.l2.preview(snapshot)
            self._history_preview[symbol] = {
                "latest_open_time": int(snapshot.latest_open_time),
                "signals": [s.event_id for s in signals],
                "checked_at_ms": int(time.time() * 1000),
                "mode": "read_only",
            }
        except Exception as exc:
            self._history_preview[symbol] = {
                "latest_open_time": int(snapshot.latest_open_time),
                "error": str(exc), "mode": "read_only"}
            log.warning("V3_L1_BACKFILL_PREVIEW_FAILED symbol=%s error=%s", symbol, exc)

    # ---- hourly archive admission (keeps BingX headroom for trading) ---
    def _archive_ready(self) -> bool:
        now_ms = int(time.time() * 1000)
        phase = now_ms % STEP_15M_MS
        if phase < 120_000 or phase > STEP_15M_MS - 120_000:
            return False
        if any(t.is_alive() for t in threading.enumerate() if t.name.startswith("v3-l3-")):
            return False
        try:
            return bool(self.client.guard.archive_ready())
        except Exception:
            return True

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        s = self.settings
        reg = self.l2.registry_view()
        log.info("V3_START execution_enabled=%s websocket_enabled=%s symbols=%s registry=%s count=%s spec_hash=%s",
                 s.execution_enabled, s.websocket_enabled, s.symbols, reg.get("version"), reg.get("count"),
                 reg.get("spec_hash"))
        if self.l2.registry is None:
            self.control.report("L2", "strategy_registry",
                                f"Registry chiến lược không hợp lệ, KHÔNG mở lệnh mới (vị thế cũ vẫn được L4 quản lý): "
                                f"{self.l2.registry_error}", details={"V3_STRATEGY_REGISTRY": s.strategy_registry})
        if not s.discord_enabled or not (s.discord_webhook_btc and s.discord_webhook_eth and s.discord_webhook_error):
            log.warning("V3_DISCORD_CONFIG_INCOMPLETE enabled=%s btc=%s eth=%s error=%s", s.discord_enabled,
                        bool(s.discord_webhook_btc), bool(s.discord_webhook_eth), bool(s.discord_webhook_error))
        else:
            self.alert.notice("Engine V3 khởi động", [
                f"Symbols: {', '.join(s.symbols)}",
                f"Registry: {reg.get('version')} ({reg.get('count')} combo)",
                f"Execution: {'bật' if s.execution_enabled else 'tắt (chỉ tín hiệu)'}",
                f"WebSocket: {'bật' if s.websocket_enabled else 'tắt'}",
            ])
        if s.is_postgres:
            self.hourly.start()
            if s.execution_enabled:
                self.sl_resize_poll.start()
        if s.websocket_enabled:
            threading.Thread(target=self._start_data, name="v3-data-start", daemon=True).start()
        else:
            self.status = "ready_no_websocket"

    def _start_data(self) -> None:
        from .exchange.websocket import BingXKlineStream
        self.status = "checking_candles"
        self.l1.startup_sync()
        # The in-memory cursor starts empty after a restart. Seed it from the persisted
        # latest candle so the watchdog does not raise a false stale alert for a complete DB.
        for symbol in self.settings.symbols:
            try:
                latest = self.candles.stats(symbol, "15m").get("latest_open_time")
            except Exception as exc:
                log.warning("V3_L1_CURSOR_SEED_FAILED symbol=%s error=%s", symbol, exc)
                continue
            if latest:
                self._processed[symbol] = max(self._processed.get(symbol, 0), int(latest))
        self.stream = BingXKlineStream(self.settings.symbols, self.on_closed, url=self.settings.bingx_ws_url,
                                       on_event=self._ws_event)
        self.stream.start()
        threading.Thread(target=self.l1.watchdog, args=(self.processed_open, self.on_recovered, self.retry_pending),
                         name="v3-candle-watchdog", daemon=True).start()
        self.status = "websocket_running"

    def _ws_event(self, key, severity, message, **kwargs) -> None:
        if str(severity).upper() in {"ERROR", "CRITICAL"}:
            self.control.report("L1", "websocket", f"{key}: {message}", symbol=kwargs.get("symbol"),
                                details=kwargs.get("details") or {})

    def stop(self) -> None:
        self.l1.stop()
        if self.stream is not None:
            self.stream.stop()
        self.hourly.stop()
        self.sl_resize_poll.stop()
        self.alert.stop()

    # ---- views ---------------------------------------------------------
    def status_view(self) -> dict:
        s = self.settings
        return clean({
            "engine": ENGINE_NAME,
            "runtime": {"status": self.status, "execution_enabled": s.execution_enabled,
                        "websocket_enabled": s.websocket_enabled, "symbols": list(s.symbols),
                        "store": type(self.store).__name__, "candle_backend": self.candles.backend},
            "strategy_registry": self.l2.registry_view(),
            "data_health": self.l1.health,
            "websocket": self.stream.status() if self.stream is not None else None,
            "last_run": self.last_run,
            "l2_last_success": dict(self._l2_last_success),
            "l2_pending": {sym: int(snap.latest_open_time) for sym, snap in self._l2_pending.items()},
            "history_preview": self._history_preview,
            "last_signals": self.l2.last_signals[-20:],
            "last_executions": self.executor.results[-20:],
            "control_recent": self.control.recent[-20:],
            "alerts_recent": self.alert.sent[-20:],
            "hourly_sync": self.hourly.status(),
            "leftover_cleanup": self.leftover.status(),
            "sl_resize": self.sl_resize_poll.status(),
            "legacy_v2_trading": False,
        })


def create_app(runtime: Runtime | None = None) -> FastAPI:
    holder: dict = {"rt": runtime}

    def rt() -> Runtime:
        if holder["rt"] is None:
            holder["rt"] = Runtime()
        return holder["rt"]

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        r = rt()
        r.start()
        try:
            yield
        finally:
            r.stop()

    app = FastAPI(title="AutoTrade V3", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    def health():
        r = rt()
        ok_data = all(v.get("ok") for v in r.l1.health.values()) if r.l1.health else not r.settings.websocket_enabled
        reg = r.l2.registry_view()
        body = {"engine": ENGINE_NAME, "ok": bool(ok_data), "status": r.status,
                "strategy_registry": {"version": reg.get("version"), "count": reg.get("count"),
                                      "spec_hash": reg.get("spec_hash"), "exit_policy": reg.get("exit_policy"),
                                      "signal_variants": reg.get("signal_variants"), "combos": reg.get("combos"),
                                      "error": reg.get("error")},
                "execution_enabled": r.settings.execution_enabled, "data_health": clean(r.l1.health)}
        # Liveness is always 200 so a platform health check never restarts the
        # process between an Entry and its protection; data health is in the body.
        return JSONResponse(body, status_code=200)

    @app.get("/status")
    def status():
        return rt().status_view()

    return app


app = create_app()
