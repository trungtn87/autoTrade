from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

_SEVERITY_LEVEL = {
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_ORDER_NOTIFY_KEYS = {
    "L3.EXEC.ORDER_SENT",
    "L3.EXEC.ORDER_FILLED",
    "L3.EXEC.TP_PLACED",
    "L3.EXEC.SL_PLACED",
    "L3.EXEC.ORDER_COMPLETE",
    "L3.EXEC.EMERGENCY_CLOSE",
}

_SECRET_KEYS = {
    "signature",
    "api_key",
    "api_secret",
    "secret",
    "database_url",
    "webhook",
    "webhook_url",
}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = _scrub(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(x) for x in value]
    return value


@dataclass(frozen=True)
class SystemEvent:
    event_key: str
    layer: str
    severity: str
    message: str
    event_id: str | None = None
    symbol: str | None = None
    combo: int | None = None
    order_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: str = ""

    def normalized(self) -> "SystemEvent":
        return SystemEvent(
            event_key=self.event_key,
            layer=self.layer,
            severity=self.severity.upper(),
            message=self.message,
            event_id=self.event_id,
            symbol=self.symbol,
            combo=self.combo,
            order_id=self.order_id,
            details=_scrub(self.details or {}),
            created_at=self.created_at or datetime.now(timezone.utc).isoformat(),
        )


class EventReporter:
    """Layer 4 audit + Discord reporter.

    emit() is non-blocking. Supabase writes and Discord HTTP calls happen on a
    daemon worker so observability can never delay strategy or execution.
    """

    def __init__(
        self,
        database_url: str,
        *,
        discord_enabled: bool = True,
        webhook_btc: str = "",
        webhook_eth: str = "",
        webhook_error: str = "",
    ):
        self.database_url = (database_url or "").strip()
        self.discord_enabled = bool(discord_enabled)
        self.webhook_btc = (webhook_btc or "").strip()
        self.webhook_eth = (webhook_eth or "").strip()
        self.webhook_error = (webhook_error or "").strip()
        self._queue: queue.Queue[SystemEvent | None] = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._dropped = 0
        self._processed = 0
        self._last_error = ""
        self._last_discord: dict[str, float] = {}
        self._thread = threading.Thread(
            target=self._worker,
            name="layer4-events",
            daemon=True,
        )
        self._thread.start()

    def emit(
        self,
        event_key: str,
        severity: str = "INFO",
        message: str = "",
        *,
        event_id: str | None = None,
        symbol: str | None = None,
        combo: int | None = None,
        order_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        layer = str(event_key).split(".", 1)[0].upper()
        severity = str(severity).upper()
        if layer not in {"L1", "L2", "L3", "L4"}:
            layer = "L4"
        if severity not in _SEVERITY_LEVEL:
            severity = "ERROR"
        event = SystemEvent(
            event_key=str(event_key),
            layer=layer,
            severity=severity,
            message=str(message),
            event_id=event_id,
            symbol=symbol,
            combo=int(combo) if combo is not None else None,
            order_id=str(order_id) if order_id else None,
            details=details or {},
        ).normalized()
        log.log(
            _SEVERITY_LEVEL[severity],
            "EVENT key=%s layer=%s event_id=%s symbol=%s combo=%s order_id=%s message=%s",
            event.event_key,
            event.layer,
            event.event_id,
            event.symbol,
            event.combo,
            event.order_id,
            event.message,
        )
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._dropped += 1
            self._last_error = "event queue full"
            log.error("EVENT key=L4.QUEUE.DROP dropped=%s source_key=%s", self._dropped, event.event_key)

    def status(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "processed": self._processed,
            "dropped": self._dropped,
            "last_error": self._last_error,
            "discord_enabled": self.discord_enabled,
            "discord_btc_configured": bool(self.webhook_btc),
            "discord_eth_configured": bool(self.webhook_eth),
            "discord_error_configured": bool(self.webhook_error),
        }

    def stop(self) -> None:
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            self._stop.set()
        self._thread.join(timeout=2.0)
        self._stop.set()

    def _worker(self) -> None:
        while True:
            event = self._queue.get()
            if event is None:
                break
            try:
                self._persist(event)
                self._notify(event)
                self._processed += 1
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("EVENT key=L4.REPORTER.WORKER_FAIL source_key=%s error=%s", event.event_key, exc)

    def _persist(self, event: SystemEvent) -> None:
        if not self.database_url:
            return
        try:
            import psycopg
            payload = json.dumps(event.details or {}, ensure_ascii=False, default=str)
            with psycopg.connect(self.database_url) as con:
                with con.cursor() as cur:
                    cur.execute(
                        """
                        insert into public.system_events(
                            created_at,event_key,layer,severity,event_id,symbol,
                            combo,order_id,message,details
                        ) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                        """,
                        (
                            event.created_at,
                            event.event_key,
                            event.layer,
                            event.severity,
                            event.event_id,
                            event.symbol,
                            event.combo,
                            event.order_id,
                            event.message,
                            payload,
                        ),
                    )
                con.commit()
        except Exception as exc:
            self._last_error = f"audit write failed: {type(exc).__name__}: {exc}"
            log.exception("EVENT key=L4.AUDIT.WRITE_FAIL source_key=%s error=%s", event.event_key, exc)

    def _notify(self, event: SystemEvent) -> None:
        if not self.discord_enabled:
            return

        url = ""
        if event.severity in {"WARNING", "ERROR", "CRITICAL"}:
            url = self.webhook_error
        elif event.event_key in _ORDER_NOTIFY_KEYS:
            if event.symbol == "BTC-USDT":
                url = self.webhook_btc
            elif event.symbol == "ETH-USDT":
                url = self.webhook_eth

        if not url:
            return

        dedupe_key = f"{event.event_key}|{event.symbol}|{event.event_id}|{event.message}"
        now = time.monotonic()
        last = self._last_discord.get(dedupe_key, 0.0)
        if now - last < 120.0:
            return

        icon = {
            "INFO": "✅",
            "WARNING": "⚠️",
            "ERROR": "❌",
            "CRITICAL": "🚨",
        }[event.severity]
        lines = [
            f"{icon} **{event.event_key}**",
            f"Severity: {event.severity}",
        ]
        if event.symbol:
            lines.append(f"Symbol: {event.symbol}")
        if event.combo is not None:
            lines.append(f"Combo: C{event.combo}")
        if event.event_id:
            lines.append(f"Event: {event.event_id}")
        if event.order_id:
            lines.append(f"Order: {event.order_id}")
        if event.message:
            lines.append(f"Message: {event.message}")
        if event.details:
            compact = json.dumps(event.details, ensure_ascii=False, default=str, separators=(",", ":"))
            lines.append(f"Details: {compact[:900]}")
        content = "\n".join(lines)[:1900]

        try:
            response = requests.post(url, json={"content": content}, timeout=8)
            if response.status_code != 204:
                raise RuntimeError(f"Discord HTTP {response.status_code}: {response.text[:200]}")
            self._last_discord[dedupe_key] = now
        except Exception as exc:
            self._last_error = f"discord send failed: {type(exc).__name__}: {exc}"
            log.exception("EVENT key=L4.DISCORD.SEND_FAIL source_key=%s error=%s", event.event_key, exc)
            fail = SystemEvent(
                event_key="L4.DISCORD.SEND_FAIL",
                layer="L4",
                severity="ERROR",
                message=str(exc),
                event_id=event.event_id,
                symbol=event.symbol,
                combo=event.combo,
                order_id=event.order_id,
                details={"source_event_key": event.event_key},
            ).normalized()
            self._persist(fail)
