from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager

from .errors import BingXApiError

log = logging.getLogger(__name__)

# Official BingX semantics (BingX-API/api-ai-skills error-codes reference):
# 109425 = unsupported symbol (business validation error, NOT rate limiting).
# 100410 = transient gateway rate limit; 429 = HTTP rate limit.
# 109403/109429 = temporary restriction after excessive/invalid requests.
# 418 = HTTP IP ban. 109415 is undocumented; retain the previous conservative
# restriction until its meaning can be confirmed by the exchange.
TRANSIENT_CODES = {"100410", "HTTP_429"}
RESTRICTED_CODES = {"109403", "109415", "109429", "HTTP_418"}


class BingXRequestGuard:
    """Shared, fail-closed throttling for every authenticated BingX request.

    Real exchange bans affect protection/close requests too. Do not bypass a
    genuine 429/418: no API client can guarantee a stop will be accepted while
    its key/IP is restricted. Entry calls also fail closed during cooldown.
    """

    def __init__(self, min_interval_sec: float = 1.10, cooldown_guard_ms: int = 60_000):
        self.min_interval_sec = float(min_interval_sec)
        self.cooldown_guard_ms = int(cooldown_guard_ms)
        self._lock = threading.RLock()
        self._last_call = 0.0
        self._blocked_until_ms = 0
        self._blocked_code: str | None = None

    @contextmanager
    def request_slot(self):
        with self._lock:
            now_ms = int(time.time() * 1000)
            if now_ms < self._blocked_until_ms:
                remain = int((self._blocked_until_ms - now_ms + 999) / 1000)
                raise BingXApiError(
                    "CIRCUIT_BREAKER",
                    f"local circuit breaker active ({self._blocked_code}); retry after about {remain}s",
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

    def status(self) -> dict:
        """Redacted, read-only health snapshot, never keys or signed URLs."""
        with self._lock:
            now_ms = int(time.time() * 1000)
            left = max(0, self._blocked_until_ms - now_ms)
            return {
                "blocked": left > 0,
                "remaining_ms": left,
                "reason_code": self._blocked_code if left > 0 else None,
                "protection_calls_blocked": left > 0,
            }

    def api_error(
        self, source: str, code, message: str, path: str, params: dict,
        retry_after: str | None = None,
    ) -> BingXApiError:
        code_str = str(code)
        with self._lock:
            now_ms = int(time.time() * 1000)
            if code_str in RESTRICTED_CODES:
                from_message = self._retry_at_from_message(message)
                until = max(
                    now_ms + 15 * 60_000,
                    from_message or 0,
                    self._retry_at_from_header(retry_after, now_ms) or 0,
                ) + self.cooldown_guard_ms
            elif code_str in TRANSIENT_CODES:
                # Minimum 60s backoff prevents repeated failures and an IP ban.
                until = max(
                    now_ms + self.cooldown_guard_ms,
                    self._retry_at_from_message(message) or 0,
                    self._retry_at_from_header(retry_after, now_ms) or 0,
                )
            else:
                until = 0
            if until > self._blocked_until_ms:
                self._blocked_until_ms = until
                self._blocked_code = code_str
            until_ms = self._blocked_until_ms
        log.warning(
            "BINGX_API_ERROR source=%s code=%s path=%s blocked_until_ms=%s msg=%s",
            source, code_str, path, until_ms, message,
        )
        return BingXApiError(code, message, path, params)

    @staticmethod
    def _retry_at_from_message(message: str) -> int | None:
        m = re.search(r"retry after time:\s*(\d{13})", message or "", re.I)
        return int(m.group(1)) if m else None

    @staticmethod
    def _retry_at_from_header(value: str | None, now_ms: int) -> int | None:
        if not value:
            return None
        # Standard HTTP Retry-After supports a number of seconds or an HTTP date.
        try:
            delay = float(value.strip())
            if 0 <= delay <= 86_400:
                return now_ms + int(delay * 1000)
        except (ValueError, OverflowError, AttributeError):
            pass
        try:
            from datetime import datetime, timezone
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result = int(parsed.timestamp() * 1000)
            return result if result > now_ms else None
        except (ValueError, OverflowError, TypeError):
            return None
