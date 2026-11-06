"""Real executor retry/abort schedules; no exchange network or real credentials."""
from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory

from app.control.errors import BingXApiError
from app.execution.service import ExecutionService
from merged_entry_test import Exchange,setup,signal,state_ids


class FailureExchange(Exchange):
    def __init__(self,kind='sl',failures=2,code='109400',side='SELL',prices=('79701.6','78778.5')):
        super().__init__(side=side,prices=prices)
        self.failed_kind=kind;self.failures=failures;self.error_code=code
        self.close_count=0;self.close_hidden=False;self.lose_close=False
        self.close_cid=None;self.fail_verify=False;self.partial_close=False

    def _protect(self,kind,symbol,side,qty,trigger,position_id,rate=None):
        self.calls.append(('post',kind))
        if kind==self.failed_kind and self.failures:
            self.failures-=1
            raise BingXApiError(self.error_code,'explicit rejection','/openApi/swap/v2/trade/order',
                {'symbol':symbol,'type':{'sl':'STOP_MARKET','tp':'TAKE_PROFIT_MARKET','trailing':'TRAILING_STOP_MARKET'}[kind]})
        return super()._protect(kind,symbol,side,qty,trigger,position_id,rate)

    def find_order_by_client_id(self,symbol,cid):
        if self.close_hidden and cid==self.close_cid:
            return None
        return super().find_order_by_client_id(symbol,cid)

    def get_order_detail(self,symbol,order_id=None,**kwargs):
        if self.fail_verify:
            raise BingXApiError('109400','verification query rejected','/openApi/swap/v2/trade/order',
                               {'symbol':symbol,'type':'STOP_MARKET'})
        return super().get_order_detail(symbol,order_id,**kwargs)

    def close_market(self,symbol,entry_side,qty,leverage,client_order_id=None,position_id=None,quantity_precision=None):
        self.close_count+=1;assert position_id==self.position_id and leverage==50
        assert Decimal(str(qty))==self.amount,'must own the entire selected container'
        assert quantity_precision==4
        oid=self._id();quantity=Decimal(str(qty))
        filled=(quantity/2).quantize(Decimal('0.0001')) if self.partial_close else quantity
        self.amount-=filled
        row={'orderID':oid,'orderId':int(oid),'symbol':symbol,'side':'SELL' if entry_side=='BUY' else 'BUY',
             'positionSide':'LONG' if entry_side=='BUY' else 'SHORT','positionID':position_id,'type':'MARKET',
             'status':'PARTIALLY_FILLED' if self.partial_close else 'FILLED','origQty':str(quantity),'executedQty':str(filled),
             'clientOrderId':client_order_id,'avgPrice':self.prices[0]}
        self.orders[oid]=row;self.clients[client_order_id]=oid;self.close_cid=client_order_id
        self.calls.append(('close',oid))
        if self.lose_close:
            self.lose_close=False
            raise TimeoutError('accepted close, reply lost')
        return self._raw(row)


def resume_now(service,store,intent):
    state=store.get_state(intent.event_id)
    if state.get('recovery'):
        state['recovery']['next_at']=0;store.save_state(intent.event_id,state)
    return service.execute(intent,recovery_only=True)


def main():
    for kind in ('sl','tp','trailing'):
        # One rejection, one retry, fully verified: retain the combo.
        with TemporaryDirectory() as tmp:
            service,store,_=setup(tmp);ex=FailureExchange(kind,1);service.executor.client=ex
            intent=signal();result=service.execute(intent)
            assert result.status=='complete',result
            assert ex.calls.count(('post',kind))==2 and ex.close_count==0
            assert store.get_state(intent.event_id)['protection_attempts'][kind]['posts']==2
            assert len([x for x in ex.calls if x[0]=='entry'])==1
        # Two documented rejections: close own full container, then clean exits.
        with TemporaryDirectory() as tmp:
            service,store,_=setup(tmp);ex=FailureExchange(kind,2);service.executor.client=ex
            intent=signal();result=service.execute(intent)
            assert result.status=='protection_setup_aborted_closed',result
            state=store.get_state(intent.event_id)
            assert state['abort']['phase']=='closed' and state['terminal']
            assert ex.calls.count(('post',kind))==2 and ex.close_count==1
            assert ex.amount==0 and not ex.get_open_orders(intent.symbol)
            assert Decimal(state['exit_cleanup']['remaining_qty'])==0
            assert service.execute(intent).status=='duplicate_skipped'
            assert ex.close_count==1
            # Abort fills must remain in the ledger after restart, allowing a
            # genuinely new event; never resurrect its original entry quantity.
            _,report=service._entry_admission(signal(7))
            assert not report['blocking_risks'],report

    # ETH LONG follows the same exact-quantity abort path for every leg.
    for kind in ('sl','tp','trailing'):
        with TemporaryDirectory() as tmp:
            service,store,_=setup(tmp)
            ex=FailureExchange(kind,2,side='BUY',prices=('2000','2010'));service.executor.client=ex
            intent=replace(signal(),symbol='ETH-USDT',combo=1,side='BUY',event_id='ETH|C1|1',
                           entry=2000,tp=2040,sl=1986.6666)
            result=service.execute(intent)
            assert result.status=='protection_setup_aborted_closed',result
            assert ex.close_count==1 and ex.calls.count(('post',kind))==2 and ex.amount==0

    # Crash after a definitive first rejection: count and verdict survive DB.
    with TemporaryDirectory() as tmp:
        service,store,_=setup(tmp);ex=FailureExchange();service.executor.client=ex;intent=signal()
        original=store.save_state
        def kill_after_rejection(eid,state):
            original(eid,state)
            if (state.get('protection_attempts',{}).get('sl') or {}).get('verdict')=='rejected':
                raise SystemExit('worker died after rejected intent commit')
        store.save_state=kill_after_rejection
        try:
            service.execute(intent);assert False
        except SystemExit:
            pass
        store.save_state=original
        restarted=ExecutionService(service.settings,store);restarted.credentials_verified=True;restarted.executor.client=ex
        result=restarted.execute(intent,recovery_only=True)
        assert result.status=='protection_setup_aborted_closed',result
        assert ex.calls.count(('post','sl'))==2 and ex.close_count==1

    # Restart after all protections were confirmed, before final COMPLETE:
    # preserve the actual trailing activation, not the hard TP as fallback.
    with TemporaryDirectory() as tmp:
        service,store,_=setup(tmp);ex=FailureExchange(failures=0);service.executor.client=ex;intent=signal()
        original=store.save_state
        def kill_after_trailing(eid,state):
            original(eid,state)
            if (state.get('protection') or {}).get('trailing') and not state.get('protection_journal'):
                raise SystemExit('after trailing confirmation, before COMPLETE')
        store.save_state=kill_after_trailing
        try:
            service.execute(intent);assert False
        except SystemExit:
            pass
        store.save_state=original
        result=service.execute(intent,recovery_only=True)
        assert result.status=='complete',result
        assert ex.calls.count(('post','trailing'))==1 and ex.close_count==0

    # Verification failure and server/unknown errors are NOT rejected POSTs.
    for mode in ('verify','server'):
        with TemporaryDirectory() as tmp:
            service,store,_=setup(tmp);ex=FailureExchange(failures=0 if mode=='verify' else 2,code='109500')
            ex.fail_verify=mode=='verify';service.executor.client=ex
            result=service.execute(signal())
            assert not result.details['ok'] and ex.calls.count(('post','sl'))==1,result
            assert ex.close_count==0

    # An uncertain MARKET close is never repeated, including after restart.
    for hidden in (False,True):
        with TemporaryDirectory() as tmp:
            service,store,_=setup(tmp);ex=FailureExchange('tp');ex.lose_close=True;ex.close_hidden=hidden
            service.executor.client=ex;intent=signal();result=service.execute(intent)
            if hidden:
                assert result.status=='protection_abort_reconcile_required',result
                resume_now(service,store,intent)
                assert ex.close_count==1
                ex.close_hidden=False
                result=resume_now(service,store,intent)
            assert result.status=='protection_setup_aborted_closed',result
            assert ex.close_count==1 and not ex.get_open_orders(intent.symbol)

    # Partial MARKET fills are tracked, never treated as flat or re-POSTed.
    with TemporaryDirectory() as tmp:
        service,store,_=setup(tmp);ex=FailureExchange('tp');ex.partial_close=True
        service.executor.client=ex;intent=signal()
        assert service.execute(intent).status=='protection_abort_reconcile_required'
        resume_now(service,store,intent)
        assert ex.close_count==1
        row=ex.orders[ex.clients[ex.close_cid]]
        row['status']='FILLED';row['executedQty']=row['origQty'];ex.amount=Decimal(0)
        result=resume_now(service,store,intent)
        assert result.status=='protection_setup_aborted_closed',result
        assert ex.close_count==1

    # Crash after close WAL but before POST: absence of a CID is not proof the
    # close was never sent. No unbounded retries or duplicate close on restart.
    with TemporaryDirectory() as tmp:
        service,store,_=setup(tmp);ex=FailureExchange();service.executor.client=ex;intent=signal()
        original=store.save_state
        def kill_before_close(eid,state):
            original(eid,state)
            if state.get('emergency_close'):
                raise SystemExit('crash after close prepared commit')
        store.save_state=kill_before_close
        try:
            service.execute(intent);assert False
        except SystemExit:
            pass
        store.save_state=original
        assert resume_now(service,store,intent).status=='protection_abort_reconcile_required'
        assert ex.close_count==0

    # Closing preserves the contract step rather than rounding the residual.
    from app.execution.executor import LegacyStyleBingXClient
    transport=object.__new__(LegacyStyleBingXClient);sent=[]
    transport._signed_request=lambda method,path,params:sent.append(params) or {'code':0}
    transport.close_market('BTC-USDT','BUY',Decimal('0.00005'),50,
                           client_order_id='abort-fixture',position_id='123',quantity_precision=5)
    assert sent[0]['quantity']=='0.00005'

    # Owner-approved Option A closes the entire shared container after fencing
    # every owned native exit. No blind partial-combo MARKET is attempted.
    with TemporaryDirectory() as tmp:
        service,store,_=setup(tmp);ex=FailureExchange(failures=0);service.executor.client=ex
        a,b=signal(),signal(7)
        assert service.execute(a).status=='complete';peer=state_ids(store,a)
        ex.failures=2
        result=service.execute(b)
        assert result.status=='force_close_confirmed',result
        assert ex.amount==0 and all(ex.orders[oid]['status']=='CANCELLED' for oid in peer.values())
        assert store.get_state(a.event_id)['stage']=='force_close_confirmed'
        assert ex.close_count==1 and ex.calls.count(('post','sl'))==3
        _,report=service._entry_admission(signal(7,2))
        assert not report['blocking_risks'],report

    print({'ok':True,'retry_once_per_leg':True,'exhaustion_closes_exclusive_combo':True,
           'restart_does_not_reset_budget':True,'verify_error_never_reposts':True,
           'uncertain_close_never_reposts':True,'shared_peer_preserved':True})


if __name__=='__main__':
    main()
