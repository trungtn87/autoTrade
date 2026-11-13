"""Durable control-log writer for layers L1-L5 (not wired into call sites yet).

Logging must never block trading: database failures are swallowed, recorded in
``last_error`` and reported as id ``-1``. Only invalid arguments raise.
"""
from __future__ import annotations

import json
from typing import Any


LAYERS = frozenset({"L1", "L2", "L3", "L4", "L5"})
DISCORD_STATUSES = frozenset({"sent", "failed", "skipped"})
_SECRET_KEY_PARTS = (
    "secret", "token", "signature", "signed", "apikey", "api_key",
    "webhook", "password", "authorization",
)
_DISCORD_URL_PREFIX = "https://discord"
_REDACTED_URL = "[REDACTED_URL]"
_INSERT_SQL = (
    "INSERT INTO autotrade_analytics.control_logs "
    "(layer, symbol, event_id, order_id, step, error_code, message, details, discord_status) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'pending') RETURNING id"
)
_MARK_SQL = "UPDATE autotrade_analytics.control_logs SET discord_status=%s WHERE id=%s"


def _is_secret_key(key: Any) -> bool:
    name = str(key).lower()
    return any(part in name for part in _SECRET_KEY_PARTS)


def sanitize_details(value: Any) -> Any:
    """Recursively drop secret-like keys and redact Discord webhook URLs."""
    if isinstance(value, dict):
        return {k: sanitize_details(v) for k, v in value.items() if not _is_secret_key(k)}
    if isinstance(value, (list, tuple)):
        return [sanitize_details(v) for v in value]
    if isinstance(value, str) and _DISCORD_URL_PREFIX in value.lower():
        return _REDACTED_URL
    return value


class ControlLogWriter:
    def __init__(self, db: Any):
        self.db = db
        self.last_error: str | None = None

    def write(
        self,
        layer: str,
        message: str,
        *,
        symbol: str | None = None,
        event_id: str | None = None,
        order_id: str | None = None,
        step: str | None = None,
        error_code: str | None = None,
        details: Any = None,
    ) -> int:
        if layer not in LAYERS:
            raise ValueError(f"unknown control layer: {layer!r}")
        try:
            sanitized = sanitize_details(details if details is not None else {})
            params = (
                layer, symbol, event_id, order_id, step, error_code, message,
                json.dumps(sanitized, default=str),
            )
            return int(self.db.execute_returning(_INSERT_SQL, params))
        except Exception as exc:
            self.last_error = str(exc)
            return -1

    def mark_discord(self, log_id: int, status: str) -> None:
        if status not in DISCORD_STATUSES:
            raise ValueError(f"unknown discord status: {status!r}")
        if log_id is None or log_id < 0:
            return
        try:
            self.db.execute(_MARK_SQL, (status, log_id))
        except Exception as exc:
            self.last_error = str(exc)
