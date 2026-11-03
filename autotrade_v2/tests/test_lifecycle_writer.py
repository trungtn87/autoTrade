from copy import deepcopy
from dataclasses import replace
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from sqlalchemy.exc import OperationalError
from autotrade_v2.analytics.contract import InvalidResult
from autotrade_v2.analytics.evidence import capture_entry, order_evidence, MAX_ENTRY_OBSERVATIONS
from autotrade_v2.analytics.projection import project_result, LifecycleEvidenceError
from autotrade_v2.analytics.writer import LifecycleWriter, LifecycleWriterRuntime, DatabaseSource
from autotrade_v2.analytics.ledger import RevisionConflict
from autotrade_v2.tests.lifecycle_fixtures import opened, exited


class LifecycleTests(unittest.TestCase):
    def test_terminal_execution_is_still_an_open_trade(self):
        state = opened(terminal=True)
        result = project_result('lifecycle-1',state)
        self.assertEqual(result['status'],'OPEN')
        self.assertEqual(result['opened_qty'],'2')
        self.assertIsNone(result['closed_ms'])
        self.assertTrue(all(result[k] is None for k in ('pnl','fees','margin','roi','outcome')))

    def test_tp_sl_and_split_exits_count_one_trade(self):
        for kinds in (('tp',),('sl',),('tp','trailing'),('tp','sl')):
            state = opened()
            previous = project_result('lifecycle-1',state)
            for i,kind in enumerate(kinds):
                state = exited(state,kind,'2' if len(kinds)==1 else '1',time_ms=2000+i,
                               triggered=kind=='trailing')
                current = project_result('lifecycle-1',state,previous)
                self.assertEqual(current['status'],'CLOSED' if i==len(kinds)-1 else 'PARTIAL')
                previous = current
            self.assertEqual(current['event_id'],'lifecycle-1')
            self.assertEqual(current['closed_qty'],'2')
            self.assertIsNone(current['outcome'])
            self.assertEqual(project_result('lifecycle-1',state,current),current)

    def test_partial_entry_does_not_pretend_to_be_a_complete_trade(self):
        state = opened(qty='1',entry_status='PARTIALLY_FILLED',terminal=False)
        self.assertEqual(project_result('lifecycle-1',state)['status'],'OPEN')
        state = exited(state,'tp','1')
        self.assertEqual(project_result('lifecycle-1',state)['status'],'UNRESOLVED')
        # Entry later completes: cumulative confirmed snapshots allow completion.
        state = opened()
        state = exited(state,'tp','0.5',status='PARTIALLY_FILLED')
        self.assertEqual(project_result('lifecycle-1',state)['status'],'PARTIAL')

    def test_cumulative_exit_parent_and_trigger_child_are_one_leg(self):
        state=exited(opened(),'tp','0.5',status='PARTIALLY_FILLED')
        previous=project_result('lifecycle-1',state)
        state=exited(state,'tp','1',time_ms=2100,triggered=True)
        current=project_result('lifecycle-1',state,previous)
        self.assertEqual(current['closed_qty'],'1')
        self.assertEqual(len(current['lifecycle_exits']),1)
        state=exited(state,'trailing','1',time_ms=3000)
        final=project_result('lifecycle-1',state,current)
        self.assertEqual((final['status'],final['closed_qty']),('CLOSED','2'))
        missing=deepcopy(state)
        missing['exit_cleanup']['orders']={}
        self.assertEqual(project_result('lifecycle-1',missing,final),final)

    def test_flat_or_cleanup_done_without_fills_cannot_close(self):
        state = opened()
        state['exit_cleanup'] = {'status':'done','flat_observations':2,'orders':{}}
        self.assertEqual(project_result('lifecycle-1',state)['status'],'OPEN')

    def test_missing_actual_time_combo_and_legacy_evidence_stay_unresolved(self):
        for change in ('time','combo','legacy','order_id'):
            state = opened()
            if change=='time': del state['analytics_entry_evidence'][0]['order']['updateTime']
            if change=='combo': state['intent']['combo'] = None
            if change=='legacy': del state['analytics_entry_evidence']
            if change=='order_id': del state['order_id']
            row = project_result('lifecycle-1',state)
            self.assertEqual(row['status'],'UNRESOLVED')
            self.assertIsNone(row['roi'])
        self.assertIsNone(project_result('prepared',{'intent':{}}))

    def test_exact_identity_and_overclosure_required(self):
        for change in ('wrong_order','wrong_symbol','wrong_side','wrong_position','wrong_trigger','entry_client'):
            state = exited(opened(),'tp','2',triggered=True)
            row = state['exit_cleanup']['orders'][state['protection']['tp']['order_id']]['analytics_order']
            if change=='wrong_order': row['orderId']='unknown'; row.pop('triggerOrderId')
            if change=='wrong_symbol': row['symbol']='ETH-USDT'
            if change=='wrong_side': row['side']='BUY'
            if change=='wrong_position': row['positionId']='other'
            if change=='wrong_trigger': row['triggerOrderId']='other'
            if change=='entry_client': state['analytics_entry_evidence'][0]['order']['clientOrderId']='other'
            with self.assertRaises(LifecycleEvidenceError): project_result('lifecycle-1',state)
        with self.assertRaises(LifecycleEvidenceError):
            project_result('lifecycle-1',exited(opened(),'tp','3'))

    def test_quantity_regressions_and_verified_money_never_overwrite(self):
        state = exited(opened(),'tp','1')
        previous = project_result('lifecycle-1',state)
        self.assertEqual(project_result('lifecycle-1',opened(),previous),previous)
        with self.assertRaises(InvalidResult):
            project_result('lifecycle-1',exited(opened(),'tp','0.5'),previous)
        full = exited(state,'trailing','1',time_ms=3000)
        previous = project_result('lifecycle-1',full,previous)
        previous.update(pnl='1',fees='0.1',margin='10',roi='10',outcome='WIN',money_verified=True,
                        money_evidence_refs=[previous['evidence_refs'][0]],margin_evidence_ref=previous['evidence_refs'][0])
        self.assertEqual(project_result('lifecycle-1',full,previous),previous)
        with self.assertRaises(InvalidResult): project_result('lifecycle-1',full,previous,ambiguous=True)

    def test_capture_is_bounded_redacted_and_nonthrowing(self):
        state = opened()
        facts = state['analytics_entry_evidence'][0]['order']
        self.assertEqual(order_evidence({**facts,'secret':'NEVER','headers':{}}),facts)
        for i in range(MAX_ENTRY_OBSERVATIONS+2):
            capture_entry(state,{**facts,'updateTime':1000+i},state['client_order_id'])
        self.assertEqual(len(state['analytics_entry_evidence']),MAX_ENTRY_OBSERVATIONS)
        self.assertTrue(state['analytics_entry_evidence_truncated'])
        with self.assertRaises(LifecycleEvidenceError): project_result('lifecycle-1',state)
        capture_entry({'analytics_entry_evidence':None},facts,'cid')  # Must not raise into execution.

    def test_cleanup_persists_the_existing_exact_trigger_response(self):
        from app.execution.exit_cleanup import _order_view, triggered_execution
        state=opened()
        state['protection']['tp']['order_id']='201'
        order=dict(orderId='999',triggerOrderId='201',symbol='BTC-USDT',side='SELL',
                   positionSide='LONG',positionId=state['position_id'],type='TAKE_PROFIT_MARKET',
                   status='FILLED',executedQty='2',origQty='2',updateTime=2000)
        view=triggered_execution({'999':order},'201',state,'tp')
        self.assertEqual(view['analytics_order'],order)
        state['exit_cleanup']={'orders':{'201':view}}
        self.assertEqual(project_result('lifecycle-1',state)['status'],'CLOSED')
        self.assertEqual(_order_view(order)['analytics_order'],order)

    def test_default_gate_never_allocates_db_or_starts_worker(self):
        factory = Mock(side_effect=AssertionError('production gate bypassed'))
        runtime = LifecycleWriterRuntime(factory=factory)
        with patch.dict(os.environ,{'V2_LIFECYCLE_WRITER_ENABLED':'true'}): runtime.start()
        factory.assert_not_called()
        self.assertIsNone(runtime._thread)
        self.assertEqual(runtime.status['state'],'disabled')

    def test_database_outage_retry_and_restart_include_terminal_states(self):
        state = opened(terminal=True)
        source = Mock()
        source.page.side_effect = [OperationalError('synthetic',{},Exception()), [('lifecycle-1',state)], []]
        source.ambiguous.return_value = set()
        writer = Mock(source=source)
        writer.process.return_value = 'inserted'
        seen = []
        runtime = LifecycleWriterRuntime(enabled=True,factory=lambda:writer,interval=.001,retry_seconds=.001)
        def notify(status):
            seen.append(status['state'])
            if status.get('counts',{}).get('inserted'): runtime.request_stop()
        runtime.notify=notify
        runtime.start()
        runtime._thread.join(timeout=2)
        self.assertFalse(runtime._thread.is_alive())
        self.assertIn('failed',seen)
        writer.process.assert_called_once_with('lifecycle-1',state,ambiguous=False)
        self.assertEqual(source.page.call_args_list[0].args,('',))
        self.assertEqual(source.page.call_args_list[1].args,('',))
        # A fresh runtime has no volatile cursor dependency.
        restarted = LifecycleWriterRuntime(enabled=True)
        restarted.run_page(writer)
        self.assertEqual(source.page.call_args.args,('',))

    def test_notify_errors_and_poison_record_cannot_block_later_intent(self):
        source = Mock()
        source.page.return_value = [('bad',None),('lifecycle-1',opened())]
        source.ambiguous.return_value=set()
        writer=Mock(source=source)
        writer.process.side_effect=[InvalidResult('bad_record'),'inserted']
        runtime=LifecycleWriterRuntime(notify=Mock(side_effect=RuntimeError('notification unavailable')))
        counts=runtime.run_page(writer)
        self.assertEqual((counts['failed'],counts['inserted']),(1,1))

    def test_revision_conflict_recomputes_and_lost_ack_retry_is_idempotent(self):
        state=opened()
        row=project_result('lifecycle-1',state)
        source=Mock()
        source.current.side_effect=[(0,None),(1,row)]
        with patch('autotrade_v2.analytics.writer.write_result',side_effect=RevisionConflict()) as write:
            result=LifecycleWriter(Mock(),source).process('lifecycle-1',state)
        self.assertEqual(result,'unchanged')
        self.assertEqual(write.call_count,1)

    def test_execution_calls_do_not_depend_on_a_working_analytics_database(self):
        # Use the existing order/protection replay fake. Writer DB is unusable;
        # execution makes exactly its established calls and saves captured proof.
        from autotrade_v2.tests.execution_reconcile_test import ExistingOrderClient, make_intent
        from app.config import Settings
        from app.execution.service import ExecutionService
        from app.execution.store import ExecutionStore
        with tempfile.TemporaryDirectory() as path:
            store=ExecutionStore(path+'/execution.db')
            settings=replace(Settings(),execution_enabled=True,dry_run=False,
                             bingx_api_key='fixture',bingx_api_secret='fixture')
            service=ExecutionService(settings,store)
            service.credentials_verified=True
            intent=make_intent()
            cid=service.executor.client_order_id(intent.event_id,'entry')
            client=ExistingOrderClient(cid)
            client.by_client[cid]['data']['order'].update(symbol=intent.symbol,side=intent.side,updateTime=1000)
            service.executor.client=client
            entered,release,db_finished=threading.Event(),threading.Event(),threading.Event()
            source=Mock()
            source.page.return_value=[('lifecycle-1',opened())]
            source.ambiguous.return_value=set()
            blocked=Mock(source=source)
            def wait_for_db(*args,**kwargs):
                entered.set()
                release.wait(3)
                db_finished.set()
                raise OperationalError('synthetic stuck analytics connection',{},Exception())
            blocked.process.side_effect=wait_for_db
            worker=LifecycleWriterRuntime(enabled=True,factory=lambda:blocked)
            worker.start()
            self.assertTrue(entered.wait(1))
            try:
                with patch('autotrade_v2.analytics.writer.writer_engine',side_effect=AssertionError('analytics DB touched')):
                    result=service.execute(intent)
                self.assertFalse(release.is_set())  # Trading finished while analytics was still blocked.
                self.assertFalse(db_finished.is_set())
            finally:
                release.set()
                worker.stop()
            self.assertEqual(result.status,'complete')
            saved=store.get_state(intent.event_id)
            self.assertTrue(saved['terminal'])
            self.assertEqual(project_result(intent.event_id,saved)['status'],'OPEN')
            self.assertFalse(any(call[0] in ('entry','close','cancel') for call in client.calls))


if __name__=='__main__': unittest.main()
