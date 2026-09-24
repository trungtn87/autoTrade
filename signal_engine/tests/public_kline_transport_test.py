from __future__ import annotations

from app.bingx_market import BingXMarketClient


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "code": 0,
            "data": [{
                "openTime": 1_000_000,
                "open": "1",
                "high": "2",
                "low": "0.5",
                "close": "1.5",
                "volume": "10",
                "closeTime": 1_899_999,
            }],
        }

    def raise_for_status(self):
        return None


class FakeHTTPClient:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append({
            "url": url,
            "params": dict(params or {}),
            "headers": dict(headers or {}),
        })
        return FakeResponse()


def run() -> None:
    market = BingXMarketClient(
        api_key="should-not-be-sent",
        api_secret="should-not-be-used",
        min_interval_sec=0.0,
    )
    fake = FakeHTTPClient()
    market._client = fake

    df = market.klines("BTC-USDT", "15m", 1000)

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"].endswith("/openApi/swap/v3/quote/klines")
    assert call["params"] == {
        "symbol": "BTC-USDT",
        "interval": "15m",
        "limit": 1000,
    }
    assert "timestamp" not in call["params"]
    assert "recvWindow" not in call["params"]
    assert "signature" not in call["params"]
    assert "X-BX-APIKEY" not in call["headers"]
    assert call["headers"] == {"X-SOURCE-KEY": "BX-AI-SKILL"}
    assert len(df) == 1

    print({
        "ok": True,
        "transport": "public",
        "api_key_sent": False,
        "signed": False,
        "source_key": call["headers"]["X-SOURCE-KEY"],
    })


if __name__ == "__main__":
    run()
