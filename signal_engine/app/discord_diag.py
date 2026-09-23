from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import httpx

from .config import Settings, resolved_discord_log_webhook


class DiscordLogHandler(logging.Handler):
    """Forward WARNING+ engine logs to Discord with duplicate suppression."""

    def __init__(self, webhook_url: str, level: int = logging.WARNING):
        super().__init__(level=level)
        self.webhook_url = webhook_url
        self.client = httpx.Client(timeout=8.0)
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}
        self.duplicate_window_sec = 120.0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name or ""
            if not (
                name == "autotrade"
                or name.startswith("app.")
                or name.startswith("signal_engine.")
            ):
                return

            msg = self.format(record)[:1700]
            key = f"{record.levelname}|{name}|{record.getMessage()[:400]}"
            now = time.monotonic()

            with self._lock:
                last = self._last_sent.get(key, 0.0)
                if now - last < self.duplicate_window_sec:
                    return
                self._last_sent[key] = now

            content = (
                f"⚠️ **Render Log {record.levelname}**\n"
                f"{name}\n"
                f"{msg}"
            )
            self.client.post(self.webhook_url, json={"content": content})
        except Exception:
            return


def install_discord_log_handler(settings: Settings) -> dict:
    url = resolved_discord_log_webhook(settings)
    if not settings.discord_log_enabled:
        return {"ok": True, "installed": False, "reason": "disabled"}
    if not url:
        return {"ok": False, "installed": False, "reason": "no_webhook"}

    level = getattr(logging, settings.discord_log_level.upper(), logging.WARNING)
    handler = DiscordLogHandler(url, level=level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    )
    logging.getLogger().addHandler(handler)
    return {
        "ok": True,
        "installed": True,
        "level": settings.discord_log_level.upper(),
    }


def send_discord_startup_test(settings: Settings) -> dict:
    """Send one diagnostic message. Never calls BingX or order webhooks."""
    if not settings.discord_startup_test:
        return {"ok": True, "sent": False, "reason": "disabled"}

    url = resolved_discord_log_webhook(settings)
    if not url:
        return {"ok": False, "sent": False, "reason": "no_webhook"}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    content = (
        "✅ **Signal Engine Discord test**\n"
        f"Time: {now}\n"
        f"Mode: {'DRY_RUN' if settings.dry_run else 'LIVE'}\n"
        "Market data: 15m-only incremental\n"
        "Offline self-test: PASS\n"
        "This message does not place an order."
    )

    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(url, json={"content": content})
        return {
            "ok": r.status_code in (200, 204),
            "sent": True,
            "status_code": r.status_code,
            "body": r.text[:300],
        }
    except Exception as exc:
        return {
            "ok": False,
            "sent": False,
            "error": str(exc),
        }


def send_discord_scan_summary(settings: Settings, result: dict) -> dict:
    """Send one compact diagnostic summary after each scheduled 15m scan."""
    if not settings.discord_periodic_log_enabled:
        return {"ok": True, "sent": False, "reason": "disabled"}

    url = resolved_discord_log_webhook(settings)
    if not url:
        return {"ok": False, "sent": False, "reason": "no_webhook"}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    status = str(result.get("status", "unknown"))
    icon = "✅" if status == "ok" else "⚠️"
    mode = "DRY_RUN" if settings.dry_run else "LIVE"

    lines = [
        f"{icon} **15m Scan Report**",
        f"Time: {now}",
        f"Mode: {mode}",
        f"Status: {status}",
    ]

    symbols = result.get("symbols") or {}
    for symbol in settings.symbols:
        if symbol not in symbols:
            lines.append(f"{symbol}: SKIPPED - not scanned")
            continue

        item = symbols.get(symbol) or {}
        if "error" in item:
            err = str(item.get("error", ""))
            if len(err) > 350:
                err = err[:347] + "..."
            lines.append(f"{symbol}: ERROR - {err}")
            continue

        sigs = item.get("signals") or []
        lines.append(
            f"{symbol}: 15m={item.get('cached_15m', '?')} "
            f"signals={len(sigs)}"
        )

    if result.get("stopped_early"):
        lines.append(f"Stopped: {result.get('stop_reason')}")

    lines.append(f"Elapsed: {result.get('elapsed_sec', '?')}s")
    content = "\n".join(lines)[:1900]

    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(url, json={"content": content})
        return {
            "ok": r.status_code in (200, 204),
            "sent": True,
            "status_code": r.status_code,
        }
    except Exception as exc:
        return {
            "ok": False,
            "sent": False,
            "error": str(exc),
        }


_SCAN_ALERT_LOCK = threading.Lock()
_SCAN_ALERT_STATE = {"active": False, "signature": "", "last_sent": 0.0}
_SCAN_ALERT_REPEAT_SEC = 6 * 60 * 60


def build_discord_scan_alert(settings: Settings, result: dict) -> tuple[str, str]:
    """Return (content, stable_signature). Empty content means scan health is OK."""
    status = str(result.get("status", "unknown"))
    lines: list[str] = []
    signature_parts: list[str] = []

    if status != "ok":
        lines.append(f"Engine status: {status}")
        signature_parts.append(f"status:{status}")

    bingx_diag = result.get("bingx_api_error_diag_15m") or {}
    if isinstance(bingx_diag, dict) and bingx_diag:
        by_source = bingx_diag.get("109425_by_source") or {}
        local_109425 = int(bingx_diag.get("109425_count") or 0)
        lines.append(
            "Local BingX 109425 / 15m: "
            f"{local_109425} "
            f"(market={int(by_source.get('market') or 0)}, "
            f"executor={int(by_source.get('executor') or 0)})"
        )
        signature_parts.append(
            f"bingx109425:{local_109425}:"
            f"{int(by_source.get('market') or 0)}:"
            f"{int(by_source.get('executor') or 0)}"
        )

    symbols = result.get("symbols") or {}
    for symbol in settings.symbols:
        item = symbols.get(symbol)
        if not isinstance(item, dict):
            lines.append(f"{symbol}: not scanned")
            signature_parts.append(f"{symbol}:not_scanned")
            continue

        if item.get("error"):
            err = str(item.get("error"))[:350]
            lines.append(f"{symbol}: ERROR - {err}")
            signature_parts.append(f"{symbol}:error:{err[:100]}")
            continue

        validation = item.get("data_validation") or {}
        if validation and validation.get("ok") is False:
            lines.append(f"{symbol}: data validation FAILED")
            signature_parts.append(f"{symbol}:validation")

        readiness = item.get("combo_readiness") or {}
        skipped = readiness.get("skipped") or []
        if skipped:
            count = item.get("cached_15m", "?")
            ready = readiness.get("ready") or []
            lines.append(
                f"{symbol}: NOT READY - 15m={count}, ready={ready}, skipped={skipped}"
            )
            signature_parts.append(
                f"{symbol}:not_ready:{','.join(str(x) for x in skipped)}"
            )

    if result.get("stopped_early"):
        reason = str(result.get("stop_reason") or "stopped early")[:350]
        lines.append(f"Stopped early: {reason}")
        signature_parts.append(f"stopped:{reason[:100]}")

    if not lines:
        return "", ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    content = "\n".join([
        "🚨 **FINAL14 AUTOTRADE ALERT**",
        f"Time: {now}",
        *lines,
        "No trade decision should be trusted for skipped/not-ready combos until this alert clears.",
    ])[:1900]
    return content, "|".join(signature_parts)


def send_discord_scan_alert(settings: Settings, result: dict) -> dict:
    """Send faults/readiness problems immediately; stay quiet on healthy no-signal scans."""
    if not settings.discord_enabled:
        return {"ok": True, "sent": False, "reason": "discord_disabled"}

    url = resolved_discord_log_webhook(settings)
    if not url:
        return {"ok": False, "sent": False, "reason": "no_error_webhook"}

    content, signature = build_discord_scan_alert(settings, result)
    now_mono = time.monotonic()

    if not content:
        with _SCAN_ALERT_LOCK:
            was_active = bool(_SCAN_ALERT_STATE["active"])
            _SCAN_ALERT_STATE["active"] = False
            _SCAN_ALERT_STATE["signature"] = ""
            _SCAN_ALERT_STATE["last_sent"] = 0.0
        if not was_active:
            return {"ok": True, "sent": False, "reason": "healthy"}

        recovery = (
            "✅ **FINAL14 AUTOTRADE RECOVERED**\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            "All configured symbols completed the scan with no readiness/data errors."
        )
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(url, json={"content": recovery})
            return {
                "ok": r.status_code in (200, 204),
                "sent": True,
                "status_code": r.status_code,
                "reason": "recovery",
            }
        except Exception as exc:
            return {"ok": False, "sent": False, "error": str(exc), "reason": "recovery_failed"}

    with _SCAN_ALERT_LOCK:
        same = (
            bool(_SCAN_ALERT_STATE["active"])
            and _SCAN_ALERT_STATE["signature"] == signature
            and now_mono - float(_SCAN_ALERT_STATE["last_sent"]) < _SCAN_ALERT_REPEAT_SEC
        )
    if same:
        return {"ok": True, "sent": False, "reason": "deduped"}

    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(url, json={"content": content})
        ok = r.status_code in (200, 204)
        if ok:
            with _SCAN_ALERT_LOCK:
                _SCAN_ALERT_STATE["active"] = True
                _SCAN_ALERT_STATE["signature"] = signature
                _SCAN_ALERT_STATE["last_sent"] = now_mono
        return {
            "ok": ok,
            "sent": True,
            "status_code": r.status_code,
            "reason": "fault",
        }
    except Exception as exc:
        return {"ok": False, "sent": False, "error": str(exc), "reason": "fault_send_failed"}
