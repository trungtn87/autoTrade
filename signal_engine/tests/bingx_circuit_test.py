from __future__ import annotations

import time

from app.bingx_market import BingXApiError, BingXMarketClient


class _ApiErrorResponse:
    status_code = 200

    def json(self):
        return {"code": 109425, "msg": "test invalid pair"}

    def raise_for_status(self):
        return None


class _ApiErrorClient:
    def get(self, *args, **kwargs):
        return _ApiErrorResponse()

    def close(self):
        return None


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

        events = []
        d = BingXMarketClient(
            api_key="x",
            api_secret="y",
            min_interval_sec=0,
            error_recorder=lambda **event: events.append(event),
        )
        d._client.close()
        d._client = _ApiErrorClient()
        try:
            d.klines("BTC-USDT", "15m", 1)
            raise AssertionError("109425 should raise BingXApiError")
        except BingXApiError as exc:
            assert str(exc.code) == "109425"
        finally:
            d._client.close()

        assert len(events) == 1
        assert events[0]["source"] == "market"
        assert events[0]["endpoint"] == "/openApi/swap/v3/quote/klines"
        assert events[0]["params"]["symbol"] == "BTC-USDT"
        assert str(events[0]["code"]) == "109425"
        print("BINGX CIRCUIT TEST PASS")
    finally:
        c._client.close()


if __name__ == "__main__":
    main()
