"""Offline tests: emergency close must prove position flat, never duplicate POST."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy

from app.config import Settings
from app.execution.final14_executor import Final14Executor
from legacy_execution_test import FakeClient,intent


class CloseExchange(FakeClient):
    def __init__(self,plans,starting_qty=1.0):
        super().__init__()
        self.position_id="456"
        self.position_qty=str(starting_qty)
        self.plans=list(plans)
        self.posts=0
        self.lookup_hidden=False
        self.close_records={}

    def find_order_by_client_id(self,symbol,client_order_id):
        if self.lookup_hidden:
            return None
        return self.close_records.get(client_order_id)

    def close_market(self,symbol,side,qty,leverage,client_order_id=None,position_id=None):
        assert position_id=="456",position_id
        self.posts+=1
        plan=self.plans.pop(0)
        applied=float(plan.get("applied",qty))
        self.position_qty=str(max(0.0,float(self.position_qty)-applied))
        data={"orderId":str(100+self.posts),"clientOrderId":client_order_id,
              "positionId":"456","symbol":symbol,"positionSide":"LONG","type":"MARKET",
              "status":plan["status"],"executedQty":str(plan.get("filled",applied)),
              "avgPrice":"100"}
        result={"code":0,"data":{"order":data}}
        self.close_records[client_order_id]=result
        if plan.get("lost"):
            raise RuntimeError("simulated HTTP timeout after BingX accepted MARKET close")
        return result


def setup(plans,qty=1.0):
    fake=CloseExchange(plans,starting_qty=qty)
    settings=replace(Settings(),bingx_api_key="fake",bingx_api_secret="fake")
    executor=Final14Executor(settings)
    executor.client=fake
    state={"intent":{"event_id":intent().event_id},"pre_entry_positions":{}}
    written=[]
    def persist(new):
        written.append(deepcopy(new))
    return executor,fake,state,persist,written


def close(ex,state,persist,qty=1.0):
    return ex._confirm_market_close(
        intent(),qty,"stop_loss_unconfirmed",
        position_id="456",resume_state=state,protection_persist=persist,
    )


def main():
    ex,fake,state,persist,writes=setup([{"status":"FILLED","filled":1,"applied":1}])
    ok=close(ex,state,persist)
    assert ok["confirmed"] and ok["remaining_qty"]=="0",ok
    assert fake.posts==1 and writes[0]["emergency_close"]["phase"]=="prepared"

    ex,fake,state,persist,writes=setup([{"status":"FILLED","filled":1,"applied":0.8}])
    incomplete=close(ex,state,persist)
    assert not incomplete["confirmed"] and float(incomplete["remaining_qty"])>0,incomplete
    assert fake.posts==1
    assert not close(ex,state,persist)["confirmed"]
    assert fake.posts==1,"FILLED but residual must not trigger a duplicate close"

    ex,fake,state,persist,writes=setup([{"status":"PARTIALLY_FILLED","filled":0.4,"applied":0.4}])
    partial=close(ex,state,persist)
    assert not partial["confirmed"] and float(partial["remaining_qty"])>0
    assert not close(ex,state,persist)["confirmed"] and fake.posts==1

    ex,fake,state,persist,writes=setup([
        {"status":"CANCELED","filled":0.4,"applied":0.4},
        {"status":"FILLED","filled":0.6,"applied":0.6},
    ])
    corrected=close(ex,state,persist)
    assert corrected["confirmed"] and corrected["remaining_qty"]=="0",corrected
    assert fake.posts==2 and writes[-1]["emergency_close"]["revision"]==1
    assert len({x["emergency_close"]["client_order_id"] for x in writes})==2

    ex,fake,state,persist,writes=setup([
        {"status":"CANCELED","filled":0,"applied":0},
        {"status":"CANCELED","filled":0,"applied":0},
        {"status":"CANCELED","filled":0,"applied":0},
    ])
    bounded=close(ex,state,persist)
    assert not bounded["confirmed"] and fake.posts==3,bounded

    ex,fake,state,persist,writes=setup([
        {"status":"CANCELED","filled":0.5,"applied":0.1}
    ])
    inconsistent=close(ex,state,persist)
    assert not inconsistent["confirmed"] and fake.posts==1,inconsistent

    ex,fake,state,persist,writes=setup([{"status":"FILLED","applied":1,"lost":True}])
    fake.lookup_hidden=True
    uncertain=close(ex,state,persist)
    assert not uncertain["confirmed"] and fake.posts==1,uncertain
    assert state["emergency_close"]["phase"]=="prepared"
    # Durable state survives worker restart. No new MARKET POST when exchange
    # visibility is delayed or the original request was lost.
    recovered=deepcopy(writes[-1])
    again=close(ex,recovered,persist)
    assert not again["confirmed"] and fake.posts==1,again
    fake.lookup_hidden=False
    resolved=close(ex,recovered,persist)
    assert resolved["confirmed"] and fake.posts==1,resolved

    ex,fake,state,persist,writes=setup([])
    def kill_after_wal(new):
        writes.append(deepcopy(new))
        raise SystemExit("worker crashed after Supabase PREPARED, before POST")
    try:
        close(ex,state,kill_after_wal)
        assert False
    except SystemExit:
        pass
    resumed=deepcopy(writes[-1])
    unresolved=close(ex,resumed,persist)
    assert not unresolved["confirmed"] and fake.posts==0,unresolved

    # A peer Separate Isolated position of the same side is never closed.
    # If the targeted positionId is absent, its quantity is already zero.
    ex,fake,state,persist,writes=setup([{"status":"FILLED"}])
    fake.position_id="457"
    peer_untouched=close(ex,state,persist)
    assert peer_untouched["confirmed"] and fake.posts==0,peer_untouched

    print({"ok":True,"filled_requires_flat":True,
           "partial_never_duplicated":True,"canceled_remaining_retried":True,
           "retry_bound":3,"inconsistent_residual_blocked":True,
           "lost_response_recovered":True,"crash_before_send_safe":True,
           "position_id_required":True})


if __name__=="__main__":
    main()
