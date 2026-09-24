from __future__ import annotations

from dataclasses import replace

from app.config import Settings
from app.execution.final14_executor import Final14Executor
from app.strategy.strategy import Signal


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

    def place_protection(self,symbol,entry_side,qty,tp,sl):
        self.calls.append(("protection",symbol,entry_side,qty,tp,sl))
        return {"tp":{"code":0},"sl":{"code":0}}

    def close_market(self,symbol,entry_side,qty,leverage):
        self.calls.append(("close",symbol,entry_side,qty,leverage))
        return {"code":0}


def signal():
    return Signal(
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
    result=ex.execute(signal())
    assert result["ok"] is True
    assert result["protection_mode"]=="separate_hard_tp_sl"
    kinds=[x[0] for x in good.calls]
    assert kinds==["entry","detail","protection"],kinds
    protection=good.calls[-1]
    assert protection[3]==1.0
    assert protection[4]==102.0 and protection[5]==99.0
    assert all(x[0]!="trailing" for x in good.calls)

    bad=FakeClient(avg_price=103.0)
    ex.client=bad
    result2=ex.execute(signal())
    kinds2=[x[0] for x in bad.calls]
    assert result2["stage"]=="fill_outside_final14_range_closed"
    assert kinds2==["entry","detail","close"],kinds2

    print({"ok":True,"legacy_transport":True,"hard_tp_sl":True,"trailing":False,"partial":False})


if __name__=="__main__":
    main()
