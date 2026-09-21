from __future__ import annotations

import json
from types import SimpleNamespace

from app.final14_config import FINAL14_CASES, DISABLED_CASES, enabled_combos, get_case
from app.final14_executor import Final14Executor
from app.strategy import Signal


class FakeFinal14Executor(Final14Executor):
    def __init__(self):
        settings=SimpleNamespace(
            leverage=100,
            order_margin_usdt=1.0,
            bingx_api_key="x",
            bingx_api_secret="y",
            bingx_base_url="https://example.invalid",
            discord_enabled=False,
            discord_on_dry_run=False,
            dry_run=False,
            discord_webhook_btc="",
            discord_webhook_eth="",
            discord_webhook_error="",
        )
        super().__init__(settings)
        self.order_params=None
        self.position_calls=0

    def _assert_hedge_mode(self): return None
    def _assert_separate_isolated(self,symbol): return None
    def _set_leverage(self,symbol,side,leverage): return {"code":0}
    def _contract_rules(self,symbol):
        return {
            "quantity_precision":4,
            "price_precision":2,
            "min_qty":0.0001,
            "min_usdt":1.0,
            "max_long_leverage":125,
            "max_short_leverage":125,
            "status":1,
            "api_state_open":"true",
            "api_state_close":"true",
        }
    def _positions(self,symbol):
        self.position_calls+=1
        if self.position_calls==1:
            return []
        return [{
            "positionId":"p1",
            "positionSide":"LONG",
            "positionAmt":"1.0",
            "avgPrice":"100.0",
        }]
    def _signed_trade_request(self,method,path,params):
        if path.endswith("/order") and method=="POST":
            self.order_params=dict(params)
            return {"code":0,"data":{"orderID":"o1","status":"FILLED"}}
        raise AssertionError((method,path,params))
    def _order_detail(self,symbol,order_id):
        return {"code":0,"data":{"orderID":"o1","status":"FILLED","executedQty":"1.0","avgPrice":"100.0"}}


def test_config():
    assert sum(len(x) for x in FINAL14_CASES.values())==14
    assert sum(len(x) for x in DISABLED_CASES.values())==8
    assert enabled_combos("BTC-USDT")== (1,2,4,6,7,9,10,11)
    assert enabled_combos("ETH-USDT")== (1,3,4,6,7,10)
    assert get_case("BTC-USDT",3) is None
    assert get_case("ETH-USDT",9) is None


def test_attached_hard_tp_sl():
    ex=FakeFinal14Executor()
    sig=Signal(
        symbol="BTC-USDT",combo=1,side="BUY",timeframe="1h",
        close_time=123456789,entry=100.0,tp=102.0,sl=99.2,smc_dir=1,
    )
    result=ex._execute_direct_bingx(sig)
    assert result["ok"] is True
    assert result["position_id"]=="p1"
    assert result["tp"]==102.0
    assert result["sl"]==99.2
    assert result["protection_mode"]=="attached_hard_tp_sl"

    p=ex.order_params
    assert p["type"]=="MARKET"
    assert "activationPrice" not in p and "priceRate" not in p
    sl=json.loads(p["stopLoss"])
    tp=json.loads(p["takeProfit"])
    assert sl=={
        "type":"STOP_MARKET","stopPrice":99.2,
        "workingType":"CONTRACT_PRICE","stopGuaranteed":False,
    }
    assert tp=={
        "type":"TAKE_PROFIT_MARKET","stopPrice":102.0,
        "workingType":"CONTRACT_PRICE","stopGuaranteed":False,
    }


if __name__=="__main__":
    test_config()
    test_attached_hard_tp_sl()
    print("FINAL14 unit tests PASS")
