"""Isolated real PostgreSQL integration. Requires dev-only `pip install pgserver`.
No application DATABASE_URL, exchange client, credentials or production data.
"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from contextlib import contextmanager

import pgserver
import psycopg

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'autotrade_v2'),str(ROOT)]
from app.execution.store import ExecutionStore

with TemporaryDirectory(prefix='merged-pg-') as tmp:
    server=pgserver.get_server(Path(tmp)/'pgdata',cleanup_mode='delete')
    uri=server.get_uri()
    try:
        with psycopg.connect(uri) as con:
            version=con.execute('show server_version').fetchone()[0]
            con.execute('''create table processed_targets (
                event_id text not null, target text not null,
                processed_at timestamp default current_timestamp, payload text,
                primary key(event_id,target))''')
        a=ExecutionStore('',database_url=uri)
        b=ExecutionStore('',database_url=uri)
        state={'intent':{'event_id':'C7','symbol':'BTC-USDT','side':'SELL'},
               'terminal':False,'stage':'protection_sl_reconcile_pending',
               'executed_qty':'0.0013','position_id':'9001',
               'protection_journal':{'sl':{'phase':'prepared','spec':{'quantity':'0.0013'}}},
               'recovery':{'attempts':3,'next_at':12345}}
        # WAL preparation is visible through an independent connection/restart.
        with a.trading_lock() as held:
            assert held
            a.save_state('C7',state)
            assert b.get_state('C7')==state
            outcomes=[]
            def rival():
                with b.trading_lock() as acquired:
                    outcomes.append(acquired)
                    if acquired:
                        b.save_state('SHOULD-NOT-EXIST',{})
            t=threading.Thread(target=rival);t.start();t.join(timeout=5)
            assert not t.is_alive() and outcomes==[False]
            assert b.get_state('SHOULD-NOT-EXIST') is None
        # Exclusion releases after normal completion and on exceptions.
        with b.trading_lock() as acquired:
            assert acquired
        try:
            with a.trading_lock() as acquired:
                assert acquired
                raise RuntimeError('simulated worker error')
        except RuntimeError:
            pass
        with b.trading_lock() as acquired:
            assert acquired
        restarted=ExecutionStore('',database_url=uri)
        assert restarted.get_state('C7')==state
        a.mark('C7',{'original':True});b.mark('C7',{'original':False})
        assert a.get('C7','bingx_account')=={'original':True}
        # The database is an isolated temporary server created above. Exercise
        # the same full engine schedules on PostgreSQL, not only store methods.
        with psycopg.connect(uri) as con:
            con.execute('TRUNCATE processed_targets')
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'autotrade_v2/tests'))
        from dataclasses import replace
        from app.config import Settings
        from app.execution.service import ExecutionService
        from merged_entry_test import Exchange,signal,state_ids
        service=ExecutionService(replace(Settings(),execution_enabled=True,dry_run=False,
            tp_trailing_split_enabled=True,bingx_api_key='offline',bingx_api_secret='offline'),a)
        service.credentials_verified=True
        exchange=Exchange();service.executor.client=exchange
        first,second=signal(),signal(7)
        assert service.execute(first).status=='complete'
        first_ids=state_ids(a,first)
        exchange.on_entry=lambda:exchange.fill(first_ids['tp'])
        assert service.execute(second).status=='complete'
        second_ids=state_ids(a,second)
        assert set(first_ids.values()).isdisjoint(second_ids.values())
        exchange.fill(first_ids['trailing'])
        service.cleanup_closed_exits()
        assert ('cancel',first_ids['sl']) in exchange.calls
        assert all(exchange.orders[oid]['status']=='NEW' for oid in second_ids.values())
        restarted=ExecutionService(service.settings,ExecutionStore('',database_url=uri))
        restarted.credentials_verified=True;restarted.executor.client=exchange
        assert restarted.execute(second).status=='duplicate_skipped'
        assert len(exchange.clients)==2
        # Exchange is now flat (for example: a user manually closed BTC C7).
        # Old execution flags must not lock a new independent entry. Persist the
        # boundary across new Postgres sessions and reject half-committed epochs.
        with psycopg.connect(uri) as con:
            con.execute('TRUNCATE processed_targets')  # isolated test server
        flat_old,flat_new=signal(),signal(7)
        ex=Exchange();service.executor.client=ex
        assert service.execute(flat_old).status=='complete'
        old=a.get_state(flat_old.event_id)
        old.update({'terminal':False,'stage':'position_identification_pending',
                    'safety_status':'RECONCILE_REQUIRED',
                    'recovery':{'attempts':8,'manual_required':True}})
        a.save_state(flat_old.event_id,old)
        ex.amount=__import__('decimal').Decimal('0')
        for row in ex.orders.values():
            if row.get('type') in {'STOP_MARKET','TAKE_PROFIT_MARKET','TRAILING_STOP_MARKET'}:
                if row.get('status')=='NEW':
                    row['status']='CANCELLED'
        assert service.execute(flat_new).status=='complete'
        historical=ExecutionStore('',database_url=uri).get_state(flat_old.event_id)
        assert historical['terminal'] and historical['exchange_flat_reconciled']
        assert historical['exchange_flat_reconciled']['previous_safety_status']=='RECONCILE_REQUIRED'
        try:
            a.save_states_atomic([
                (flat_old.event_id,{'stage':'corrupted-partial-epoch'}),
                ('no-such-event',{'stage':'should-fail'}),
            ])
            raise AssertionError('missing history row must cause atomic rollback')
        except RuntimeError as exc:
            if str(exc)=='missing history row must cause atomic rollback':
                raise
            assert 'state missing' in str(exc)
        historical=ExecutionStore('',database_url=uri).get_state(flat_old.event_id)
        assert historical['exchange_flat_reconciled']
        assert historical['stage']!='corrupted-partial-epoch'
        print('PASS PostgreSQL: 2-snapshot exchange-flat history, durable epoch and rollback')

        from protection_retry_abort_test import FailureExchange
        for kind in ('sl','tp','trailing'):
            with psycopg.connect(uri) as con:
                con.execute('TRUNCATE processed_targets')  # isolated test server only
            failed=FailureExchange(kind,2);service.executor.client=failed
            result=service.execute(signal())
            assert result.status=='protection_setup_aborted_closed',result
            assert failed.close_count==1 and failed.amount==0
            state=a.get_state(signal().event_id)
            assert state['protection_attempts'][kind]['posts']==2
            assert state['abort']['phase']=='closed' and state['terminal']
        from force_close_test import schedules,start,resume,assert_closed
        @contextmanager
        def factory():
            with psycopg.connect(uri) as con:
                con.execute('TRUNCATE processed_targets')  # disposable server only
            store=ExecutionStore('',database_url=uri)
            worker=ExecutionService(service.settings,store)
            worker.credentials_verified=True
            yield worker,store
        count=schedules(factory)
        print(f'PASS PostgreSQL: {count} whole-container Force Close fault schedules')
        # Two deployed services contend on a real database session, followed
        # by terminating the owner session at a live cancellation boundary.
        with factory() as (worker,store):
            ex,intents=start(worker,store,kind='tp')
            rival=ExecutionService(service.settings,ExecutionStore('',database_url=uri))
            rival.credentials_verified=True;rival.executor.client=ex
            original=ex.cancel_order;observed=[]
            def lose_session(symbol,order_id=None,**kwargs):
                observed.append(rival.execute(intents[-1]).status)
                assert rival.cleanup_closed_exits()==[]
                value=original(symbol,order_id,**kwargs)
                pid=store._session.connection.info.backend_pid
                with psycopg.connect(uri) as killer:
                    killer.execute('SELECT pg_terminate_backend(%s)',(pid,))
                return value
            ex.cancel_order=lose_session
            try:
                worker.execute(intents[-1])
                raise AssertionError('terminated owner must fail closed')
            except psycopg.Error:
                pass
            assert observed==['execution_lock_busy'] and ex.close_count==0
            assert len([x for x in ex.calls if x[0]=='cancel'])==1
            ex.cancel_order=original
            assert rival.execute(intents[-1],recovery_only=True).status=='force_close_confirmed'
            assert_closed(rival,rival.store,ex,intents)
        print('PASS PostgreSQL: second deployment exclusion, session termination fences subsequent mutations, new owner resumes same epoch')
        print(f'PASS PostgreSQL {version}: independent-session exclusion, exception release, WAL restart recovery, immutable historical mark, full same-side entry/TP-overlap/owned-cleanup engine schedule; retry-once/abort-close for SL, TP and trailing')
    finally:
        server.cleanup()
