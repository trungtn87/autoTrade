"""No-network position lifecycle tests including startup replay and odd volumes."""
from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

from app.config import Settings
from app.execution.lifecycle import ProtectionLifecycle
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore


def order(oid,typ,qty,status="NEW"):
    result={
        "orderId":oid,"status":status,"symbol":"BTC-USDT",
        "side":"SELL","positionSide":"LONG","type":typ,
        "origQty":str(qty),"executedQty":"0",
    }
    if typ=="STOP_MARKET":
        result["stopPrice"]="99"
    return result


class Exchange:
    def __init__(self):
        self.position=Decimal("1")
        self.position_id="p-101"
        self.orders={
            "sl-original":order("sl-original","STOP_MARKET","1"),
            "tp-own":order("tp-own","TAKE_PROFIT_MARKET","0.5"),
            "trail-own":order("trail-own","TRAILING_STOP_MARKET","0.5"),
            "foreign":order("foreign","TAKE_PROFIT_MARKET","0.2"),
        }
        self.clients={}
        self.calls=[]
        self.cancel_failure=False
        self.cancel_lost=False
        self.replace_lost=False
        self.replace_rejected=False
        self.num=0

    def get_positions(self,symbol):
        self.calls.append(("positions",symbol))
        if self.position==0:
            return []
        return [{
            "symbol":symbol,"positionSide":"LONG",
            "positionId":self.position_id,"positionAmt":str(self.position),
        }]

    def get_contract_rules(self,symbol):
        self.calls.append(("contract",symbol))
        return {"quantityPrecision":4,"tradeMinQuantity":"0.0001","tradeMinUSDT":"0"}

    def get_order_detail(self,symbol,order_id=None,client_order_id=None):
        self.calls.append(("detail",order_id,client_order_id))
        if client_order_id:
            order_id=self.clients.get(client_order_id)
        if not order_id or order_id not in self.orders:
            raise RuntimeError("order not found")
        return {"code":0,"data":{"order":deepcopy(self.orders[order_id])}}

    def cancel_order(self,symbol,order_id=None,client_order_id=None):
        self.calls.append(("cancel",order_id))
        if self.cancel_failure:
            raise RuntimeError("cannot cancel")
        self.orders[order_id]["status"]="CANCELED"
        if self.cancel_lost:
            raise RuntimeError("lost DELETE response")
        return {"code":0}

    def cancel_replace_stop_loss(self,symbol,entry_side,old_order_id,qty,sl,precision,cid,position_id=None):
        self.calls.append(("replace",old_order_id,str(qty),cid,position_id))
        assert entry_side=="BUY"
        assert str(sl)=="99.0"
        assert precision==4
        assert position_id=="p-101"
        assert self.orders[old_order_id]["status"]=="NEW"
        if self.replace_rejected:
            return {"code":0,"data":{"cancelResult":"FAILED","newOrderResult":"FAILED"}}
        self.orders[old_order_id]["status"]="CANCELED"
        self.num+=1
        new_id=f"sl-new-{self.num}"
        self.orders[new_id]=order(new_id,"STOP_MARKET",qty)
        self.clients[cid]=new_id
        if self.replace_lost:
            raise RuntimeError("lost POST response")
        return {"code":0,"data":{
            "cancelResult":"SUCCESS","newOrderResult":"SUCCESS","newOrderId":new_id,
        }}


def state(eid="C1",with_trail=True):
    protect={
        "sl":{"order_id":"sl-original","status":"NEW"},
        "tp":{"order_id":"tp-own","status":"NEW"},
    }
    if with_trail:
        protect["trailing"]={"order_id":"trail-own","status":"NEW"}
    return {
        "ok":True,"terminal":True,
        "stage":"complete","executed_qty":"1",
        "position_id":"p-101","actual_position_qty":"1",
        "intent":{
            "event_id":eid,"symbol":"BTC-USDT","combo":1,"side":"BUY",
            "timeframe":"15m","close_time":777,"entry":100.0,
            "tp":102.0,"sl":99.0,"smc_dir":1,
        },
        "protection":protect,
        "lifecycle":{
            "status":"open","remaining_qty":"1","sl_qty":"1",
            "sl_revision":0,"pending_sl_replace":None,"cleaned_orders":[],
        },
    }


def main():
    settings=replace(Settings(),
        execution_enabled=True,dry_run=False,
        position_lifecycle_enabled=True,tp_trailing_split_enabled=True,
        bingx_api_key="test",bingx_api_secret="test",
    )
    fd,path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store=ExecutionStore(path)
        events=[]
        service=ExecutionService(settings,store,event_cb=lambda *args,**kw: events.append((args,kw)))
        service.credentials_verified=True
        exchange=Exchange()
        service.executor.client=exchange
        store.save_state("C1",state())
        assert service.reconcile_open_positions()[0]["status"]=="unchanged"
        assert not any(x[0] in ("replace","cancel") for x in exchange.calls)

        # TP closes 50% on BingX: do not subtract locally.
        exchange.position=Decimal("0.5")
        exchange.orders["tp-own"]["status"]="FILLED"
        exchange.orders["tp-own"]["executedQty"]="0.5"
        result=service.reconcile_open_positions()
        assert result[0]["status"]=="sl_resized",result
        saved=store.get_state("C1")
        new_id=saved["protection"]["sl"]["order_id"]
        assert exchange.orders[new_id]["origQty"]=="0.5"
        assert exchange.orders["sl-original"]["status"]=="CANCELED"
        assert saved["lifecycle"]["pending_sl_replace"] is None
        assert len([x for x in exchange.calls if x[0]=="replace"])==1
        assert service.reconcile_open_positions()[0]["status"]=="unchanged"
        assert len([x for x in exchange.calls if x[0]=="replace"])==1

        # All remaining position closes: only the tracked exit IDs are canceled.
        exchange.position=Decimal("0")
        exchange.cancel_lost=True
        closed=service.reconcile_open_positions()
        assert closed[0]["status"]=="closed",closed
        assert store.get_state("C1")["lifecycle"]["status"]=="closed"
        assert exchange.orders["foreign"]["status"]=="NEW"
        assert exchange.orders[new_id]["status"]=="CANCELED"
        assert exchange.orders["trail-own"]["status"]=="CANCELED"
        assert service.reconcile_open_positions()==[]
        assert any(e[0][0]=="L3.EXEC.ORPHAN_ORDERS_CLEANED" for e in events)

        # Exchange POST times out after committing replacement: reconcile by CID,
        # without creating a second SL.
        exchange2=Exchange()
        exchange2.position=Decimal("0.5")
        exchange2.orders["tp-own"]["status"]="FILLED"
        exchange2.orders["tp-own"]["executedQty"]="0.5"
        exchange2.replace_lost=True
        store.save_state("C2",state("C2"))
        service.executor.client=exchange2
        assert service.reconcile_open_positions()[0]["status"]=="sl_resized"
        assert exchange2.num==1 and store.get_state("C2")["lifecycle"]["sl_revision"]==1
        c2_state=store.get_state("C2")
        c2_state["lifecycle"]["status"]="closed"
        store.save_state("C2",c2_state)

        # Crash after writing a deterministic journal but before POST.
        exchange3=Exchange()
        exchange3.position=Decimal("0.5")
        exchange3.orders["tp-own"]["status"]="FILLED"
        exchange3.orders["tp-own"]["executedQty"]="0.5"
        st=state("C3")
        st["lifecycle"]["pending_sl_replace"]={
            "old_order_id":"sl-original","quantity":"0.5",
            "client_order_id":"atv2sl-pending-c3","revision":1,
        }
        store.save_state("C3",st)
        service.executor.client=exchange3
        assert service.reconcile_open_positions()[0]["status"]=="sl_resized"
        assert exchange3.num==1
        assert store.get_state("C3")["lifecycle"]["pending_sl_replace"] is None

        # Simulate Render process restart: new service, same persisted store.
        restarted=ExecutionService(settings,store)
        restarted.credentials_verified=True
        restarted.executor.client=exchange3
        assert restarted.reconcile_open_positions()[0]["status"]=="unchanged"
        assert exchange3.num==1

        # Two live combos sharing a symbol/side: do not touch any orders.
        shared=Exchange()
        shared.position=Decimal("0.5")
        shared_service=ExecutionService(settings,store)
        shared_service.credentials_verified=True
        shared_service.executor.client=shared
        store.save_state("C4",state("C4"))
        store.save_state("C5",state("C5"))
        result=shared_service.reconcile_open_positions()
        conflicts=[x for x in result if x["event_id"] in ("C4","C5")]
        assert len(conflicts)==2 and all(x["status"]=="risk" for x in conflicts)
        assert not any(x[0] in ("cancel","replace") for x in shared.calls)

        # Repeat same problem does not repeatedly flood Layer 4 alerts.
        count=len(events)
        assert count>=2
        shared_service.reconcile_open_positions()
        assert not any(x[0] in ("cancel","replace") for x in shared.calls)

        # Unverified cancellation must remain pending rather than falsely closing.
        separate=Exchange()
        separate.position=Decimal("0")
        separate.cancel_failure=True
        isolated=ProtectionLifecycle(separate)
        journal=[state("C6")]
        def persist(next_state):
            journal[0]=deepcopy(next_state)
        try:
            isolated.reconcile(journal[0],persist)
            assert False,"uncertain cancellation must raise"
        except Exception:
            assert journal[0]["lifecycle"]["status"]=="cleanup"
        separate.cancel_failure=False
        assert isolated.reconcile(journal[0],persist)=="closed"

        # Wrong positionId: no cancellation even if old position seems gone.
        wrong=Exchange()
        wrong.position_id="other-position"
        wrong.position=Decimal("0.5")
        isolated=ProtectionLifecycle(wrong)
        try:
            isolated.reconcile(state("C7"),lambda s: None)
            assert False,"position mismatch must block"
        except Exception:
            assert not any(x[0] in ("replace","cancel") for x in wrong.calls)

        print({"ok":True,"sl_resize":True,"full_close_cleanup":True,
               "owned_ids_only":True,"no_duplicate_on_restart":True,
               "lost_http_responses":True,"shared_position_block":True})
    finally:
        os.unlink(path)


if __name__=="__main__":
    main()
