from __future__ import annotations

import json
import logging
import queue
import smtplib
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

import requests

log = logging.getLogger(__name__)

_SEVERITY_LEVEL = {
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_EMAIL_ALERT_KEYS = {
    # Data/strategy failures that can prevent a valid trading decision.
    "L1.DATA.BOOTSTRAP_FAIL",
    "L1.DATA.PIPELINE_FAIL",
    "L1.DATA.RECOVERY_FAIL",
    "L2.FINAL14.CALC_FAIL",
    "L2.FINAL14.INSUFFICIENT_CANDLES",
    # Execution failures that can block an order or leave account state unsafe.
    "L3.EXEC.PREFLIGHT_FAIL",
    "L3.EXEC.PREFLIGHT_BLOCK",
    "L3.EXEC.ORDER_FAIL",
    "L3.EXEC.RECONCILE_REQUIRED",
    "L3.EXEC.EMERGENCY_CLOSE",
    "L3.EXEC.UNSAFE_OPEN_POSITION",
    "L3.EXEC.PROTECTION_RECONCILE_REQUIRED",
    "L3.EXEC.ORPHAN_PROTECTION_CLEANUP",
    "L3.EXEC.TP_FAILED_SL_ACTIVE",
    "L3.EXEC.RECOVERY_LOOP_FAIL",
    # Observability failures. Discord delivery failures are deliberately
    # audit-only because they do not affect trading.
    "L4.AUDIT.WRITE_FAIL",
    "L4.REPORTER.WORKER_FAIL",
}

_DISCORD_SYSTEM_WARNING_KEYS = {
    # Data freshness is trading-critical even when the process is otherwise alive.
    # Route exactly these WARNING events to the error webhook.
    "L1.DATA.CANDLE_STALE",
    "L1.DATA.STARTUP_RECOVERY",
}

_ORDER_RESULT_KEYS = {
    # Exactly one Discord summary per execution result. Intermediate execution
    # stages remain audit-only so one trade never bursts several webhook posts.
    "L3.EXEC.ORDER_COMPLETE",
    "L3.EXEC.ORDER_FAIL",
    "L3.EXEC.RECONCILE_REQUIRED",
    "L3.EXEC.RECONCILE_CLEARED",
    "L3.EXEC.ENTRY_NOT_FILLED",
    "L3.EXEC.EMERGENCY_CLOSE",
    "L3.EXEC.UNSAFE_OPEN_POSITION",
    "L3.EXEC.PROTECTION_EXIT_CONFIRMED",
    "L3.EXEC.PROTECTION_RECONCILE_REQUIRED",
    "L3.EXEC.ORPHAN_PROTECTION_CLEANUP",
    "L3.EXEC.TP_FAILED_SL_ACTIVE",
    "L3.EXEC.PREFLIGHT_BLOCK",
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


def _fmt_trade_number(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.8f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _combo_label(combo: Any) -> str:
    """Human-facing combo name while preserving internal IDs for audit/state."""
    if combo is None or combo == "":
        return "-"
    try:
        combo_id = int(combo)
    except (TypeError, ValueError):
        return str(combo)
    if 101 <= combo_id <= 106:
        return f"C{combo_id - 90}"
    if combo_id == 11:
        return "TIER"
    return f"C{combo_id}"


def _order_discord_content(event: "SystemEvent") -> str:
    """Compact human-facing order summary using Layer-2 intent prices."""
    details = event.details or {}
    side = str(details.get("side") or "").upper()
    symbol_side = " ".join(x for x in (event.symbol or "", side) if x)
    combo = _combo_label(event.combo)
    lines = [
        f"{'✅' if event.event_key == 'L3.EXEC.ORDER_COMPLETE' else '❌'} Đặt lệnh",
        symbol_side or "-",
        "",
        f"📊 Combo {combo}",
        f"Entry: {_fmt_trade_number(details.get('entry'))}",
        "",
        f"TP : {_fmt_trade_number(details.get('tp'))}",
        f"SL : {_fmt_trade_number(details.get('sl'))}",
        "",
    ]
    if event.event_key == "L3.EXEC.ORDER_COMPLETE":
        lines.append("✅ Đặt lệnh thành công")
    else:
        reason = str(details.get("error") or event.message or "Không rõ lý do")
        lines.append("❌ Đặt lệnh thất bại")
        lines.append(f"Lý do: {reason}")
    return "\n".join(lines)[:1900]


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
        email_enabled: bool = False,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        email_from: str = "",
        email_to: str = "",
        smtp_starttls: bool = True,
    ):
        self.database_url = (database_url or "").strip()
        self.discord_enabled = bool(discord_enabled)
        self.webhook_btc = (webhook_btc or "").strip()
        self.webhook_eth = (webhook_eth or "").strip()
        self.webhook_error = (webhook_error or "").strip()
        self.email_enabled = bool(email_enabled)
        self.smtp_host = (smtp_host or "").strip()
        self.smtp_port = int(smtp_port or 587)
        self.smtp_user = (smtp_user or "").strip()
        self.smtp_password = str(smtp_password or "")
        self.email_from = (email_from or self.smtp_user).strip()
        self.email_to = (email_to or "").strip()
        self.smtp_starttls = bool(smtp_starttls)
        self._queue: queue.Queue[SystemEvent | None] = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._dropped = 0
        self._processed = 0
        self._last_error = ""
        self._last_discord: dict[str, float] = {}
        self._last_email: dict[str, float] = {}
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
            "email_enabled": self.email_enabled,
            "email_configured": self._email_configured(),
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
                fail = SystemEvent(
                    event_key="L4.REPORTER.WORKER_FAIL",
                    layer="L4",
                    severity="CRITICAL",
                    message=f"{type(exc).__name__}: {exc}",
                    event_id=event.event_id,
                    symbol=event.symbol,
                    combo=event.combo,
                    order_id=event.order_id,
                    details={"source_event_key": event.event_key},
                ).normalized()
                self._send_email_alert(fail)

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
            fail = SystemEvent(
                event_key="L4.AUDIT.WRITE_FAIL",
                layer="L4",
                severity="ERROR",
                message=f"{type(exc).__name__}: {exc}",
                event_id=event.event_id,
                symbol=event.symbol,
                combo=event.combo,
                order_id=event.order_id,
                details={"source_event_key": event.event_key},
            ).normalized()
            self._send_email_alert(fail)

    def _email_configured(self) -> bool:
        return bool(
            self.email_enabled
            and self.smtp_host
            and self.smtp_port
            and self.smtp_user
            and self.smtp_password
            and self.email_from
            and self.email_to
        )

    @staticmethod
    def _should_email(event: SystemEvent) -> bool:
        if event.severity == "CRITICAL":
            return True
        if event.event_key not in _EMAIL_ALERT_KEYS:
            return False
        # First failed candle recovery can be transient. Alert only when the
        # configured second recovery attempt has also failed.
        if event.event_key == "L1.DATA.RECOVERY_FAIL":
            try:
                return int((event.details or {}).get("attempt") or 0) >= 2
            except (TypeError, ValueError):
                return True
        return True

    def _send_email_alert(self, event: SystemEvent) -> None:
        if not self._email_configured() or not self._should_email(event):
            return

        dedupe_key = f"{event.event_key}|{event.symbol}|{event.event_id}|{event.message}"
        now = time.monotonic()
        last = self._last_email.get(dedupe_key)
        if last is not None and now - last < 900.0:
            return

        subject_parts = [f"[AutoTrade {event.severity}]", event.event_key]
        if event.symbol:
            subject_parts.append(event.symbol)
        msg = EmailMessage()
        msg["Subject"] = " ".join(subject_parts)
        msg["From"] = self.email_from
        msg["To"] = self.email_to

        lines = [
            "AutoTrade V2 - cảnh báo hệ thống",
            "",
            f"Severity: {event.severity}",
            f"Layer: {event.layer}",
            f"Event: {event.event_key}",
            f"Time UTC: {event.created_at}",
        ]
        if event.symbol:
            lines.append(f"Symbol: {event.symbol}")
        if event.combo is not None:
            lines.append(f"Combo: {_combo_label(event.combo)}")
        if event.order_id:
            lines.append(f"Order: {event.order_id}")
        if event.message:
            lines.append(f"Message: {event.message}")
        if event.details:
            details = json.dumps(event.details, ensure_ascii=False, default=str, indent=2)
            lines.extend(["", "Details:", details[:5000]])
        msg.set_content("\n".join(lines))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as smtp:
                smtp.ehlo()
                if self.smtp_starttls:
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(self.smtp_user, self.smtp_password)
                smtp.send_message(msg)
            self._last_email[dedupe_key] = now
            log.info(
                "EVENT key=L4.EMAIL.SENT source_key=%s severity=%s symbol=%s",
                event.event_key,
                event.severity,
                event.symbol,
            )
        except Exception as exc:
            self._last_error = f"email send failed: {type(exc).__name__}: {exc}"
            log.exception(
                "EVENT key=L4.EMAIL.SEND_FAIL source_key=%s error=%s",
                event.event_key,
                exc,
            )

    def _notify(self, event: SystemEvent) -> None:
        # Email is an independent Layer-4 path. A broken Discord webhook must
        # not suppress severe system alerts.
        self._send_email_alert(event)

        if not self.discord_enabled:
            return

        url = ""
        if event.event_key in _ORDER_RESULT_KEYS:
            if event.symbol == "BTC-USDT":
                url = self.webhook_btc
            elif event.symbol == "ETH-USDT":
                url = self.webhook_eth
        elif (
            event.event_key in _DISCORD_SYSTEM_WARNING_KEYS
            or event.severity in {"ERROR", "CRITICAL"}
        ):
            url = self.webhook_error

        if not url:
            return

        cycle_key = ""
        if event.event_key == "L1.DATA.CANDLE_STALE":
            cycle_key = str((event.details or {}).get("expected_open_time") or "")
        dedupe_key = (
            f"{event.event_key}|{event.symbol}|{event.event_id}|"
            f"{event.message}|{cycle_key}"
        )
        now = time.monotonic()
        last = self._last_discord.get(dedupe_key)
        if last is not None and now - last < 120.0:
            return

        if event.event_key in _ORDER_RESULT_KEYS:
            content = _order_discord_content(event)
        else:
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
                severity="WARNING",
                message=str(exc),
                event_id=event.event_id,
                symbol=event.symbol,
                combo=event.combo,
                order_id=event.order_id,
                details={
                    "source_event_key": event.event_key,
                    "source_message": event.message,
                },
            ).normalized()
            self._persist(fail)
