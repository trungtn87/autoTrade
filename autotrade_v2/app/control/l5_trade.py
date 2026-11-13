"""L5 adapter: turns L3 execution events into control logs and Discord alerts.

Rules:
- Any event with severity CRITICAL or ERROR, or whose key is an error key, is
  logged to control_logs and sent once to the ERROR channel. Every occurrence
  is logged and sent: no dedup, cooldown or repeated-failure suppression.
- Internal progress events are silent. A success announcement goes to BTC or
  ETH only after the entry and all protections are confirmed, and includes the
  real fill and protection facts.
- Log writes and Discord sends run in ``start_thread`` so the trading thread
  never waits on the database or on Discord. Each send is one attempt.
- ``handle`` never raises.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable

from .alert_v2 import format_signal, route_for_symbol


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
    "L3.EXEC.FORCE_CLOSE_CONFIRMED",
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


def _fact_lines(facts: dict | None) -> list[str]:
    if not facts:
        return []
    lines = []
    if facts.get("side") is not None:
        lines.append(f"Chiều: {_safe(facts['side'])}")
    if facts.get("entry") is not None:
        lines.append(f"Giá tín hiệu: entry {_safe(facts['entry'])}, TP {_safe(facts.get('tp'))}, SL {_safe(facts.get('sl'))}")
    if facts.get("avg_price") is not None:
        lines.append(f"Giá khớp thực tế: {_safe(facts['avg_price'])}")
    if facts.get("executed_qty") is not None:
        lines.append(f"Khối lượng khớp: {_safe(facts['executed_qty'])}")
    protection = facts.get("protection")
    if isinstance(protection, dict) and protection:
        parts = [f"{_safe(k)}={_safe(v)}" for k, v in protection.items()
                 if v is not None and not isinstance(v, (dict, list))]
        if parts:
            lines.append("Bảo vệ BingX xác nhận: " + ", ".join(parts))
    if facts.get("client_order_id") is not None:
        lines.append(f"Client Order ID: {_safe(facts['client_order_id'])}")
    if facts.get("stage") is not None:
        lines.append(f"Giai đoạn: {_safe(facts['stage'])}")
    if facts.get("error") is not None:
        lines.append(f"Lỗi API hoặc nội bộ: {_safe(facts['error'])}")
    return lines


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


def _error_text(key, symbol, event_id, order_id, message, facts) -> str:
    return "\n".join(["LỖI " + _safe(key),
                      *_field_lines(symbol, event_id, order_id, message),
                      *_fact_lines(facts)])


def _success_text(symbol, event_id, order_id, message, facts) -> str:
    return "\n".join(["ĐẶT LỆNH THÀNH CÔNG",
                      *_field_lines(symbol, event_id, order_id, message),
                      *_fact_lines(facts)])


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
        facts: dict | None = None,
    ) -> None:
        try:
            is_error = (
                severity in {"CRITICAL", "ERROR"}
                or key.endswith("_FAIL")
                or key in ERROR_KEYS
            )
            if is_error:
                content = _error_text(key, symbol, event_id, order_id, message, facts)
                self._start_thread(
                    self._log_and_deliver,
                    (ERROR_CHANNEL, content, key, message, symbol, event_id, order_id, details))
                return
            if key in SILENT_TRADE_KEYS:
                return
            if key in TRADE_ORDER_KEYS:
                channel = route_for_symbol(symbol)
                content = _success_text(symbol, event_id, order_id, message, facts)
                self._start_thread(self._deliver, (channel, content))
                return
        except Exception:
            return

    def signal(self, intent: Any) -> None:
        """L2 signal announcement: BTC or ETH channel, one attempt, never raises."""
        try:
            channel = route_for_symbol(intent.symbol)
            content = format_signal(
                intent.symbol, intent.combo, intent.side, intent.close_time,
                intent.entry, intent.tp, intent.sl, intent.event_id,
            )
            self._start_thread(self._deliver, (channel, content))
        except Exception:
            return

    def _log_and_deliver(self, channel, content, key, message, symbol, event_id, order_id, details):
        log_id = -1
        try:
            log_id = int(self.writer.write(
                "L3", message, symbol=symbol, event_id=event_id,
                order_id=order_id, step=key, error_code=None, details=details))
        except Exception:
            log_id = -1
        status = self._send(channel, content)
        if log_id >= 0:
            try:
                self.writer.mark_discord(log_id, status)
            except Exception:
                pass

    def _deliver(self, channel: str, content: str) -> None:
        self._send(channel, content)

    def _send(self, channel: str, content: str) -> str:
        try:
            return str(self.router.send(channel, content))
        except Exception:
            return "failed"


def _default_start_thread(target: Callable, args: tuple) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()
