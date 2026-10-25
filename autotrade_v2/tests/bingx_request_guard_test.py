"""Offline BingX circuit-breaker regression tests; never open a network socket."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import httpx

from app.config import Settings
from app.control.bingx_guard import BingXRequestGuard
from app.control.errors import BingXApiError
from app.execution.executor import LegacyStyleBingXClient


def available(guard):
    with guard.request_slot():
        pass


def blocked(guard):
    try:
        with guard.request_slot():
            raise AssertionError("guard improperly admitted a restricted request")
    except BingXApiError as exc:
        assert exc.code=="CIRCUIT_BREAKER",exc


def fake_response(status,body,headers=None):
    request=httpx.Request("GET","https://example.invalid/openApi/swap/v2/user/positions")
    return httpx.Response(status_code=status,content=body.encode(),headers=headers or {},request=request)


def main():
    now=1_700_000_000_000
    with patch("app.control.bingx_guard.time.time",return_value=now/1000):
        for code in ("109425","109400","109421"):
            guard=BingXRequestGuard(min_interval_sec=0)
            assert guard.api_error("test",code,"business validation error","/orders",{}).code==code
            assert guard.status()["blocked"] is False,code
            available(guard)

        for code in ("109403","109415","109429","HTTP_418"):
            guard=BingXRequestGuard(min_interval_sec=0)
            guard.api_error("test",code,"risk restricted","/orders",{})
            assert guard._blocked_until_ms==now+16*60_000,code
            assert guard.status()["reason_code"]==code
            blocked(guard)

        for code in ("100410","HTTP_429"):
            guard=BingXRequestGuard(min_interval_sec=0)
            guard.api_error("test",code,"gateway throttle","/orders",{})
            assert guard._blocked_until_ms==now+60_000,code
            assert guard.status()["protection_calls_blocked"] is True
            blocked(guard)

        guard=BingXRequestGuard(min_interval_sec=0)
        guard.api_error("test","HTTP_429","throttle","/orders",{},retry_after="120")
        assert guard._blocked_until_ms==now+120_000
        guard.api_error("test","109425","unsupported","/orders",{})
        assert guard._blocked_until_ms==now+120_000

        guard2=BingXRequestGuard(min_interval_sec=0)
        guard2.api_error("test","109429","retry after time: 1700000030000","/orders",{})
        assert guard2._blocked_until_ms==now+16*60_000
        guard3=BingXRequestGuard(min_interval_sec=0)
        guard3.api_error("test","HTTP_429","throttle","/orders",{},
                         retry_after="Wed, 15 Nov 2023 00:15:00 GMT")
        assert guard3._blocked_until_ms==1700007300000

    with patch("app.control.bingx_guard.time.time",return_value=(now+120_001)/1000):
        assert guard.status()["blocked"] is False
        available(guard)

    # Reproduce Cloudflare HTML non-JSON 429/418. The guard must see the HTTP
    # status before attempting response.json(), then prevent ANY new trade call.
    settings=replace(Settings(),bingx_api_key="dummy",bingx_api_secret="dummy")
    for code,expected in ((429,60_000),(418,16*60_000)):
        guard=BingXRequestGuard(min_interval_sec=0)
        client=LegacyStyleBingXClient(settings,request_guard=guard)
        calls=[]
        response=fake_response(code,"<html>rate limited</html>")
        client.http.get=lambda *a,**kw: calls.append(("GET",a,kw)) or response
        client.http.post=lambda *a,**kw: calls.append(("POST",a,kw)) or response
        with patch("app.control.bingx_guard.time.time",return_value=now/1000):
            try:
                client.query_balance()
                raise AssertionError("non-JSON restriction must raise")
            except BingXApiError as exc:
                assert exc.code==f"HTTP_{code}",exc
            assert guard.status()["remaining_ms"]==expected
            try:
                client.place_market_entry("BTC-USDT","BUY",0.001,50,"test-id")
                raise AssertionError("new entry must be blocked by the guard")
            except BingXApiError as exc:
                assert exc.code=="CIRCUIT_BREAKER"
            try:
                client.close_market("BTC-USDT","BUY",0.001,50,"close-id",position_id="123")
                raise AssertionError("must not bypass real BingX restriction")
            except BingXApiError as exc:
                assert exc.code=="CIRCUIT_BREAKER"
            assert len(calls)==1,"no network retries or close bypass"
        client.http.close()

    # The exchange can return a business code (HTTP 200). 109425 must NOT
    # impose a 16m guard ban on unrelated protection requests.
    guard=BingXRequestGuard(min_interval_sec=0)
    client=LegacyStyleBingXClient(settings,request_guard=guard)
    responses=[
        fake_response(200,'{"code":109425,"msg":"trading pair not supported"}'),
        fake_response(200,'{"code":0,"data":[]}'),
    ]
    client.http.get=lambda *a,**kw: responses.pop(0)
    try:
        client.query_balance()
        raise AssertionError("invalid symbol must produce BingXApiError")
    except BingXApiError as exc:
        assert exc.code=="109425"
    assert client.query_balance()["code"]==0
    assert not guard.status()["blocked"]
    client.http.close()

    print({
        "ok":True,
        "invalid_symbol_no_global_ban":True,
        "ordinary_rate_limit_60s":True,
        "real_bans_conservatively_blocked":True,
        "html_429_418_detected":True,
        "no_unsafe_bypass_or_duplicate_post":True,
        "retry_after_respected":True,
    })


if __name__=="__main__":
    main()
