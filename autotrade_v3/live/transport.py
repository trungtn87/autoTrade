"""BingX signed transport (``LegacyStyleBingXClient``, copied into ``autotrade_v3.live.exchange``).

Two small V3 adaptations, no change to V2 code:
* ``SpacingGuard`` keeps the 1.1 s request spacing but has NO local circuit
  breaker: an error returned for one signal never becomes a lock on other
  signals. BingX's own answer (e.g. HTTP 429) is reported as that request's error.
* ``place_order`` sends a protection/close order with an explicit
  ``clientOrderId`` so a lost POST response can be verified by L4 by ID.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

from .exchange.bingx_guard import BingXRequestGuard
from .exchange.errors import BingXApiError
from .exchange.executor import LegacyStyleBingXClient

log = logging.getLogger("autotrade_v3.transport")

__all__ = ["BingXApiError", "SpacingGuard", "V3BingXClient"]


class SpacingGuard(BingXRequestGuard):
    def __init__(self, min_interval_sec: float = 1.10):
        super().__init__(min_interval_sec=min_interval_sec)
        self._lock = threading.RLock()

    @contextmanager
    def request_slot(self):
        with self._lock:
            wait = self.min_interval_sec - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                yield
            finally:
                self._last_call = time.monotonic()

    def api_error(self, source, code, message, path, params, retry_after=None) -> BingXApiError:
        log.warning("BINGX_API_ERROR source=%s code=%s path=%s retry_after=%s msg=%s",
                    source, code, path, retry_after, message)
        return BingXApiError(code, message, path, params)


class V3BingXClient(LegacyStyleBingXClient):
    def __init__(self, settings, request_guard=None):
        super().__init__(settings, request_guard=request_guard or SpacingGuard())
        self._clock_lock = threading.Lock()
        # Atomic tuple (server milliseconds, monotonic receive time); independent
        # of later wall-clock jumps. A failed refresh retains the last good sample.
        self._clock_sample = None

    SERVER_TIME_PATH = "/openApi/swap/v2/server/time"
    RECV_WINDOW_MS = 5000
    MAX_CLOCK_RTT_SEC = 2.0

    def _timestamp_ms(self) -> int:
        sample = self._clock_sample
        if sample is None:
            return super()._timestamp_ms()
        server_ms, received = sample
        return server_ms + int((time.monotonic() - received) * 1000)

    def refresh_server_time(self, *, after=None) -> bool:
        """Unsigned, rate-spaced time sample; no trading mutation or secret.
        
        Anchor the server time at receipt (conservative, slightly behind the
        actual clock). Reject slow or invalid samples; never replace a good
        sample with an error. Concurrent GET recovery may reuse a newer sample.
        """
        with self._clock_lock:
            sample = self._clock_sample
            if after is not None and sample is not None and sample[1] >= after:
                return True
            try:
                with self.guard.request_slot():
                    started = time.monotonic()
                    response = self.http.get(
                        f"{self.settings.bingx_base_url}{self.SERVER_TIME_PATH}",
                        timeout=self.MAX_CLOCK_RTT_SEC,
                    )
                    received = time.monotonic()
                    wall_received_ms = int(time.time() * 1000)
                    rtt = received - started
                    if response.status_code != 200 or rtt > self.MAX_CLOCK_RTT_SEC:
                        log.warning("BINGX_CLOCK_SYNC_FAILED status=%s rtt_ms=%.1f",
                                    response.status_code, rtt * 1000)
                        return False
                    payload = response.json()
                    if not isinstance(payload, dict) or payload.get("code") not in (0, "0"):
                        log.warning("BINGX_CLOCK_SYNC_FAILED invalid exchange response")
                        return False
                    raw = (payload.get("data") or {}).get("serverTime")
                    if not isinstance(raw, (int, str)) or isinstance(raw, bool):
                        raise ValueError("invalid serverTime")
                    if not str(raw).isdigit() or len(str(raw)) != 13:
                        raise ValueError("invalid serverTime milliseconds")
                    server_ms = int(raw)
                    self._clock_sample = (server_ms, received)
                    offset_ms = server_ms - wall_received_ms + rtt * 500
                    log.info("BINGX_CLOCK_SYNC_OK offset_ms=%.1f rtt_ms=%.1f",
                             offset_ms, rtt * 1000)
                    return True
            except Exception as exc:
                log.warning("BINGX_CLOCK_SYNC_FAILED error_class=%s", type(exc).__name__)
                return False

    @staticmethod
    def _is_timestamp_error(exc: BingXApiError) -> bool:
        return exc.code == "100421" or (
            exc.code == "109400" and "timestamp" in exc.message.lower()
        )

    def _signed_request(self, method: str, path: str, params: dict) -> dict:
        # Keep the default maximum documented validity window, not a wider
        # replay window. Re-sign each attempt inside the existing rate guard.
        body = dict(params)
        body.setdefault("recvWindow", str(self.RECV_WINDOW_MS))
        started = time.monotonic()
        try:
            return super()._signed_request(method, path, body)
        except BingXApiError as exc:
            if method.upper() != "GET" or not self._is_timestamp_error(exc):
                raise
            if not self.refresh_server_time(after=started):
                log.warning("BINGX_TIMESTAMP_RETRY_SKIPPED path=%s", path)
                raise
            log.info("BINGX_TIMESTAMP_RETRY path=%s code=%s", path, exc.code)
            # Exactly one read retry. Never retry POST or DELETE here.
            result = super()._signed_request(method, path, body)
            log.info("BINGX_TIMESTAMP_RETRY_OK path=%s", path)
            return result

    def place_order(self, params: dict) -> dict:
        return self._signed_request("POST", self.ORDER_PATH, dict(params))

    def amend_order(self, symbol: str, *, order_id: str, quantity: str) -> dict:
        """BingX in-place quantity-only amendment (exact STOP_MARKET support unverified).

        This method never falls back to cancel/replace. Callers own proof and
        explicit opt-in; transport only signs the exact parameters.
        """
        from decimal import Decimal, InvalidOperation
        import re

        if not re.fullmatch(r"[1-9][0-9]*", str(order_id)):
            raise ValueError("invalid exact order ID")
        try:
            qty = Decimal(str(quantity))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("invalid amend quantity") from exc
        if not qty.is_finite() or qty <= 0:
            raise ValueError("amend quantity must be positive")
        return self._signed_request("POST", "/openApi/swap/v1/trade/amend",
                                    {"symbol": symbol, "orderId": str(order_id),
                                     "quantity": format(qty, "f")})

    fmt_price = staticmethod(LegacyStyleBingXClient._fmt_price)
    fmt_qty = staticmethod(LegacyStyleBingXClient._fmt_qty)
