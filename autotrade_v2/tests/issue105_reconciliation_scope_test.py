"""Issue #105: live C4 trailing GET parsing fault must not freeze safe peers.

Offline simulation with the actual execution service and BingX-style order
rows; uses confirmed C4 trailing quantities and empty historical GET fields.
No exchange credentials, sockets or production mutations.
"""
from __future__ import annotations

from decimal import Decimal
from tempfile import TemporaryDirectory

from app.contracts import TradeIntent
from merged_entry_test import Exchange, setup, state_ids


def signal(combo):
    name=f"C{combo-90}" if combo>=101 else f"C{combo}"
    return TradeIntent(
        event_id=f"ETH-USDT|1h|{name}|SELL|{1794542399999+combo}",
        symbol="ETH-USDT", combo=combo, side="SELL", timeframe="1h",
        close_time=1794542399999+combo, entry=1933.24,
        tp=1900.0, sl=1950.0, smc_dir=-1,
    )


def eth_setup(tmp):
    service,store,_=setup(tmp)
    # Same symbol, same SHORT container as the actual 13 Nov incident.
    exchange=Exchange(side="SELL",prices=("1933.24","1930.0"))
    service.executor.client=exchange
    return service,store,exchange


def incident(service, store, exchange):
    """Reconstruct C4's *existing* acknowledged trailing from persisted WAL.

    GET has a valid, active exact-id order whose trailing metadata is blank.
    The state deliberately lacks VERIFIED trailing. It must remain pending
    and must never result in a replacement conditional POST.
    """
    c4=signal(4)
    first=service.execute(c4)
    assert first.status=="complete",first
    saved=store.get_state(c4.event_id)
    ids=state_ids(store,c4)
    trail=exchange.orders[ids["trailing"]]
    rate=trail["priceRate"]
    activation=trail["activationPrice"]
    assert Decimal(trail["origQty"])>0
    saved["protection"].pop("trailing")
    saved["protection_journal"]={"trailing":{
        "phase":"prepared","baseline_ids":[],
        "acknowledged_order_id":ids["trailing"],
        "spec":{
            "kind":"trailing","symbol":c4.symbol,"side":"BUY",
            "position_side":"SHORT","position_id":saved["position_id"],
            "quantity":trail["origQty"],"trigger":activation,
            "callback_rate":rate,
        },
    }}
    saved.update({
        "terminal":False,"stage":"protection_reconcile_required",
        "safety_status":"RECONCILE_REQUIRED",
        "recovery":{"attempts":3,"next_at":999999999999},
    })
    store.save_state(c4.event_id,saved)
    trail.pop("activationPrice")
    trail["actPrice"]=""
    trail["priceRate"]=""
    return c4,ids


def check_structural_warning_is_not_full_verification():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        c4,ids=incident(service,store,exchange)
        c13,c15=signal(103),signal(105)
        before,report=service._entry_admission(c13)
        assert before,report
        assert report["blocking_risks"]==[],report
        assert report["status"]=="ADMISSION_SAFE_TRAILING_PARAMS_UNVERIFIED",report
        assert f"{c4.event_id}:TRAILING_PARAMS_UNVERIFIED" in report["warnings"],report
        assert store.get_state(c4.event_id)["protection"].get("trailing") is None
        assert store.get_state(c4.event_id)["protection_journal"]["trailing"]["acknowledged_order_id"]==ids["trailing"]
        assert service.execute(c13).status=="complete"
        assert service.execute(c15).status=="complete"
        assert len(exchange.clients)==3
        assert sum(kind=="trailing" for kind,oid in exchange.calls)==3,"no repost of C4 trailing"
        assert set(state_ids(store,c13).values()).isdisjoint(set(ids.values()))
        assert set(state_ids(store,c15).values()).isdisjoint(set(ids.values()))
        service.cleanup_closed_exits()
        old=store.get_state(c4.event_id)
        assert old["terminal"] is False and old["protection"].get("trailing") is None,old
        assert old["safety_status"]=="TRAILING_PARAMS_UNVERIFIED",old
        assert old["stage"]=="trailing_parameters_unverified",old
        assert store.get_state(c13.event_id)["stage"]=="complete","healthy peers must not be relabelled"
        assert store.get_state(c15.event_id)["stage"]=="complete","healthy peers must not be relabelled"


def check_real_sl_loss_still_blocks():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        _,ids=incident(service,store,exchange)
        exchange.orders[ids["sl"]]["status"]="CANCELLED"
        newer=signal(103)
        result=service.execute(newer)
        assert result.status in {"merged_reconcile_required","position_preflight_blocked"},result
        assert not result.accepted and len(exchange.clients)==1
        assert not store.get_state(newer.event_id)


def check_wrong_trailing_quantity_still_blocks():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        _,ids=incident(service,store,exchange)
        exchange.orders[ids["trailing"]]["origQty"]="0.1234"
        newer=signal(103)
        result=service.execute(newer)
        assert not result.accepted and result.status=="merged_reconcile_required",result
        assert len(exchange.clients)==1


def check_wrong_trailing_identity_still_blocks():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        _,ids=incident(service,store,exchange)
        exchange.orders[ids["trailing"]]["positionID"]="9999"
        newer=signal(103)
        result=service.execute(newer)
        assert not result.accepted and result.status in {"merged_reconcile_required","position_preflight_blocked"},result
        assert len(exchange.clients)==1


def check_trailing_explicit_price_mismatch_still_blocks():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        _,ids=incident(service,store,exchange)
        exchange.orders[ids["trailing"]]["activationPrice"]="999999"
        newer=signal(103)
        result=service.execute(newer)
        assert not result.accepted and result.status=="merged_reconcile_required",result
        assert len(exchange.clients)==1


def check_conflicting_actprice_alias_still_blocks():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        _,ids=incident(service,store,exchange)
        exchange.orders[ids["trailing"]]["actPrice"]="999999"
        newer=signal(103)
        result=service.execute(newer)
        assert not result.accepted and result.status=="merged_reconcile_required",result
        assert len(exchange.clients)==1


def check_missing_trailing_order_still_blocks():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        _,ids=incident(service,store,exchange)
        exchange.orders[ids["trailing"]]["status"]="CANCELLED"
        newer=signal(103)
        result=service.execute(newer)
        assert not result.accepted and result.status=="merged_reconcile_required",result
        assert len(exchange.clients)==1


def check_single_combo_recovery_exception_not_account_lock():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        c4,ids=incident(service,store,exchange)
        old=store.get_state(c4.event_id)
        old["recovery"]["next_at"]=0
        store.save_state(c4.event_id,old)
        service.entry_recovery_pending=True
        original_execute=service.execute
        def flaky_recovery(intent,recovery_only=False):
            if recovery_only and intent.event_id==c4.event_id:
                raise ValueError("simulated C4 GET numeric field unreadable")
            return original_execute(intent,recovery_only=recovery_only)
        service.execute=flaky_recovery
        result=service.preflight_and_recover()
        assert result["ok"] and result["credentials_verified"],result
        assert not service.entry_recovery_pending
        assert result["recovery"]==[{"event_id":c4.event_id,"status":"recovery_combo_failed","accepted":True}] or (
            result["recovery"][0]["status"]=="recovery_combo_failed"),result
        service.execute=original_execute
        assert service.execute(signal(103)).status=="complete","C4 parsing error must not globally gate C13"
        assert len(exchange.clients)==2


def check_shared_database_failure_still_fails_closed():
    with TemporaryDirectory() as tmp:
        service,store,exchange=eth_setup(tmp)
        service.entry_recovery_pending=True
        original=store.list_states
        def db_failure():
            raise OSError("simulated unavailable execution database")
        store.list_states=db_failure
        result=service.preflight_and_recover()
        assert result["ok"] is False and result["phase"]=="recovery",result
        assert service.entry_recovery_pending and not service.entry_preflight_ready
        store.list_states=original


def main():
    check_structural_warning_is_not_full_verification()
    check_real_sl_loss_still_blocks()
    check_wrong_trailing_quantity_still_blocks()
    check_wrong_trailing_identity_still_blocks()
    check_trailing_explicit_price_mismatch_still_blocks()
    check_conflicting_actprice_alias_still_blocks()
    check_missing_trailing_order_still_blocks()
    check_single_combo_recovery_exception_not_account_lock()
    check_shared_database_failure_still_fails_closed()
    print({"ok":True,"issue":105,
           "trailing_params_unverified_not_promoted":True,
           "c4_does_not_block_c13_c15_with_live_full_sl":True,
           "missing_sl_and_wrong_trailing_proof_block":True,
           "single_combo_failure_not_global_gate":True,
           "database_outage_fails_closed":True,
           "duplicate_conditional_posts":0})


if __name__=="__main__":
    main()
