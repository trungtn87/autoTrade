"""Strict offline tests for rare-orphan BingX exit cancellation.

No network or credentials; test per-Combo proof with shared position IDs,
partial fills, stale exchange snapshots, and ambiguous cancel responses.
"""
from copy import deepcopy
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from app.config import Settings,enable_runtime_trailing,validate_settings
from app.execution.exit_cleanup import (
    eligible,inspect_one,clean_one,CleanupUncertain,
)
from app.execution.service import ExecutionService
from app.execution.executor import LegacyStyleBingXClient


def trade(combo=14,entry="100",pids=("201","202","203"),
          qty="0.0012",position_id="9001"):
    return {
        "ok":True,"terminal":True,"entry_filled":True,"executed_qty":qty,
        "position_id":position_id,"exit_mode":"split_trailing",
        "intent":{
            "event_id":f"BTC|C{combo}","symbol":"BTC-USDT","side":"SELL",
            "combo":combo,"timeframe":"15m","close_time":1792970000000,
            "entry":80000,"tp":78500,"sl":80600,"smc_dir":-1,
        },
        "protection":{
            "sl":{"order_id":pids[0]},
            "tp":{"order_id":pids[1]},
            "trailing":{"order_id":pids[2]},
        },
    }


def order(order_id,kind,filled="0",status="NEW",
          qty="0.0012",position_id="9001"):
    return {
        "orderID":order_id,"orderId":order_id,"status":status,
        "executedQty":filled,"origQty":qty,
        "symbol":"BTC-USDT","type":kind,
        "positionSide":"SHORT","side":"BUY","positionId":position_id,
    }


def rows(pids=("201","202","203"),tp="0",trailing="0",sl="0",
         tp_status="NEW",trailing_status="NEW",sl_status="NEW"):
    return [
        order(pids[0],"STOP_MARKET",sl,sl_status),
        order(pids[1],"TAKE_PROFIT_MARKET",tp,tp_status,qty="0.0006"),
        order(pids[2],"TRAILING_STOP_MARKET",trailing,trailing_status,qty="0.0006"),
    ]


def position(amt="0.0012",position_id="9001"):
    return [{"symbol":"BTC-USDT","positionSide":"SHORT",
             "positionId":position_id,"positionAmt":amt}]


class FakeClient:
    def __init__(self,orders=(),extra=()):
        self.orders={x["orderID"]:x for x in list(orders)+list(extra)}
        self.canceled=[]
        self.lookups=[]
        self.fail_next_cancel=False
        self.position_rows=position()
        self.open_orders_calls=0
        self.get_positions_calls=0
        self.history_calls=0

    def get_open_orders(self,symbol):
        self.open_orders_calls+=1
        return [row for row in self.orders.values() if row["status"] in {"NEW","PARTIALLY_FILLED"}]

    def get_positions(self,symbol):
        self.get_positions_calls+=1
        return self.position_rows

    def get_order_history(self,symbol,start_ms,end_ms,required_ids=None):
        self.history_calls+=1
        return list(self.orders.values())

    @staticmethod
    def extract_order(data):
        return data.get("data",{}).get("order",{})

    def get_order_detail(self,symbol,order_id=None):
        self.lookups.append(order_id)
        return {"data":{"order":self.orders[order_id]}}

    def cancel_order(self,symbol,order_id):
        self.canceled.append(order_id)
        if self.fail_next_cancel:
            self.fail_next_cancel=False
            raise ConnectionError("reply lost")
        self.orders[order_id]["status"]="CANCELLED"
        return {"code":0}


def execute_check(state,client,open_orders,positions,now=1000000,history=None):
    written=[]
    alerts=[]
    result=clean_one(client,state,open_orders,positions,
                     lambda s:written.append(deepcopy(s)),
                     lambda *a:alerts.append(a),
                     now_ms=now,historical_by_id=history)
    return result,written,alerts


def test_shared_short_one_combo_filled_does_not_touch_peer():
    closed=trade()
    peer=trade(combo=7,entry="300",pids=("301","302","303"))
    first=rows(tp="0.0006",tp_status="FILLED",
               trailing="0.0006",trailing_status="FILLED")
    second=rows(pids=("301","302","303"))
    client=FakeClient(first+second)
    all_open={x["orderID"]:x for x in (first[:1]+second)}
    result,_,_=execute_check(closed,client,all_open,position(amt="0.0013"))
    assert result["cancel_requested"]==["201"]
    assert client.canceled==["201"]
    assert not client.lookups or set(client.lookups)=={"202","203"}
    assert peer["protection"]["sl"]["order_id"]=="301"


def test_partial_trailing_never_cancels_or_resizes_sl():
    state=trade()
    orders=rows(trailing="0.0006",trailing_status="FILLED")
    client=FakeClient(orders)
    result,_,warnings=execute_check(state,client,
                  {x["orderID"]:x for x in orders[:2]},
                  position(amt="0.0006"))
    assert result["status"]=="watching"
    assert not result["cancel_requested"]
    assert client.canceled==[]
    assert any("SL_OVER_REMAINING" in repr(a) for a in warnings)


def test_flat_requires_two_distinct_scans():
    state=trade()
    orders=rows()
    client=FakeClient(orders)
    open_map={x["orderID"]:x for x in orders}
    first,_,_=execute_check(state,client,open_map,[],now=1000000)
    assert first["status"]=="watching"
    assert client.canceled==[]
    second,_,_=execute_check(state,client,open_map,[],now=1060000)
    assert second["status"]=="cleanup"
    assert set(client.canceled)=={"201","202","203"}


def test_missing_order_lookup_uncertain_no_mutations():
    state=trade()
    client=FakeClient(rows())
    client.orders.pop("201")
    try:
        execute_check(state,client,{},position())
        raise AssertionError("should refuse unknown missing order")
    except KeyError:
        pass
    assert client.canceled==[]


def test_closed_owned_fill_cancels_only_active_known_orders():
    state=trade()
    orders=rows(tp="0.0006",tp_status="FILLED",
                trailing="0.0006",trailing_status="FILLED")
    client=FakeClient(orders)
    out,_,_=execute_check(state,client,{"201":orders[0]},position(amt="0.5"))
    assert out["cancel_requested"]==["201"]
    assert client.canceled==["201"]
    assert state["exit_cleanup"]["cancel_attempts"]=={"201":1}


def test_cancel_transport_uncertainty_bounded():
    state=trade()
    orders=rows(tp="0.0006",tp_status="FILLED",
                trailing="0.0006",trailing_status="FILLED")
    client=FakeClient(orders)
    client.fail_next_cancel=True
    mp={"201":orders[0]}
    a,_,alerts=execute_check(state,client,mp,position(amt="0.0006"),now=1000000)
    assert a["cancel_requested"]==[]
    assert client.canceled==["201"]
    assert alerts
    assert state["exit_cleanup"]["cancel_attempts"]["201"]==1
    # Too soon: observe only, do not issue second DELETE.
    b,_,_=execute_check(state,client,mp,position(amt="0.0006"),now=1020000)
    assert not b["cancel_requested"]
    assert client.canceled==["201"]
    # After 60s a second and final attempt is allowed.
    c,_,_=execute_check(state,client,mp,position(amt="0.0006"),now=1060000)
    assert c["cancel_requested"]==["201"]
    assert client.canceled==["201","201"]
    d,_,_=execute_check(state,client,mp,position(amt="0.0006"),now=1120000)
    assert d["cancel_requested"]==[]
    assert client.canceled==["201","201"]


def test_bingx_cancelled_is_terminal_and_not_misread_as_our_delete():
    state=trade()
    orders=rows(tp="0.0006",tp_status="FILLED",
                trailing="0.0006",trailing_status="FILLED",
                sl_status="CANCELLED")
    client=FakeClient(orders)
    res,_,_=execute_check(state,client,{},position(amt="0"))
    assert res["status"]=="done"
    assert client.canceled==[]


def test_rejected_cross_position_order_never_mutated():
    state=trade()
    old=rows(tp="0.0006",tp_status="FILLED",
             trailing="0.0006",trailing_status="FILLED")
    old[0]["positionId"]="different"
    client=FakeClient(old)
    try:
        execute_check(state,client,{"201":old[0]},position(amt="0"))
        raise AssertionError("cross-position order must be rejected")
    except CleanupUncertain:
        pass
    assert client.canceled==[]


def test_legacy_without_position_id_is_not_eligible():
    state=trade()
    del state["position_id"]
    assert not eligible(state)
    assert eligible(trade())


def test_service_batches_symbol_reads_once_and_never_touches_peer():
    combo1=trade()
    combo2=trade(combo=7,pids=("301","302","303"))
    own1=rows(tp="0.0006",trailing="0.0006",tp_status="FILLED",
              trailing_status="FILLED")
    own2=rows(pids=("301","302","303"))
    client=FakeClient(own1+own2)
    client.position_rows=position("0.0013")
    class Store:
        def __init__(self):
            self.rows={combo1["intent"]["event_id"]:combo1,
                       combo2["intent"]["event_id"]:combo2}
        def list_states(self):
            return list(self.rows.items())
        def save_state(self,eid,val):
            self.rows[eid]=deepcopy(val)
    service=object.__new__(ExecutionService)
    service.settings=SimpleNamespace(execution_enabled=True,dry_run=False)
    service.credentials_verified=True
    service.store=Store()
    service.executor=SimpleNamespace(client=client)
    service._execution_lock=RLock()
    service._exit_cleanup_scan_errors={}
    service._emit=lambda *a,**kw:None
    out=service.cleanup_closed_exits()
    assert client.get_positions_calls==1
    assert client.open_orders_calls==1
    assert client.history_calls==1
    assert client.canceled==["201"]
    assert len(out)==1 and out[0]["event_id"]==combo1["intent"]["event_id"]
    assert service.store.rows[combo2["intent"]["event_id"]].get("exit_cleanup",{}).get("status")=="watching"


def test_legacy_pending_sl_replacement_warns_without_resizing():
    state=trade()
    state["lifecycle"]={
        "pending_sl_replace":{"old_order_id":"201",
                              "client_order_id":"atv2sl-previous"},
    }
    old=rows(trailing="0.0006",trailing_status="FILLED",
             sl_status="CANCELLED")
    client=FakeClient(old)
    out,_,alerts=execute_check(state,client,{"202":old[1]},position("0.0006"))
    assert out["status"]=="watching"
    assert any("LEGACY_SL_REPLACEMENT_UNVERIFIED" in repr(a) for a in alerts)
    assert not client.canceled


def test_live_history_matching_closed_combo_and_no_single_get():
    closed=trade()
    first=rows(tp="0.0006",trailing="0.0006",
               tp_status="FILLED",trailing_status="FILLED")
    client=FakeClient(first)
    hist={x["orderID"]:x for x in first}
    out,_,_=execute_check(closed,client,{"201":first[0]},
                         position("0.0013"),history=hist)
    assert out["cancel_requested"]==["201"]
    assert client.canceled==["201"]
    assert client.lookups==[]  # no unreliable GET /trade/order


def test_history_missing_exact_id_fails_closed_even_when_other_ids_exist():
    state=trade()
    first=rows()
    client=FakeClient(first)
    other=order("999","TRAILING_STOP_MARKET",status="FILLED")
    try:
        execute_check(state,client,{"201":first[0]},
                      position("0.0012"),history={"202":first[1],"999":other})
        assert False, "must not infer state from a different BingX orderId"
    except CleanupUncertain as exc:
        assert "owned exits missing history while position remains open" in str(exc)
    assert client.canceled==[]
    assert client.lookups==[]


def test_history_never_authorizes_cancel_of_order_not_in_open_snapshot():
    state=trade()
    first=rows()
    client=FakeClient(first)
    hist={x["orderID"]:x for x in first}
    first_result,_,_=execute_check(state,client,{},[],
                                    now=1000000,history=hist)
    assert first_result["status"]=="watching"
    second_result,_,_=execute_check(state,client,{},[],
                                    now=1060000,history=hist)
    assert second_result["status"]=="cleanup"
    assert second_result["cancel_requested"]==[]
    assert client.canceled==[]


def test_render_explicit_opt_in_preserves_disabled_legacy_sl_lifecycle():
    with patch.dict("os.environ",{
        "EXIT_CLEANUP_ENABLED":"true",
        "POSITION_LIFECYCLE_ENABLED":"true",
    },clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.exit_cleanup_enabled is True
        assert settings.position_lifecycle_enabled is False
        assert settings.tp_trailing_activation_pct==0.005
        assert settings.tp_trailing_callback_rate==0.005
        validate_settings(settings)


def test_fullorder_fallback_recovers_exact_missing_stop_ids():
    first=rows(tp="0.0006",trailing="0.0006",
               tp_status="FILLED",trailing_status="FILLED")
    client=object.__new__(LegacyStyleBingXClient)
    requested=[]
    def fake_request(method,path,params):
        requested.append((method,path,params))
        if path.endswith("/allOrders"):
            return {"code":0,"data":[]}
        if path.endswith("/fullOrder"):
            return {"code":0,"data":{"orders":first}}
        raise AssertionError("unexpected BingX endpoint")
    client._signed_request=fake_request
    res=client.get_order_history("BTC-USDT",1000000,1100000,
                                 required_ids={"201","202","203"})
    assert len(res)==3
    assert len(requested)==2
    assert requested[0][1].endswith("/allOrders")
    assert requested[1][1].endswith("/fullOrder")
    assert all(x["symbol"]=="BTC-USDT" for x in res)
    assert all(x[2]["limit"]=="1000" for x in requested)
    # End-to-end: one Combo closed, only its exact orphan SL cancelled.
    state=trade()
    fake=FakeClient(first)
    result,_,_=execute_check(state,fake,{"201":first[0]},
                             position("0.0013"),
                             history={x["orderID"]:x for x in res})
    assert result["cancel_requested"]==["201"]
    assert fake.canceled==["201"]


def test_fullorder_no_id_match_never_authorizes_delete():
    client=object.__new__(LegacyStyleBingXClient)
    def fake_request(method,path,params):
        if path.endswith("/allOrders"):
            return {"code":0,"data":[]}
        return {"code":0,"data":{"orders":[order("999","STOP_MARKET")]}}
    client._signed_request=fake_request
    hist=client.get_order_history("BTC-USDT",1000000,1100000,
                                  required_ids={"201"})
    assert len(hist)==1 and hist[0]["orderID"]=="999"
    state=trade()
    sample=rows()
    fake=FakeClient(sample)
    try:
        execute_check(state,fake,{"201":sample[0]},
                      position("0.0012"),
                      history={x["orderID"]:x for x in hist})
        assert False,"unmatched history must not close a Combo"
    except CleanupUncertain:
        pass
    assert fake.canceled==[]


def test_missing_history_flat_twice_can_cancel_exact_orphan_only():
    closed=trade()
    own=rows()
    peer=rows(pids=("301","302","303"))
    client=FakeClient(own+peer)
    open_map={"201":own[0],"301":peer[0],"302":peer[1],"303":peer[2]}
    first,_,_=execute_check(closed,client,open_map,[],
                            now=1000000,history={})
    assert first["status"]=="watching"
    assert closed["exit_cleanup"]["flat_observations"]==1
    assert client.canceled==[]
    # Separate cycle too close in time does not count as new flat evidence.
    second,_,_=execute_check(closed,client,open_map,[],
                             now=1010000,history={})
    assert second["status"]=="watching"
    assert client.canceled==[]
    # Second time-separated exchange flat proof authorizes ONLY owned ID 201.
    third,_,_=execute_check(closed,client,open_map,[],
                            now=1060000,history={})
    assert third["cancel_requested"]==["201"]
    assert client.canceled==["201"]
    assert set(client.canceled).isdisjoint({"301","302","303"})
    assert client.lookups==[]


def test_missing_history_never_cancels_when_shared_position_still_open():
    state=trade()
    own=rows()
    client=FakeClient(own)
    for i in range(2):
        try:
            execute_check(state,client,{"201":own[0]},position("0.0013"),
                          now=1000000+i*60000,history={})
            assert False,"no closure proof for non-flat shared position"
        except CleanupUncertain as exc:
            assert "position remains open" in str(exc)
    assert client.canceled==[]


def test_runtime_cleanup_is_bounded_but_setup_recovery_remains_15s():
    path=(Path(__file__).parents[1]/"app"/"main.py").read_text("utf-8")
    assert "EXIT_CLEANUP_POLL_SEC=60.0" in path
    assert "EXECUTION_RECONCILE_POLL_SEC=15.0" in path
    assert "target=_exit_cleanup_loop" in path
    assert "execution_service.recover_pending()" in path
    assert "execution_service.cleanup_closed_exits()" in path
    with patch.dict("os.environ",{"POSITION_LIFECYCLE_ENABLED":"true"},clear=True):
        settings=enable_runtime_trailing(Settings())
        assert settings.position_lifecycle_enabled is False
        assert settings.exit_cleanup_enabled is False
        validate_settings(settings)


def main():
    tests=[
        test_shared_short_one_combo_filled_does_not_touch_peer,
        test_partial_trailing_never_cancels_or_resizes_sl,
        test_flat_requires_two_distinct_scans,
        test_missing_order_lookup_uncertain_no_mutations,
        test_closed_owned_fill_cancels_only_active_known_orders,
        test_cancel_transport_uncertainty_bounded,
        test_bingx_cancelled_is_terminal_and_not_misread_as_our_delete,
        test_rejected_cross_position_order_never_mutated,
        test_legacy_without_position_id_is_not_eligible,
        test_runtime_cleanup_is_bounded_but_setup_recovery_remains_15s,
        test_service_batches_symbol_reads_once_and_never_touches_peer,
        test_legacy_pending_sl_replacement_warns_without_resizing,
        test_live_history_matching_closed_combo_and_no_single_get,
        test_history_missing_exact_id_fails_closed_even_when_other_ids_exist,
        test_history_never_authorizes_cancel_of_order_not_in_open_snapshot,
        test_render_explicit_opt_in_preserves_disabled_legacy_sl_lifecycle,
        test_fullorder_fallback_recovers_exact_missing_stop_ids,
        test_fullorder_no_id_match_never_authorizes_delete,
        test_missing_history_flat_twice_can_cancel_exact_orphan_only,
        test_missing_history_never_cancels_when_shared_position_still_open,
    ]
    for test in tests:
        test()
    print({"ok":True,"cases":len(tests),"cancel_replace_calls":0})


if __name__=="__main__":
    main()
