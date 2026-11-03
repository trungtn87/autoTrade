"""Pure lifecycle projection from durable execution evidence, never prices/PnL.

terminal/flat/cleanup=done are NOT closure evidence. Quantities are cumulative
per exact owned order, so TP + trailing is one result and never double counted.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from .contract import InvalidResult, validate_result, validate_transition


class LifecycleEvidenceError(InvalidResult):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def oid(row):
    if row.get('orderId') and row.get('orderID') and str(row['orderId']) != str(row['orderID']):
        raise LifecycleEvidenceError('provider_order_alias_conflict')
    return str(row.get('orderID') or row.get('orderId') or '')


def quantity(value):
    try:
        if value is None or isinstance(value, bool):
            raise InvalidOperation
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise InvalidOperation
        return number
    except (InvalidOperation, ValueError) as exc:
        raise LifecycleEvidenceError('invalid_confirmed_quantity') from exc


def fill_time(order):
    # Creation time, signal time and local observation time are never fill proof.
    for field in ('filledTime', 'fillTime', 'updateTime'):
        value = order.get(field)
        if value is not None:
            if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
                raise LifecycleEvidenceError('invalid_fill_timestamp')
            # updateTime only proves fill time while the order is filled/active.
            if field == 'updateTime' and order.get('status') not in ('FILLED', 'PARTIALLY_FILLED'):
                continue
            return int(value)
    return None


def combo_name(value):
    if isinstance(value, bool) or not str(value).isdigit():
        return None
    n = int(value)
    if n not in (*range(1,15), *range(101,107)):
        return None
    return 'TIER' if n == 11 else f'C{n-90 if n >= 101 else n}'


def project_result(event_id, state, previous=None, *, ambiguous=False):
    intent = state.get('intent') or {}
    if intent.get('event_id') not in (None,event_id):
        raise LifecycleEvidenceError('intent_event_id_mismatch')
    ambiguous = ambiguous or intent.get('event_id') != event_id
    symbol, side = intent.get('symbol'), intent.get('side')
    combo = combo_name(intent.get('combo'))
    observations = state.get('analytics_entry_evidence') or []
    if not observations and not state.get('entry_accepted') and not state.get('entry_filled'):
        return None  # A prepared signal/absent order is not a confirmed trade.
    if state.get('analytics_entry_evidence_truncated'):
        raise LifecycleEvidenceError('entry_observations_truncated')
    refs, entries = [], []
    query_id = state.get('client_order_id')
    for record in observations:
        order = record['order']
        if order.get('status') not in ('FILLED','PARTIALLY_FILLED','CANCELED','CANCELLED','EXPIRED'):
            continue
        qty = quantity(order.get('executedQty'))
        if qty == 0:
            continue
        if (not query_id or record.get('query_client_order_id') != query_id
                or (order.get('clientOrderId') or order.get('clientOrderID')) != query_id
                or (order.get('clientOrderId') and order.get('clientOrderID')
                    and order['clientOrderId'] != order['clientOrderID'])
                or not oid(order) or order.get('symbol') != symbol or order.get('side') != side):
            raise LifecycleEvidenceError('entry_identity_unverified')
        if order.get('origQty', order.get('quantity')) is not None and qty > quantity(order.get('origQty', order.get('quantity'))):
            raise LifecycleEvidenceError('entry_exceeds_order_quantity')
        entries.append((order, qty, fill_time(order)))
        refs.append('entry:' + str(symbol) + ':' + digest(record))
    entry_ids = {oid(order) for order, _, _ in entries}
    if len(entry_ids) > 1:
        raise LifecycleEvidenceError('multiple_entry_order_ids')
    entry_id = state.get('order_id')
    if entries and not entry_id:
        # Journal snapshots before the final execution write may not yet have
        # the top-level ID. Keep them unresolved until recovery confirms it.
        entries = []
        entry_ids = set()
    if entry_ids and entry_ids != {str(entry_id)}:
        raise LifecycleEvidenceError('entry_state_order_mismatch')
    if entry_id and entries and not ambiguous:
        refs.append('binding:' + digest({'event_id':event_id,'entry_order_id':str(entry_id),
                    'symbol':symbol,'side':side,'combo':combo,'client_order_id':query_id}))
    opened_qty = None
    opened_ms = None
    final_entry = False
    if entries:
        amounts = [qty for _,qty,_ in entries]
        if any(b < a for a,b in zip(amounts, amounts[1:])):
            raise LifecycleEvidenceError('entry_quantity_regressed')
        opened_qty = amounts[-1]
        opened_ms = min((ts for _,_,ts in entries if ts is not None), default=None)
        final_entry = entries[-1][0].get('status') == 'FILLED'
        if previous and previous.get('opened_ms') is not None:
            # Preserve the first published confirmed fill timestamp. New earlier
            # history requires explicit correction instead of rewriting it.
            if opened_ms is not None and opened_ms < previous['opened_ms']:
                raise LifecycleEvidenceError('earlier_entry_time_correction_required')
            opened_ms = previous['opened_ms']
    if previous and quantity(previous.get('closed_qty') or 0) > 0 and 'lifecycle_exits' not in previous:
        raise LifecycleEvidenceError('legacy_exit_evidence_reconciliation_required')
    # Keep proven cumulative legs even if a later source page lacks a cached
    # view. Key by original protection parent to avoid parent+child double count.
    legs = {parent:dict(leg) for parent,leg in (previous or {}).get('lifecycle_exits',{}).items()}
    exits = {}
    protection = state.get('protection') or {}
    monitor = (state.get('exit_cleanup') or {}).get('orders') or {}
    owned = {}
    for key, kind in (('tp','TP'), ('sl','SL'), ('trailing','TRAILING')):
        parent = str((protection.get(key) or {}).get('order_id') or '')
        if not parent:
            continue
        if parent == str(entry_id):
            raise LifecycleEvidenceError('entry_reused_as_exit_identity')
        if parent in owned:
            raise LifecycleEvidenceError('duplicate_protection_identity')
        owned[parent] = kind
        view = monitor.get(parent) or {}
        order = view.get('analytics_order')
        if not order:
            continue
        child, trigger = oid(order), str(order.get('triggerOrderId') or order.get('triggerOrderID') or '')
        pid = str(order.get('positionId') or order.get('positionID') or '')
        if order.get('positionId') and order.get('positionID') and str(order['positionId']) != str(order['positionID']):
            raise LifecycleEvidenceError('provider_position_alias_conflict')
        if (not child or (child != parent and trigger != parent)
                or (trigger and trigger != parent)
                or order.get('symbol') != symbol
                or order.get('side') != ('SELL' if side == 'BUY' else 'BUY')
                or (state.get('position_id') and pid != str(state['position_id']))):
            raise LifecycleEvidenceError('exit_identity_unverified')
        qty = quantity(order.get('executedQty'))
        if view.get('executed_qty') is not None and quantity(view['executed_qty']) != qty:
            raise LifecycleEvidenceError('exit_monitor_quantity_conflict')
        old_leg = legs.get(parent)
        if old_leg and qty < quantity(old_leg['qty']):
            raise LifecycleEvidenceError('exit_quantity_regressed')
        if qty == 0:
            continue
        if order.get('status') not in ('FILLED','PARTIALLY_FILLED','CANCELED','CANCELLED','EXPIRED'):
            raise LifecycleEvidenceError('exit_quantity_without_execution_status')
        if child in exits or child == str(entry_id):
            raise LifecycleEvidenceError('duplicate_exit_execution')
        if order.get('origQty', order.get('quantity')) is not None and qty > quantity(order.get('origQty', order.get('quantity'))):
            raise LifecycleEvidenceError('exit_exceeds_order_quantity')
        exits[child] = qty
        timestamp = fill_time(order)
        if old_leg:
            old_child = old_leg['execution_order_id']
            if old_leg['kind'] != kind or (old_child != parent and child != parent and old_child != child):
                raise LifecycleEvidenceError('exit_execution_identity_changed')
            if qty == quantity(old_leg['qty']) and old_leg['time_ms'] is not None:
                timestamp = old_leg['time_ms']
        legs[parent] = dict(qty=str(qty),kind=kind,execution_order_id=child,time_ms=timestamp)
        refs.append('exit:' + str(symbol) + ':' + parent + ':' + digest(order))
    if len({leg['execution_order_id'] for leg in legs.values()}) != len(legs):
        raise LifecycleEvidenceError('duplicate_exit_execution')
    exit_types = {leg['kind'] for leg in legs.values()}
    exit_times = [leg['time_ms'] for leg in legs.values()]
    closed_qty = sum((quantity(leg['qty']) for leg in legs.values()), Decimal(0))
    status = 'UNRESOLVED'
    if not ambiguous and symbol in ('BTC-USDT','ETH-USDT') and side in ('BUY','SELL') and combo and opened_ms and opened_qty:
        if closed_qty > opened_qty:
            raise LifecycleEvidenceError('exit_exceeds_confirmed_entry')
        if closed_qty == 0:
            status = 'OPEN'
        elif closed_qty < opened_qty:
            status = 'PARTIAL'
        elif final_entry and exit_times and all(ts is not None for ts in exit_times):
            status = 'CLOSED'
    if not refs:
        refs = ['state:' + digest({'event_id':event_id, 'state':state})]
    row = dict(contract_version=1, event_id=event_id, symbol=symbol, side=side, combo=combo,
               entry_order_id=str(entry_id) if entry_id else None, opened_ms=opened_ms,
               closed_ms=max(exit_times) if status == 'CLOSED' else None, status=status,
               opened_qty=str(opened_qty) if opened_qty is not None else None,
               closed_qty=str(closed_qty), exit_types=sorted(exit_types),
               lifecycle_exits=legs,
               pnl=None, fees=None, margin=None, roi=None, outcome=None,
               verification_source='V2_CONFIRMED_ORDER_EVIDENCE', evidence_refs=sorted(set(refs)))
    if previous:
        # Only preserve missing facts/evidence, never silently replace a known
        # identity or regress a quantity. The PR 1 transition guard decides.
        for key in ('pnl','fees','margin','roi','outcome','money_verified','money_evidence_refs','margin_evidence_ref'):
            if key in previous:
                row[key] = previous[key]
        row['verification_source'] = previous.get('verification_source') or previous.get('source') or row['verification_source']
        row['evidence_refs'] = sorted(set(row['evidence_refs'] + (previous.get('evidence_refs') or [])))
        row['exit_types'] = sorted(set(row['exit_types'] + (previous.get('exit_types') or [])))
    row = validate_result(row)
    if previous:
        validate_transition(previous, row)
    return row
