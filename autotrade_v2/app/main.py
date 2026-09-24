from __future__ import annotations

import logging
import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings, validate_settings
from .control.logging import configure_logging
from .control.events import EventReporter
from .data.historical import HistoricalKlineClient
from .data.service import DataService
from .data.store import CandleStore
from .data.websocket import BingXKlineStream
from .execution.service import ExecutionService
from .execution.store import ExecutionStore
from .orchestrator import Orchestrator
from .strategy.engine import StrategyEngine

STEP_15M_MS=15*60_000
# BingX live kline handoff can arrive ~10-20s after the 15m boundary.\n# Give WS priority; REST recovery is only a true fallback.\nCANDLE_GRACE_MS=30_000
WATCHDOG_POLL_SEC=2.0
RECOVERY_RETRY_MS=60_000
RECOVERY_MAX_ATTEMPTS=2

settings=Settings()
configure_logging(settings.log_level)
log=logging.getLogger("autotrade_v2")
validate_settings(settings)

candle_store=CandleStore(settings.state_db,settings.database_url)
execution_store=ExecutionStore(settings.state_db,settings.database_url)
event_reporter=EventReporter(
    settings.database_url,
    discord_enabled=settings.discord_enabled,
    webhook_btc=settings.discord_webhook_btc,
    webhook_eth=settings.discord_webhook_eth,
    webhook_error=settings.discord_webhook_error,
)
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
execution_service=ExecutionService(settings,execution_store,event_cb=event_reporter.emit)
orchestrator=Orchestrator(
    settings,data_service,strategy_engine,execution_service,event_cb=event_reporter.emit
)

app=FastAPI(title="AutoTrade V2")
_state_lock=threading.Lock()
_pipeline_lock=threading.Lock()
_watchdog_stop=threading.Event()
_processed_open:dict[str,int]={}
_recovery_attempts:dict[tuple[str,int],dict]={}
last_run:dict={"status":"not_started"}
runtime:dict={
    "status":"idle",
    "bootstrap_enabled":settings.bootstrap_enabled,
    "websocket_enabled":settings.websocket_enabled,
    "execution_enabled":settings.execution_enabled,
    "execution_preflight":{"ok":False,"credentials_verified":False,"error":"not_run"},
    "data_health":{},
}
stream:BingXKlineStream|None=None


def _effective_validation_now(now_ms:int|None=None)->int:
    now_ms=int(now_ms or time.time()*1000)
    boundary=(now_ms//STEP_15M_MS)*STEP_15M_MS
    if now_ms-boundary<CANDLE_GRACE_MS:
        return boundary-1
    return now_ms


def _set_data_health(symbol:str,ok:bool,**details)->None:
    with _state_lock:
        health=dict(runtime.get("data_health") or {})
        health[symbol]={"ok":bool(ok),**details}
        runtime["data_health"]=health


def _on_closed(symbol,frame)->None:
    global last_run
    symbol=str(symbol).upper()
    open_time=int(frame.iloc[-1]["open_time"])
    with _pipeline_lock:
        if open_time<=int(_processed_open.get(symbol,0)):
            log.info("PIPELINE_SKIP_DUP symbol=%s open_time=%s",symbol,open_time)
            return
        result=orchestrator.on_closed_candle(symbol,frame)
        if result.get("ok"):
            _processed_open[symbol]=open_time
            _set_data_health(
                symbol,True,
                latest_open_time=open_time,
                source="pipeline",
                checked_at_ms=int(time.time()*1000),
            )
        else:
            _set_data_health(
                symbol,False,
                error=result.get("error"),
                stage=result.get("stage"),
                checked_at_ms=int(time.time()*1000),
            )
        with _state_lock:
            last_run=result


def _candle_watchdog()->None:
    while not _watchdog_stop.wait(WATCHDOG_POLL_SEC):
        now_ms=int(time.time()*1000)
        boundary=(now_ms//STEP_15M_MS)*STEP_15M_MS
        if now_ms-boundary<CANDLE_GRACE_MS:
            continue
        expected_open=boundary-STEP_15M_MS

        for symbol in settings.symbols:
            try:
                snapshot=data_service.snapshot_from_store(symbol,now_ms)
                _set_data_health(
                    symbol,True,
                    latest_open_time=snapshot.latest_open_time,
                    expected_open_time=expected_open,
                    source="store",
                    checked_at_ms=now_ms,
                )
                continue
            except Exception as exc:
                _set_data_health(
                    symbol,False,
                    expected_open_time=expected_open,
                    error=str(exc),
                    source="watchdog",
                    checked_at_ms=now_ms,
                )

            key=(symbol,expected_open)
            state=_recovery_attempts.get(key,{"count":0,"last_ms":0})
            if state["count"]>=RECOVERY_MAX_ATTEMPTS:
                continue
            if state["count"]>0 and now_ms-int(state["last_ms"])<RECOVERY_RETRY_MS:
                continue
            state={"count":int(state["count"])+1,"last_ms":now_ms}
            _recovery_attempts[key]=state

            log.warning(
                "CANDLE_STALE symbol=%s expected_open=%s recovery_attempt=%s",
                symbol,expected_open,state["count"],
            )
            event_reporter.emit(
                "L1.DATA.CANDLE_STALE","WARNING","latest closed 15m candle missing or invalid",
                symbol=symbol,
                details={
                    "expected_open_time":expected_open,
                    "attempt":state["count"],
                },
            )
            try:
                snapshot,missing=data_service.recover_missing_closed(symbol,now_ms)
                log.warning(
                    "CANDLE_RECOVERY_OK symbol=%s recovered=%s latest_open=%s",
                    symbol,len(missing),snapshot.latest_open_time,
                )
                event_reporter.emit(
                    "L1.DATA.RECOVERY_OK","INFO","missing closed candles recovered from public REST",
                    symbol=symbol,
                    details={
                        "recovered_count":len(missing),
                        "recovered_open_times":missing,
                        "latest_open_time":snapshot.latest_open_time,
                    },
                )
                _set_data_health(
                    symbol,True,
                    latest_open_time=snapshot.latest_open_time,
                    expected_open_time=expected_open,
                    recovered_count=len(missing),
                    source="rest_recovery",
                    checked_at_ms=int(time.time()*1000),
                )
                if missing and snapshot.latest_open_time==expected_open:
                    _on_closed(symbol,snapshot.candles.tail(1).copy(deep=True))
            except Exception as exc:
                log.exception(
                    "CANDLE_RECOVERY_FAIL symbol=%s expected_open=%s error=%s",
                    symbol,expected_open,exc,
                )
                event_reporter.emit(
                    "L1.DATA.RECOVERY_FAIL","ERROR",str(exc),
                    symbol=symbol,
                    details={
                        "expected_open_time":expected_open,
                        "attempt":state["count"],
                    },
                )
                _set_data_health(
                    symbol,False,
                    expected_open_time=expected_open,
                    error=str(exc),
                    source="rest_recovery",
                    checked_at_ms=int(time.time()*1000),
                )

        if len(_recovery_attempts)>64:
            cutoff=expected_open-8*STEP_15M_MS
            for key in list(_recovery_attempts):
                if key[1]<cutoff:
                    _recovery_attempts.pop(key,None)


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
        for item in results:
            if item.get("ok"):
                _set_data_health(
                    item["symbol"],True,
                    latest_close_time=item.get("snapshot_close"),
                    source="bootstrap",
                    checked_at_ms=int(time.time()*1000),
                )
    else:
        checks=[]
        validation_now=_effective_validation_now()
        for symbol in settings.symbols:
            try:
                snap=data_service.snapshot_from_store(symbol,validation_now)
                checks.append({"ok":True,"symbol":symbol,"candles":len(snap.candles)})
                _set_data_health(
                    symbol,True,
                    latest_open_time=snap.latest_open_time,
                    source="store_check",
                    checked_at_ms=int(time.time()*1000),
                )
            except Exception as exc:
                checks.append({"ok":False,"symbol":symbol,"error":str(exc)})
                _set_data_health(
                    symbol,False,error=str(exc),source="store_check",
                    checked_at_ms=int(time.time()*1000),
                )
        with _state_lock:
            runtime["store_check"]=checks
        if settings.websocket_enabled and not all(x.get("ok") for x in checks):
            with _state_lock:
                runtime["status"]="store_not_ready"
            return

    if settings.websocket_enabled:
        stream=BingXKlineStream(
            settings.symbols,_on_closed,url=settings.bingx_ws_url,on_event=event_reporter.emit
        )
        stream.start()
        _watchdog_stop.clear()
        threading.Thread(target=_candle_watchdog,name="v2-candle-watchdog",daemon=True).start()
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
    log.info(
        "LAYER4_CONFIG discord_enabled=%s btc=%s eth=%s error=%s audit_db=%s",
        settings.discord_enabled,
        bool(settings.discord_webhook_btc),
        bool(settings.discord_webhook_eth),
        bool(settings.discord_webhook_error),
        bool(settings.database_url),
    )
    preflight=execution_service.preflight()
    with _state_lock:
        runtime["execution_preflight"]=preflight
    if preflight.get("ok"):
        log.info("EXECUTION_PREFLIGHT ok=true credentials_verified=true")
        event_reporter.emit(
            "L3.EXEC.PREFLIGHT_OK","INFO","BingX private read-only preflight passed",
            details={"credentials_verified":True},
        )
    else:
        log.warning("EXECUTION_PREFLIGHT ok=false error=%s",preflight.get("error"))
        event_reporter.emit(
            "L3.EXEC.PREFLIGHT_FAIL","CRITICAL",str(preflight.get("error") or "preflight failed"),
            details={"credentials_verified":False},
        )

    event_reporter.emit(
        "L4.SYSTEM.STARTUP","INFO","AutoTrade V2 process started",
        details={
            "symbols":list(settings.symbols),
            "bootstrap_enabled":settings.bootstrap_enabled,
            "websocket_enabled":settings.websocket_enabled,
            "execution_enabled":settings.execution_enabled,
            "dry_run":settings.dry_run,
            "db_backend":candle_store.backend,
        },
    )

    if settings.bootstrap_enabled or settings.websocket_enabled:
        threading.Thread(target=_start_data_runtime,name="v2-data-runtime",daemon=True).start()


@app.on_event("shutdown")
def shutdown()->None:
    _watchdog_stop.set()
    if stream is not None:
        stream.stop()
    event_reporter.emit("L4.SYSTEM.SHUTDOWN","INFO","AutoTrade V2 process stopping")
    event_reporter.stop()


@app.get("/health")
def health():
    validation_now=_effective_validation_now()
    symbol_health={}
    for symbol in settings.symbols:
        try:
            snap=data_service.snapshot_from_store(symbol,validation_now)
            symbol_health[symbol]={
                "ok":True,
                "latest_open_time":snap.latest_open_time,
                "latest_close_time":snap.latest_close_time,
            }
        except Exception as exc:
            symbol_health[symbol]={"ok":False,"error":str(exc)}

    data_ok=all(x.get("ok") for x in symbol_health.values()) if symbol_health else True
    body={
        "ok":data_ok,
        "engine":"autotrade-v2",
        "pipeline":"historical BingX -> DB + BingX WS 15m -> DB -> FINAL14 -> BingX trade API",
        "strategy":"FINAL14_RR_TP2_2026-09-21",
        "bootstrap_enabled":settings.bootstrap_enabled,
        "websocket_enabled":settings.websocket_enabled,
        "execution_enabled":settings.execution_enabled,
        "execution_credentials_verified":execution_service.credentials_verified,
        "dry_run":settings.dry_run,
        "db_backend":candle_store.backend,
        "runtime_status":runtime.get("status"),
        "data":symbol_health,
        "ws":stream.status() if stream is not None else None,
        "layer4":event_reporter.status(),
    }
    return JSONResponse(status_code=200 if data_ok else 503,content=body)


@app.get("/status")
def status()->dict:
    with _state_lock:
        return {
            "runtime":dict(runtime),
            "last_run":dict(last_run),
            "processed_open":dict(_processed_open),
        }
