from __future__ import annotations

import os
import tempfile
from dataclasses import replace

import app.execution.final14_executor as fe
from app.config import Settings
from app.contracts import TradeIntent
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore

fe.ENTRY_CONFIRM_SLEEP_SEC=0
fe.AMBIGUOUS_RECHECK_SLEEP_SEC=0


def make_intent(close_time=777):
    return TradeIntent(
        event_id=f"BTC-USDT|15m|C1|BUY|{close_time}",
        symbol="BTC-USDT",
        combo=1,
        side="BUY",
        timeframe="15m",
        close_time=close_time,
        entry=100.0,
        tp=102.0,
        sl=99.0,
        smc_dir=1,
    )


class BaseClient:
    def __init__(self):
        self.calls=[]
        self.by_client={}
        self.by_order={}

    @staticmethod
    def extract_order(payload):
        data=payload.get("data") or {}
        if isinstance(data,dict) and isinstance(data.get("order"),dict):
            return data["order"]
        return data if isinstance(data,dict) else {}

    def find_order_by_client_id(self,symbol,client_order_id):
        self.calls.append(("lookup",client_order_id))
        return self.by_client.get(client_order_id)

    def get_order_detail(self,symbol,order_id=None,client_order_id=None):
        self.calls.append(("detail",order_id,client_order_id))
        payload=(
            self.by_client.get(client_order_id)
            if client_order_id
            else self.by_order.get(str(order_id))
        )
        if payload is None:
            raise RuntimeError("order not found")
        return payload

    def place_stop_loss(self,symbol,entry_side,qty,sl):
        self.calls.append(("sl",qty,sl))
        payload={"code":0,"data":{"order":{
            "orderId":"sl-recovery","status":"NEW",
            "executedQty":"0","avgPrice":"0",
        }}}
        self.by_order["sl-recovery"]=payload
        return payload

    def place_take_profit(self,symbol,entry_side,qty,tp):
        self.calls.append(("tp",qty,tp))
        payload={"code":0,"data":{"order":{
            "orderId":"tp-recovery","status":"NEW",
            "executedQty":"0","avgPrice":"0",
        }}}
        self.by_order["tp-recovery"]=payload
        return payload

    def cancel_order(self,*args,**kwargs):
        self.calls.append(("cancel",))
        return {"code":0}

    def close_market(self,*args,**kwargs):
        self.calls.append(("close",))
        raise RuntimeError("close should not be used in this test")


class AmbiguousSendClient(BaseClient):
    def place_market_entry(self,symbol,side,qty,leverage,client_order_id=None):
        self.calls.append(("entry",client_order_id))
        raise RuntimeError("simulated lost response")


class ExistingOrderClient(BaseClient):
    def __init__(self,client_order_id):
        super().__init__()
        payload={"code":0,"data":{"order":{
            "orderId":"entry-existing",
            "clientOrderId":client_order_id,
            "status":"FILLED",
            "executedQty":"1",
            "avgPrice":"100",
        }}}
        self.by_client[client_order_id]=payload
        self.by_order["entry-existing"]=payload

    def place_market_entry(self,*args,**kwargs):
        self.calls.append(("entry",kwargs.get("client_order_id")))
        raise AssertionError("restart recovery must not create a new entry")


class NoOrderClient(BaseClient):
    def place_market_entry(self,*args,**kwargs):
        self.calls.append(("entry",))
        raise AssertionError("restart recovery must fail closed when order is absent")


def main():
    settings=replace(
        Settings(),
        execution_enabled=True,
        dry_run=False,
        bingx_api_key="test",
        bingx_api_secret="test",
    )

    fd,path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store=ExecutionStore(path)
        intent=make_intent()
        service1=ExecutionService(settings,store)
        service1.credentials_verified=True
        client1=AmbiguousSendClient()
        service1.executor.client=client1

        first=service1.execute(intent)
        assert first.status=="entry_unknown_reconcile_required",first
        assert [x[0] for x in client1.calls].count("entry")==1
        state1=store.get_state(intent.event_id)
        assert state1 and state1["terminal"] is False
        assert store.seen(intent.event_id) is False

        cid=service1.executor.client_order_id(intent.event_id,"entry")
        service2=ExecutionService(settings,store)
        service2.credentials_verified=True
        client2=ExistingOrderClient(cid)
        service2.executor.client=client2

        recovered=service2.recover_pending()
        assert recovered and recovered[0]["status"]=="complete",recovered
        assert not any(x[0]=="entry" for x in client2.calls),client2.calls
        assert store.seen(intent.event_id) is True
        state2=store.get_state(intent.event_id)
        assert state2 and state2["terminal"] is True
        assert state2["stage"]=="complete"

        # A crash before the POST reached BingX must not cause a stale signal
        # to be opened after restart.
        intent2=make_intent(888)
        cid2=service2.executor.client_order_id(intent2.event_id,"entry")
        store.save_state(intent2.event_id,{
            "terminal":False,
            "stage":"entry_intent",
            "client_order_id":cid2,
            "intent":{
                "event_id":intent2.event_id,
                "symbol":intent2.symbol,
                "combo":intent2.combo,
                "side":intent2.side,
                "timeframe":intent2.timeframe,
                "close_time":intent2.close_time,
                "entry":intent2.entry,
                "tp":intent2.tp,
                "sl":intent2.sl,
                "smc_dir":intent2.smc_dir,
            },
        })
        service3=ExecutionService(settings,store)
        service3.credentials_verified=True
        client3=NoOrderClient()
        service3.executor.client=client3

        recovered2=service3.recover_pending()
        match=[x for x in recovered2 if x["event_id"]==intent2.event_id]
        assert match and match[0]["status"]=="entry_absent_reconciled",recovered2
        assert not any(x[0]=="entry" for x in client3.calls),client3.calls
        assert store.seen(intent2.event_id) is True

        print({
            "ok":True,
            "ambiguous_post_persisted":True,
            "restart_found_existing_without_resend":True,
            "restart_absent_order_not_resent":True,
        })
    finally:
        os.unlink(path)


if __name__=="__main__":
    main()
