from __future__ import annotations

from dataclasses import replace

from app.config import Settings
from app.contracts import TradeIntent
from app.execution.final14_executor import Final14Executor


class FakeClient:
    def __init__(self, avg_price=100.0):
        self.avg_price=avg_price
        self.calls=[]

    @staticmethod
    def extract_order(payload):
        return (payload.get("data") or {}).get("order") or {}

    def place_market_entry(self,symbol,side,qty,leverage):
        self.calls.append(("entry",symbol,side,qty,leverage))
        return {"code":0,"data":{"order":{"orderId":"abc"}}}

    def get_order_detail(self,symbol,order_id):
        self.calls.append(("detail",symbol,order_id))
        return {"code":0,"data":{"order":{
            "orderId":order_id,
            "executedQty":"1",
            "avgPrice":str(self.avg_price),
            "status":"FILLED",
        }}}

    def place_stop_loss(self,symbol,entry_side,qty,sl):
        self.calls.append(("sl",symbol,entry_side,qty,sl))
        return {"code":0,"data":{"order":{"orderId":"sl-1"}}}

    def place_take_profit(self,symbol,entry_side,qty,tp):
        self.calls.append(("tp",symbol,entry_side,qty,tp))
        return {"code":0,"data":{"order":{"orderId":"tp-1"}}}

    def close_market(self,symbol,entry_side,qty,leverage):
        self.calls.append(("close",symbol,entry_side,qty,leverage))
        return {"code":0}


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
    assert result["ok"] is True
    assert result["protection_mode"]=="separate_hard_tp_sl"
    kinds=[x[0] for x in good.calls]
    assert kinds==["entry","detail","sl","tp"],kinds
    sl_call=good.calls[-2]
    tp_call=good.calls[-1]
    assert sl_call[3]==1.0 and sl_call[4]==99.0
    assert tp_call[3]==1.0 and tp_call[4]==102.0
    assert all(x[0]!="trailing" for x in good.calls)

    bad=FakeClient(avg_price=103.0)
    ex.client=bad
    result2=ex.execute(intent())
    kinds2=[x[0] for x in bad.calls]
    assert result2["stage"]=="fill_outside_final14_range_closed"
    assert kinds2==["entry","detail","close"],kinds2

    class SlFails(FakeClient):
        def place_stop_loss(self,symbol,entry_side,qty,sl):
            self.calls.append(("sl",symbol,entry_side,qty,sl))
            raise RuntimeError("simulated SL failure")

    sl_bad=SlFails(avg_price=100.0)
    ex.client=sl_bad
    result3=ex.execute(intent())
    kinds3=[x[0] for x in sl_bad.calls]
    assert result3["stage"]=="protection_sl_failed_closed",result3
    assert result3["emergency_closed"] is True
    assert kinds3==["entry","detail","sl","close"],kinds3

    class TpFails(FakeClient):
        def place_take_profit(self,symbol,entry_side,qty,tp):
            self.calls.append(("tp",symbol,entry_side,qty,tp))
            raise RuntimeError("simulated TP failure")

    tp_bad=TpFails(avg_price=100.0)
    ex.client=tp_bad
    result4=ex.execute(intent())
    kinds4=[x[0] for x in tp_bad.calls]
    assert result4["stage"]=="protection_tp_failed_sl_active",result4
    assert result4["sl_protected"] is True
    assert kinds4==["entry","detail","sl","tp"],kinds4

    class SlAndCloseFail(SlFails):
        def close_market(self,symbol,entry_side,qty,leverage):
            self.calls.append(("close",symbol,entry_side,qty,leverage))
            raise RuntimeError("simulated close failure")

    unsafe=SlAndCloseFail(avg_price=100.0)
    ex.client=unsafe
    result5=ex.execute(intent())
    kinds5=[x[0] for x in unsafe.calls]
    assert result5["stage"]=="unsafe_open_position",result5
    assert result5["unsafe_open_position"] is True
    assert kinds5==["entry","detail","sl","close"],kinds5

    print({
        "ok":True,
        "legacy_transport":True,
        "hard_tp_sl":True,
        "sl_first":True,
        "sl_failure_emergency_close":True,
        "tp_failure_keeps_sl":True,
        "unsafe_open_position_detected":True,
        "trailing":False,
        "partial":False,
    })


if __name__=="__main__":
    main()
