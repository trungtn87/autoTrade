from __future__ import annotations

from dataclasses import replace

import app.execution.final14_executor as fe
from app.config import Settings
from app.contracts import TradeIntent
from app.execution.final14_executor import Final14Executor

fe.ENTRY_CONFIRM_SLEEP_SEC=0
fe.AMBIGUOUS_RECHECK_SLEEP_SEC=0


class FakeClient:
    def __init__(self, avg_price=100.0, entry_status="FILLED"):
        self.avg_price=avg_price
        self.entry_status=entry_status
        self.calls=[]
        self.by_client={}
        self.by_order={}
        self.fail_sl=False
        self.fail_tp=False
        self.fail_close=False
        self.matching={}

    @staticmethod
    def extract_order(payload):
        data=payload.get("data") or {}
        if isinstance(data,dict) and isinstance(data.get("order"),dict):
            return data["order"]
        return data if isinstance(data,dict) else {}

    def query_balance(self):
        return {"code":0,"data":[{"asset":"USDT"}]}

    def find_order_by_client_id(self,symbol,client_order_id):
        self.calls.append(("lookup",symbol,client_order_id))
        return self.by_client.get(client_order_id)

    def place_market_entry(self,symbol,side,qty,leverage,client_order_id=None):
        self.calls.append(("entry",symbol,side,qty,leverage,client_order_id))
        order={
            "orderId":"abc",
            "clientOrderId":client_order_id,
            "executedQty":"1" if self.entry_status in {"FILLED","PARTIALLY_FILLED"} else "0",
            "avgPrice":str(self.avg_price) if self.entry_status in {"FILLED","PARTIALLY_FILLED"} else "0",
            "status":self.entry_status,
        }
        payload={"code":0,"data":{"order":order}}
        self.by_client[client_order_id]=payload
        self.by_order["abc"]=payload
        return payload

    def get_order_detail(self,symbol,order_id=None,client_order_id=None):
        self.calls.append(("detail",symbol,order_id,client_order_id))
        if client_order_id:
            payload=self.by_client.get(client_order_id)
        else:
            payload=self.by_order.get(str(order_id))
        if payload is None:
            raise RuntimeError("order not found")
        return payload

    def cancel_order(self,symbol,order_id=None,client_order_id=None):
        self.calls.append(("cancel",symbol,order_id,client_order_id))
        payload=self.by_client.get(client_order_id)
        if payload:
            order=self.extract_order(payload)
            order["status"]="CANCELED"
        return {"code":0}

    def find_matching_protection(self,symbol,entry_side,qty,stop_price,order_type):
        self.calls.append(("find_protection",order_type,qty,stop_price))
        return self.matching.get(order_type)

    def place_stop_loss(self,symbol,entry_side,qty,sl):
        self.calls.append(("sl",symbol,entry_side,qty,sl))
        if self.fail_sl:
            raise RuntimeError("simulated SL failure")
        order={
            "orderId":"sl-1",
            "status":"NEW",
            "executedQty":"0",
            "avgPrice":"0",
        }
        payload={"code":0,"data":{"order":order}}
        self.by_order["sl-1"]=payload
        return payload

    def place_take_profit(self,symbol,entry_side,qty,tp):
        self.calls.append(("tp",symbol,entry_side,qty,tp))
        if self.fail_tp:
            raise RuntimeError("simulated TP failure")
        order={
            "orderId":"tp-1",
            "status":"NEW",
            "executedQty":"0",
            "avgPrice":"0",
        }
        payload={"code":0,"data":{"order":order}}
        self.by_order["tp-1"]=payload
        return payload

    def close_market(self,symbol,entry_side,qty,leverage,client_order_id=None):
        self.calls.append(("close",symbol,entry_side,qty,leverage,client_order_id))
        if self.fail_close:
            raise RuntimeError("simulated close failure")
        order={
            "orderId":"close-1",
            "clientOrderId":client_order_id,
            "status":"FILLED",
            "executedQty":str(qty),
            "avgPrice":str(self.avg_price),
        }
        payload={"code":0,"data":{"order":order}}
        self.by_client[client_order_id]=payload
        self.by_order["close-1"]=payload
        return payload


def intent():
    return TradeIntent(
        event_id="BTC-USDT|15m|C1|BUY|1",
        symbol="BTC-USDT",
        combo=1,
        side="BUY",
        timeframe="15m",
        close_time=1,
        entry=100.0,
        tp=102.0,
        sl=99.0,
        smc_dir=1,
    )


def main():
    settings=replace(
        Settings(),
        dry_run=False,
        execution_enabled=True,
        bingx_api_key="test",
        bingx_api_secret="test",
    )

    ex=Final14Executor(settings)
    good=FakeClient(avg_price=100.0)
    ex.client=good
    result=ex.execute(intent())
    assert result["ok"] is True,result
    assert result["protection_mode"]=="separate_hard_tp_sl"
    assert result["stage"]=="complete"
    assert result["client_order_id"].startswith("atv2e-")
    kinds=[x[0] for x in good.calls]
    assert kinds[:3]==["lookup","entry","lookup"],kinds
    assert "sl" in kinds and "tp" in kinds
    assert kinds.index("sl") < kinds.index("tp")
    assert kinds.count("detail")>=2

    bad=FakeClient(avg_price=103.0)
    ex.client=bad
    result2=ex.execute(intent())
    kinds2=[x[0] for x in bad.calls]
    assert result2["stage"]=="fill_outside_final14_range_closed",result2
    assert "close" in kinds2,kinds2

    sl_bad=FakeClient(avg_price=100.0)
    sl_bad.fail_sl=True
    ex.client=sl_bad
    result3=ex.execute(intent())
    kinds3=[x[0] for x in sl_bad.calls]
    assert result3["stage"]=="protection_sl_failed_closed",result3
    assert result3["emergency_closed"] is True
    assert "sl" in kinds3 and "close" in kinds3,kinds3

    tp_bad=FakeClient(avg_price=100.0)
    tp_bad.fail_tp=True
    ex.client=tp_bad
    result4=ex.execute(intent())
    kinds4=[x[0] for x in tp_bad.calls]
    assert result4["stage"]=="protection_tp_failed_sl_active",result4
    assert result4["sl_protected"] is True
    assert result4["processed"] is False
    assert kinds4.index("sl") < kinds4.index("tp")

    unsafe=FakeClient(avg_price=100.0)
    unsafe.fail_sl=True
    unsafe.fail_close=True
    ex.client=unsafe
    result5=ex.execute(intent())
    kinds5=[x[0] for x in unsafe.calls]
    assert result5["stage"]=="unsafe_open_position",result5
    assert result5["unsafe_open_position"] is True
    assert result5["processed"] is False
    assert "close" in kinds5

    partial=FakeClient(avg_price=100.0,entry_status="PARTIALLY_FILLED")
    ex.client=partial
    result6=ex.execute(intent())
    kinds6=[x[0] for x in partial.calls]
    assert result6["stage"]=="partial_fill_closed",result6
    assert result6["processed"] is True
    assert "cancel" in kinds6 and "close" in kinds6,kinds6
    assert "sl" not in kinds6 and "tp" not in kinds6,kinds6


    class LostSlResponse(FakeClient):
        def place_stop_loss(self,symbol,entry_side,qty,sl):
            self.calls.append(("sl",symbol,entry_side,qty,sl))
            order={"orderId":"sl-lost","status":"NEW","executedQty":"0","avgPrice":"0"}
            payload={"code":0,"data":{"order":order}}
            self.by_order["sl-lost"]=payload
            self.matching["STOP_MARKET"]=payload
            raise RuntimeError("simulated lost SL response")

    lost_sl=LostSlResponse(avg_price=100.0)
    ex.client=lost_sl
    result7=ex.execute(intent())
    kinds7=[x[0] for x in lost_sl.calls]
    assert result7["stage"]=="complete",result7
    assert "find_protection" in kinds7,kinds7
    assert "close" not in kinds7,kinds7

    class LostTpResponse(FakeClient):
        def place_take_profit(self,symbol,entry_side,qty,tp):
            self.calls.append(("tp",symbol,entry_side,qty,tp))
            order={"orderId":"tp-lost","status":"NEW","executedQty":"0","avgPrice":"0"}
            payload={"code":0,"data":{"order":order}}
            self.by_order["tp-lost"]=payload
            self.matching["TAKE_PROFIT_MARKET"]=payload
            raise RuntimeError("simulated lost TP response")

    lost_tp=LostTpResponse(avg_price=100.0)
    ex.client=lost_tp
    result8=ex.execute(intent())
    kinds8=[x[0] for x in lost_tp.calls]
    assert result8["stage"]=="complete",result8
    assert "find_protection" in kinds8,kinds8
    assert kinds8.count("tp")==1,kinds8

    print({
        "ok":True,
        "client_order_id":True,
        "pre_post_entry_query":True,
        "hard_tp_sl_verified":True,
        "sl_first":True,
        "sl_failure_emergency_close":True,
        "tp_failure_keeps_sl":True,
        "unsafe_open_position_detected":True,
        "partial_fill_cancelled_and_closed":True,
        "lost_sl_response_reconciled":True,
        "lost_tp_response_reconciled":True,
        "trailing":False,
    })


if __name__=="__main__":
    main()
