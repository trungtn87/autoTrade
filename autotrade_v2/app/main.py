from __future__ import annotations

import logging
import threading

from fastapi import FastAPI

from .config import Settings, validate_settings
from .control.logging import configure_logging
from .data.historical import HistoricalKlineClient
from .data.service import DataService
from .data.store import CandleStore
from .data.websocket import BingXKlineStream
from .execution.service import ExecutionService
from .execution.store import ExecutionStore
from .orchestrator import Orchestrator
from .strategy.engine import StrategyEngine

settings=Settings()
configure_logging(settings.log_level)
log=logging.getLogger("autotrade_v2")
validate_settings(settings)

candle_store=CandleStore(settings.state_db,settings.database_url)
execution_store=ExecutionStore(settings.state_db,settings.database_url)
historical=HistoricalKlineClient(
    base_url=settings.bingx_base_url,
    min_interval_sec=settings.historical_min_interval_ms/1000.0,
    request_limit=settings.historical_request_limit,
)
data_service=DataService(
    historical,
    candle_store,
    keep=settings.candle_keep_15m,
    required=settings.required_15m,
)
strategy_engine=StrategyEngine()
execution_service=ExecutionService(settings,execution_store)
orchestrator=Orchestrator(settings,data_service,strategy_engine,execution_service)

app=FastAPI(title="AutoTrade V2")
_state_lock=threading.Lock()
last_run:dict={"status":"not_started"}
runtime:dict={
    "status":"idle",
    "bootstrap_enabled":settings.bootstrap_enabled,
    "websocket_enabled":settings.websocket_enabled,
    "execution_enabled":settings.execution_enabled,
}
stream:BingXKlineStream|None=None


def _on_closed(symbol,frame)->None:
    global last_run
    result=orchestrator.on_closed_candle(symbol,frame)
    with _state_lock:
        last_run=result


def _start_data_runtime()->None:
    global stream
    with _state_lock:
        runtime["status"]="bootstrapping" if settings.bootstrap_enabled else "checking_store"

    if settings.bootstrap_enabled:
        results=[orchestrator.bootstrap_symbol(s) for s in settings.symbols]
        with _state_lock:
            runtime["bootstrap"]=results
        if not all(x.get("ok") for x in results):
            with _state_lock:
                runtime["status"]="bootstrap_failed"
            return
    else:
        checks=[]
        for symbol in settings.symbols:
            try:
                snap=data_service.snapshot_from_store(symbol)
                checks.append({"ok":True,"symbol":symbol,"candles":len(snap.candles)})
            except Exception as exc:
                checks.append({"ok":False,"symbol":symbol,"error":str(exc)})
        with _state_lock:
            runtime["store_check"]=checks
        if settings.websocket_enabled and not all(x.get("ok") for x in checks):
            with _state_lock:
                runtime["status"]="store_not_ready"
            return

    if settings.websocket_enabled:
        stream=BingXKlineStream(settings.symbols,_on_closed,url=settings.bingx_ws_url)
        stream.start()
        with _state_lock:
            runtime["status"]="websocket_running"
    else:
        with _state_lock:
            runtime["status"]="ready_no_websocket"


@app.on_event("startup")
def startup()->None:
    log.info(
        "V2_START symbols=%s data=historical_rest+websocket strategy=FINAL14 "
        "bootstrap=%s websocket=%s execution_enabled=%s dry_run=%s db=%s",
        settings.symbols,
        settings.bootstrap_enabled,
        settings.websocket_enabled,
        settings.execution_enabled,
        settings.dry_run,
        candle_store.backend,
    )
    if settings.bootstrap_enabled or settings.websocket_enabled:
        threading.Thread(target=_start_data_runtime,name="v2-data-runtime",daemon=True).start()


@app.on_event("shutdown")
def shutdown()->None:
    if stream is not None:
        stream.stop()


@app.get("/health")
def health()->dict:
    return {
        "ok":True,
        "engine":"autotrade-v2",
        "pipeline":"historical BingX -> DB + BingX WS 15m -> DB -> FINAL14 -> BingX trade API",
        "strategy":"FINAL14_RR_TP2_2026-09-21",
        "bootstrap_enabled":settings.bootstrap_enabled,
        "websocket_enabled":settings.websocket_enabled,
        "execution_enabled":settings.execution_enabled,
        "dry_run":settings.dry_run,
        "db_backend":candle_store.backend,
        "runtime_status":runtime.get("status"),
        "ws":stream.status() if stream is not None else None,
    }


@app.get("/status")
def status()->dict:
    with _state_lock:
        return {"runtime":dict(runtime),"last_run":dict(last_run)}
