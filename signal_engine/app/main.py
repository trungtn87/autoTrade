from __future__ import annotations

import json
import logging
import threading
import time

import pandas as pd
from contextlib import asynccontextmanager
from dataclasses import asdict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException

from .bingx_market import BingXApiError, BingXMarketClient, closed_only
from .data_validation import DataValidationError, validate_15m_candles
from .config import Settings, safe_config_snapshot, validate_settings
from .executor import Executor
from .discord_diag import install_discord_log_handler, send_discord_scan_summary, send_discord_startup_test
from .state import SignalState
from .strategy import scan_latest, strategy_static_snapshot
from .self_test import run_self_test
from .timeframes import aggregate_15m

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
# httpx/httpcore INFO records include the full request URL, which may contain
# Discord/order webhook secrets. Keep only warnings/errors from these libraries.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("autotrade")

market = BingXMarketClient(base_url=settings.bingx_base_url, api_key=settings.bingx_api_key, api_secret=settings.bingx_api_secret)
state = SignalState(settings.state_db, settings.database_url)
executor = Executor(settings)
scan_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None
last_scan_summary: dict = {"status": "not_run"}


def fetch_bundle(symbol: str):
    """Production data path: BingX 15m only.

    First run bootstraps recent 15m history once. Normal operation requests
    only the latest two 15m candles, then builds 1H/4H/6H locally.
    """
    started = time.monotonic()
    now_ms = int(time.time() * 1000)
    cached_count = state.candle_count(symbol, "15m")
    bootstrap = cached_count == 0

    limit = min(settings.bootstrap_limit_15m, 1000) if bootstrap else settings.live_limit_15m
    log.info(
        "FETCH_START symbol=%s mode=%s cached_15m=%s limit=%s",
        symbol, "bootstrap" if bootstrap else "incremental", cached_count, limit,
    )
    incoming = closed_only(market.klines(symbol, "15m", limit), now_ms)
    if len(incoming):
        log.info(
            "FETCH_DATA symbol=%s received_closed=%s first_open=%s last_open=%s last_close=%s",
            symbol, len(incoming), int(incoming.iloc[0]["open_time"]),
            int(incoming.iloc[-1]["open_time"]), int(incoming.iloc[-1]["close_time"]),
        )
    else:
        log.warning("FETCH_DATA symbol=%s received_closed=0", symbol)

    last_before = state.latest_open_time(symbol, "15m")
    if len(incoming):
        state.upsert_candles(symbol, "15m", incoming)

    # A normal limit=2 request should contain the just-closed candle. If the
    # cached timeline has a gap, make at most one small recovery request.
    if not bootstrap and last_before is not None and len(incoming):
        newest = int(incoming.iloc[-1]["open_time"])
        expected_next = int(last_before) + 15 * 60_000
        if newest > expected_next:
            missing = max(0, (newest - expected_next) // (15 * 60_000))
            log.warning(
                "CANDLE_GAP symbol=%s last_cached=%s newest=%s missing_intervals=%s recovery_limit=%s",
                symbol, last_before, newest, missing, settings.recovery_limit_15m,
            )
            recovery = closed_only(
                market.klines(symbol, "15m", settings.recovery_limit_15m),
                now_ms,
            )
            if len(recovery):
                state.upsert_candles(symbol, "15m", recovery)
                log.info("RECOVERY_OK symbol=%s recovered=%s", symbol, len(recovery))
            else:
                log.warning("RECOVERY_EMPTY symbol=%s", symbol)

    state.trim_candles(symbol, "15m", settings.candle_keep_15m)
    m15 = state.load_candles(symbol, "15m")

    validation = validate_15m_candles(m15, now_ms)
    log.info(
        "DATA_VALIDATION symbol=%s ok=true count=%s latest_open=%s latest_close=%s",
        symbol,
        validation.get("count"),
        validation.get("latest_open"),
        validation.get("latest_close"),
    )

    h1 = aggregate_15m(m15, 60)
    h4 = aggregate_15m(m15, 240)
    h6 = aggregate_15m(m15, 360)

    log.info(
        "DERIVED symbol=%s 15m=%s 1h=%s 4h=%s 6h=%s elapsed_ms=%s",
        symbol, len(m15), len(h1), len(h4), len(h6),
        round((time.monotonic() - started) * 1000, 1),
    )

    if min(len(m15), len(h1), len(h4), len(h6)) == 0:
        raise RuntimeError(
            f"Warmup incomplete for {symbol}: "
            f"15m={len(m15)} 1h={len(h1)} 4h={len(h4)} 6h={len(h6)}"
        )

    return now_ms, m15, h1, h4, h6, bootstrap, validation


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
            symbol_started = time.monotonic()
            try:
                log.info("SYMBOL_SCAN_START symbol=%s execute=%s", symbol, execute)
                now_ms, m15, h1, h4, h6, bootstrap, data_validation = fetch_bundle(symbol)
                calc_started = time.monotonic()
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
                calc_ms = round((time.monotonic() - calc_started) * 1000, 1)
                log.info(
                    "SIGNAL_CALC symbol=%s signals=%s calc_ms=%s ids=%s",
                    symbol, len(signals), calc_ms, [s.event_id for s in signals],
                )
                symbol_result = {
                    "server_time": now_ms,
                    "latest_15m_close": int(m15.iloc[-1]["close_time"]),
                    "latest_1h_close": int(h1.iloc[-1]["close_time"]),
                    "market_source": "bingx_15m_only",
                    "state_backend": state.backend,
                    "bootstrap": bootstrap,
                    "data_validation": data_validation,
                    "cached_15m": len(m15),
                    "derived_1h": len(h1),
                    "derived_4h": len(h4),
                    "derived_6h": len(h6),
                    "signals": [],
                }
                for sig in signals:
                    item = asdict(sig)
                    item["event_id"] = sig.event_id

                    # Safety for a brand-new/rotated database: bootstrap loads
                    # historical context but must never execute a signal from
                    # data that existed before this engine instance had a
                    # persistent dedupe history. Trading resumes on the next
                    # incremental 15m cycle.
                    if bootstrap:
                        item["action"] = "bootstrap_suppressed"
                        item["reason"] = "first persistent-data warmup; execution starts next incremental candle"
                        symbol_result["signals"].append(item)
                        log.warning(
                            "BOOTSTRAP_SIGNAL_SUPPRESSED symbol=%s event_id=%s combo=%s side=%s tf=%s",
                            sig.symbol, sig.event_id, sig.combo, sig.side, sig.timeframe,
                        )
                        continue

                    expected_close = (
                        int(m15.iloc[-1]["close_time"])
                        if sig.timeframe == "15m"
                        else int(h1.iloc[-1]["close_time"])
                    )
                    if sig.close_time != expected_close:
                        item["action"] = "rejected_stale_signal"
                        item["expected_close_time"] = expected_close
                        symbol_result["signals"].append(item)
                        log.error(
                            "SIGNAL_REJECT_STALE symbol=%s event_id=%s tf=%s signal_close=%s expected_close=%s",
                            sig.symbol, sig.event_id, sig.timeframe, sig.close_time, expected_close,
                        )
                        continue

                    if not execute:
                        item["action"] = "preview"
                        symbol_result["signals"].append(item)
                        continue

                    discord_result = None
                    discord_state_key = f"discord:{sig.symbol}"
                    if executor.discord_url(sig.symbol) and executor.discord_allowed():
                        if state.seen(sig.event_id, discord_state_key):
                            discord_result = {
                                "target": discord_state_key,
                                "action": "duplicate_ignored",
                                "ok": True,
                            }
                        else:
                            discord_result = executor.send_discord(sig)
                            if discord_result.get("ok"):
                                state.mark(
                                    sig.event_id,
                                    discord_state_key,
                                    json.dumps(discord_result, ensure_ascii=False),
                                )
                    if discord_result is not None:
                        item["discord"] = discord_result

                    targets = executor.targets()
                    results = []
                    for target_name, url, amount in targets:
                        state_target = f"{target_name}:{'dryrun' if settings.dry_run else 'live'}"
                        if state.seen(sig.event_id, state_target):
                            results.append({"target": target_name, "action": "duplicate_ignored", "ok": True})
                            continue
                        result = executor.send_target(sig, target_name, url, amount)
                        results.append(result)
                        if result.get("ok"):
                            state.mark(sig.event_id, state_target, json.dumps(result, ensure_ascii=False))

                    item["execution"] = results
                    if not targets:
                        item["action"] = "discord_only" if discord_result and discord_result.get("ok") else "no_order_webhook_configured"
                    elif all(r.get("ok") for r in results):
                        item["action"] = "dry_run" if settings.dry_run else "sent"
                    elif any(r.get("ok") for r in results):
                        item["action"] = "partial"
                    else:
                        item["action"] = "failed"
                    symbol_result["signals"].append(item)
                summary["symbols"][symbol] = symbol_result
                log.info(
                    "SYMBOL_SCAN_DONE symbol=%s signals=%s elapsed_ms=%s",
                    symbol, len(signals), round((time.monotonic() - symbol_started) * 1000, 1),
                )
            except Exception as exc:
                log.exception(
                    "SYMBOL_SCAN_ERROR symbol=%s elapsed_ms=%s error=%s",
                    symbol, round((time.monotonic() - symbol_started) * 1000, 1), exc,
                )
                summary["status"] = "partial_error"
                summary["symbols"][symbol] = {"error": str(exc)}
                if isinstance(exc, BingXApiError) and str(exc.code) in {"109415", "109425", "109429"}:
                    summary["stopped_early"] = True
                    summary["stop_reason"] = f"BingX {exc.code}; stopped to avoid further invalid requests"
                    break
        summary["elapsed_sec"] = round(time.time() - started, 3)
        last_scan_summary = summary
        return summary
    finally:
        scan_lock.release()


def scheduled_scan():
    log.info(
        "SCHEDULE_TICK dry_run=%s symbols=%s market_mode=15m_only_incremental",
        settings.dry_run, list(settings.symbols),
    )
    result = run_scan(execute=True)
    log.info(
        "SCHEDULE_DONE status=%s elapsed_sec=%s summary=%s",
        result.get("status"), result.get("elapsed_sec"),
        json.dumps(result, ensure_ascii=False)[:3000],
    )

    discord_report = send_discord_scan_summary(settings, result)
    log.info(
        "DISCORD_15M_REPORT ok=%s sent=%s status_code=%s reason=%s error=%s",
        discord_report.get("ok"),
        discord_report.get("sent"),
        discord_report.get("status_code"),
        discord_report.get("reason"),
        discord_report.get("error"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler

    errors, warnings = validate_settings(settings)
    log.info("STARTUP_CONFIG %s", json.dumps(safe_config_snapshot(settings), ensure_ascii=False))
    log.info("STRATEGY_PROFILE %s", json.dumps(strategy_static_snapshot(), ensure_ascii=False))

    for warning in warnings:
        log.warning("CONFIG_WARNING %s", warning)

    if errors:
        for error in errors:
            log.error("CONFIG_ERROR %s", error)
        raise RuntimeError("Invalid startup configuration; see CONFIG_ERROR lines above")

    log.info("CONFIG_VALIDATION ok=true warnings=%s", len(warnings))
    log.info(
        "STATE_BACKEND backend=%s persistent=%s",
        state.backend,
        state.backend == "postgres",
    )

    self_test_result = run_self_test(settings)
    for item in self_test_result.get("checks", []):
        level = log.info if item.get("ok") else log.error
        level(
            "SELF_TEST name=%s ok=%s elapsed_ms=%s details=%s error=%s",
            item.get("name"),
            item.get("ok"),
            item.get("elapsed_ms"),
            json.dumps(item.get("details", {}), ensure_ascii=False)[:1000],
            item.get("error"),
        )
    if not self_test_result.get("ok"):
        raise RuntimeError("Offline self-test failed; see SELF_TEST lines above")
    log.info("SELF_TEST_SUMMARY ok=true checks=%s network_calls=0 order_calls=0", len(self_test_result.get("checks", [])))

    discord_test = send_discord_startup_test(settings)
    if discord_test.get("ok"):
        log.info(
            "DISCORD_STARTUP_TEST ok=true sent=%s status_code=%s",
            discord_test.get("sent"), discord_test.get("status_code"),
        )
    else:
        log.warning(
            "DISCORD_STARTUP_TEST ok=false reason=%s error=%s status_code=%s body=%s",
            discord_test.get("reason"),
            discord_test.get("error"),
            discord_test.get("status_code"),
            discord_test.get("body"),
        )

    if settings.discord_log_enabled:
        discord_log = install_discord_log_handler(settings)
        if discord_log.get("ok"):
            log.info(
                "DISCORD_LOG_FORWARD installed=%s level=%s reason=%s",
                discord_log.get("installed"), discord_log.get("level"), discord_log.get("reason"),
            )
        else:
            log.warning(
                "DISCORD_LOG_FORWARD installed=false reason=%s",
                discord_log.get("reason"),
            )
    else:
        log.info("DISCORD_LOG_FORWARD installed=false reason=disabled")

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
        "market_mode": "15m_only_incremental",
        "state_backend": state.backend,
        "execution_ready": (not settings.dry_run) and bool(executor.targets()),
        "order_target_count": len(executor.targets()),
        "live_limit_15m": settings.live_limit_15m,
    }



def _require_scan_token(x_scan_token: str | None) -> None:
    # Live scheduler never uses these routes. Manual API-triggered market
    # requests are disabled unless a secret token is explicitly configured.
    if not settings.scan_token:
        raise HTTPException(status_code=403, detail="manual market requests disabled")
    if x_scan_token != settings.scan_token:
        raise HTTPException(status_code=403, detail="invalid scan token")


@app.get("/market-check")
def market_check(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
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



@app.get("/kline-check")
def kline_check(symbol: str = "BTC-USDT", interval: str = "15m", limit: int = 3, x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    """Single Kline request with timestamp diagnostics; never places orders."""
    try:
        now_ms = int(time.time() * 1000)
        df = market.klines(symbol.upper(), interval, limit)
        rows = []
        for _, row in df.tail(3).iterrows():
            rows.append({
                "open_time": int(row["open_time"]),
                "close_time": int(row["close_time"]),
                "close": float(row["close"]),
                "close_minus_now_ms": int(row["close_time"]) - now_ms,
            })
        closed = closed_only(df, now_ms)
        result = {
            "ok": True,
            "symbol": symbol.upper(),
            "interval": interval,
            "requested_limit": limit,
            "now_ms": now_ms,
            "received_candles": len(df),
            "closed_candles": len(closed),
            "rows": rows,
        }
        if len(closed):
            last = closed.iloc[-1]
            result["last_closed"] = {
                "close": float(last["close"]),
                "open_time": int(last["open_time"]),
                "close_time": int(last["close_time"]),
            }
        return result
    except Exception as exc:
        return {
            "ok": False,
            "symbol": symbol.upper(),
            "interval": interval,
            "requested_limit": limit,
            "error": str(exc),
        }


@app.post("/self-test")
def self_test(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = run_self_test(settings)
    log.info("SELF_TEST_MANUAL ok=%s checks=%s", result.get("ok"), len(result.get("checks", [])))
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result)
    return result


@app.post("/discord-test")
def discord_test(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = send_discord_startup_test(settings)
    log.info(
        "DISCORD_MANUAL_TEST ok=%s sent=%s status_code=%s",
        result.get("ok"), result.get("sent"), result.get("status_code"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@app.get("/status")
def status():
    return last_scan_summary


@app.post("/preview")
def preview(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = run_scan(execute=False)
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/scan")
def scan(x_scan_token: str | None = Header(default=None)):
    _require_scan_token(x_scan_token)
    result = run_scan(execute=True)
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail=result)
    return result
