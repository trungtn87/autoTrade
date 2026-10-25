"""No-network crash-window tests for SL / fixed TP / trailing idempotency."""
from __future__ import annotations

from copy import deepcopy

from app.execution.protection_journal import ProtectionJournal,ProtectionReconcilePending


class Exchange:
    def __init__(self):
        self.orders=[]
        self.posts=0
        self.next_id=100
        self.hide_open=False

    def get_open_orders(self,symbol):
        return [] if self.hide_open else deepcopy(self.orders)

    def get_order_detail(self,symbol,order_id=None):
        rows=[o for o in self.orders if str(o["orderId"])==str(order_id)]
        if len(rows)!=1:
            raise RuntimeError("order missing")
        return {"code":0,"data":{"order":deepcopy(rows[0])}}

    def submit(self,kind,qty,trigger,rate=None,position_id="123",lose_response=False):
        self.posts+=1
        self.next_id+=1
        oid=str(self.next_id)
        types={"sl":"STOP_MARKET","tp":"TAKE_PROFIT_MARKET","trailing":"TRAILING_STOP_MARKET"}
        row={"orderId":oid,"symbol":"BTC-USDT","side":"SELL","positionSide":"LONG",
             "positionId":position_id,"origQty":str(qty),"type":types[kind],
             "status":"NEW"}
        if kind=="trailing":
            row.update({"activationPrice":str(trigger),"priceRate":str(rate)})
        else:
            row["stopPrice"]=str(trigger)
        self.orders.append(row)
        if lose_response:
            raise RuntimeError("simulated BingX HTTP reply lost after POST")
        return {"code":0,"data":{"order":deepcopy(row)}}


def verify(exchange):
    def check(raw,kind,position_id):
        payload=raw.get("data") or {}
        order=payload.get("order") or payload
        oid=order.get("orderId")
        actual=exchange.get_order_detail("BTC-USDT",order_id=oid)["data"]["order"]
        assert actual["positionId"]==position_id
        return {"order_id":str(oid),"status":actual["status"],"payload":raw}
    return check


def journal(exchange,state,on_commit):
    return ProtectionJournal(exchange,state,on_commit,verify(exchange))


def run(kind,trigger,qty,rate=None):
    exchange=Exchange()
    state={"event_id":"C1","protection":{}}
    durable=[deepcopy(state)]
    history=[]
    def persist(s):
        durable[0]=deepcopy(s)
        history.append(deepcopy(s))
    args=("BTC-USDT","BUY",qty,trigger,"123")
    send=lambda lose=False: exchange.submit(kind,qty,trigger,rate,lose_response=lose)

    # Happy path: durable prepare BEFORE POST, confirmed orderId after POST.
    result=journal(exchange,state,persist).place(kind,*args,send)
    assert result["order_id"]=="101"
    assert exchange.posts==1
    assert len(history)==2
    assert history[0]["protection_journal"][kind]["phase"]=="prepared"
    assert history[0]["protection"].get(kind) is None
    assert durable[0]["protection"][kind]["order_id"]=="101"
    assert durable[0]["protection_journal"]=={}

    # Response lost AFTER BingX has created a conditional order.
    exchange=Exchange()
    state={"event_id":"C2","protection":{}}
    durable=[deepcopy(state)]
    def persist(s):
        durable[0]=deepcopy(s)
    result=journal(exchange,state,persist).place(
        kind,*args,lambda:exchange.submit(kind,qty,trigger,rate,lose_response=True),
        callback_rate=rate,
    )
    assert result["order_id"]=="101",result
    assert exchange.posts==1,"lost reply must not create a second order"

    # Crash AFTER POST but BEFORE the verified orderId could be persisted.
    exchange=Exchange()
    state={"event_id":"C3","protection":{}}
    durable=[deepcopy(state)]
    def persist(s):
        durable[0]=deepcopy(s)
    def abrupt_post():
        exchange.submit(kind,qty,trigger,rate)
        raise SystemExit("simulated worker killed after BingX POST")
    try:
        journal(exchange,state,persist).place(kind,*args,abrupt_post,callback_rate=rate)
        assert False,"expected simulated hard process exit"
    except SystemExit:
        pass
    assert exchange.posts==1
    restarted=deepcopy(durable[0])
    resolved=journal(exchange,restarted,persist).place(
        kind,*args,lambda:exchange.submit(kind,qty,trigger,rate),
        callback_rate=rate,
    )
    assert resolved["order_id"]=="101"
    assert exchange.posts==1,"restart must not re-submit an accepted order"

    # Crash AFTER durable prepare BEFORE BingX POST. There is no proof of
    # acceptance; DO NOT submit based on an empty openOrders result.
    exchange=Exchange()
    state={"event_id":"C4","protection":{}}
    durable=[deepcopy(state)]
    def prepare_then_die(s):
        durable[0]=deepcopy(s)
        raise SystemExit("simulated worker killed after Supabase WAL commit")
    try:
        journal(exchange,state,prepare_then_die).place(
            kind,*args,lambda:exchange.submit(kind,qty,trigger,rate),
            callback_rate=rate,
        )
        assert False
    except SystemExit:
        pass
    assert exchange.posts==0
    restarted=deepcopy(durable[0])
    try:
        journal(exchange,restarted,lambda s:durable.__setitem__(0,deepcopy(s))).place(
            kind,*args,lambda:exchange.submit(kind,qty,trigger,rate),
            callback_rate=rate,
        )
        assert False,"uncertain prepared order must fail closed"
    except ProtectionReconcilePending:
        pass
    assert exchange.posts==0,"prepared-but-unknown cannot trigger duplicate POST"

    # An ambiguous response followed by invisible/openOrders outage: no re-POST
    # even if the order was truly accepted on BingX.
    exchange=Exchange()
    state={"event_id":"C5","protection":{}}
    durable=[deepcopy(state)]
    exchange.hide_open=True
    try:
        journal(exchange,state,lambda s:durable.__setitem__(0,deepcopy(s))).place(
            kind,*args,lambda:exchange.submit(kind,qty,trigger,rate,lose_response=True),
            callback_rate=rate,
        )
        assert False
    except ProtectionReconcilePending:
        pass
    assert exchange.posts==1
    exchange.hide_open=False
    after_restart=deepcopy(durable[0])
    journal(exchange,after_restart,lambda s:durable.__setitem__(0,deepcopy(s))).place(
        kind,*args,lambda:exchange.submit(kind,qty,trigger,rate),callback_rate=rate,
    )
    assert exchange.posts==1
    assert durable[0]["protection"][kind]["order_id"]=="101"

    # Multiple new exact matches, e.g. external actor submitted same order:
    # never pick the first or send again.
    exchange=Exchange()
    exchange.submit(kind,qty,trigger,rate)
    exchange.submit(kind,qty,trigger,rate)
    state={"protection":{},"protection_journal":{
        kind:{"phase":"prepared","spec":{
            "kind":kind,"symbol":"BTC-USDT","side":"SELL","position_side":"LONG",
            "quantity":str(qty),"trigger":str(trigger),"position_id":"123",
            **({"callback_rate":str(rate)} if rate else {}),
        },"baseline_ids":[]}
    }}
    previous=exchange.posts
    try:
        journal(exchange,state,lambda s:None).place(
            kind,*args,lambda:exchange.submit(kind,qty,trigger,rate),callback_rate=rate,
        )
        assert False
    except ProtectionReconcilePending:
        pass
    assert exchange.posts==previous


def main():
    run("sl",99,1)
    run("tp",102,0.5)
    run("trailing",102,0.5,0.005)
    print({"ok":True,"durable_prepare":True,"lost_response_recovered":True,
           "crash_after_post_recovered":True,"crash_before_post_no_resend":True,
           "hidden_orders_fail_closed":True,"duplicate_candidates_refused":True})


if __name__=="__main__":
    main()
