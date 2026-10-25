"""No-network position lifecycle tests including startup replay and odd volumes."""
from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

from app.config import Settings
from app.execution.lifecycle import ProtectionLifecycle,LifecycleRisk
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore
from app.contracts import TradeIntent


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
        self.other_positions={}
        self.anonymous_same_side_position=Decimal("0")
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
        self.seq=0

    def get_open_orders(self,symbol):
        # Offline fake: never query the network; known trades are confirmed by
        # their saved exchange orderId after the journaling POST.
        return []

    @staticmethod
    def extract_order(payload):
        data=payload.get("data") or {}
        return data.get("order",data) if isinstance(data,dict) else {}

    def find_order_by_client_id(self,symbol,client_order_id):
        self.calls.append(("find_client",client_order_id))
        oid=self.clients.get(client_order_id)
        return self.get_order_detail(symbol,order_id=oid) if oid else None

    def place_market_entry(self,symbol,side,qty,leverage,client_order_id=None):
        self.calls.append(("entry",client_order_id,str(qty)))
        self.seq+=1
        oid=f"entry-new-{self.seq}"
        self.position+=Decimal(str(qty))
        self.orders[oid]={
            "orderId":oid,"clientOrderId":client_order_id,
            "symbol":symbol,"side":side,
            "positionSide":"LONG" if side=="BUY" else "SHORT",
            "status":"FILLED","type":"MARKET",
            "executedQty":str(qty),"origQty":str(qty),"avgPrice":"100",
        }
        self.clients[client_order_id]=oid
        return self.get_order_detail(symbol,order_id=oid)

    def _new_exit(self,kind,symbol,entry_side,qty,price=None):
        self.seq+=1
        oid=f"{kind}-{self.seq}"
        self.calls.append((kind,oid,str(qty)))
        types={"sl":"STOP_MARKET","tp":"TAKE_PROFIT_MARKET","trailing":"TRAILING_STOP_MARKET"}
        item=order(oid,types[kind],qty)
        if price is not None:
            item["stopPrice"]=str(price)
        self.orders[oid]=item
        return self.get_order_detail(symbol,order_id=oid)

    def place_stop_loss(self,symbol,entry_side,qty,sl,position_id=None):
        return self._new_exit("sl",symbol,entry_side,qty,sl)

    def place_take_profit(self,symbol,entry_side,qty,tp,quantity_precision=None,position_id=None):
        return self._new_exit("tp",symbol,entry_side,qty,tp)

    def place_trailing_stop(self,symbol,entry_side,qty,activation_price,callback_rate,quantity_precision=None,position_id=None):
        return self._new_exit("trailing",symbol,entry_side,qty)

    def get_positions(self,symbol):
        self.calls.append(("positions",symbol))
        rows=[]
        if self.position>0:
            rows.append({
                "symbol":symbol,"positionSide":"LONG",
                "positionId":self.position_id,"positionAmt":str(self.position),
            })
        for pid,qty in self.other_positions.items():
            if qty>0:
                rows.append({
                    "symbol":symbol,"positionSide":"LONG",
                    "positionId":pid,"positionAmt":str(qty),
                })
        if self.anonymous_same_side_position>0:
            rows.append({"symbol":symbol,"positionSide":"LONG",
                         "positionAmt":str(self.anonymous_same_side_position)})
        return rows

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

        # Two independently protected combos in the same aggregated LONG position.
        c3=store.get_state("C3")
        c3["lifecycle"]["status"]="closed"
        store.save_state("C3",c3)
        shared=Exchange()
        shared.position=Decimal("2")
        for oid,typ,qty in (
            ("sl-second","STOP_MARKET","1"),
            ("tp-second","TAKE_PROFIT_MARKET","0.5"),
            ("trail-second","TRAILING_STOP_MARKET","0.5"),
        ):
            shared.orders[oid]=order(oid,typ,qty)
        shared_service=ExecutionService(settings,store)
        shared_service.credentials_verified=True
        shared_service.executor.client=shared
        store.save_state("C4",state("C4"))
        second=state("C5")
        second["protection"]["sl"]["order_id"]="sl-second"
        second["protection"]["tp"]["order_id"]="tp-second"
        second["protection"]["trailing"]["order_id"]="trail-second"
        store.save_state("C5",second)
        result=shared_service.reconcile_open_positions()
        status_by_id={x["event_id"]:x["status"] for x in result}
        assert status_by_id["C4"]=="unchanged" and status_by_id["C5"]=="unchanged",result
        assert not any(x[0] in ("cancel","replace") for x in shared.calls)

        # TP for C4 fills 0.5; C5 remains exactly 1, untouched.
        shared.position=Decimal("1.5")
        shared.orders["tp-own"]["status"]="FILLED"
        shared.orders["tp-own"]["executedQty"]="0.5"
        result=shared_service.reconcile_open_positions()
        status_by_id={x["event_id"]:x["status"] for x in result}
        assert status_by_id["C4"]=="sl_resized" and status_by_id["C5"]=="unchanged",result
        c4_sl=store.get_state("C4")["protection"]["sl"]["order_id"]
        assert shared.orders[c4_sl]["origQty"]=="0.5"
        assert shared.orders["sl-second"]["status"]=="NEW"
        assert shared.orders["sl-second"]["origQty"]=="1"

        # C4 trailing completes while C5 remains open. Only C4 orders cleaned.
        shared.position=Decimal("1")
        shared.orders["trail-own"]["status"]="FILLED"
        shared.orders["trail-own"]["executedQty"]="0.5"
        result=shared_service.reconcile_open_positions()
        status_by_id={x["event_id"]:x["status"] for x in result}
        assert status_by_id["C4"]=="closed" and status_by_id["C5"]=="unchanged",result
        assert shared.orders[c4_sl]["status"]=="CANCELED"
        assert shared.orders["sl-second"]["status"]=="NEW"
        assert shared.orders["tp-second"]["status"]=="NEW"
        assert shared.orders["trail-second"]["status"]=="NEW"
        assert shared.orders["foreign"]["status"]=="NEW"

        # Another combo can now enter with the existing C5 still open.
        next_trade=TradeIntent(
            event_id="C99",symbol="BTC-USDT",combo=1,side="BUY",
            timeframe="15m",close_time=9999,entry=100.0,tp=102.0,
            sl=99.0,smc_dir=1,
        )
        entered=shared_service.execute(next_trade)
        assert entered.status=="complete" and entered.accepted is True,entered
        c99=store.get_state("C99")
        assert Decimal(str(c99["executed_qty"]))==Decimal("1") and Decimal(c99["actual_position_qty"])==Decimal("1"),c99
        assert c99["tp_qty"] and c99["trailing_qty"],c99
        assert shared.position==Decimal("2")
        assert shared.orders["sl-second"]["status"]=="NEW"

        # Subsequent polls must not touch completed C4 protection orders.
        shared_service.reconcile_open_positions()
        assert store.get_state("C4")["lifecycle"]["status"]=="closed"

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

        # Independent Separate Isolated LONG positions of same symbol.
        # The independently owned peer must not inflate this trade's SL.
        separate_ids=Exchange()
        separate_ids.other_positions={"p-202":Decimal("1")}
        for oid,typ,q in (
            ("sl-202","STOP_MARKET","1"),
            ("tp-202","TAKE_PROFIT_MARKET","0.5"),
            ("trail-202","TRAILING_STOP_MARKET","0.5"),
        ):
            separate_ids.orders[oid]=order(oid,typ,q)
            separate_ids.orders[oid]["positionId"]="p-202"
        for oid in ("sl-original","tp-own","trail-own"):
            separate_ids.orders[oid]["positionId"]="p-101"
        a=state("C8")
        b=state("C9")
        b["position_id"]="p-202"
        b["protection"]["sl"]["order_id"]="sl-202"
        b["protection"]["tp"]["order_id"]="tp-202"
        b["protection"]["trailing"]["order_id"]="trail-202"
        ledger=[deepcopy(a),deepcopy(b)]
        isolated=ProtectionLifecycle(separate_ids)
        def save_a(v):
            ledger[0]=deepcopy(v)
        def save_b(v):
            ledger[1]=deepcopy(v)
        assert isolated.reconcile(ledger[0],save_a,peer_states=[ledger[1]])=="unchanged"
        assert isolated.reconcile(ledger[1],save_b,peer_states=[ledger[0]])=="unchanged"
        assert not any(x[0] in ("replace","cancel") for x in separate_ids.calls)
        # A TP fills, but the other positionId B stays the same.
        separate_ids.position=Decimal("0.5")
        separate_ids.orders["tp-own"]["status"]="FILLED"
        separate_ids.orders["tp-own"]["executedQty"]="0.5"
        assert isolated.reconcile(ledger[0],save_a,peer_states=[ledger[1]])=="sl_resized"
        assert isolated.reconcile(ledger[1],save_b,peer_states=[ledger[0]])=="unchanged"
        assert separate_ids.orders["sl-202"]["status"]=="NEW"
        assert separate_ids.orders["sl-202"]["origQty"]=="1"
        # A disappears (e.g. external liquidation) while B remains LONG.
        # Cleanup ONLY A's stored Order IDs.
        separate_ids.position=Decimal("0")
        assert isolated.reconcile(ledger[0],save_a,peer_states=[ledger[1]])=="closed"
        assert ledger[0]["lifecycle"]["closure_evidence"]=="exchange_position_flat"
        assert isolated.reconcile(ledger[1],save_b,peer_states=[ledger[0]])=="unchanged"
        for oid in ("sl-202","tp-202","trail-202","foreign"):
            assert separate_ids.orders[oid]["status"]=="NEW",oid

        # A positive position without positionId could be the tracked trade;
        # fail closed instead of mistakenly canceling live protection orders.
        unknown=Exchange()
        unknown.position=Decimal("0")
        unknown.anonymous_same_side_position=Decimal("0.5")
        try:
            ProtectionLifecycle(unknown).reconcile(state("C10"),lambda v:None)
            assert False,"unidentified live position must fail closed"
        except LifecycleRisk:
            assert not any(x[0] in ("replace","cancel") for x in unknown.calls)

        # A different, positively identified position is not our trade.
        other=Exchange()
        other.position_id="p-202"
        closed=[state("C11")]
        assert ProtectionLifecycle(other).reconcile(
            closed[0],lambda v:closed.__setitem__(0,deepcopy(v))
        )=="closed"
        assert other.orders["foreign"]["status"]=="NEW"

        # Trailing may now fill its half BEFORE the fixed TP, because it
        # activates at entry +/- 0.5% rather than at the later hard TP.
        # Remaining hard TP must stay live while own full SL resizes to 0.5.
        trail_first=Exchange()
        trail_first.position=Decimal("0.5")
        trail_first.orders["trail-own"]["status"]="FILLED"
        trail_first.orders["trail-own"]["executedQty"]="0.5"
        own=[state("CTRAIL")]
        trailing_lifecycle=ProtectionLifecycle(trail_first)
        def save_trail(v):
            own[0]=deepcopy(v)
        assert trailing_lifecycle.reconcile(own[0],save_trail)=="sl_resized"
        assert trail_first.orders["tp-own"]["status"]=="NEW"
        resized_id=own[0]["protection"]["sl"]["order_id"]
        assert trail_first.orders[resized_id]["origQty"]=="0.5"
        trail_first.position=Decimal("0")
        trail_first.orders["tp-own"]["status"]="FILLED"
        trail_first.orders["tp-own"]["executedQty"]="0.5"
        assert trailing_lifecycle.reconcile(own[0],save_trail)=="closed"
        assert own[0]["lifecycle"]["status"]=="closed"

        print({"ok":True,"sl_resize":True,"full_close_cleanup":True,
               "owned_ids_only":True,"no_duplicate_on_restart":True,
               "lost_http_responses":True,"concurrent_combo_entry_allowed":True,
                "distinct_position_id_lifecycle":True,
                "unrelated_combo_orders_untouched":True})
    finally:
        os.unlink(path)


if __name__=="__main__":
    main()
