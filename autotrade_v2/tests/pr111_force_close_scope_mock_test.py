"""MOCK tests for Force Close scope (PR #111): only the failing combo is closed.

In-memory exchange fake only. No network, no live orders. This is NOT live
BingX evidence: whether BingX merges same-side combos into one container
exactly as modelled here is unverified and needs owner sign-off before deploy.
"""
from dataclasses import replace
from decimal import Decimal
from tempfile import TemporaryDirectory

from app.execution.force_close import side_key, TARGET
from merged_entry_test import setup, signal, state_ids
from protection_retry_abort_test import FailureExchange


def entry_calls(ex):
    return [x for x in ex.calls if x[0] == 'entry']


def main():
    # 1. C1 force close leaves C2 intact in the same container.
    with TemporaryDirectory() as tmp:
        service, store, _ = setup(tmp)
        ex = FailureExchange(failures=0)
        service.executor.client = ex
        c2, c1 = signal(), signal(7)  # c2 is the peer that must survive
        assert service.execute(c2).status == 'complete'
        c2_ids = state_ids(store, c2)
        c2_qty = Decimal(str(store.get_state(c2.event_id)['executed_qty']))
        ex.failures = 2
        assert service.execute(c1).status == 'force_close_confirmed'
        assert ex.amount == c2_qty, (ex.amount, c2_qty)
        assert all(ex.orders[oid]['status'] == 'NEW' for oid in c2_ids.values())
        assert not any(c[0] == 'cancel' and c[1] in c2_ids.values() for c in ex.calls)
        assert store.get_state(c2.event_id).get('stage') != 'force_close_confirmed'
        assert store.get_state(c1.event_id)['stage'] == 'force_close_confirmed'

    # 2. C2 signal while C1 emergency is pending: refused, and no new Entry POST.
    with TemporaryDirectory() as tmp:
        service, store, _ = setup(tmp)
        ex = FailureExchange(failures=0)
        service.executor.client = ex
        c1, c2, c3 = signal(), signal(7), signal(7, 2)
        assert service.execute(c1).status == 'complete'
        ex.failures = 2
        ex.close_hidden = True
        assert service.execute(c2).status == 'force_close_unresolved'
        epoch = store.get(side_key(c1.symbol, c1.side), TARGET)
        assert epoch['phase'] != 'closed'
        entries_before = len(entry_calls(ex))
        refused = service.execute(c3)
        assert refused.status == 'force_close_side_blocked', refused
        assert len(entry_calls(ex)) == entries_before
        # The lock lifts once the emergency is confirmed.
        ex.close_hidden = False
        assert service.execute(c2, recovery_only=True).status == 'force_close_confirmed'
        assert service.execute(c3).status == 'complete'

    print('PASS: pr111 force-close scope (MOCK, not live evidence)')


if __name__ == '__main__':
    main()
