from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException

from .bingx_market import BingXMarketClient, closed_only
from .config import Settings
from .executor import Executor
from .state import SignalState
from .strategy import scan_latest

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("autotrade")

market = BingXMarketClient(base_url=settings.bingx_base_url, api_key=settings.bingx_api_key, api_secret=settings.bingx_api_secret)
state = SignalState(settings.state_db)
executor = Executor(settings)
scan_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None
last_scan_summary: dict = {"status": "not_run"}


def fetch_bundle(symbol: str):
    now_ms = market.server_time_ms()
    m15 = closed_only(market.klines(symbol, "15m", settings.limit_15m), now_ms)
    h1 = closed_only(market.klines(symbol, "1h", settings.limit_1h), now_ms)
    h4 = closed_only(market.klines(symbol, "4h", settings.limit_4h), now_ms)
    h6 = closed_only(market.klines(symbol, "6h", settings.limit_6h), now_ms)
    if min(len(m15), len(h1), len(h4), len(h6)) == 0:
        raise RuntimeError(f"Missing kline data for {symbol}")
    return now_ms, m15, h1, h4, h6


def run_scan(execute: bool = True) -> dict:
    global last_scan_summary
    if not scan_lock.acquire(blocking=False):
        return {"status": "busy", "message": "scan already running"}
    started = time.time()
    summary = {
        "status": "ok",
        "dry_run": settings.dry_run,
        "execute": execute,
        "symbols": {},
        "started_at": started,
    }
    try:
        for symbol in settings.symbols:
            try:
                now_ms, m15, h1, h4, h6 = fetch_bundle(symbol)
                signals = scan_latest(
                    symbol=symbol,
                    m15=m15,
                    h1=h1,
                    h4=h4,
                    h6=h6,
                    smc_mode=settings.smc_mode,
                    smc_swing_len=settings.smc_swing_len,
                    smc_confluence=settings.smc_confluence,
                    sl_pct=settings.sl_pct,
                    tp_pct=settings.tp_pct,
                    include_1h=True,
                )
                symbol_result = {
                    "server_time": now_ms,
                    "latest_15m_close": int(m15.iloc[-1]["close_time"]),
                    "latest_1h_close": int(h1.iloc[-1]["close_time"]),
                    "signals": [],
                }
                for sig in signals:
                    item = asdict(sig)
                    item["event_id"] = sig.event_id
                    if not execute:
                        item["action"] = "preview"
                        symbol_result["signals"].append(item)
                        continue

                    targets = executor.targets()
                    if not targets:
                        item["action"] = "no_webhook_configured"
                        symbol_result["signals"].append(item)
                        continue

                    results = []
                    for target_name, url, amount in targets:
                        if state.seen(sig.event_id, target_name):
                            results.append({"target": target_name, "action": "duplicate_ignored", "ok": True})
                            continue
                        result = executor.send_target(sig, target_name, url, amount)
                        results.append(result)
                        if result.get("ok"):
                            state.mark(sig.event_id, target_name, json.dumps(result, ensure_ascii=False))

                    item["execution"] = results
                    if all(r.get("ok") for r in results):
                        item["action"] = "dry_run" if settings.dry_run else "sent"
                    elif any(r.get("ok") for r in results):
                        item["action"] = "partial"
                    else:
                        item["action"] = "failed"
                    symbol_result["signals"].append(item)
                summary["symbols"][symbol] = symbol_result
            except Exception as exc:
                log.exception("Scan failed for %s", symbol)
                summary["status"] = "partial_error"
                summary["symbols"][symbol] = {"error": str(exc)}
        summary["elapsed_sec"] = round(time.time() - started, 3)
        last_scan_summary = summary
        return summary
    finally:
        scan_lock.release()


def scheduled_scan():
    log.info("Scheduled scan started")
    result = run_scan(execute=True)
    log.info("Scheduled scan finished: %s", json.dumps(result, ensure_ascii=False)[:3000])


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler
    if settings.auto_scheduler:
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            scheduled_scan,
            CronTrigger(minute="0,15,30,45", second=settings.scheduler_second, timezone="UTC"),
            id="scan_15m",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )
        scheduler.start()
        log.info("Scheduler enabled at minutes 00/15/30/45 + %ss UTC", settings.scheduler_second)
    else:
        log.info("Scheduler disabled; call POST /scan externally")
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="BingX 10 Combo + SMC Autotrade Engine", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {
        "ok": True,
        "dry_run": settings.dry_run,
        "symbols": settings.symbols,
        "smc_mode": settings.smc_mode,
        "scheduler": settings.auto_scheduler,
    }



@app.get("/market-check")
def market_check():
    try:
        symbols = market.contract_symbols()
        configured = list(settings.symbols)
        return {
            "ok": True,
            "configured_symbols": configured,
            "supported": {s: s in symbols for s in configured},
            "contract_count": len(symbols),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


@app.get("/status")
def status():
    return last_scan_summary


@app.post("/preview")
def preview():
    result = run_scan(execute=False)
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/scan")
def scan():
    result = run_scan(execute=True)
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result)
    return result
