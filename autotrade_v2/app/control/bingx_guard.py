from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager

from .errors import BingXApiError

log = logging.getLogger(__name__)
BLOCK_CODES = {"109415", "109425", "109429", "HTTP_429"}


class BingXRequestGuard:
    """Layer-4 throttle/circuit breaker shared by authenticated execution calls."""

    def __init__(self, min_interval_sec: float = 1.10, cooldown_guard_ms: int = 60_000):
        self.min_interval_sec = float(min_interval_sec)
        self.cooldown_guard_ms = int(cooldown_guard_ms)
        self._lock = threading.RLock()
        self._last_call = 0.0
        self._blocked_until_ms = 0

    @contextmanager
    def request_slot(self):
        with self._lock:
            now_ms = int(time.time() * 1000)
            if now_ms < self._blocked_until_ms:
                remain = int((self._blocked_until_ms - now_ms + 999) / 1000)
                raise BingXApiError(
                    "CIRCUIT_BREAKER",
                    f"local circuit breaker active; retry after about {remain}s",
                    "local://circuit-breaker",
                    {},
                )
            wait = self.min_interval_sec - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                self._last_call = time.monotonic()

    def api_error(self, source: str, code, message: str, path: str, params: dict) -> BingXApiError:
        if str(code) in BLOCK_CODES:
            retry_at = self._retry_at_from_message(message)
            now_ms = int(time.time() * 1000)
            self._blocked_until_ms = max(
                self._blocked_until_ms,
                (retry_at or now_ms + 15 * 60_000) + self.cooldown_guard_ms,
            )
        log.warning(
            "BINGX_API_ERROR source=%s code=%s path=%s blocked_until_ms=%s msg=%s",
            source, code, path, self._blocked_until_ms, message,
        )
        return BingXApiError(code, message, path, params)

    @staticmethod
    def _retry_at_from_message(message: str) -> int | None:
        m = re.search(r"retry after time:\s*(\d{13})", message or "", re.I)
        return int(m.group(1)) if m else None
