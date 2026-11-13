"""MOCK integration tests for the entry path after the legacy-gate cleanup (PR #111).

Every scenario runs against the in-memory exchange fake from merged_entry_test.py.
This is mock evidence only. It is NOT live BingX evidence and proves nothing
about the real exchange behaviour. No network access.
"""
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore

from merged_entry_test import Exchange, setup, signal, state_ids


class BlindExchange(Exchange):
    """GET by clientOrderId fails with an error, so the order status is UNKNOWN."""

    blind = False

    def find_order_by_client_id(self, symbol, cid):
        if self.blind:
            raise TimeoutError('GET by clientOrderId failed')
        return super().find_order_by_client_id(symbol, cid)


def entry_calls(ex):
    return [x for x in ex.calls if x[0] == 'entry']


def restart(path, ex):
    """Simulate a process restart: new service and store over the same durable DB."""
    store = ExecutionStore(str(Path(path) / 'execution.db'))
    service = ExecutionService(replace(Settings(), execution_enabled=True, dry_run=False,
        tp_trailing_split_enabled=True, bingx_api_key='offline', bingx_api_secret='offline'), store)
    service.credentials_verified = True
    service.executor.client = ex
    return service, store


def main():
    # 1. Two same-direction BTC combos are independent: distinct orders, distinct client IDs.
    with TemporaryDirectory() as tmp:
        service, store, ex = setup(tmp)
        a, b = signal(), signal(7)
        assert service.execute(a).status == 'complete'
        first = state_ids(store, a)
        assert service.execute(b).status == 'complete'
        second = state_ids(store, b)
        assert set(first.values()).isdisjoint(second.values())
        assert len(entry_calls(ex)) == 2 and len(ex.clients) == 2
        assert ex.amount == Decimal('0.0026')

    # 2. An old order whose history is missing does not block a new entry.
    with TemporaryDirectory() as tmp:
        service, store, ex = setup(tmp)
        a, b = signal(), signal(7)
        assert service.execute(a).status == 'complete'
        ex.hide_history = True
        result = service.execute(b)
        assert result.status == 'complete', result
        assert len(entry_calls(ex)) == 2

    # 3. A failed TP/SL on an old combo does not lose a new signal.
    with TemporaryDirectory() as tmp:
        service, store, ex = setup(tmp)
        a, b = signal(), signal(7)
        assert service.execute(a).status == 'complete'
        first = state_ids(store, a)
        ex.orders[first['sl']]['status'] = 'REJECTED'
        result = service.execute(b)
        assert result.status == 'complete', result
        assert store.get_state(b.event_id) is not None
        assert len(entry_calls(ex)) == 2

    # 4. Entry POST times out, then GET by clientOrderId finds the order: no second Entry.
    with TemporaryDirectory() as tmp:
        service, store, ex = setup(tmp)
        intent = signal()
        ex.lose_entry = True
        service.execute(intent)
        assert len(entry_calls(ex)) == 1, ex.calls
        assert len(ex.clients) == 1
        service.execute(intent)
        assert len(entry_calls(ex)) == 1, ex.calls

    # 5. GET UNKNOWN: a failed lookup after a timed-out POST never assumes the order is absent.
    with TemporaryDirectory() as tmp:
        store_path = tmp
        service, store, _ = setup(tmp)
        ex = BlindExchange()
        service.executor.client = ex
        intent = signal()
        # Blind GET on a never-sent signal: the durable "not_sent" state proves no
        # POST happened, so the one POST is allowed. Its confirmation is UNKNOWN.
        ex.blind = True
        first = service.execute(intent)
        assert first.status == 'entry_unknown_reconcile_required', first
        assert len(entry_calls(ex)) == 1, ex.calls
        # The send is now marked "sending". Repeated attempts while blind never re-POST.
        service.execute(intent)
        service.execute(intent)
        assert len(entry_calls(ex)) == 1, ex.calls
        # Once GET works again, the same signal is recovered from the one existing order.
        ex.blind = False
        service.execute(intent)
        assert len(entry_calls(ex)) == 1, ex.calls

    # 5b. Legacy state without the "not_sent" mark: an UNKNOWN lookup must block, no POST.
    with TemporaryDirectory() as tmp:
        service, store, _ = setup(tmp)
        ex = BlindExchange()
        service.executor.client = ex
        intent = signal()
        cid = service.executor.client_order_id(intent.event_id, 'entry')
        store.save_state(intent.event_id, {'terminal': False, 'stage': 'entry_intent',
            'intent': asdict(intent), 'client_order_id': cid, 'pre_entry_positions': {},
            'shared_position': False, 'admission': {'blocking_risks': [], 'shared': False,
            'existing_entry_recovery': False}, 'exit_mode': 'split_trailing'})
        ex.blind = True
        result = service.execute(intent)
        assert result.status == 'entry_unknown_reconcile_required', result
        assert entry_calls(ex) == [], ex.calls

    # 6. Restart after an entry POST was accepted: the restarted worker must not re-POST.
    with TemporaryDirectory() as tmp:
        service, store, ex = setup(tmp)
        intent = signal()

        def crash():
            raise SystemExit('worker died after entry POST')

        ex.on_entry = crash
        try:
            service.execute(intent)
            raise AssertionError('expected simulated crash')
        except SystemExit:
            pass
        assert len(entry_calls(ex)) == 1
        service2, store2 = restart(tmp, ex)
        service2.execute(intent)
        assert len(entry_calls(ex)) == 1, ex.calls

    print('PASS: pr111 entry integration (MOCK, not live evidence)')


if __name__ == '__main__':
    main()
