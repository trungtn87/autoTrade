"""Offline regression tests: no real exchange, Discord or production DB calls."""
from __future__ import annotations

import importlib
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
import pandas as pd

from app.bingx_market import BingXApiError, BingXMarketClient
from app.config import Settings
from app.final14_config import FINAL14_CASES
from app.final14_executor import Final14Executor
from app.state import SignalState
from app.strategy import Signal


class GuardHelpers:
    def guard(self, handler, **kwargs):
        c = BingXMarketClient(api_key='fake', api_secret='fake', min_interval_sec=0, **kwargs)
        c._client.close()
        c._client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        self.addCleanup(c._client.close)
        return c

    def executor(self, guard, handler):
        ex = Final14Executor(Settings(bingx_api_key='fake', bingx_api_secret='fake', discord_enabled=False), request_guard=guard)
        ex.client.close()
        ex.client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        self.addCleanup(ex.client.close)
        return ex


class GuardTests(GuardHelpers, unittest.TestCase):
    def test_concurrent_failures_send_only_one_request(self):
        calls = []
        start = threading.Barrier(12)
        def response(req):
            calls.append(req)
            time.sleep(.02)
            return httpx.Response(200, json={'code':109425, 'msg':'invalid pair'})
        c = self.guard(response)
        def run(_):
            start.wait(timeout=5)
            try:
                c.klines('BTC-USDT', '15m', 8)
            except BingXApiError as exc:
                return str(exc.code)
        with ThreadPoolExecutor(max_workers=12) as pool:
            codes = list(pool.map(run, range(12)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(codes.count('109425'), 1)
        self.assertEqual(codes.count('CIRCUIT_BREAKER'), 11)

    def test_trade_error_blocks_market_and_restores_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            state = SignalState(str(Path(td)/'state.db'))
            key = 'bingx_market_blocked_until_ms'
            callbacks = dict(cooldown_reader=lambda:state.get_runtime_value(key,'0'),
                             cooldown_writer=lambda v:state.set_runtime_value(key,str(v)))
            events = []
            def unexpected(req):
                self.fail('outbound request during persisted cooldown')
            c = self.guard(unexpected, error_recorder=lambda **event:events.append(event), **callbacks)
            ex = self.executor(c, lambda req:httpx.Response(400, json={'code':109425,'msg':'invalid pair'}))
            with self.assertRaises(BingXApiError):
                ex._positions('BTC-USDT')
            self.assertGreater(int(state.get_runtime_value(key)), int(time.time()*1000))
            self.assertEqual(events[0]['source'], 'executor')
            with self.assertRaises(BingXApiError):
                c.klines('BTC-USDT','15m',8)
            restarted = self.guard(unexpected, **callbacks)
            with self.assertRaises(BingXApiError):
                restarted.klines('ETH-USDT','15m',8)

    def test_market_error_blocks_trade(self):
        c = self.guard(lambda req:httpx.Response(200,json={'code':109429,'msg':'locked'}))
        ex = self.executor(c, lambda req:self.fail('trade request after market cooldown'))
        with self.assertRaises(BingXApiError):c.klines('BTC-USDT','15m',8)
        with self.assertRaises(BingXApiError):ex._positions('BTC-USDT')

    def test_http_429_without_json_blocks(self):
        c = self.guard(lambda req:httpx.Response(429,text='rate limited'))
        with self.assertRaises(BingXApiError) as caught:c.klines('BTC-USDT','15m',8)
        self.assertEqual(caught.exception.code,'HTTP_429')
        self.assertGreater(c.cooldown_remaining_ms(),0)

    def test_persisted_state_read_failure_fails_closed(self):
        def fail():raise RuntimeError('state unavailable')
        c=self.guard(lambda req:self.fail('request without state check'),cooldown_reader=fail)
        with self.assertRaisesRegex(RuntimeError,'state unavailable'):c.klines('BTC-USDT','15m',8)

    def test_honors_server_deadline_and_recovers(self):
        deadline=int(time.time()*1000)+600_000
        c=self.guard(lambda req:httpx.Response(200,json={'code':109429,'msg':f'can retry after time: {deadline}'}))
        with self.assertRaises(BingXApiError):c.klines('BTC-USDT','15m',8)
        self.assertEqual(c.blocked_until_ms,deadline+60_000)
        c.clear_blocked_until_ms()
        c._client.close()
        c._client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'code':0,'data':[]})),trust_env=False)
        self.addCleanup(c._client.close)
        self.assertTrue(c.klines('BTC-USDT','15m',8).empty)

    def test_accepted_entry_not_reported_as_unsent_after_lockout(self):
        orders=[]
        def response(req):
            path=req.url.path
            if path.endswith('/positionSide/dual'):data={'dualSidePosition':True}
            elif path.endswith('/marginType'):data={'marginType':'SEPARATE_ISOLATED'}
            elif path.endswith('/contracts'):data=[{'symbol':'BTC-USDT','quantityPrecision':4,'pricePrecision':2}]
            elif path.endswith('/positions'):data=[]
            elif path.endswith('/leverage'):data={}
            elif path.endswith('/order') and req.method=='POST':
                orders.append(req);data={'order':{'orderId':'accepted-123'}}
            elif path.endswith('/order'):
                return httpx.Response(200,json={'code':109429,'msg':'locked after acceptance'})
            else:self.fail(path)
            return httpx.Response(200,json={'code':0,'data':data})
        c=self.guard(response);ex=self.executor(c,response)
        sig=Signal('BTC-USDT',1,'BUY','15m',123,100,102,98,0)
        result=ex.send_target(sig,'bingx_account','direct://bingx',100)
        self.assertTrue(result['processed'])
        self.assertTrue(result['entry_accepted'])
        self.assertEqual(result['order_id'],'accepted-123')
        self.assertEqual(result['bingx_error']['code'],109429)
        self.assertEqual(len(orders),1)


class ScanTests(GuardHelpers, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        isolated = replace(Settings(), database_url='', state_db=str(Path(cls.tmp.name)/'startup.db'),
                           bingx_api_key='fake', bingx_api_secret='fake', auto_scheduler=False, discord_enabled=False)
        with patch('app.config.Settings', return_value=isolated):
            cls.main=importlib.import_module('app.main')
    @classmethod
    def tearDownClass(cls):
        cls.main.market._client.close();cls.main.executor.client.close();cls.tmp.cleanup()

    def test_first_trade_error_stops_remaining_combos(self):
        m=self.main
        with tempfile.TemporaryDirectory() as td:
            state=SignalState(str(Path(td)/'test.db'))
            # Use SQLite persistence, while satisfying the production execution gate.
            class LiveTestState:
                backend='postgres'
                def __getattr__(self,k):return getattr(state,k)
            errors=[]
            def response(req):
                if req.url.path.endswith('/positionSide/dual'):
                    return httpx.Response(200,json={'code':0,'data':{'dualSidePosition':True}})
                errors.append(req)
                return httpx.Response(200,json={'code':109425,'msg':'invalid pair'})
            c=self.guard(response,cooldown_reader=lambda:state.get_runtime_value(m.BINGX_COOLDOWN_STATE_KEY,'0'),
                         cooldown_writer=lambda v:state.set_runtime_value(m.BINGX_COOLDOWN_STATE_KEY,str(v)))
            ex=self.executor(c,response)
            frame=pd.DataFrame([{'close_time':123}])
            def signals(**kw):return [Signal(kw['symbol'],n,'BUY','15m',123,100,102,98,0) for n in FINAL14_CASES[kw['symbol']]]
            with patch.multiple(m,state=LiveTestState(),market=c,executor=ex,scan_lock=threading.Lock(),
                                settings=replace(ex.settings,symbols=('BTC-USDT','ETH-USDT')),
                                fetch_bundle=lambda s:(123,frame,frame,frame,frame,False,{}),
                                refresh_symbol=lambda *a:{},combo_readiness=lambda *a,**k:{},scan_latest=signals):
                result=m.run_scan()
                self.assertEqual(result['status'],'partial_error')
                self.assertTrue(result['stopped_early'])
                self.assertEqual(len(errors),1)
                self.assertNotIn('ETH-USDT',result['symbols'])
                self.assertEqual(m.run_scan()['status'],'bingx_cooldown')
                self.assertEqual(len(errors),1)

    def test_diagnostic_rejects_unconfigured_symbol_without_network(self):
        m=self.main
        with patch.object(m,'settings',replace(m.settings,scan_token='test',symbols=('BTC-USDT','ETH-USDT'))):
            with self.assertRaises(m.HTTPException) as caught:m.kline_check('BAD-USDT','15m',3,'test')
            self.assertEqual(caught.exception.status_code,400)

    def test_diagnostic_and_scan_lock_not_leaked_on_database_failure(self):
        m=self.main
        class FailedState:
            def try_acquire_cluster_lock(self,*a):raise RuntimeError('database unavailable')
        lock=threading.Lock()
        with patch.multiple(m,state=FailedState(),scan_lock=lock):
            with self.assertRaises(RuntimeError):m.run_scan()
            self.assertFalse(lock.locked())
            with self.assertRaises(RuntimeError):
                with m._diagnostic_slot():pass
            self.assertFalse(lock.locked())

if __name__=='__main__':unittest.main()
