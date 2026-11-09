"""Regression tests: exchange-flat histories cannot permanently disable trading.

Offline exchange fake. Never creates actual BingX orders.
Run: PYTHONPATH=autotrade_v2:autotrade_v2/tests python autotrade_v2/tests/exchange_flat_reconciliation_test.py
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from app.execution.store import ExecutionStore
from merged_entry_test import setup, signal, state_ids


def remove_all_exposure(exchange):
    exchange.amount=Decimal("0")
    for row in exchange.orders.values():
        if row.get("type") in {
            "STOP_MARKET","TAKE_PROFIT_MARKET","TRAILING_STOP_MARKET"
        } and row["status"] in {"NEW","PARTIALLY_FILLED"}:
            row["status"]="CANCELLED"


def test_closed_manually_with_old_history_still_admits_fresh_entry():
    with TemporaryDirectory() as tmp:
        service,store,exchange=setup(tmp)
        a,b=signal(),signal(7)
        assert service.execute(a).status=="complete"
        old_protection=set(state_ids(store,a).values())
        remove_all_exposure(exchange)
        old=store.get_state(a.event_id)
        old["stage"]="protection_reconcile_required"
        old["safety_status"]="RECONCILE_REQUIRED"
        old["exit_cleanup"]={"status":"watching","last_inspection_error":"missing old TP history"}
        store.save_state(a.event_id,old)

        events=[]
        service.event_cb=lambda key,sev,message,**k:events.append((key,sev,k))
        results=service.cleanup_closed_exits()
        assert len(results)==1 and results[0]["status"]=="EXCHANGE_FLAT_RECONCILED",results
        persisted=store.get_state(a.event_id)
        assert persisted["exchange_flat_reconciled"]["status"]=="EXCHANGE_FLAT_RECONCILED"
        assert persisted["historical_reconcile_pending"]
        assert persisted["exit_cleanup"]["last_inspection_error"]=="missing old TP history"
        assert persisted["exchange_flat_reconciled"]["previous_safety_status"]=="RECONCILE_REQUIRED"
        assert "safety_status" not in persisted
        assert any(x[0]=="L3.EXEC.EXCHANGE_FLAT_RECONCILED" and x[1]=="WARNING" for x in events)

        assert service.execute(b).status=="complete"
        new=store.get_state(b.event_id)
        assert not new.get("exchange_flat_reconciled")
        assert set(state_ids(store,b).values()).isdisjoint(old_protection)
        assert all(exchange.orders[x]["status"]=="CANCELLED" for x in old_protection)
        assert not any(x[0]=="cancel" for x in exchange.calls)
        assert exchange.amount==Decimal("0.0013")
        # Flat retirement cannot allow a replay of the original old event,
        # even when the old bingx_account idempotency marker was never written.
        count=len(exchange.clients)
        replay=service.execute(a)
        assert replay.status=="historical_flat_skipped" and not replay.accepted
        assert len(exchange.clients)==count


def test_btc_exhausted_entry_recovers_when_exchange_is_flat():
    with TemporaryDirectory() as tmp:
        service,store,exchange=setup(tmp)
        old,new=signal(),signal(7)
        assert service.execute(old).status=="complete"
        remove_all_exposure(exchange)
        saved=store.get_state(old.event_id)
        saved.update({
            "stage":"position_identification_pending",
            "terminal":False,
            "safety_status":"RECONCILE_REQUIRED",
            "recovery":{"attempts":8,"manual_required":True},
        })
        store.save_state(old.event_id,saved)
        assert service.execute(new).status=="complete"
        past=store.get_state(old.event_id)
        assert past["terminal"] is True
        assert past["stage"]=="exchange_flat_reconciled"
        assert past["recovery"]["manual_required"] is True
        assert past["exchange_flat_reconciled"]["previous_stage"]=="position_identification_pending"


def test_zero_position_with_open_sl_must_still_block():
    with TemporaryDirectory() as tmp:
        service,store,exchange=setup(tmp)
        old,new=signal(),signal(7)
        assert service.execute(old).status=="complete"
        exchange.amount=Decimal("0")  # Orphan protective orders remain NEW.
        result=service.execute(new)
        assert not result.accepted
        assert result.status in {"merged_reconcile_required","position_preflight_blocked"},result
        assert not store.get_state(old.event_id).get("exchange_flat_reconciled")
        assert len(exchange.clients)==1


def test_unknown_inflight_entry_must_not_be_flattened():
    with TemporaryDirectory() as tmp:
        service,store,exchange=setup(tmp)
        old,new=signal(),signal(7)
        store.save_state(old.event_id,{
            "intent":asdict(old),"stage":"entry_intent","terminal":False,
            "entry_filled":False,"client_order_id":"unknown-post",
        })
        result=service.execute(new)
        assert not result.accepted
        assert result.status in {"merged_reconcile_required","position_preflight_blocked"},result
        assert not store.get_state(old.event_id).get("exchange_flat_reconciled")
        assert not exchange.clients


def test_two_snapshot_race_must_not_clear_history():
    with TemporaryDirectory() as tmp:
        service,store,exchange=setup(tmp)
        old,new=signal(),signal(7)
        assert service.execute(old).status=="complete"
        remove_all_exposure(exchange)
        previous=exchange.get_positions
        checks=[0]
        def oscillating(symbol):
            checks[0]+=1
            if checks[0]==2:
                exchange.amount=Decimal("0.0013")
            return previous(symbol)
        exchange.get_positions=oscillating
        result=service.execute(new)
        assert not result.accepted,result
        assert checks[0]>=2
        assert not store.get_state(old.event_id).get("exchange_flat_reconciled")


def test_reconcile_incident_alert_once_per_hour():
    with TemporaryDirectory() as tmp:
        service,store,exchange=setup(tmp)
        old=signal()
        assert service.execute(old).status=="complete"
        saved=store.get_state(old.event_id)
        saved["safety_status"]="RECONCILE_REQUIRED"
        saved["stage"]="protection_reconcile_required"
        store.save_state(old.event_id,saved)
        events=[]
        service.event_cb=lambda key,sev,msg,**kw:events.append((key,sev,kw))
        service.cleanup_closed_exits()
        service._last_reconciliation_check["BTC-USDT|SELL"]-=301
        service.cleanup_closed_exits()
        stuck=[x for x in events if x[0]=="L3.EXEC.RECONCILIATION_STUCK"]
        assert len(stuck)==1,stuck
        assert stuck[0][1]=="CRITICAL"


def test_atomic_history_marker_rolls_back_on_missing_state():
    with TemporaryDirectory() as tmp:
        store=ExecutionStore(str(Path(tmp)/"state.db"))
        store.save_state("present",{"stage":"complete"})
        try:
            store.save_states_atomic([
                ("present",{"stage":"exchange_flat_reconciled"}),
                ("missing",{"stage":"exchange_flat_reconciled"}),
            ])
        except RuntimeError as exc:
            assert "state missing" in str(exc)
        else:
            raise AssertionError("expected failed all-or-nothing update")
        assert store.get_state("present")["stage"]=="complete"


def main():
    test_closed_manually_with_old_history_still_admits_fresh_entry()
    test_btc_exhausted_entry_recovers_when_exchange_is_flat()
    test_zero_position_with_open_sl_must_still_block()
    test_unknown_inflight_entry_must_not_be_flattened()
    test_two_snapshot_race_must_not_clear_history()
    test_reconcile_incident_alert_once_per_hour()
    test_atomic_history_marker_rolls_back_on_missing_state()
    print({"ok":True,"exchange_flat_unblocks":True,"old_history_preserved":True,
           "existing_sl_blocks":True,"unknown_post_blocks":True,
           "two_snapshot_required":True,"recovery_notification":True,
           "stuck_alert_throttled":True,"atomic_state_update":True})


if __name__=="__main__":
    main()
