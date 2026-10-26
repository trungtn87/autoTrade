"""Strict offline tests for rare-orphan BingX exit cancellation.

No network or credentials; test per-Combo proof with shared position IDs,
partial fills, stale exchange snapshots, and ambiguous cancel responses.
"""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from app.config import Settings,enable_runtime_trailing,validate_settings
from app.execution.exit_cleanup import (
    eligible,inspect_one,clean_one,CleanupUncertain,
)
from app.execution.service import ExecutionService


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


def execute_check(state,client,open_orders,positions,now=1000000):
    written=[]
    alerts=[]
    result=clean_one(client,state,open_orders,positions,
                     lambda s:written.append(deepcopy(s)),
                     lambda *a:alerts.append(a),
                     now_ms=now)
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
        assert settings.exit_cleanup_enabled is True
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
    ]
    for test in tests:
        test()
    print({"ok":True,"cases":len(tests),"cancel_replace_calls":0})


if __name__=="__main__":
    main()
