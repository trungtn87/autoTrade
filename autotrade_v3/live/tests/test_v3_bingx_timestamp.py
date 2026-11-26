"""Offline regression tests: no real BingX traffic or trading credentials."""
import hashlib
import hmac
from types import SimpleNamespace

import httpx
import pytest

from autotrade_v3.live.transport import BingXApiError, SpacingGuard, V3BingXClient

SERVER_MS = 1795673000000
CLOCK = V3BingXClient.SERVER_TIME_PATH
ORDERS = V3BingXClient.OPEN_ORDERS_PATH
ORDER = V3BingXClient.ORDER_PATH


def client_with_responses(responses):
    calls = []
    queue = iter(responses)

    def handler(request):
        calls.append(request)
        status, payload = next(queue)
        return httpx.Response(status, json=payload)

    settings = SimpleNamespace(bingx_base_url="https://mock.invalid",
                               bingx_api_key="fake-key", bingx_api_secret="fake-secret")
    client = V3BingXClient(settings, request_guard=SpacingGuard(0))
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    return client, calls


def reply(code=0, message="", data=None):
    return 200, {"code": code, "msg": message, "data": data or {}}


def signed(request):
    params = dict(request.url.params)
    signature = params.pop("signature")
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    assert signature == hmac.new(b"fake-secret", query.encode(), hashlib.sha256).hexdigest()
    assert params["recvWindow"] == "5000"
    return params


@pytest.mark.parametrize("code,message", [(109400, "timestamp is invalid"),
                                          (100421, "timestamp mismatch")])
def test_get_resynchronizes_and_resigns(code, message):
    client, calls = client_with_responses([
        reply(code, message), reply(data={"serverTime": SERVER_MS}), reply(data={"orders": []})
    ])
    try:
        assert client._signed_request("GET", ORDERS, {"symbol": "ETH-USDT"})["code"] == 0
        assert [r.url.path for r in calls] == [ORDERS, CLOCK, ORDERS]
        assert not calls[1].url.params
        assert "X-BX-APIKEY" not in calls[1].headers
        signed(calls[0])
        retry = signed(calls[2])
        assert SERVER_MS <= int(retry["timestamp"]) < SERVER_MS + 2000
        assert retry["symbol"] == "ETH-USDT"
    finally:
        client.http.close()


@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_never_retry_mutations(method):
    client, calls = client_with_responses([reply(109400, "timestamp is invalid")])
    try:
        with pytest.raises(BingXApiError):
            client._signed_request(method, ORDER, {"symbol": "ETH-USDT"})
        assert len(calls) == 1
        assert calls[0].method == method
    finally:
        client.http.close()


@pytest.mark.parametrize("code,message", [(109400, "invalid quantity"), (100410, "rate limit"),
                                          (109421, "order not exist")])
def test_other_get_errors_do_not_retry(code, message):
    client, calls = client_with_responses([reply(code, message)])
    try:
        with pytest.raises(BingXApiError):
            client._signed_request("GET", ORDERS, {"symbol": "ETH-USDT"})
        assert len(calls) == 1
    finally:
        client.http.close()


def test_failed_sync_preserves_original_error_and_does_not_retry():
    client, calls = client_with_responses([
        reply(109400, "timestamp is invalid"), (429, {})
    ])
    try:
        with pytest.raises(BingXApiError) as caught:
            client._signed_request("GET", ORDERS, {})
        assert caught.value.code == "109400"
        assert len(calls) == 2
    finally:
        client.http.close()


def test_retry_is_bounded_to_one():
    client, calls = client_with_responses([
        reply(109400, "timestamp is invalid"), reply(data={"serverTime": SERVER_MS}),
        reply(109400, "timestamp is invalid")
    ])
    try:
        with pytest.raises(BingXApiError):
            client._signed_request("GET", ORDERS, {})
        assert len(calls) == 3
    finally:
        client.http.close()


def test_clock_sample_survives_wall_clock_jump_and_invalid_refresh(monkeypatch):
    client, calls = client_with_responses([
        reply(data={"serverTime": SERVER_MS}), reply(data={"serverTime": 123}),
        reply()
    ])
    try:
        assert client.refresh_server_time()
        monkeypatch.setattr("autotrade_v3.live.transport.time.time", lambda: 1.0)
        assert not client.refresh_server_time()
        client._signed_request("POST", ORDER, {"symbol": "ETH-USDT"})
        assert SERVER_MS <= int(signed(calls[-1])["timestamp"]) < SERVER_MS + 2000
        assert len(calls) == 3
    finally:
        client.http.close()
