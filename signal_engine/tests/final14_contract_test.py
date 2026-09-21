"""No exchange/network access. Exercise scan_latest -> durable send -> API payload."""
from __future__ import annotations
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from app.final14_config import FINAL14_CASES
from app.final14_policy import levels
from app import final14_exact_strategy as strategy
from app.final14_positions import refresh_symbol, is_case_active
from app.state import SignalState
from app.strategy import Signal
from final14_test import FakeFinal14Executor

class Exchange(FakeFinal14Executor):
    def __init__(self):
        super().__init__()
        self.sent=[];self.remote={};self.mode='normal';self.flat=False
    def _positions(self,symbol):
        if self.flat:return []
        return [{'positionId':o['positionID'],'positionSide':o['positionSide'],
                 'positionAmt':o['executedQty'],'avgPrice':o['avgPrice']}
                for o in self.remote.values() if o['symbol']==symbol and o['status']=='FILLED']
    def _signed_trade_request(self,method,path,params):
        if method=='POST' and path.endswith('/order'):
            self.sent.append(dict(params));i=str(len(self.sent))
            order={'orderId':i,'positionID':'p'+i,'symbol':params['symbol'],
                   'positionSide':params['positionSide'],'status':'FILLED',
                   'executedQty':params['quantity'],'avgPrice':'100',
                   'takeProfit':json.loads(params['takeProfit']),
                   'stopLoss':json.loads(params['stopLoss'])}
            self.remote[params['clientOrderId']]=order
            if self.mode=='post_timeout':raise TimeoutError('POST response lost')
            return {'data':{'orderId':i}}
        if method=='GET' and path.endswith('/order'):
            return {'data':self.remote[params['clientOrderId']]}
        raise AssertionError((method,path))
    def _order_detail(self,symbol,order_id):
        if self.mode=='get_timeout':raise TimeoutError('GET timeout after acceptance')
        order=next(x for x in self.remote.values() if x['orderId']==order_id)
        if self.mode=='unfilled':return {'data':{**order,'status':'NEW','executedQty':'0','avgPrice':'0'}}
        if self.mode=='missing_protection':return {'data':{**order,'takeProfit':{}}}
        return {'data':order}

def make_signal(symbol='BTC-USDT',combo=1,side='BUY',time=123):
    tp,sl=levels(symbol,combo,side,100.)
    return Signal(symbol,combo,side,'1h',time,100.,tp,sl,1)

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=str(Path(self.tmp.name)/'state.sqlite')
        self.state=SignalState(self.db);self.ex=Exchange()
    def tearDown(self):self.tmp.cleanup()
    def test_28_scan_to_order_paths(self):
        idx=pd.date_range('2026-01-01',periods=12000,freq='15min',tz='UTC')
        frame=pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'volume':10.},index=idx)
        for side in ['BUY','SELL']:
            def chosen(pc,symbol):
                out={}
                for combo,cfg in FINAL14_CASES[symbol].items():
                    name=strategy._combo_name(combo)
                    ix=idx if strategy.NATIVE[name]=='15m' else strategy.resample_ohlcv(frame,'1h').index
                    active=pd.Series(False,index=ix);active.iloc[-1]=True
                    zero=pd.Series(False,index=ix)
                    out[name]=(cfg['entry_variant'],active if side=='BUY' else zero,active if side=='SELL' else zero)
                return out
            for symbol,cases in FINAL14_CASES.items():
                with patch.object(strategy,'precompute',return_value={}),patch.object(strategy,'_selected_variants',side_effect=chosen),patch.object(strategy,'smc_direction',side_effect=lambda f,*a:pd.Series(1 if side=='BUY' else -1,index=f.index)),patch.object(strategy,'ob_context',side_effect=lambda f,*a:pd.DataFrame({'buy_blocked':False,'sell_blocked':False},index=f.index)):
                    signals=strategy.scan_latest(symbol,frame)
                self.assertEqual(len(signals),len(cases))
                for sig in signals:
                    cfg=cases[sig.combo];result=self.ex.send_case(self.state,sig,'test')
                    self.assertTrue(result['ok'],result)
                    payload=self.ex.sent[-1]
                    direction=1 if side=='BUY' else -1
                    # Independent formula using the FINAL14 reference percentages.
                    self.assertAlmostEqual(json.loads(payload['takeProfit'])['stopPrice'],round(100*(1+direction*cfg['tp_pct']),2))
                    self.assertAlmostEqual(json.loads(payload['stopLoss'])['stopPrice'],round(100*(1-direction*cfg['sl_pct']),2))
                    self.assertEqual(payload['type'],'MARKET')
                    self.assertEqual(float(payload['quantity']),1.)
                    self.assertNotIn('priceRate',payload)
                    self.assertTrue(is_case_active(self.state,symbol,sig.combo))
                    self.state.update_final14(sig.event_id,{'status':'closed'})
        self.assertEqual(len(self.ex.sent),28)
    def test_atomic_case_and_event_claims(self):
        sig=make_signal();r={'case_id':'BTC-USDT|C1','event_id':sig.event_id,'symbol':sig.symbol}
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            successes=list(pool.map(lambda _:SignalState(self.db).claim_final14(r),range(8)))
        self.assertEqual(sum(successes),1)
        self.state.update_final14(sig.event_id,{'status':'closed'})
        self.assertFalse(self.state.claim_final14(r))
        self.assertTrue(self.state.claim_final14({**r,'event_id':'new'}))
    def test_post_timeout_restart_no_resend(self):
        self.ex.mode='post_timeout';sig=make_signal()
        r=self.ex.send_case(self.state,sig,'test');self.assertEqual(r['stage'],'reconcile_pending')
        restarted=SignalState(self.db);self.assertTrue(is_case_active(restarted,sig.symbol,sig.combo))
        self.ex.mode='normal';refresh_symbol(restarted,self.ex,sig.symbol)
        self.assertEqual(restarted.final14_records()[0]['status'],'active')
        self.ex.send_case(restarted,sig,'test');self.ex.send_case(restarted,make_signal(time=124),'test')
        self.assertEqual(len(self.ex.sent),1)
    def test_get_timeout_keeps_order_identity(self):
        self.ex.mode='get_timeout';sig=make_signal();r=self.ex.send_case(self.state,sig,'test')
        self.assertEqual(r['stage'],'reconcile_pending')
        self.assertEqual(self.state.final14_records()[0]['entry_order_id'],'1')
        self.ex.mode='normal';refresh_symbol(self.state,self.ex,sig.symbol)
        self.assertEqual(self.state.final14_records()[0]['status'],'active')
    def test_unfilled_stays_locked(self):
        self.ex.mode='unfilled';sig=make_signal()
        with patch('app.final14_executor.time.sleep'):
            r=self.ex.send_case(self.state,sig,'test')
        self.assertFalse(r['ok']);self.assertTrue(is_case_active(self.state,sig.symbol,sig.combo))
        self.ex.send_case(self.state,make_signal(time=124),'test');self.assertEqual(len(self.ex.sent),1)
    def test_missing_protection_not_complete(self):
        self.ex.mode='missing_protection';r=self.ex.send_case(self.state,make_signal(),'test')
        self.assertFalse(r['ok']);self.assertEqual(self.state.final14_records()[0]['status'],'protection_unverified')
    def test_closed_position_allows_next_event_not_old(self):
        sig=make_signal();self.ex.send_case(self.state,sig,'test');self.ex.flat=True
        r=refresh_symbol(self.state,self.ex,sig.symbol);self.assertEqual(r['closed'],['BTC-USDT|C1'])
        self.assertFalse(is_case_active(self.state,sig.symbol,sig.combo))
        self.ex.send_case(self.state,sig,'test');self.assertEqual(len(self.ex.sent),1)
        self.ex.flat=False;self.ex.send_case(self.state,make_signal(time=124),'test');self.assertEqual(len(self.ex.sent),2)
    def test_fail_closed_on_invalid_units(self):
        sig=Signal('BTC-USDT',1,'BUY','1h',123,100.,100.02,99.992,1)
        r=self.ex.send_case(self.state,sig,'test');self.assertFalse(r['ok']);self.assertEqual(len(self.ex.sent),0)
    def test_unknown_query_does_not_release(self):
        self.ex.mode='post_timeout';sig=make_signal();self.ex.send_case(self.state,sig,'test')
        self.ex.remote.clear();refresh_symbol(self.state,self.ex,sig.symbol)
        self.assertTrue(is_case_active(self.state,sig.symbol,sig.combo))
    def test_malformed_positions_never_mean_flat(self):
        from app.final14_executor import Final14Executor
        for body in [{},{'code':0,'data':{}},{'code':0,'data':[{}]},
                     {'code':0,'data':[{'positionAmt':'NaN','positionId':'p1'}]}]:
            with patch.object(self.ex,'_signed_trade_request',return_value=body):
                with self.assertRaises((RuntimeError,ValueError)):
                    Final14Executor._positions(self.ex,'BTC-USDT')
        with patch.object(self.ex,'_signed_trade_request',return_value={'code':0,'data':[]}):
            self.assertEqual(Final14Executor._positions(self.ex,'BTC-USDT'),[])

    def test_legacy_state_migration_is_idempotent(self):
        self.state.set_runtime_value('final14_active_cases_v1',json.dumps([{'event_id':'old','case_id':'ETH-USDT|C1','symbol':'ETH-USDT'}]))
        upgraded=SignalState(self.db);self.assertTrue(is_case_active(upgraded,'ETH-USDT',1))
        upgraded.update_final14('old',{'status':'closed'})
        self.assertFalse(is_case_active(SignalState(self.db),'ETH-USDT',1))

if __name__=='__main__':unittest.main()
