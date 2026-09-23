from __future__ import annotations

import json
import logging
import sys
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
from .final14_executor import Final14Executor
from .discord_diag import install_discord_log_handler, send_discord_scan_summary, send_discord_startup_test
from .state import SignalState
from .final14_exact_strategy import combo_readiness, scan_latest, strategy_static_snapshot
from .final14_positions import refresh_symbol, register_execution, snapshot as final14_position_snapshot
from .self_test import run_self_test

settings = Settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    stream=sys.stdout,
)
# httpx/httpcore INFO records include the full request URL, which may contain
# Discord/order webhook secrets. Keep only warnings/errors from these libraries.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("autotrade")

market = BingXMarketClient(base_url=settings.bingx_base_url, api_key=settings.bingx_api_key, api_secret=settings.bingx_api_secret)
state = SignalState(settings.state_db, settings.database_url)
executor = Final14Executor(settings)
scan_lock = threading.Lock()
scheduler: BackgroundScheduler | None = None
last_scan_summary: dict = {"status": "not_run"}


INTERVAL_15M_MS = 15 * 60_000


def _fetch_15m_range(
    symbol: str,
    start_open: int,
    end_open: int,
    now_ms: int,
) -> pd.DataFrame:
    """Fetch a closed, UTC-aligned 15m range with backward pagination."""
    if end_open < start_open:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])

    pages: list[pd.DataFrame] = []
    cursor_end = int(end_open) + INTERVAL_15M_MS - 1
    wanted = ((int(end_open) - int(start_open)) // INTERVAL_15M_MS) + 1
    remaining = wanted

    while remaining > 0 and cursor_end >= start_open:
        page_limit = min(1000, remaining)
        page = closed_only(
            market.klines(
                symbol,
                "15m",
                page_limit,
                start_time=int(start_open),
                end_time=int(cursor_end),
            ),
            now_ms,
        )
        if page.empty:
            break

        page = page[
            (page["open_time"] >= int(start_open))
            & (page["open_time"] <= int(end_open))
        ].copy()
        if page.empty:
            break

        pages.append(page)
        earliest = int(page.iloc[0]["open_time"])
        if earliest <= start_open:
            break

        cursor_end = earliest - 1
        remaining = max(
            0,
            ((earliest - int(start_open)) // INTERVAL_15M_MS),
        )

    if not pages:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])

    return (
        pd.concat(pages, ignore_index=True)
        .sort_values("open_time")
        .drop_duplicates("open_time", keep="last")
        .reset_index(drop=True)
    )


def _recover_15m_validation_gap(symbol: str, m15: pd.DataFrame, now_ms: int, exc: DataValidationError) -> pd.DataFrame:
    details = getattr(exc, "details", {}) or {}
    if str(exc) != "15m cache contains a candle gap":
        raise exc

    start_open = details.get("expected_next_open")
    next_open = details.get("next_open")
    missing = int(details.get("missing_intervals", 0) or 0)
    if start_open is None or next_open is None or missing <= 0:
        raise exc

    end_open = int(next_open) - INTERVAL_15M_MS
    log.warning(
        "CANDLE_GAP_INTERNAL symbol=%s previous_open=%s expected_next=%s next_open=%s missing_intervals=%s",
        symbol,
        details.get("previous_open"),
        start_open,
        next_open,
        missing,
    )
    recovery = _fetch_15m_range(
        symbol,
        int(start_open),
        int(end_open),
        now_ms,
    )
    if recovery.empty:
        log.error(
            "RECOVERY_EMPTY symbol=%s start_open=%s end_open=%s missing_intervals=%s",
            symbol, start_open, end_open, missing,
        )
        raise exc

    state.upsert_candles(symbol, "15m", recovery)
    log.info(
        "RECOVERY_BACKFILL symbol=%s requested_missing=%s recovered=%s first_open=%s last_open=%s",
        symbol,
        missing,
        len(recovery),
        int(recovery.iloc[0]["open_time"]),
        int(recovery.iloc[-1]["open_time"]),
    )
    state.trim_candles(symbol, "15m", settings.candle_keep_15m)
    return state.load_candles(symbol, "15m")


def fetch_bundle(symbol: str):
    """Production data path: keep the latest candle current, then warm history gradually."""
    started = time.monotonic()
    now_ms = int(time.time() * 1000)
    cached_count = state.candle_count(symbol, "15m")
    bootstrap = cached_count == 0
    target = max(3400, int(settings.bootstrap_limit_15m))

    # Always refresh the latest closed candle first. This keeps live data current
    # even while the historical warmup is still being filled.
    log.info(
        "FETCH_START symbol=%s mode=%s cached_15m=%s live_limit=%s target=%s",
        symbol,
        "bootstrap" if bootstrap else ("warmup_backfill" if cached_count < target else "incremental"),
        cached_count,
        settings.live_limit_15m,
        target,
    )
    latest = closed_only(
        market.klines(symbol, "15m", settings.live_limit_15m),
        now_ms,
    )
    if len(latest):
        state.upsert_candles(symbol, "15m", latest)
        log.info(
            "FETCH_DATA symbol=%s received_closed=%s first_open=%s last_open=%s last_close=%s",
            symbol,
            len(latest),
            int(latest.iloc[0]["open_time"]),
            int(latest.iloc[-1]["open_time"]),
            int(latest.iloc[-1]["close_time"]),
        )
    else:
        log.warning("FETCH_DATA symbol=%s received_closed=0", symbol)

    # Historical warmup is intentionally incremental: at most one older page
    # per scheduled scan. This avoids hammering BingX and, critically, every
    # successful page is persisted immediately so progress is never lost.
    cached_count = state.candle_count(symbol, "15m")
    if cached_count < target:
        earliest = state.earliest_open_time(symbol, "15m")
        if earliest is not None:
            missing = target - cached_count
            page_limit = min(1000, missing)
            history_end = int(earliest) - 1
            history_start = max(
                0,
                int(earliest) - page_limit * INTERVAL_15M_MS,
            )
            log.info(
                "WARMUP_BACKFILL_START symbol=%s cached_15m=%s target=%s page_limit=%s start=%s end=%s",
                symbol, cached_count, target, page_limit, history_start, history_end,
            )
            older = closed_only(
                market.klines(
                    symbol,
                    "15m",
                    page_limit,
                    start_time=history_start,
                    end_time=history_end,
                ),
                now_ms,
            )
            if len(older):
                state.upsert_candles(symbol, "15m", older)
                log.info(
                    "WARMUP_BACKFILL_SAVED symbol=%s recovered=%s first_open=%s last_open=%s cached_after=%s",
                    symbol,
                    len(older),
                    int(older.iloc[0]["open_time"]),
                    int(older.iloc[-1]["open_time"]),
                    state.candle_count(symbol, "15m"),
                )
            else:
                log.warning(
                    "WARMUP_BACKFILL_EMPTY symbol=%s start=%s end=%s",
                    symbol, history_start, history_end,
                )

    state.trim_candles(symbol, "15m", settings.candle_keep_15m)
    m15 = state.load_candles(symbol, "15m")

    try:
        validation = validate_15m_candles(m15, now_ms)
    except DataValidationError as exc:
        if str(exc) == "15m cache contains a candle gap":
            m15 = _recover_15m_validation_gap(symbol, m15, now_ms, exc)
            validation = validate_15m_candles(m15, now_ms)
        else:
            log.error(
                "DATA_VALIDATION_FAILED symbol=%s error=%s details=%s",
                symbol, exc, json.dumps(getattr(exc, "details", {}) or {}, ensure_ascii=False),
            )
            raise

    log.info(
        "DATA_VALIDATION symbol=%s ok=true count=%s latest_open=%s latest_close=%s",
        symbol,
        validation.get("count"),
        validation.get("latest_open"),
        validation.get("latest_close"),
    )

    log.info(
        "FETCH_READY symbol=%s 15m=%s elapsed_ms=%s",
        symbol, len(m15), round((time.monotonic() - started) * 1000, 1),
    )

    return now_ms, m15, bootstrap, validation


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
                now_ms, m15, bootstrap, data_validation = fetch_bundle(symbol)
                readiness = combo_readiness(m15, symbol=symbol)
                ready_combos = sorted(
                    combo for combo, item in readiness.items() if item.get("ready")
                )
                skipped_combos = sorted(
                    combo for combo, item in readiness.items() if not item.get("ready")
                )
                log.info(
                    "COMBO_READINESS symbol=%s ready=%s skipped=%s 15m=%s",
                    symbol, ready_combos, skipped_combos, len(m15),
                )

                calc_started = time.monotonic()
                signals = scan_latest(symbol=symbol, m15=m15)
                calc_ms = round((time.monotonic() - calc_started) * 1000, 1)
                log.info(
                    "SIGNAL_CALC symbol=%s signals=%s calc_ms=%s ids=%s",
                    symbol, len(signals), calc_ms, [s.event_id for s in signals],
                )

                # FINAL14 backtest semantics: one independent active position
                # per symbol+combo. Reconcile against BingX only when a new
                # signal exists; zero-signal scans keep the same request surface.
                position_state = {
                    "symbol": symbol,
                    "active": 0,
                    "closed": [],
                    "resolved": [],
                    "checked": False,
                }
                if execute and signals:
                    position_state = refresh_symbol(state, executor, symbol)
                    position_state["checked"] = True

                active_case_keys = {
                    (str(row.get("symbol") or "").upper(), int(row.get("combo")))
                    for row in final14_position_snapshot(state)
                    if row.get("combo") is not None
                } if signals else set()

                symbol_result = {
                    "server_time": now_ms,
                    "latest_15m_close": int(m15.iloc[-1]["close_time"]),
                    "market_source": "bingx_15m_only",
                    "state_backend": state.backend,
                    "bootstrap": bootstrap,
                    "data_validation": data_validation,
                    "cached_15m": len(m15),
                    "combo_readiness": {
                        "ready": ready_combos,
                        "skipped": skipped_combos,
                    },
                    "signals": [],
                    "final14_positions": position_state,
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

                    expected_close = int(m15.iloc[-1]["close_time"])
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
                        item["case_active"] = (
                            sig.symbol.upper(), int(sig.combo)
                        ) in active_case_keys
                        symbol_result["signals"].append(item)
                        continue

                    case_key = (sig.symbol.upper(), int(sig.combo))
                    if case_key in active_case_keys:
                        item["action"] = "active_case_suppressed"
                        item["reason"] = "FINAL14 one-position-per-symbol-combo"
                        symbol_result["signals"].append(item)
                        log.info(
                            "FINAL14_ACTIVE_SUPPRESS symbol=%s combo=%s event_id=%s",
                            sig.symbol, sig.combo, sig.event_id,
                        )
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

                    # Engine stays LIVE, but unsafe execution conditions block
                    # only the order leg rather than stopping the service.
                    execution_block_reason = None
                    if state.backend != "postgres":
                        execution_block_reason = "persistent_postgres_unavailable"
                    elif not targets:
                        execution_block_reason = "bingx_trade_credentials_unavailable"

                    if execution_block_reason:
                        log.error(
                            "ORDER_EXECUTION_BLOCKED symbol=%s event_id=%s reason=%s",
                            sig.symbol, sig.event_id, execution_block_reason,
                        )
                        item["execution"] = []
                        item["action"] = "execution_blocked"
                        item["execution_block_reason"] = execution_block_reason
                        symbol_result["signals"].append(item)
                        continue

                    for target_name, url, amount in targets:
                        state_target = f"{target_name}:{'dryrun' if settings.dry_run else 'live'}"
                        if state.seen(sig.event_id, state_target):
                            results.append({"target": target_name, "action": "duplicate_ignored", "ok": True})
                            continue
                        result = executor.send_target(sig, target_name, url, amount)
                        results.append(result)

                        # Once BingX accepts the MARKET entry, the event is
                        # permanently processed even if a later fill/position
                        # lookup fails. This prevents duplicate live entries.
                        if result.get("processed"):
                            state.mark(
                                sig.event_id,
                                state_target,
                                json.dumps(result, ensure_ascii=False),
                            )

                        if result.get("entry_accepted"):
                            register_execution(state, sig, result)
                            active_case_keys.add(case_key)

                    item["execution"] = results
                    if not targets:
                        item["action"] = "discord_only" if discord_result and discord_result.get("ok") else "no_order_webhook_configured"
                    elif all(r.get("ok") for r in results):
                        item["action"] = "dry_run" if settings.dry_run else "sent"
                    elif any(r.get("processed") for r in results):
                        item["action"] = "processed_with_error"
                    else:
                        item["action"] = "failed_before_entry"
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

    # Install ERROR-only Discord forwarding before validation so startup
    # configuration/runtime errors are visible without killing the service.
    discord_log = install_discord_log_handler(settings)
    if discord_log.get("ok"):
        log.info(
            "DISCORD_LOG_FORWARD installed=%s level=%s reason=%s",
            discord_log.get("installed"), discord_log.get("level"), discord_log.get("reason"),
        )
    else:
        log.error(
            "DISCORD_LOG_FORWARD installed=false reason=%s",
            discord_log.get("reason"),
        )

    errors, warnings = validate_settings(settings)
    log.info("STARTUP_CONFIG %s", json.dumps(safe_config_snapshot(settings), ensure_ascii=False))
    log.info("STRATEGY_PROFILE %s", json.dumps(strategy_static_snapshot(), ensure_ascii=False))

    for warning in warnings:
        log.warning("CONFIG_WARNING %s", warning)

    if errors:
        for error in errors:
            log.error("CONFIG_ERROR %s", error)
        log.error(
            "CONFIG_VALIDATION ok=false errors=%s warnings=%s service_continues=true",
            len(errors), len(warnings),
        )
    else:
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
        log.error(
            "SELF_TEST_SUMMARY ok=false checks=%s service_continues=true order_execution_guarded=true",
            len(self_test_result.get("checks", [])),
        )
    else:
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
        "execution_ready": state.backend == "postgres" and bool(executor.targets()),
        "execution_mode": "final14_100pct_hard_tp_sl",
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
