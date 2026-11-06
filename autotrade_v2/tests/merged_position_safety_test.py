"""Offline merged-container safety regressions. No BingX traffic."""
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.execution.identity import entry_baseline, position_id, identify_entry_position
from app.execution.merged import reconcile_side, entry_proof
from app.execution.exit_cleanup import CleanupUncertain, inspect_one
from app.execution.quantity import PositionQuantityMismatch
from app.execution.store import ExecutionStore
from app.execution.service import ExecutionService
from app.config import Settings
from app.execution.final14_executor import Final14Executor
from types import SimpleNamespace
from exit_cleanup_test import trade, rows, position, order, FakeClient, execute_check


def assert_raises(kind, fn):
    try:
        fn()
    except kind:
        return
    raise AssertionError(f"expected {kind}")


def ledger(first, second, sample, qty):
    opened={x['orderID']:x for x in sample if x['status'] in {'NEW','PARTIALLY_FILLED'}}
    history={x['orderID']:x for x in sample}
    states=[('C16',first),('C7',second)]
    return reconcile_side(states,position(qty),opened,history),opened,history


def main():
    # 1, 6: container ID is shared; orders remain separate owned IDs.
    c16=trade(combo=16)
    c7=trade(combo=7,pids=('301','302','303'))
    report,opened,hist=ledger(c16,c7,rows()+rows(pids=('301','302','303')),'0.0024')
    assert report['tracked_qty']=='0.0024'
    assert report['shared']
    assert report['risks']==['EXECUTABLE_EXIT_BUDGET_EXCEEDED']
    # Never silently assume conditional exits are OCO.
    assert report['executable_exit_qty']=='0.0048'

    # 2, 13: own TP fill during peer entry; only the owned fill reduces C16.
    partial=rows(tp='0.0006',tp_status='FILLED')
    report,opened,hist=ledger(c16,c7,partial+rows(pids=('301','302','303')),'0.0018')
    assert report['combos']['C16']['remaining_qty']=='0.0006'
    assert report['combos']['C7']['remaining_qty']=='0.0012'
    assert 'C16:SL_QUANTITY_MISMATCH' in report['risks']
    assert 'POSITION_LEDGER_MISMATCH' not in report['risks']

    # 3, 4: two exact owned fills close C16; cancel only its SL, never C7.
    finished=rows(tp='0.0006',tp_status='FILLED',trailing='0.0006',trailing_status='FILLED')
    sample=finished+rows(pids=('301','302','303'))
    report,opened,hist=ledger(c16,c7,sample,'0.0012')
    assert report['combos']['C16']['remaining_qty']=='0.0000'
    client=FakeClient(sample)
    result,_,_=execute_check(deepcopy(c16),client,opened,position('0.0012'),history=hist)
    assert result['cancel_requested']==['201']
    assert client.canceled==['201']

    # 5, 15: both trailing exits settle; only two proven orphan SLs can cancel.
    finished=rows(tp='0.0006',tp_status='FILLED',trailing='0.0006',trailing_status='FILLED')
    sample=finished+rows(pids=('301','302','303'),tp='0.0006',tp_status='FILLED',trailing='0.0006',trailing_status='FILLED')
    report,opened,hist=ledger(c16,c7,sample,'0')
    assert all(float(x['remaining_qty'])==0 for x in report['combos'].values())
    client=FakeClient(sample)
    for state in (deepcopy(c16),deepcopy(c7)):
        execute_check(state,client,opened,[],history=hist)
    assert set(client.canceled)=={'201','301'}

    # 7: repeated missing/stale position snapshots cannot prove own closure.
    state=deepcopy(c16); client=FakeClient(rows())
    for now in (1000000,1060000,1120000):
        result,_,_=execute_check(state,client,{'201':rows()[0]},[],now=now,history={})
        assert not result['cancel_requested']
    assert not client.canceled

    # 10: manually canceled SL is risk, never healthy COMPLETE.
    canceled=rows(sl_status='CANCELED')+rows(pids=('301','302','303'))
    report,_,_=ledger(c16,c7,canceled,'0.0024')
    assert 'C16:NO_VERIFIED_ACTIVE_SL' in report['risks']
    assert report['status']=='RECONCILE_REQUIRED'

    # 11: a manual position decrease cannot be assigned to either combo.
    report,_,_=ledger(c16,c7,rows()+rows(pids=('301','302','303')),'0.0020')
    assert 'POSITION_LEDGER_MISMATCH' in report['risks']
    assert report['combos']['C16']['remaining_qty']=='0.0012'
    assert report['combos']['C7']['remaining_qty']=='0.0012'

    # 12: wrong side/type, shared order IDs, missing/duplicate history.
    for field,value in [('positionSide','LONG'),('type','MARKET'),('side','SELL')]:
        bad=rows();bad[0][field]=value
        assert_raises(CleanupUncertain,lambda:ledger(c16,c7,bad+rows(pids=('301','302','303')),'0.0024'))
    assert_raises(CleanupUncertain,lambda:ledger(c16,c16,rows(),'0.0024'))
    sample=rows()+rows(pids=('301','302','303'))
    report,opened,hist=ledger(c16,c7,sample,'0.0024')
    del opened['202']; del hist['202']
    missing=reconcile_side([('C16',c16),('C7',c7)],position('0.0024'),opened,hist)
    assert 'C16:EXIT_HISTORY_UNVERIFIED' in missing['risks']

    # Entry order evidence, rather than average price, proves the fill identity.
    entry={'orderID':'500','clientOrderId':'event-client','symbol':'BTC-USDT',
           'side':'SELL','positionSide':'SHORT','type':'MARKET','status':'FILLED',
           'origQty':'0.0012','executedQty':'0.0012','positionID':'9001'}
    qty,pid=entry_proof(c16,entry,'event-client')
    assert str(qty)=='0.0012' and pid=='9001'
    for field,value in [('positionSide','LONG'),('type','STOP_MARKET'),('clientOrderId','other')]:
        assert_raises(CleanupUncertain,lambda:entry_proof(c16,{**entry,field:value},'event-client'))
    assert entry_baseline([{'symbol':'BTC-USDT','positionSide':'SHORT','positionID':'9001','positionAmt':'0.0012'}],'BTC-USDT','SELL')=={'9001':'0.0012'}
    assert_raises(PositionQuantityMismatch,lambda:position_id({'positionID':'1','positionId':'2'}))

    # Single-order GET may return an unrelated order with HTTP 200; never adopt it.
    ex=object.__new__(Final14Executor)
    ex.client=SimpleNamespace(extract_order=lambda raw:raw['data']['order'],
        get_order_detail=lambda *a,**k:{'data':{'order':{'orderID':'999','status':'NEW'}}})
    assert_raises(RuntimeError,lambda:ex._verify_protection('BTC-USDT',
        {'data':{'order':{'orderID':'201'}}},'stop-loss','9001'))
    ex.client.get_order_detail=lambda *a,**k:{'data':{'order':{'orderID':'201','status':'NEW','positionID':'999'}}}
    assert_raises(RuntimeError,lambda:ex._verify_protection('BTC-USDT',
        {'data':{'order':{'orderID':'201'}}},'stop-loss','9001'))

    # Exact incident: C16 0.0013, C7 0.0013; C16 TP 0.0007 overlaps entry.
    # Old delta method sees +0.0006 instead of +0.0013 and refuses identity.
    assert_raises(PositionQuantityMismatch,lambda:identify_entry_position(
        position('0.0019'),'BTC-USDT','SELL','0.0013',{'9001':'0.0013'}))
    # 14: independent service/store instances cannot hold the mutation lock together.
    with TemporaryDirectory() as tmp:
        a=ExecutionStore(str(Path(tmp)/'state.db'));b=ExecutionStore(str(Path(tmp)/'state.db'))
        with a.trading_lock() as held:
            assert held
            with b.trading_lock() as peer:
                assert not peer
        with b.trading_lock() as peer:
            assert peer
    with TemporaryDirectory() as tmp:
        store=ExecutionStore(str(Path(tmp)/'guard.db'))
        store.save_state('C16',c16)
        service=ExecutionService(replace(Settings(),execution_enabled=True,dry_run=False),store)
        service.credentials_verified=True
        class NoExchange:
            def __getattr__(self,name):
                raise AssertionError(f"unexpected exchange call: {name}")
        service.executor.client=NoExchange()
        pending=service._intent_from_state(c7)
        blocked=service.execute(pending)
        assert blocked.status=='position_preflight_blocked' and not blocked.accepted
        assert store.get_state(pending.event_id) is None
        store.save_state('C16',{**c16,'terminal':False,'recovery':{'attempts':8}})
        assert service.recover_pending()==[],"exhaustion survives restart without another method retry"
        store.save_state('C16',{**c16,'terminal':False,'recovery':{'attempts':1,'next_at':10**12}})
        assert service.recover_pending()==[],"durable backoff prevents immediate retry"
    print({'ok':True,'shared_ledger':True,'concurrent_tp_trailing':True,
           'exact_cleanup_only':True,'manual_mutation_blocks':True,
           'missing_history_blocks':True,'budget_overcommit_blocks':True,
           'cross_store_lock':True,'incident_delta_reproduced':True})


if __name__=='__main__':
    main()
