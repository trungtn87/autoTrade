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
