from __future__ import annotations

import logging
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from .config import Settings, validate_settings
from .control.logging import configure_logging
from .data.market_client import PublicMarketClient
from .data.service import DataService
from .data.store import CandleStore
from .execution.service import ExecutionService
from .execution.store import ExecutionStore
from .orchestrator import Orchestrator
from .strategy.engine import StrategyEngine

settings = Settings()
configure_logging(settings.log_level)
log = logging.getLogger("autotrade_v2")
validate_settings(settings)

candle_store = CandleStore(settings.state_db, settings.database_url)
execution_store = ExecutionStore(settings.state_db, settings.database_url)
data_service = DataService(
    PublicMarketClient(settings.bingx_base_url),
    candle_store,
    fetch_limit=settings.market_limit_15m,
    keep=settings.candle_keep_15m,
    required=settings.required_15m,
)
strategy_engine = StrategyEngine()
execution_service = ExecutionService(settings, execution_store)
orchestrator = Orchestrator(settings, data_service, strategy_engine, execution_service)

app = FastAPI(title="AutoTrade V2")
scheduler: BackgroundScheduler | None = None
_run_lock = Lock()
last_run: dict = {"status": "not_started"}


def scheduled_run() -> None:
    global last_run
    if not _run_lock.acquire(blocking=False):
        log.warning("PIPELINE_SKIP reason=local_lock")
        return
    try:
        last_run = orchestrator.run_all()
    finally:
        _run_lock.release()


@app.on_event("startup")
def startup() -> None:
    global scheduler
    log.info(
        "V2_START symbols=%s data=bingx_public strategy=FINAL14 execution_enabled=%s dry_run=%s db=%s",
        settings.symbols,
        settings.execution_enabled,
        settings.dry_run,
        candle_store.backend,
    )
    if settings.auto_scheduler:
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            scheduled_run,
            CronTrigger(minute="0,15,30,45", second=settings.scheduler_second, timezone="UTC"),
            id="v2_scan_15m",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
            replace_existing=True,
        )
        scheduler.start()
        log.info("V2_SCHEDULER_ENABLED second=%s", settings.scheduler_second)


@app.on_event("shutdown")
def shutdown() -> None:
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "engine": "autotrade-v2",
        "layers": ["data", "strategy", "execution", "control"],
        "strategy": "FINAL14_RR_TP2_2026-09-21",
        "execution_enabled": settings.execution_enabled,
        "dry_run": settings.dry_run,
        "scheduler": settings.auto_scheduler,
        "db_backend": candle_store.backend,
    }


@app.get("/status")
def status() -> dict:
    return last_run
