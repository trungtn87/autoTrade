"""Reuse the existing BingX signed transport (V2 ``LegacyStyleBingXClient``).

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

from autotrade_v2.app.control.bingx_guard import BingXRequestGuard
from autotrade_v2.app.control.errors import BingXApiError
from autotrade_v2.app.execution.executor import LegacyStyleBingXClient

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

    def place_order(self, params: dict) -> dict:
        return self._signed_request("POST", self.ORDER_PATH, dict(params))

    fmt_price = staticmethod(LegacyStyleBingXClient._fmt_price)
    fmt_qty = staticmethod(LegacyStyleBingXClient._fmt_qty)
