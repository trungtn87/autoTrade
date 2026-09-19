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


def _aggregate_15m(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """Build complete UTC-aligned HTF candles from cached closed 15m candles."""
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    source_ms = 15 * 60_000
    target_ms = target_minutes * 60_000
    required = target_minutes // 15

    x = df.sort_values("open_time").drop_duplicates("open_time", keep="last").copy()
    x["bucket"] = (x["open_time"] // target_ms) * target_ms

    rows = []
    for bucket, g in x.groupby("bucket", sort=True):
        g = g.sort_values("open_time")
        if len(g) != required:
            continue
        expected = [int(bucket) + i * source_ms for i in range(required)]
        actual = [int(v) for v in g["open_time"].tolist()]
        if actual != expected:
            continue
        rows.append({
            "open_time": int(bucket),
            "open": float(g.iloc[0]["open"]),
            "high": float(g["high"].max()),
            "low": float(g["low"].min()),
            "close": float(g.iloc[-1]["close"]),
            "volume": float(g["volume"].sum()),
            "close_time": int(bucket) + target_ms - 1,
        })
    return pd.DataFrame(rows, columns=cols)


def fetch_bundle(symbol: str):
    """Production data path: BingX 15m only.

    First run bootstraps recent 15m history once. Normal operation requests
    only the latest two 15m candles, then builds 1H/4H/6H locally.
    """
    now_ms = int(time.time() * 1000)
    cached_count = state.candle_count(symbol, "15m")
    bootstrap = cached_count == 0

    limit = settings.bootstrap_limit_15m if bootstrap else settings.live_limit_15m
    incoming = closed_only(market.klines(symbol, "15m", limit), now_ms)

    last_before = state.latest_open_time(symbol, "15m")
    if len(incoming):
        state.upsert_candles(symbol, "15m", incoming)

    # A normal limit=2 request should contain the just-closed candle. If the
    # cached timeline has a gap, make at most one small recovery request.
    if not bootstrap and last_before is not None and len(incoming):
        newest = int(incoming.iloc[-1]["open_time"])
        expected_next = int(last_before) + 15 * 60_000
        if newest > expected_next:
            recovery = closed_only(
                market.klines(symbol, "15m", settings.recovery_limit_15m),
                now_ms,
            )
            if len(recovery):
                state.upsert_candles(symbol, "15m", recovery)

    state.trim_candles(symbol, "15m", settings.candle_keep_15m)
    m15 = state.load_candles(symbol, "15m")

    h1 = _aggregate_15m(m15, 60)
    h4 = _aggregate_15m(m15, 240)
    h6 = _aggregate_15m(m15, 360)

    if min(len(m15), len(h1), len(h4), len(h6)) == 0:
        raise RuntimeError(
            f"Warmup incomplete for {symbol}: "
            f"15m={len(m15)} 1h={len(h1)} 4h={len(h4)} 6h={len(h6)}"
        )

    return now_ms, m15, h1, h4, h6, bootstrap


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
                now_ms, m15, h1, h4, h6, bootstrap = fetch_bundle(symbol)
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
                    "market_source": "bingx_15m_only",
                    "bootstrap": bootstrap,
                    "cached_15m": len(m15),
                    "derived_1h": len(h1),
                    "derived_4h": len(h4),
                    "derived_6h": len(h6),
                    "signals": [],
                }
                for sig in signals:
                    item = asdict(sig)
                    item["event_id"] = sig.event_id
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
            except Exception as exc:
                log.exception("Scan failed for %s", symbol)
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
        "market_mode": "15m_only_incremental",
        "live_limit_15m": settings.live_limit_15m,
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



@app.get("/kline-check")
def kline_check(symbol: str = "BTC-USDT", interval: str = "15m", limit: int = 3):
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


@app.get("/status")
def status():
    return last_scan_summary


def _require_scan_token(x_scan_token: str | None) -> None:
    # Live scheduler never uses this route. Manual API-triggered scans are
    # disabled unless a secret token is explicitly configured.
    if not settings.scan_token:
        raise HTTPException(status_code=403, detail="manual scans disabled")
    if x_scan_token != settings.scan_token:
        raise HTTPException(status_code=403, detail="invalid scan token")


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
