"""Realistic no-network service schedules: concurrent combos share a container.

The exchange fake validates exact IDs, applies real Decimal fills and retains
conditional parents plus trigger execution children. It never implements OCO.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.contracts import TradeIntent
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore
from app.execution.identity import identify_entry_position, PositionQuantityMismatch
from app.execution.merged import reconcile_side


class Exchange:
    def __init__(self):
        self.amount=Decimal(0)
        self.next_id=2118327594881601000
        self.orders={};self.clients={};self.calls=[]
        self.on_entry=None;self.on_trailing=None;self.after_entry_snapshot=None;self.lose_entry=False
        self.lag=0;self.hide_history=False;self.bad_get=False
        self.lose_kind=None;self.restart_kind=None;self.restart_verify_kind=None;self.kinds={}
        self.position_id='2118327594902573058'

    @staticmethod
    def extract_order(raw):
        return deepcopy(raw['data']['order'])

    def _id(self):
        self.next_id+=1
        return str(self.next_id)

    def _raw(self,row):
        return {'code':0,'data':{'order':deepcopy(row)}}

    def get_positions(self,symbol):
        if self.after_entry_snapshot and self.clients:
            callback=self.after_entry_snapshot;self.after_entry_snapshot=None;callback()
        if self.lag:
            self.lag-=1
            return []
        return ([] if not self.amount else [{'symbol':symbol,'positionSide':'SHORT',
                'positionID':self.position_id,'positionAmt':str(self.amount)}])

    def get_open_orders(self,symbol):
        return [deepcopy(x) for x in self.orders.values()
                if x['symbol']==symbol and x['status'] in {'NEW','PARTIALLY_FILLED'}]

    def get_order_history(self,symbol,*_,**__):
        return [] if self.hide_history else [deepcopy(x) for x in self.orders.values() if x['symbol']==symbol]

    def find_order_by_client_id(self,symbol,cid):
        oid=self.clients.get(cid)
        return None if oid is None else self._raw(self.orders[oid])

    def get_order_detail(self,symbol,order_id=None,**_):
        if self.restart_verify_kind and self.kinds.get(order_id)==self.restart_verify_kind:
            self.restart_verify_kind=None
            raise SystemExit('crash after durable POST acknowledgement, before verification')
        if self.bad_get:
            return self._raw({**self.orders[order_id],'orderID':'999','orderId':999})
        return self._raw(self.orders[order_id])

    def place_market_entry(self,symbol,side,qty,leverage,client_order_id=None):
        assert side=='SELL'
        oid=self._id();quantity=Decimal(str(qty));self.amount+=quantity
        price='79701.6' if not self.clients else '78778.5'
        row={'orderID':oid,'orderId':int(oid),'clientOrderId':client_order_id,
             'symbol':symbol,'side':side,'positionSide':'SHORT','type':'MARKET',
             'status':'FILLED','origQty':str(quantity),'executedQty':str(quantity),
             'avgPrice':price,'positionID':self.position_id}
        self.orders[oid]=row;self.clients[client_order_id]=oid
        self.calls.append(('entry',oid))
        if self.on_entry:
            callback=self.on_entry;self.on_entry=None;callback()
        if self.lose_entry:
            self.lose_entry=False
            raise TimeoutError('entry accepted but POST response lost')
        return self._raw(row)

    def _protect(self,kind,symbol,side,qty,trigger,position_id,rate=None):
        assert side=='SELL' and position_id==self.position_id
        oid=self._id()
        row={'orderID':oid,'orderId':int(oid),'symbol':symbol,'side':'BUY',
             'positionSide':'SHORT','positionID':position_id,'type':{
                'sl':'STOP_MARKET','tp':'TAKE_PROFIT_MARKET','trailing':'TRAILING_STOP_MARKET'}[kind],
             'status':'NEW','origQty':str(qty),'executedQty':'0','avgPrice':'0'}
        row.update({'activationPrice':str(trigger),'priceRate':str(rate)} if kind=='trailing' else {'stopPrice':str(trigger)})
        self.orders[oid]=row;self.kinds[oid]=kind;self.calls.append((kind,oid))
        if kind=='trailing' and self.on_trailing:
            callback=self.on_trailing;self.on_trailing=None;callback()
        if self.restart_kind==kind:
            self.restart_kind=None
            raise SystemExit('worker died after POST')
        if self.lose_kind==kind:
            self.lose_kind=None
            raise TimeoutError('POST accepted but response lost')
        return self._raw(row)

    def place_stop_loss(self,symbol,side,qty,trigger,position_id=None):
        return self._protect('sl',symbol,side,qty,trigger,position_id)

    def place_take_profit(self,symbol,side,qty,trigger,quantity_precision=None,position_id=None):
        return self._protect('tp',symbol,side,qty,trigger,position_id)

    def place_trailing_stop(self,symbol,side,qty,trigger,rate,quantity_precision=None,position_id=None):
        return self._protect('trailing',symbol,side,qty,trigger,position_id,rate)

    def get_contract_rules(self,symbol):
        return {'symbol':symbol,'quantityPrecision':4,'tradeMinQuantity':'0.0001','tradeMinUSDT':'2'}

    def fill(self,oid):
        parent=self.orders[oid]
        assert parent['status']=='NEW'
        qty=Decimal(parent['origQty']);assert qty<=self.amount
        parent['status']='FILLED'  # BingX conditional parent itself may report zero fill.
        child={**parent,'orderID':self._id(),'triggerOrderId':oid,'executedQty':str(qty)}
        child['orderId']=int(child['orderID'])
        self.orders[child['orderID']]=child;self.amount-=qty

    def cancel_order(self,symbol,order_id=None,**_):
        assert self.orders[order_id]['status']=='NEW'
        self.calls.append(('cancel',order_id));self.orders[order_id]['status']='CANCELLED'
        self.orders[order_id]['triggerOrderId']=order_id
        return {'code':0}

    def close_market(self,*_,**__):
        raise AssertionError('must never close merged exposure in these schedules')


def signal(combo=106,sequence=1):
    old=combo==106
    return TradeIntent(event_id=f'BTC-USDT|C{combo}|{sequence}',symbol='BTC-USDT',
        combo=combo,side='SELL',timeframe='1h' if old else '15m',close_time=sequence,
        entry=79701.6 if old else 78853.3,tp=78785.3224 if old else 77276.234,
        sl=80378.5544 if old else 79484.1264,smc_dir=-1)


def setup(path):
    store=ExecutionStore(str(Path(path)/'execution.db'))
    service=ExecutionService(replace(Settings(),execution_enabled=True,dry_run=False,
        tp_trailing_split_enabled=True,bingx_api_key='offline',bingx_api_secret='offline'),store)
    service.credentials_verified=True;exchange=Exchange();service.executor.client=exchange
    return service,store,exchange


def state_ids(store,intent):
    state=store.get_state(intent.event_id)
    return {k:v['order_id'] for k,v in state['protection'].items()}


def main():
    # 1/6: same side and same positionID must admit TWO distinct filled entries.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);a,b=signal(),signal(7)
        assert service.execute(a).status=='complete'
        first=state_ids(store,a)
        assert service.execute(b).status=='complete'
        second=state_ids(store,b)
        assert set(first.values()).isdisjoint(second.values())
        assert store.get_state(a.event_id)['position_id']==store.get_state(b.event_id)['position_id']
        assert ex.amount==Decimal('0.0026') and len(ex.clients)==2
        assert all(ex.orders[oid]['status']=='NEW' for oid in first.values())
        assert not service.execute(b).accepted and len(ex.clients)==2

    # 2/3/4: C16 TP overlaps C7 entry; C16 trailing then closes its own rest.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);a,b=signal(),signal(7)
        assert service.execute(a).status=='complete';first=state_ids(store,a)
        ex.on_entry=lambda:ex.fill(first['tp'])
        result=service.execute(b)
        assert result.status=='complete',result
        assert ex.amount==Decimal('0.0019')
        second=state_ids(store,b)
        assert store.get_state(b.event_id)['executed_qty']==0.0013
        ex.fill(first['trailing'])
        service.cleanup_closed_exits()
        assert ('cancel',first['sl']) in ex.calls
        assert all(ex.orders[oid]['status']=='NEW' for oid in second.values())
        assert ex.amount==Decimal('0.0013')
        assert store.get_state(a.event_id)['exit_cleanup']['remaining_qty']=='0.0000'
        service.cleanup_closed_exits()
        assert store.get_state(a.event_id)['exit_cleanup']['status']=='done'
        assert store.get_state(b.event_id)['stage']=='complete'

    # C16 fully exits DURING C7 POST: zero net delta still proves C7 by its order.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);a,b=signal(),signal(7)
        assert service.execute(a).status=='complete';ids=state_ids(store,a)
        ex.on_entry=lambda:(ex.fill(ids['tp']),ex.fill(ids['trailing']))
        assert service.execute(b).status=='complete'
        assert ex.amount==Decimal('0.0013') and len(ex.clients)==2

    # 7: delayed position snapshot reconciles read-only before protection.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp)
        ex.on_entry=lambda:setattr(ex,'lag',2)
        result=service.execute(signal())
        assert result.status=='complete',result
        assert len([x for x in ex.calls if x[0]=='entry'])==1

    # 8/9: accepted protection POST loses reply or process; no duplicate or new entry.
    for crash in (False,True):
        with TemporaryDirectory() as tmp:
            service,store,ex=setup(tmp);intent=signal()
            setattr(ex,'restart_kind' if crash else 'lose_kind','sl')
            try:
                result=service.execute(intent)
                assert not crash and result.status=='protection_sl_reconcile_pending'
            except SystemExit:
                assert crash
            state=store.get_state(intent.event_id)
            assert state['entry_filled'] and Decimal(str(state['executed_qty']))==Decimal('0.0013'),state
            service.execute(intent,recovery_only=True)
            assert len([x for x in ex.calls if x[0]=='entry'])==1
            assert len([x for x in ex.calls if x[0]=='sl'])==1
            assert not store.get_state(intent.event_id).get('ok')

    # An exact acknowledgement survives restart and can be recovered without
    # claiming an arbitrary same-price order or re-POSTing the conditional.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);intent=signal();ex.restart_verify_kind='sl'
        try:
            service.execute(intent)
            assert False
        except SystemExit:
            pass
        durable=store.get_state(intent.event_id)
        assert durable['protection_journal']['sl']['acknowledged_order_id']
        assert service.execute(intent,recovery_only=True).status=='complete'
        assert len([x for x in ex.calls if x[0]=='sl'])==1 and len(ex.clients)==1

    # 10/11: genuine missing SL/manual drift blocks increased risk, not same-side presence.
    for drift in (False,True):
        with TemporaryDirectory() as tmp:
            service,store,ex=setup(tmp);a=signal()
            assert service.execute(a).status=='complete'
            if drift:
                ex.amount+=Decimal('0.001')
            else:
                ex.orders[state_ids(store,a)['sl']]['status']='CANCELLED'
            result=service.execute(signal(7))
            assert result.status=='merged_reconcile_required',result
            assert len(ex.clients)==1
            service.cleanup_closed_exits()
            assert store.get_state(a.event_id)['stage']=='protection_reconcile_required'

    # 5/15: both combo exits complete nearly simultaneously. Never touch any
    # ID until that combo's exact child fills prove its entire quantity closed.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);a,b=signal(),signal(7)
        assert service.execute(a).status=='complete'
        assert service.execute(b).status=='complete'
        ids=[state_ids(store,x) for x in (a,b)]
        for kind in ('tp','trailing'):
            for owned in ids:
                ex.fill(owned[kind])
        service.cleanup_closed_exits()
        assert set(x[1] for x in ex.calls if x[0]=='cancel')=={x['sl'] for x in ids}
        assert ex.amount==0

    # 8: an ambiguous MARKET response is reconciled by exact client ID.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);ex.lose_entry=True
        assert service.execute(signal()).status=='complete'
        assert len(ex.clients)==1

    # 9: restart after durable fill but before even preparing SL.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);intent=signal()
        def crash():
            raise SystemExit('after durable fill, before position/protection')
        ex.after_entry_snapshot=crash
        try:
            service.execute(intent)
            assert False
        except SystemExit:
            pass
        durable=store.get_state(intent.event_id)
        assert durable['entry_filled'] and durable['verified_entry']['orderID']
        assert not any(x[0]=='sl' for x in ex.calls)
        assert service.execute(intent,recovery_only=True).status=='complete'
        assert len(ex.clients)==1

    # 10: SL canceled while TP/trailing are being established cannot yield COMPLETE.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp)
        def cancel_sl():
            for oid,kind in ex.kinds.items():
                if kind=='sl':
                    ex.orders[oid]['status']='CANCELLED'
        ex.on_trailing=cancel_sl
        result=service.execute(signal())
        assert result.status=='protection_changed_during_setup' and not result.details['ok'],result
        assert not store.seen(signal().event_id)

    # A manual size change during protection setup cannot be declared COMPLETE.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp)
        ex.on_trailing=lambda:setattr(ex,'amount',ex.amount+Decimal('0.001'))
        result=service.execute(signal())
        assert result.status=='exposure_reconcile_required_sl_active' and not result.details['ok'],result
        assert not store.seen(signal().event_id) and len(ex.clients)==1

    # 12: missing history, duplicate rows, invalid side/type cannot authorize entry.
    for bad in ('missing','duplicate','side','type'):
        with TemporaryDirectory() as tmp:
            service,store,ex=setup(tmp);a=signal()
            assert service.execute(a).status=='complete';ids=state_ids(store,a)
            if bad=='missing':
                ex.fill(ids['tp']);ex.hide_history=True
            elif bad=='duplicate':
                original=ex.get_open_orders
                ex.get_open_orders=lambda symbol:original(symbol)+original(symbol)[:1]
            else:
                ex.orders[ids['sl']]['positionSide' if bad=='side' else 'type']='LONG' if bad=='side' else 'MARKET'
            result=service.execute(signal(7))
            assert result.status in {'merged_reconcile_required','position_preflight_blocked'},result
            assert len(ex.clients)==1

    # 13: a TP fill is attributed to C16, not to the shared remaining amount.
    # Until its oversized old SL can be adjusted safely, retain it and escalate.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp);a=signal()
        assert service.execute(a).status=='complete';ids=state_ids(store,a)
        ex.fill(ids['tp'])
        service.cleanup_closed_exits()
        state=store.get_state(a.event_id)
        assert Decimal(state['exit_cleanup']['remaining_qty'])==Decimal('0.0006')
        assert state['safety_status']=='RECONCILE_REQUIRED'
        assert ex.orders[ids['sl']]['status']=='NEW'
        assert not any(x[0]=='cancel' for x in ex.calls)

    # 14: a second service cannot act while another process/store owns the lock.
    with TemporaryDirectory() as tmp:
        service,store,ex=setup(tmp)
        other=ExecutionStore(str(Path(tmp)/'execution.db'))
        with other.trading_lock() as held:
            assert held
            assert service.execute(signal()).status=='execution_lock_busy'
        assert not ex.clients

    print({'ok':True,'same_side_entries_allowed':True,'shared_id_fill_identity':True,
           'tp_and_full_exit_during_peer_entry':True,'owned_cleanup_preserves_peer':True,
           'post_timeout_and_restart_no_duplicate':True,'real_risk_block_only':True})


if __name__=='__main__':
    main()
