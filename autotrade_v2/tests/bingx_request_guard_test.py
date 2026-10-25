from __future__ import annotations

from unittest.mock import patch

from app.control.bingx_guard import BingXRequestGuard
from app.control.errors import BingXApiError


def assert_available(guard: BingXRequestGuard) -> None:
    with guard.request_slot():
        pass


def assert_blocked(guard: BingXRequestGuard) -> None:
    try:
        with guard.request_slot():
            raise AssertionError("guard unexpectedly admitted a blocked request")
    except BingXApiError as exc:
        assert exc.code == "CIRCUIT_BREAKER", exc


def main() -> None:
    now_ms = 1_700_000_000_000
    with patch("app.control.bingx_guard.time.time", return_value=now_ms / 1000):
        # BingX 109425 is an unsupported-symbol validation error. It must not
        # block SL or emergency-close transport for an unrelated valid symbol.
        for code in ("109425",):
            guard = BingXRequestGuard(min_interval_sec=0)
            error = guard.api_error("test", code, "business error", "/order", {})
            assert error.code == code
            assert guard._blocked_until_ms == 0, code
            assert_available(guard)

        # A genuine HTTP/exchange ban, or undocumented 109415, is NOT safe to bypass.
        for code in ("HTTP_429", "109429", "109415"):
            guard = BingXRequestGuard(min_interval_sec=0)
            guard.api_error("test", code, "rate restricted", "/order", {})
            assert guard._blocked_until_ms == now_ms + 16 * 60_000
            assert_blocked(guard)

        # Gateway code 100410 is transient: use a bounded 60s backoff rather
        # than incorrectly applying the generic 16-minute ban.
        guard = BingXRequestGuard(min_interval_sec=0)
        guard.api_error("test", "100410", "rate limit exceeded", "/order", {})
        assert guard._blocked_until_ms == now_ms + 60_000
        assert_blocked(guard)

        # Honor a concrete retry timestamp when the exchange provides one.
        guard = BingXRequestGuard(min_interval_sec=0)
        guard.api_error(
            "test", "109429", "retry after time: 1700000030000", "/order", {}
        )
        assert guard._blocked_until_ms == now_ms + 90_000
        assert_blocked(guard)

        # An unsupported-symbol response cannot prolong a real active ban.
        guard.api_error("test", "109425", "not supported", "/order", {})
        assert guard._blocked_until_ms == now_ms + 90_000

    with patch("app.control.bingx_guard.time.time", return_value=(now_ms + 90_001) / 1000):
        assert_available(guard)

    print({
        "ok": True,
        "business_errors_do_not_open_breaker": True,
        "genuine_bans_remain_fail_closed": True,
        "gateway_transient_backoff": True,
        "retry_timestamp_honored": True,
    })


if __name__ == "__main__":
    main()
