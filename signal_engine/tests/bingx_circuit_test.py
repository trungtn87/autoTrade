from __future__ import annotations

import time

from app.bingx_market import BingXApiError, BingXMarketClient


def main():
    c = BingXMarketClient(api_key="x", api_secret="y")
    try:
        future = int(time.time() * 1000) + 120_000
        restored = c.set_blocked_until_ms(future)
        assert restored >= future
        assert c.cooldown_remaining_ms() > 0

        try:
            c._throttle()
            raise AssertionError("circuit breaker should block")
        except BingXApiError as exc:
            assert str(exc.code) == "CIRCUIT_BREAKER"
            assert int(exc.retry_at_ms or 0) >= future

        parsed = c._retry_at_from_message(
            "can retry after time: 1790118528144"
        )
        assert parsed == 1790118528144
        print("BINGX CIRCUIT TEST PASS")
    finally:
        c._client.close()


if __name__ == "__main__":
    main()
