"""Fault schedules through the real service, executor and durable store.

The exchange fixture has independent native fills (no OCO), Decimal exposure,
parent/child history and transport faults. Also run on isolated PostgreSQL.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory

from app.execution.service import ExecutionService
from app.execution.force_close import side_key,TARGET
from merged_entry_test import setup,signal,state_ids
from protection_retry_abort_test import FailureExchange


class EmergencyExchange(FailureExchange):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,failures=0,**kwargs)
        self.cancel_race=None;self.cancel_lost=False;self.cancel_hidden=False
        self.read_error=False;self.stale_zero=False;self.terminal_partial=False

    def get_positions(self,symbol):
        if self.read_error:
            raise RuntimeError('HTTP_429 REST unavailable')
        if self.stale_zero:
            return []
        return super().get_positions(symbol)

    def cancel_order(self,symbol,order_id=None,**kwargs):
        if self.cancel_race:
            callback=self.cancel_race;self.cancel_race=None;callback()
        if self.cancel_hidden:
            raise TimeoutError('DELETE outcome unknown')
        if self.orders[order_id]['status']=='NEW':
            result=super().cancel_order(symbol,order_id,**kwargs)
        else:
            raise RuntimeError('native exit filled while cancellation raced')
        if self.cancel_lost:
            raise TimeoutError('DELETE accepted; response lost')
        return result

    def close_market(self,*args,**kwargs):
        value=super().close_market(*args,**kwargs)
        if self.terminal_partial:
            self.orders[self.clients[self.close_cid]]['status']='CANCELED'
        return value


def start(service,store,*,kind='sl',symbol='BTC-USDT',side='SELL',count=2):
    ex=EmergencyExchange(kind=kind,side=side,
        prices=('2000','2010') if symbol=='ETH-USDT' else ('79701.6','78778.5'))
    service.executor.client=ex
    intents=[signal(),signal(7),signal(7,2)]
    if symbol=='ETH-USDT':
        from app.strategy.final14_config import get_case
        ex.prices=('2000','2000')
        intents=[replace(x,symbol=symbol,side=side,combo=1 if n%2==0 else 3,
            event_id=f'ETH|{n}|{side}',entry=2000,
            tp=2000*(1+(1 if side=='BUY' else -1)*get_case(symbol,1 if n%2==0 else 3)['tp_pct']),
            sl=2000*(1-(1 if side=='BUY' else -1)*get_case(symbol,1 if n%2==0 else 3)['sl_pct'])) for n,x in enumerate(intents)]
    elif side=='BUY':
        ex.prices=('79701.6','79701.6')
        # Use a known LONG combo and matching contract strategy ratio.
        intents=[replace(x,combo=1,side='BUY',event_id=f'BTC|{n}|BUY',
            entry=79701.6,tp=81295.632,sl=79170.256) for n,x in enumerate(intents)]
    for intent in intents[:count-1]:
        result=service.execute(intent)
        assert result.status=='complete',result
    ex.failures=2
    return ex,intents[:count]


def resume(service,store,intent):
    state=store.get_state(intent.event_id)
    if (state or {}).get('recovery'):
        state['recovery']['next_at']=0;store.save_state(intent.event_id,state)
    restarted=ExecutionService(service.settings,store)
    restarted.credentials_verified=True;restarted.executor.client=service.executor.client
    return restarted.execute(intent,recovery_only=True)


def assert_closed(service,store,ex,intents):
    assert ex.amount==0 and ex.close_count<=3
    for intent in intents:
        state=store.get_state(intent.event_id)
        assert state['terminal'] and state['stage']=='force_close_confirmed',state
        assert state['force_close_allocation']
    epoch=store.get(side_key(intents[0].symbol,intents[0].side),TARGET)
    assert epoch['phase']=='closed' and epoch['remaining_qty']=='0'
    assert not ex.get_open_orders(intents[0].symbol)
    # Closed allocation remains in the actual admission ledger after restart.
    _,report=service._entry_admission(replace(intents[-1],event_id='fresh-event'))
    assert not report['blocking_risks'],report
    before=deepcopy(ex.calls)
    resume(service,store,intents[-1]);service.cleanup_closed_exits()
    assert ex.calls==before,'recovery/cleanup mutated a confirmed closed epoch'


def schedules(factory):
    completed=0
    # Matrix: every leg, BTC/ETH, LONG/SHORT, 2 and 3 combos.
    for symbol in ('BTC-USDT','ETH-USDT'):
        for side in ('SELL','BUY'):
            for count in (2,3):
                for kind in ('sl','tp','trailing'):
                    with factory() as (service,store):
                        ex,intents=start(service,store,kind=kind,symbol=symbol,side=side,count=count)
                        result=service.execute(intents[-1])
                        assert result.status=='force_close_confirmed',result
                        assert ex.calls.count(('post',kind))==count+1
                        assert_closed(service,store,ex,intents)
                        completed+=1
    # Native TP half then SL, two trailing triggers simultaneously, and entry
    # overlapping a peer TP. All independent fills reduce the MARKET quantity.
    for mode in ('tp_overlap','tp_sl','two_trailing','cancel_race','lost_cancel'):
        with factory() as (service,store):
            ex,intents=start(service,store,kind='tp',count=3)
            a,b,c=intents;ids=[state_ids(store,x) for x in (a,b)]
            def half_sl():
                ex.fill(ids[0]['tp'])
                # A native SL may execute only the residual of its origQty.
                row=ex.orders[ids[0]['sl']];qty=Decimal('0.0006')
                row['status']='FILLED'
                child={**row,'orderID':ex._id(),'triggerOrderId':ids[0]['sl'],'executedQty':str(qty)}
                child['orderId']=int(child['orderID']);ex.orders[child['orderID']]=child;ex.amount-=qty
            if mode=='tp_overlap':
                ex.on_entry=lambda:ex.fill(ids[0]['tp'])
            elif mode=='tp_sl':
                ex.on_entry=half_sl
            elif mode=='two_trailing':
                ex.on_entry=lambda:(ex.fill(ids[0]['trailing']),ex.fill(ids[1]['trailing']))
            elif mode=='cancel_race':
                ex.cancel_race=lambda:ex.fill(ids[0]['tp'])
            else:
                ex.cancel_lost=True
            result=service.execute(c)
            assert result.status=='force_close_confirmed',(mode,result)
            assert_closed(service,store,ex,intents);completed+=1
    for mode in ('lost_market','hidden_market','partial','terminal_partial','cancel_unknown',
                 'history_missing','stale_zero','rest_error','manual_order','manual_quantity','wrong_close'):
        with factory() as (service,store):
            ex,intents=start(service,store,kind='tp');a,b=intents
            if mode in ('lost_market','hidden_market'):
                ex.lose_close=True;ex.close_hidden=mode=='hidden_market'
            if mode in ('partial','terminal_partial'):
                ex.partial_close=True;ex.terminal_partial=mode=='terminal_partial'
            if mode=='cancel_unknown':
                ex.cancel_hidden=True
            if mode=='history_missing':
                ex.hide_history=True
            # Install observation errors exactly when emergency begins, after
            # the failed combo's entry/protective retries have been recorded.
            original=store.upsert
            def inject(key,value,target):
                original(key,value,target)
                if target==TARGET and value['phase']=='requested':
                    ex.stale_zero=mode=='stale_zero';ex.read_error=mode=='rest_error'
                    if mode=='manual_quantity':
                        ex.amount+=Decimal('0.0001')
                    if mode=='manual_order':
                        row=deepcopy(ex.orders[state_ids(store,a)['sl']]);new=ex._id()
                        row.update(orderID=new,orderId=int(new));ex.orders[new]=row
            store.upsert=inject
            if mode=='wrong_close':
                original_find=ex.find_order_by_client_id
                def wrong(symbol,cid):
                    raw=original_find(symbol,cid)
                    if cid.startswith('fc_') and raw:
                        raw['data']['order']['positionSide']='LONG'
                    return raw
                ex.find_order_by_client_id=wrong
            result=service.execute(b);store.upsert=original
            if mode=='lost_market':
                assert result.status=='force_close_confirmed',result
                assert_closed(service,store,ex,intents);completed+=1;continue
            assert result.status=='force_close_unresolved',(mode,result)
            epoch=store.get(side_key(b.symbol,b.side),TARGET)
            assert epoch['phase']=='unresolved' and epoch['last_alert_at']
            before=ex.close_count
            assert service.execute(replace(b,event_id='blocked-new')).status=='force_close_side_blocked'
            if mode!='terminal_partial':
                resume(service,store,b)
            assert ex.close_count==before,(mode,ex.close_count,before)
            assert not store.get_state(b.event_id).get('terminal'),mode
            if mode=='hidden_market':
                ex.close_hidden=False
                assert resume(service,store,b).status=='force_close_confirmed'
                assert_closed(service,store,ex,intents)
            if mode=='partial':
                row=ex.orders[ex.clients[ex.close_cid]];row.update(status='FILLED',executedQty=row['origQty'])
                ex.amount=Decimal(0)
                assert resume(service,store,b).status=='force_close_confirmed'
                assert_closed(service,store,ex,intents)
            if mode=='terminal_partial':
                ex.partial_close=False;ex.terminal_partial=False
                assert resume(service,store,b).status=='force_close_confirmed'
                assert ex.close_count==2 and len({x for x in ex.clients if x.startswith('fc_')})==2
                assert_closed(service,store,ex,intents)
            completed+=1
    # Crash sweep across EVERY emergency durable-write boundary in a normal
    # shared close, including finalizing each peer and epoch final commit.
    with factory() as (service,store):
        ex,intents=start(service,store,kind='tp');writes=[]
        original_upsert=store.upsert
        def capture(key,value,target):
            original_upsert(key,value,target)
            if target==TARGET or (target=='bingx_state' and value.get('force_close_epoch')):
                writes.append((key,target))
        store.upsert=capture
        assert service.execute(intents[-1]).status=='force_close_confirmed'
        store.upsert=original_upsert
    for boundary in range(len(writes)):
        with factory() as (service,store):
            ex,intents=start(service,store,kind='tp');original=store.upsert;seen=0
            def crash(key,value,target):
                nonlocal seen
                original(key,value,target)
                if target==TARGET or (target=='bingx_state' and value.get('force_close_epoch')):
                    current=seen;seen+=1
                    if current==boundary:
                        raise SystemExit('crash after durable emergency boundary')
            store.upsert=crash
            try:
                service.execute(intents[-1])
                raise AssertionError('boundary not reached')
            except SystemExit:
                pass
            store.upsert=original
            before=ex.close_count;result=resume(service,store,intents[-1])
            assert ex.close_count<=1
            if result.status=='force_close_unresolved':
                # A durable CID with no observed order is deliberately held;
                # cannot distinguish pre-POST crash from a hidden accepted POST.
                epoch=store.get(side_key(intents[-1].symbol,intents[-1].side),TARGET)
                assert epoch['closes'] and not epoch['closes'][-1].get('order'),result
                assert ex.close_count==before
            else:
                assert_closed(service,store,ex,intents)
            completed+=1
    # Unrelated symbol/side native orders survive; an unresolved SHORT epoch
    # does not disable a healthy LONG or another symbol's entry.
    with factory() as (service,store):
        ex,intents=start(service,store,kind='tp');a,b=intents
        untouched=deepcopy(ex.orders[state_ids(store,a)['sl']]);new=ex._id()
        untouched.update(orderID=new,orderId=int(new),positionSide='LONG',side='SELL',positionID='999')
        ex.orders[new]=deepcopy(untouched)
        ex.close_hidden=True
        assert service.execute(b).status=='force_close_unresolved'
        assert ex.orders[new]==untouched
        service.cleanup_closed_exits()
        assert ex.orders[new]==untouched
        from merged_entry_test import Exchange
        original_client=service.executor.client
        for symbol in ('BTC-USDT','ETH-USDT'):
            service.executor.client=Exchange(side='BUY',prices=('2000','2000'))
            intent=replace(signal(),symbol=symbol,combo=1,side='BUY',event_id='other-'+symbol,
                entry=2000,tp=2040,sl=1984 if symbol=='BTC-USDT' else 1986.6666)
            assert service.execute(intent).status=='complete'
        service.executor.client=original_client
        ex.close_hidden=False
        assert resume(service,store,b).status=='force_close_confirmed'
        assert ex.orders[new]==untouched;completed+=1
    # Failed durable writes must prevent the corresponding mutation; retry
    # reconstructs the epoch without resetting budgets or resending a MARKET.
    for phase in ('requested','canceling','close_prepared','finalizing'):
        with factory() as (service,store):
            ex,intents=start(service,store,kind='tp');original=store.upsert;broken=False
            def fail_write(key,value,target):
                nonlocal broken
                if target==TARGET and value['phase']==phase and not broken:
                    broken=True
                    raise OSError('injected database commit failure')
                return original(key,value,target)
            store.upsert=fail_write
            service.execute(intents[-1]);store.upsert=original
            assert broken
            if phase in ('requested','canceling','close_prepared'):
                assert ex.close_count==0
            result=resume(service,store,intents[-1])
            assert ex.close_count<=1
            if result.status!='force_close_confirmed':
                # Prepared CID written by the catch after an uncertain failed
                # commit is still reconciled conservatively, never re-POSTed.
                assert result.status=='force_close_unresolved',result
            completed+=1
    # Check coordinator ownership again after request-guard waiting; this is
    # the actual private HTTP transport, not just a fake mutation wrapper.
    from contextlib import contextmanager
    from app.execution.executor import LegacyStyleBingXClient
    transport=object.__new__(LegacyStyleBingXClient)
    class Guard:
        @contextmanager
        def request_slot(self):
            yield
    transport.guard=Guard();attempts=[]
    def lost_owner():
        attempts.append('fenced')
        raise RuntimeError('lock session lost while request was paced')
    transport.mutation_check=lost_owner
    try:
        transport._signed_request('POST','/must-not-send',{})
        raise AssertionError('transport fence absent')
    except RuntimeError as exc:
        assert 'session lost' in str(exc)
    assert attempts==['fenced'];completed+=1
    return completed


def main():
    from contextlib import contextmanager
    @contextmanager
    def factory():
        with TemporaryDirectory() as tmp:
            service,store,_=setup(tmp)
            yield service,store
    print({'force_close_schedules':schedules(factory),'real_service_executor_store':True})


if __name__=='__main__':
    main()
