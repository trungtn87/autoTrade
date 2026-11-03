"""Lifecycle/restart/disconnect integration on the isolated synthetic PG runner."""
import json
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError
from autotrade_v2.analytics.writer import DatabaseSource, LifecycleWriter, LifecycleWriterRuntime
from autotrade_v2.tests.lifecycle_fixtures import opened, exited


def verify_lifecycle(engine):
    with engine.begin() as conn:
        conn.execute(text('''CREATE TABLE public.processed_targets (
            event_id text NOT NULL, target text NOT NULL, payload text,
            processed_at timestamp DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(event_id,target))'''))

    def save(eid, state):
        with engine.begin() as conn:
            conn.execute(text('''INSERT INTO public.processed_targets(event_id,target,payload)
                VALUES (:eid,'bingx_state',:payload) ON CONFLICT(event_id,target)
                DO UPDATE SET payload=excluded.payload'''), {'eid':eid,'payload':json.dumps(state)})

    def current(eid):
        return DatabaseSource(engine).current(eid)

    def receipts(eid):
        with engine.connect() as conn:
            return conn.execute(text('''SELECT revision,snapshot FROM autotrade_analytics.trade_result_receipts
                WHERE event_id=:eid ORDER BY revision'''), {'eid':eid}).all()

    eid='cycle-1'
    state=opened(eid,terminal=True)
    state['intent'].update(tp=9999,sl=0.1)
    state.update(pnl=999,margin=1,execution_leverage=100)
    save(eid,state)
    runtime=LifecycleWriterRuntime(enabled=True)
    writer=LifecycleWriter(engine)
    assert runtime.run_page(writer)['inserted']==1
    assert current(eid)[0]==1 and current(eid)[1]['status']=='OPEN'
    assert all(current(eid)[1][key] is None for key in ('pnl','fees','margin','roi','outcome'))

    partial=exited(state,'tp','1')
    save(eid,partial)
    runtime._cursor=''
    killed=[False]
    def kill_before_receipt(conn,cursor,statement,parameters,context,executemany):
        if not killed[0] and 'INSERT INTO autotrade_analytics.trade_result_receipts' in statement:
            killed[0]=True
            pid=conn.connection.driver_connection.info.backend_pid
            with engine.connect() as killer:
                assert killer.execute(text('SELECT pg_terminate_backend(:pid)'), {'pid':pid}).scalar()
    event.listen(engine,'before_cursor_execute',kill_before_receipt)
    try:
        try:
            runtime.run_page(writer)
        except SQLAlchemyError:
            pass
        else:
            raise AssertionError('Killed analytics transaction did not fail')
    finally:
        event.remove(engine,'before_cursor_execute',kill_before_receipt)
    assert killed[0] and current(eid)[0]==1 and len(receipts(eid))==1
    # Restart with no in-memory queue/cursor; source still has the TP evidence.
    runtime=LifecycleWriterRuntime(enabled=True)
    writer=LifecycleWriter(engine)
    assert runtime.run_page(writer)['updated']==1
    assert current(eid)[0]==2 and current(eid)[1]['status']=='PARTIAL'
    runtime._cursor=''
    assert runtime.run_page(writer)['unchanged']==1 and len(receipts(eid))==2

    full=exited(partial,'trailing','1',time_ms=3000,triggered=True)
    save(eid,full)
    runtime=LifecycleWriterRuntime(enabled=True)
    assert runtime.run_page(LifecycleWriter(engine))['updated']==1
    assert current(eid)[0]==3 and current(eid)[1]['status']=='CLOSED'
    assert current(eid)[1]['exit_types']==['TP','TRAILING']
    assert [r.snapshot['status'] for r in receipts(eid)]==['OPEN','PARTIAL','CLOSED']
    with engine.connect() as conn:
        assert json.loads(conn.execute(text("SELECT payload FROM public.processed_targets WHERE event_id='cycle-1'" )).scalar())==full
    # SL and TP->SL: one current row per intent, not one completed row per exit.
    for label,kinds in (('cycle-sl',('sl',)),('cycle-mixed',('tp','sl'))):
        item=opened(label)
        for kind in kinds:
            item=exited(item,kind,'2' if len(kinds)==1 else '1')
        save(label,item)
    runtime=LifecycleWriterRuntime(enabled=True)
    runtime.run_page(LifecycleWriter(engine))
    for label in ('cycle-sl','cycle-mixed'):
        assert current(label)[1]['status']=='CLOSED' and len(receipts(label))==1
    # Schema missing => visible failure, no auto-DDL or false zero report.
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE autotrade_analytics.trade_results RENAME TO lifecycle_results_unavailable'))
    try:
        try:
            LifecycleWriterRuntime(enabled=True).run_page(LifecycleWriter(engine))
        except SQLAlchemyError:
            pass
        else:
            raise AssertionError('Missing result schema was silently accepted')
    finally:
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE autotrade_analytics.lifecycle_results_unavailable RENAME TO trade_results'))
    runtime=LifecycleWriterRuntime(enabled=True)
    assert runtime.run_page(LifecycleWriter(engine))['unchanged']==3
    # Ownership collisions are checked beyond the selected page. CLOSED must
    # remain intact; the contradictory source is a degraded record, no downgrade.
    peer=opened('zzz-peer')
    peer['order_id']=state['order_id']
    save('zzz-peer',peer)
    assert state['order_id'] in DatabaseSource(engine).ambiguous([(eid,full)])
    before=current(eid),receipts(eid)
    runtime=LifecycleWriterRuntime(enabled=True)
    assert runtime.run_page(LifecycleWriter(engine))['failed']>=1
    assert (current(eid),receipts(eid))==before
    # Malformed legacy state cannot starve a later valid intent.
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO public.processed_targets VALUES ('000-poison','bingx_state','{broken',now())"))
        conn.execute(text("DELETE FROM public.processed_targets WHERE event_id='zzz-peer'"))
    save('zzz-valid',opened('zzz-valid'))
    runtime=LifecycleWriterRuntime(enabled=True)
    counts=runtime.run_page(LifecycleWriter(engine))
    assert counts['failed']==1 and current('zzz-valid')[1]['status']=='OPEN'
    print('PASS: lifecycle OPEN/PARTIAL/CLOSED, terminal recovery, exact TP+trailing and SL, real backend disconnect atomic rollback/retry, schema recovery, cross-page ownership, poison isolation, zero provider/order calls')
