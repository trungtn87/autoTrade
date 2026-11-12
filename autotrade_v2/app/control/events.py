from __future__ import annotations

import json
import logging
import queue
import re
import smtplib
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

import requests

from .discord_batch import DiscordTradeOutbox, _format_vn_time
from .discord_format import incident_view, risk_codes, safe_line, trade_status

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
    "L3.EXEC.FORCE_CLOSE_CONFIRMED",
    # Notify when a previously stuck execution history is safely retired.
    "L3.EXEC.EXCHANGE_FLAT_RECONCILED",
    "L3.EXEC.PREFLIGHT_RECOVERED",
    # Data freshness is trading-critical even when the process is otherwise alive.
    # Route exactly these WARNING events to the error webhook.
    "L1.DATA.CANDLE_STALE",
    "L1.DATA.STARTUP_RECOVERY",
}

# Recurring incidents can generate a new candle/signal ID every 15m.
# Rate-limit notification delivery only; keep all audit events.
_DISCORD_ALERT_COOLDOWN_SEC = {
    "L1.DATA.CANDLE_STALE": 3600.0,
    "L1.DATA.RECOVERY_FAIL": 1800.0,
    "L3.EXEC.MERGED_RECONCILE_REQUIRED": 3600.0,
    "L3.EXEC.EXIT_CLEANUP_RISK": 3600.0,
    "L3.EXEC.PREFLIGHT_BLOCK": 3600.0,
    "L3.EXEC.PREFLIGHT_STILL_BLOCKED": 1800.0,
}

_AI_REPORT_KEYS = {"L4.AI.PRE_REVIEW", "L4.AI.POST_REVIEW"}

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


_DISCORD_REQUEST_HEADERS = {
    "User-Agent": "DiscordBot (https://github.com/trungtn87/autoTrade, 2.0)",
    "Accept": "application/json",
}

_DISCORD_DIAG_HEADERS = (
    "Retry-After",
    "X-RateLimit-Scope",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "X-RateLimit-Reset-After",
    "X-RateLimit-Bucket",
    "X-RateLimit-Global",
    "CF-Ray",
    "Server",
    "Via",
    "Content-Type",
)


def _discord_response_diag(response: Any) -> dict[str, Any]:
    """Safe Discord/Cloudflare diagnostics without logging webhook secrets."""
    headers = {
        key: response.headers.get(key)
        for key in _DISCORD_DIAG_HEADERS
        if response.headers.get(key) is not None
    }
    diag: dict[str, Any] = {
        "status": int(response.status_code),
        "headers": headers,
    }
    raw = response.text or ""
    if response.status_code not in {200, 204}:
        body = raw.replace("\r", " ").replace("\n", " ").strip()
        diag["body_prefix"] = body[:240]
        title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.I | re.S)
        cf_error = re.search(r"(?:Error|error)\s*(?:code\s*)?(\d{3,4})", raw)
        if title:
            diag["html_title"] = re.sub(r"<[^>]+>", "", title.group(1)).strip()[:160]
        if h1:
            diag["html_h1"] = re.sub(r"<[^>]+>", "", h1.group(1)).strip()[:160]
        if cf_error:
            diag["cf_error_code"] = cf_error.group(1)
        lowered = raw.lower()
        if "you are being rate limited" in lowered:
            diag["cf_marker"] = "you_are_being_rate_limited"
        elif "access denied" in lowered:
            diag["cf_marker"] = "access_denied"
    return diag


def _order_discord_content(event: "SystemEvent") -> str:
    """Readable result even without the durable trade notification outbox."""
    details = {"event_key": event.event_key, **(event.details or {})}
    title, status, action = trade_status(details)
    side = str(details.get("side") or "").upper()
    lines = [
        title,
        " · ".join(x for x in (event.symbol or "", _combo_label(event.combo), side) if x),
        f"Entry dự kiến: {_fmt_trade_number(details.get('entry'))}",
        f"TP dự kiến: {_fmt_trade_number(details.get('tp'))}",
        f"SL dự kiến: {_fmt_trade_number(details.get('sl'))}",
        f"Trạng thái: {status}",
        f"Cần làm: {action}",
    ]
    order_time = _format_vn_time(details.get("order_time") or event.created_at)
    if order_time:
        lines.append(f"Thời gian (VN): {order_time}")
    lines.append(f"Mã tra cứu: {safe_line(event.event_key, 65)}"
                 + (f" / {safe_line(details['stage'], 50)}" if details.get("stage") else ""))
    return "\n".join(lines)[:1900]



def _ai_one_line(value: Any, limit: int = 320) -> str:
    """Human-readable model output; never let it ping Discord members."""
    if value is None:
        return "-"
    value = " ".join(str(value).split()).replace("@", "＠").replace(chr(96), "'")
    return value[:limit] + ("…" if len(value) > limit else "")


def _ai_discord_content(event: "SystemEvent") -> str:
    """Plain Vietnamese observer reports. Never imply AI controls execution."""
    details = event.details or {}
    combo = (_combo_label(event.combo) if event.combo is not None
             else _ai_one_line(details.get("combo"), 30))
    side = {"BUY": "LONG", "SELL": "SHORT"}.get(
        str(details.get("side") or "").upper(), _ai_one_line(details.get("side"), 20))
    header = f"{_ai_one_line(event.symbol, 35)} · {combo} · {side}"
    if event.event_key == "L4.AI.PRE_REVIEW":
        decision = str(details.get("ai_decision") or "UNSURE").upper()
        verdict = {
            "ALLOW": "✅ GIẢ ĐỊNH: NÊN VÀO",
            "BLOCK": "⛔ GIẢ ĐỊNH: KHÔNG NÊN VÀO",
            "UNSURE": "❔ GIẢ ĐỊNH: CHƯA ĐỦ CƠ SỞ",
        }.get(decision, "❔ CHƯA CÓ NHẬN ĐỊNH")
        news_text = ("Chưa có dữ liệu tin tức kiểm chứng"
            if details.get("news_status") == "UNAVAILABLE"
            else _ai_one_line(details.get("news_risk"), 60))
        lines = [
            "🤖 **GEMINI · ĐÁNH GIÁ TRƯỚC ENTRY**", header, verdict,
            "🔒 AutoTrade vẫn giao dịch theo 20FINAL; AI không chặn lệnh.",
            "SMC: " + {
                "ALIGNED": "ĐỒNG THUẬN",
                "MIXED": "HỖN HỢP",
                "CONFLICT": "XUNG ĐỘT",
                "UNKNOWN": "CHƯA XÁC ĐỊNH",
            }.get(str(details.get("smc_alignment") or "").upper(), "CHƯA XÁC ĐỊNH"),
            f"Tin tức: {news_text}",
            f"Entry: {_fmt_trade_number(details.get('entry'))} | "
            f"TP: {_fmt_trade_number(details.get('tp'))} | "
            f"SL: {_fmt_trade_number(details.get('sl'))}",
            f"Bằng chứng: {_ai_one_line(details.get('reasoning_verified'), 390)}",
            f"Ý kiến AI: {_ai_one_line(details.get('reasoning_ai_opinion'), 390)}",
        ]
        if details.get("independent_smc_summary"):
            lines.append("SMC Binance độc lập: " +
                         _ai_one_line(details["independent_smc_summary"], 175))
        if details.get("news_source_count"):
            lines.append("Tin ngoài: Đã tra cứu sau tín hiệu; chưa xác minh thời điểm công bố.")
            sources = details.get("news_sources") or []
            if sources and isinstance(sources[0], dict):
                uri = str(sources[0].get("url") or "")
                if uri.startswith("https://"):
                    lines.append("Nguồn tra cứu: " + _ai_one_line(uri, 170))
    else:
        outcome = str(details.get("outcome") or "").upper()
        label = {"WIN": "✅ THẮNG", "LOSS": "❌ THUA",
                 "BREAKEVEN": "➖ HÒA VỐN"}.get(outcome, "⚪ PNL CHƯA XÁC MINH")
        comparison = {
            "CORRECT": "Đúng (theo đánh giá AI)",
            "INCORRECT": "Sai (theo đánh giá AI)",
            "INCONCLUSIVE": "Chưa đủ bằng chứng",
        }.get(str(details.get("pre_trade_assessment") or ""), "Chưa kết luận")
        lessons = details.get("lessons")
        lesson = " | ".join(str(x) for x in lessons[:2]) if isinstance(lessons, list) else ""
        earlier = {
            "ALLOW": "NÊN VÀO", "BLOCK": "KHÔNG NÊN VÀO",
            "UNSURE": "CHƯA RÕ",
        }.get(str(details.get("pre_decision") or ""), "KHÔNG CÓ")
        lines = [
            "🤖 **GEMINI · PHÂN TÍCH SAU EXIT**", header,
            f"Nhận định trước Entry: {earlier}",
            f"Kết quả đối soát: {label}",
            f"PnL: {_fmt_trade_number(details.get('pnl'))} USDT"
            if details.get("pnl") is not None else "PnL: chưa có số liệu xác minh",
            f"ROI: {_fmt_trade_number(details.get('roi'))}%"
            if details.get("roi") is not None else "ROI: chưa xác minh",
            f"Đánh giá lại nhận định trước: {comparison}",
            f"Phân tích: {_ai_one_line(details.get('explanation'), 480)}",
            f"Bài học: {_ai_one_line(lesson, 360)}",
            "🔒 Chỉ gửi khi ledger xác nhận combo đã CLOSED.",
        ]
        ext = details.get("external_exit_window") or {}
        if isinstance(ext, dict) and ext.get("status") == "AVAILABLE":
            lines.append(
                f"Giá Binance tham khảo: thấp {_fmt_trade_number(ext.get('lowest'))}"
                f", cao {_fmt_trade_number(ext.get('highest'))} (nến 1H; không phải giá khớp BingX)."
            )
    lines.append(f"Mã tín hiệu: {_ai_one_line(event.event_id, 120)}")
    if details.get("revision") is not None:
        lines.append(f"Revision: {_ai_one_line(details['revision'], 12)}")
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
        relay_url: str = "",
        relay_token: str = "",
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
        self.relay_url = (relay_url or "").strip()
        self.relay_token = str(relay_token or "")
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
        self._trade_outbox = (
            DiscordTradeOutbox(self.database_url)
            if self.discord_enabled and self.database_url else None
        )
        self._next_batch_poll = 0.0
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

    def _flush_trade_batches(self) -> None:
        if self._trade_outbox is None:
            return
        now = time.monotonic()
        if now < self._next_batch_poll:
            return
        # Avoid a new Supabase connection on each 1-second worker wake-up.
        self._next_batch_poll = now + 5.0
        try:
            self._trade_outbox.flush_one(
                {"BTC-USDT": self.webhook_btc, "ETH-USDT": self.webhook_eth},
                _DISCORD_REQUEST_HEADERS,
            )
        except Exception as exc:
            self._last_error = f"discord batch worker failed: {type(exc).__name__}: {exc}"
            log.exception("EVENT key=L4.DISCORD.BATCH_WORKER_FAIL error=%s", exc)

    def _worker(self) -> None:
        while True:
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._flush_trade_batches()
                continue
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
            finally:
                self._flush_trade_batches()

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

        dedupe_key = self._discord_dedupe_key(event)
        now = time.monotonic()
        last = self._last_email.get(dedupe_key)
        cooldown = _DISCORD_ALERT_COOLDOWN_SEC.get(event.event_key, 900.0)
        if last is not None and now - last < cooldown:
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

    @staticmethod
    def _discord_dedupe_key(event: SystemEvent) -> str:
        # A failed startup is one system incident, not a fresh failed trade
        # per BTC/ETH combo. Applies to both email and Discord throttling.
        if event.event_key in {"L3.EXEC.PREFLIGHT_BLOCK",
                               "L3.EXEC.PREFLIGHT_STILL_BLOCKED"}:
            return event.event_key
        if event.event_key in {"L3.EXEC.MERGED_RECONCILE_REQUIRED",
                               "L3.EXEC.EXIT_CLEANUP_RISK"}:
            # Same incident can mention different signal IDs on every scan.
            # New risk categories still bypass cooldown immediately.
            raw_risks = (event.details or {}).get("risks") or []
            risks = set(risk_codes(raw_risks, event.message))
            # Unknown *new* risk codes must also bypass cooldown. We can
            # fingerprint their stable suffix without exposing it to Discord.
            sources = list(raw_risks) if isinstance(raw_risks, (list, tuple)) else [raw_risks]
            sources.append(event.message)
            for source in sources:
                for segment in str(source or "").split("|"):
                    code = segment.rsplit(":", 1)[-1].strip()
                    if re.fullmatch(r"[A-Z][A-Z0-9_]{4,}", code):
                        risks.add(code)
            if not risks:
                # Never coalesce unrelated CRITICAL events just because the
                # formatter doesn't yet know their technical categories.
                return f"{event.event_key}|{event.symbol}|{safe_line(event.message, 240)}"
            return (f"{event.event_key}|{event.symbol}|"
                    + "|".join(sorted(risks)))
        if event.event_key == "L1.DATA.CANDLE_STALE":
            return f"{event.event_key}|{event.symbol}"
        if event.event_key == "L1.DATA.RECOVERY_FAIL":
            return f"{event.event_key}|{event.symbol}|{event.message}"
        return (
            f"{event.event_key}|{event.symbol}|{event.event_id}|"
            f"{event.message}"
        )

    @staticmethod
    def _system_discord_content(event: SystemEvent) -> str:
        details = event.details or {}
        title, explanation, action, risks = incident_view(event.event_key, event.message, details)
        lines = [
            f"**{title}**",
            " · ".join(x for x in (event.symbol or "", _combo_label(event.combo)
                      if event.combo is not None else "") if x) or "AutoTrade V2",
            f"Vấn đề: {explanation}",
        ]
        if risks:
            lines.append("Chưa xác minh:")
            lines.extend(risks)
        affected = details.get("affected_combos")
        if isinstance(affected, list) and affected:
            lines.append("Combo liên quan: " + ", ".join(safe_line(x, 20) for x in affected[:12]))
        if details.get("remaining_exposure") is not None:
            lines.append("Khối lượng còn mở: " + safe_line(details["remaining_exposure"], 35))
        closes = details.get("closes") or []
        if isinstance(closes, list) and closes:
            first = closes[0]
            if isinstance(first, dict):
                if first.get("requested_qty") is not None:
                    lines.append("Yêu cầu đóng: " + safe_line(first["requested_qty"], 30))
                if first.get("filled_qty") is not None:
                    lines.append("Đã khớp đóng: " + safe_line(first["filled_qty"], 30))
        lines.append(f"Cần làm: {action}")
        event_time = _format_vn_time(event.created_at)
        if event_time:
            lines.append(f"Giờ VN: {event_time}")
        lines.append("Mã tra cứu: " + safe_line(event.event_key, 70))
        return "\n".join(lines)[:1900]

    def _notify(self, event: SystemEvent) -> None:
        # Email is an independent Layer-4 path. A broken Discord webhook must
        # not suppress severe system alerts.
        self._send_email_alert(event)

        if not self.discord_enabled:
            return

        # Execution emits several intermediate events. Only the final result
        # is staged, with Entry/TP/SL already available, and nearby results
        # sharing the candle boundary are sent in a single Discord message.
        if event.event_key in _ORDER_RESULT_KEYS and self._trade_outbox is not None:
            self._trade_outbox.stage(event)
            return

        url = ""
        if event.event_key in (_ORDER_RESULT_KEYS | _AI_REPORT_KEYS):
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

        # Let the immediate REST gap recovery run before notifying. A failed
        # recovery has its own independent ERROR event and still alerts.
        if (
            event.event_key == "L1.DATA.CANDLE_STALE"
            and str((event.details or {}).get("attempt") or "") == "1"
        ):
            return

        dedupe_key = self._discord_dedupe_key(event)
        now = time.monotonic()
        last = self._last_discord.get(dedupe_key)
        cooldown = _DISCORD_ALERT_COOLDOWN_SEC.get(event.event_key, 120.0)
        if last is not None and now - last < cooldown:
            return

        if event.event_key in _ORDER_RESULT_KEYS:
            content = _order_discord_content(event)
        elif event.event_key in _AI_REPORT_KEYS:
            content = _ai_discord_content(event)
        else:
            content = self._system_discord_content(event)

        try:
            response = None
            for attempt in range(3):
                response = requests.post(
                    url,
                    json=({"content": content, "allowed_mentions": {"parse": []}}
                          if event.event_key in _AI_REPORT_KEYS else {"content": content}),
                    headers=_DISCORD_REQUEST_HEADERS,
                    timeout=8,
                )
                if response.status_code == 204:
                    log.info(
                        "EVENT key=L4.DISCORD.SEND_OK source_key=%s symbol=%s status=204 attempt=%s",
                        event.event_key,
                        event.symbol,
                        attempt + 1,
                    )
                    break

                diag = _discord_response_diag(response)
                log.warning(
                    "EVENT key=L4.DISCORD.HTTP_DIAG source_key=%s symbol=%s attempt=%s diag=%s",
                    event.event_key,
                    event.symbol,
                    attempt + 1,
                    json.dumps(diag, ensure_ascii=True, separators=(",", ":")),
                )
                if response.status_code != 429:
                    raise RuntimeError(
                        f"Discord HTTP {response.status_code}: {response.text[:200]}"
                    )

                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 1.0
                except (TypeError, ValueError):
                    delay = 1.0

                # Cloudflare Error 1015 is an edge/WAF block, not the normal
                # Discord webhook bucket limit. Retrying every few seconds
                # cannot succeed while that block is active and Cloudflare
                # explicitly warns that repeated attempts may prolong it.
                raw_lower = (response.text or "").lower()
                cloudflare_1015 = (
                    "error 1015" in raw_lower
                    or "you are being rate limited" in raw_lower
                    or "you are being rate-limited by the website owner" in raw_lower
                )
                if cloudflare_1015 or delay > 30.0 or attempt >= 2:
                    raise RuntimeError(
                        f"Discord Cloudflare/rate-limit HTTP 429 retry_after={delay:.0f}s"
                    )

                time.sleep(max(0.25, delay))
            else:
                raise RuntimeError("Discord retry loop exhausted")

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
