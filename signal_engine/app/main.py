from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("bingx_ip_probe")

app = FastAPI(title="BingX Render IP Probe")

IP_URL = "https://api.ipify.org"
BINGX_URL = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
BINGX_PARAMS = {
    "symbol": "BTC-USDT",
    "interval": "15m",
    "limit": 1,
}
BINGX_HEADERS = {
    "X-SOURCE-KEY": "BX-AI-SKILL",
}
TIMEOUT = 10.0

scheduler: BackgroundScheduler | None = None
last_probe: dict = {
    "mode": "probe_only",
    "bingx_authenticated": False,
    "trading_enabled": False,
    "retry_enabled": False,
    "last_run": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_probe() -> dict:
    result = {
        "mode": "probe_only",
        "started_at": _now_iso(),
        "egress_ip": None,
        "ip_error": None,
        "bingx_http_status": None,
        "bingx_code": None,
        "bingx_msg": None,
        "bingx_error": None,
        "request": {
            "endpoint": "/openApi/swap/v3/quote/klines",
            "params": dict(BINGX_PARAMS),
            "authenticated": False,
            "retry": False,
        },
    }

    # Non-BingX call used only to observe Render's actual public egress IP.
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            ip_resp = client.get(IP_URL)
            ip_resp.raise_for_status()
            result["egress_ip"] = ip_resp.text.strip()
    except Exception as exc:
        result["ip_error"] = f"{type(exc).__name__}: {exc}"

    log.info(
        "PROBE_IP started_at=%s egress_ip=%s error=%s",
        result["started_at"],
        result["egress_ip"],
        result["ip_error"],
    )

    # Exactly ONE BingX request. No API key, no signature, no retry/fallback.
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(
                BINGX_URL,
                params=BINGX_PARAMS,
                headers=BINGX_HEADERS,
            )
            result["bingx_http_status"] = resp.status_code
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict):
                result["bingx_code"] = payload.get("code")
                result["bingx_msg"] = payload.get("msg")
    except Exception as exc:
        result["bingx_error"] = f"{type(exc).__name__}: {exc}"

    log.info(
        "PROBE_BINGX egress_ip=%s http_status=%s code=%s msg=%s error=%s "
        "symbol=BTC-USDT interval=15m limit=1 auth=public retry=false",
        result["egress_ip"],
        result["bingx_http_status"],
        result["bingx_code"],
        result["bingx_msg"],
        result["bingx_error"],
    )

    result["finished_at"] = _now_iso()
    last_probe.clear()
    last_probe.update(result)
    return result


@app.on_event("startup")
def startup() -> None:
    global scheduler
    log.warning(
        "PROBE_MODE_ENABLED trading=false account_api=false strategy=false "
        "database=false retry=false bingx_requests_per_run=1"
    )
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_probe,
        CronTrigger(minute="0,15,30,45", second=8, timezone="UTC"),
        id="bingx_ip_probe_15m",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True,
    )
    scheduler.start()
    log.info("PROBE_SCHEDULER_ENABLED minutes=0,15,30,45 second=8 timezone=UTC")


@app.on_event("shutdown")
def shutdown() -> None:
    global scheduler
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            log.exception("PROBE_SCHEDULER_SHUTDOWN_ERROR")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "mode": "probe_only",
        "trading_enabled": False,
        "bingx_authenticated": False,
        "scheduled_bingx_requests_per_run": 1,
    }


@app.get("/status")
def status() -> dict:
    return dict(last_probe)
