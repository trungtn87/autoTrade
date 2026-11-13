"""L5 adapter: turns L3 execution events into control logs and Discord alerts.

Rules: internal progress events are silent, only errors are logged and sent to
the ERROR channel, and a success announcement is sent only after the entry and
all protections are confirmed. Sends run through ``start_thread`` so the trading
thread never waits on Discord. Each send is one attempt with no retry, dedup,
cooldown or batching. ``handle`` never raises.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable

from .alert_v2 import route_for_symbol


TRADE_ORDER_KEYS = {"L3.EXEC.ORDER_COMPLETE"}
SILENT_TRADE_KEYS = {
    "L3.EXEC.ORDER_SENT",
    "L3.EXEC.ORDER_FILLED",
    "L3.EXEC.SL_PLACED",
    "L3.EXEC.TP_PLACED",
    "L3.EXEC.TRAILING_PLACED",
}
ERROR_KEYS = {
    "L3.EXEC.EXIT_CLEANUP_RISK",
    "L3.EXEC.FORCE_CLOSE_UNRESOLVED",
    "L3.EXEC.UNSAFE_OPEN_POSITION",
    "L3.EXEC.EXIT_CLEANUP_SCAN_FAIL",
    "L3.EXEC.EXIT_ORPHAN_CANCEL_REQUESTED",
}
# Keys that the ExecutionService forwards to the legacy event_cb. Empty for now;
# a future key can be added here to keep forwarding it unchanged.
LEGACY_FORWARDED_KEYS: set[str] = set()

ERROR_CHANNEL = "ERROR"
_URL_PATTERN = re.compile(r"https?://\S+")


def _safe(value: Any) -> str:
    return _URL_PATTERN.sub("[URL]", str(value))


def _field_lines(symbol, event_id, order_id, message) -> list[str]:
    lines = []
    if symbol:
        lines.append(f"Cặp: {_safe(symbol)}")
    if event_id:
        lines.append(f"Event ID: {_safe(event_id)}")
    if order_id:
        lines.append(f"Order ID: {_safe(order_id)}")
    lines.append(f"Nội dung: {_safe(message)}")
    return lines


def _error_text(key, symbol, event_id, order_id, message) -> str:
    return "\n".join(["LỖI " + _safe(key),
                      *_field_lines(symbol, event_id, order_id, message)])


def _success_text(symbol, event_id, order_id, message) -> str:
    return "\n".join(["ĐẶT LỆNH THÀNH CÔNG",
                      *_field_lines(symbol, event_id, order_id, message)])


class L5TradeAlerts:
    def __init__(self, writer: Any, router: Any, start_thread: Callable | None = None):
        self.writer = writer
        self.router = router
        self._start_thread = start_thread or _default_start_thread

    def handle(
        self,
        key: str,
        severity: str,
        message: str,
        *,
        symbol: str | None,
        event_id: str | None = None,
        order_id: str | None = None,
        details: dict | None = None,
    ) -> None:
        try:
            if key in SILENT_TRADE_KEYS:
                return
            is_error = (
                severity in {"CRITICAL", "ERROR"}
                or key.endswith("_FAIL")
                or key in ERROR_KEYS
            )
            if is_error:
                log_id = self._write_log(message, symbol, event_id, order_id, key, details)
                content = _error_text(key, symbol, event_id, order_id, message)
                self._start_thread(
                    self._deliver, (ERROR_CHANNEL, content, log_id))
                return
            if key in TRADE_ORDER_KEYS:
                channel = route_for_symbol(symbol)
                content = _success_text(symbol, event_id, order_id, message)
                self._start_thread(
                    self._deliver, (channel, content, -1))
                return
        except Exception:
            return

    def _write_log(self, message, symbol, event_id, order_id, key, details) -> int:
        try:
            log_id = self.writer.write(
                "L3", message, symbol=symbol, event_id=event_id,
                order_id=order_id, step=key, error_code=None, details=details)
            return int(log_id)
        except Exception:
            return -1

    def _deliver(self, channel: str, content: str, log_id: int) -> None:
        try:
            status = self.router.send(channel, content)
        except Exception:
            status = "failed"
        if log_id is not None and log_id >= 0:
            try:
                self.writer.mark_discord(log_id, status)
            except Exception:
                return


def _default_start_thread(target: Callable, args: tuple) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()
