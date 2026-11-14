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
from .l1_data import STEP_15M_MS, L1Data
from .l2_strategy import L2Strategy
from .l3_execution import L3Executor
from .l4_leftover import LeftoverCleaner, v3_threads_busy
from .l4_control import ControlLog, HourlySync, L4Reader
from .l5_alert import L5Alert
from .sanitize import clean
from .store import CandleStore, make_store
from .transport import V3BingXClient

log = logging.getLogger("autotrade_v3")
ENGINE_NAME = "autotrade-v3"


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
        self.l1 = L1Data(s, self.candles, self.control)
        self.l2 = L2Strategy(self.store, self.control, self.alert, self.executor if s.execution_enabled else None)
        self.leftover = LeftoverCleaner(s, self.client, self.store, self.reader, self.control, self.alert,
                                        busy=v3_threads_busy)
        self.executor.on_settled = self.leftover.request
        self.hourly = HourlySync(self.store, self.control, admit=self._archive_ready, after_sync=self.leftover.request)
        self.stream = None
        self._processed: dict[str, int] = {}
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
            self.l1.set_health(symbol, True, latest_open_time=open_time, source="pipeline")
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
        try:
            signals = self.l2.calculate(snapshot)
        except Exception as exc:
            self.control.report("L2", "strategy_calculate", f"20FINAL không tính được cho {symbol}: {exc}", symbol=symbol,
                                details={"latest_open_time": getattr(snapshot, "latest_open_time", None)})
            self.last_run = {"ok": False, "symbol": symbol, "stage": "strategy", "error": str(exc)}
            return
        self.l2.dispatch(signals)
        self.last_run = {"ok": True, "symbol": symbol, "latest_open_time": snapshot.latest_open_time,
                         "signals": [s.event_id for s in signals], "at_ms": int(time.time() * 1000),
                         "execution_enabled": self.settings.execution_enabled}

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
        log.info("V3_START execution_enabled=%s websocket_enabled=%s symbols=%s", s.execution_enabled,
                 s.websocket_enabled, s.symbols)
        if s.is_postgres:
            self.hourly.start()
        if s.websocket_enabled:
            threading.Thread(target=self._start_data, name="v3-data-start", daemon=True).start()
        else:
            self.status = "ready_no_websocket"

    def _start_data(self) -> None:
        from autotrade_v2.app.data.websocket import BingXKlineStream
        self.status = "checking_candles"
        self.l1.startup_sync()
        self.stream = BingXKlineStream(self.settings.symbols, self.on_closed, url=self.settings.bingx_ws_url,
                                       on_event=self._ws_event)
        self.stream.start()
        threading.Thread(target=self.l1.watchdog, args=(self.processed_open, self.on_recovered),
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
        self.alert.stop()

    # ---- views ---------------------------------------------------------
    def status_view(self) -> dict:
        s = self.settings
        return clean({
            "engine": ENGINE_NAME,
            "runtime": {"status": self.status, "execution_enabled": s.execution_enabled,
                        "websocket_enabled": s.websocket_enabled, "symbols": list(s.symbols),
                        "store": type(self.store).__name__, "candle_backend": self.candles.backend},
            "data_health": self.l1.health,
            "websocket": self.stream.status() if self.stream is not None else None,
            "last_run": self.last_run,
            "last_signals": self.l2.last_signals[-20:],
            "last_executions": self.executor.results[-20:],
            "control_recent": self.control.recent[-20:],
            "alerts_recent": self.alert.sent[-20:],
            "hourly_sync": self.hourly.status(),
            "leftover_cleanup": self.leftover.status(),
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
        body = {"engine": ENGINE_NAME, "ok": bool(ok_data), "status": r.status,
                "execution_enabled": r.settings.execution_enabled, "data_health": clean(r.l1.health)}
        # Liveness is always 200 so a platform health check never restarts the
        # process between an Entry and its protection; data health is in the body.
        return JSONResponse(body, status_code=200)

    @app.get("/status")
    def status():
        return rt().status_view()

    return app


app = create_app()
