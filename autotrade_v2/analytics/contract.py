"""Versioned result storage contract. Pure validation; no exchange access.

PnL means net attributable PnL, including actual fees and funding. Verification
references are evidence identifiers, never a substitute for provider validation.
"""
from decimal import Decimal, InvalidOperation
import json


class InvalidResult(ValueError):
    pass


# Normal observations only. Corrections must never reuse this transition path.
ALLOWED_TRANSITIONS = {
    'UNRESOLVED': ('UNRESOLVED', 'OPEN', 'PARTIAL', 'CLOSED'),
    'OPEN': ('OPEN', 'PARTIAL', 'CLOSED'),
    'PARTIAL': ('PARTIAL', 'CLOSED'),
    'CLOSED': ('CLOSED',),
}


def validate_result(payload):
    """Return a detached JSON snapshot; unknown facts must be explicit NULLs."""
    try:
        row = json.loads(json.dumps(payload, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise InvalidResult('invalid_json') from exc
    required = {'event_id', 'symbol', 'combo', 'side', 'entry_order_id',
                'opened_ms', 'closed_ms', 'status', 'outcome', 'exit_types',
                'opened_qty', 'closed_qty', 'pnl', 'fees', 'margin', 'roi',
                'verification_source', 'evidence_refs', 'contract_version'}
    if not isinstance(row, dict) or not required <= row.keys():
        raise InvalidResult('missing_fields')
    if type(row['contract_version']) is not int or row['contract_version'] != 1:
        raise InvalidResult('unsupported_version')
    if not isinstance(row['event_id'], str) or not 1 <= len(row['event_id']) <= 120:
        raise InvalidResult('invalid_event_id')
    if row['status'] not in ('OPEN', 'PARTIAL', 'CLOSED', 'UNRESOLVED'):
        raise InvalidResult('invalid_status')
    if row['outcome'] not in (None, 'WIN', 'LOSS', 'BREAKEVEN'):
        raise InvalidResult('invalid_outcome')
    for key in ('symbol', 'combo', 'side', 'entry_order_id'):
        if row[key] is not None and (not isinstance(row[key], str) or not row[key].strip()):
            raise InvalidResult('invalid_identity')
    if row['side'] not in (None, 'BUY', 'SELL'):
        raise InvalidResult('invalid_side')
    if not isinstance(row['verification_source'], str) or not row['verification_source'].strip():
        raise InvalidResult('missing_verification_source')
    if not isinstance(row['evidence_refs'], list) or not row['evidence_refs'] or any(
            not isinstance(ref, str) or not ref.strip() for ref in row['evidence_refs']):
        raise InvalidResult('missing_evidence_refs')
    if (not isinstance(row['exit_types'], list)
            or any(not isinstance(kind, str) or kind not in {'TP', 'SL', 'TRAILING', 'MANUAL', 'LIQUIDATION', 'ADL', 'OTHER'}
                   for kind in row['exit_types'])
            or len(set(row['exit_types'])) != len(row['exit_types'])):
        raise InvalidResult('invalid_exit_types')
    numbers = {}
    for key in ('opened_qty', 'closed_qty', 'pnl', 'fees', 'margin', 'roi'):
        value = row[key]
        if value is None:
            numbers[key] = None
            continue
        try:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise InvalidOperation
            numbers[key] = Decimal(str(value))
            if not numbers[key].is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError) as exc:
            raise InvalidResult('invalid_' + key) from exc
    for key in ('opened_qty', 'closed_qty', 'margin'):
        if numbers[key] is not None and numbers[key] < 0:
            raise InvalidResult('negative_' + key)
    for key in ('opened_ms', 'closed_ms'):
        if row[key] is not None and (type(row[key]) is not int or row[key] <= 0):
            raise InvalidResult('invalid_' + key)
    opened, closed = numbers['opened_qty'], numbers['closed_qty']
    tolerance = max(Decimal('0.000000001'), (opened or 0) * Decimal('0.000001'))
    if row['status'] != 'UNRESOLVED':
        if (any(row[key] is None for key in ('symbol', 'combo', 'side', 'entry_order_id', 'opened_ms'))
                or opened is None or opened <= 0 or closed is None or closed > opened + tolerance):
            raise InvalidResult('unverified_quantity_or_identity')
        if row['status'] == 'OPEN' and closed != 0:
            raise InvalidResult('open_has_exit')
        if row['status'] == 'PARTIAL' and not 0 < closed < opened - tolerance:
            raise InvalidResult('invalid_partial_quantity')
    if row['status'] == 'CLOSED':
        if (row['closed_ms'] is None or row['closed_ms'] < row['opened_ms']
                or closed <= 0 or abs(opened - closed) > tolerance or not row['exit_types']):
            raise InvalidResult('unverified_closure')
    elif row['closed_ms'] is not None or row['outcome'] is not None or row['roi'] is not None:
        raise InvalidResult('nonfinal_result')
    if numbers['pnl'] is not None:
        if (numbers['fees'] is None or row.get('money_verified') is not True
                or not row.get('money_evidence_refs')
                or not isinstance(row['money_evidence_refs'], list)
                or any(not isinstance(ref, str) or ref not in row['evidence_refs'] for ref in row['money_evidence_refs'])):
            raise InvalidResult('unverified_net_pnl')
    if row['outcome'] is not None:
        pnl = numbers['pnl']
        expected = None if pnl is None else 'WIN' if pnl > 0 else 'LOSS' if pnl < 0 else 'BREAKEVEN'
        if row['outcome'] != expected:
            raise InvalidResult('outcome_pnl_mismatch')
    if numbers['margin'] is not None:
        if (numbers['margin'] <= 0 or not row.get('margin_evidence_ref')
                or row['margin_evidence_ref'] not in row['evidence_refs']):
            raise InvalidResult('unverified_actual_margin')
    if numbers['roi'] is not None:
        if numbers['margin'] is None or numbers['pnl'] is None:
            raise InvalidResult('roi_without_money')
        if abs(numbers['roi'] - 100 * numbers['pnl'] / numbers['margin']) > Decimal('0.000001'):
            raise InvalidResult('roi_mismatch')
    # Compatibility with the existing generated source column and DB reader.
    if row.get('source', row['verification_source']) != row['verification_source']:
        raise InvalidResult('verification_source_mismatch')
    row['source'] = row['verification_source']
    return row


def validate_transition(previous, next_row):
    """Append observations without retracting verified facts.

    CLOSED is final; quantities advance monotonically until closure, then freeze.
    Once published, monetary totals/results are immutable, including zero. NULL
    may be enriched with verified evidence. There is deliberately no correction
    flag/API here: retractions require a separately reviewed correction mechanism.
    """
    old_status, new_status = previous.get('status'), next_row.get('status')
    if new_status not in ALLOWED_TRANSITIONS.get(old_status, ()):
        raise InvalidResult('status_regression_correction_required')
    for key in ('event_id', 'symbol', 'side', 'entry_order_id', 'combo', 'verification_source', 'source'):
        if previous.get(key) not in (None, '') and previous[key] != next_row.get(key):
            raise InvalidResult('confirmed_identity_changed')
    progressed = old_status != new_status
    for key in ('opened_qty', 'closed_qty'):
        before, after = _fact_number(previous.get(key)), _fact_number(next_row.get(key))
        if before is not None and (after is None or after < before):
            raise InvalidResult('quantity_regression_correction_required')
        if old_status == 'CLOSED' and before != after:
            raise InvalidResult('closed_quantity_correction_required')
        progressed = progressed or before != after
    for key in ('opened_ms', 'closed_ms', 'outcome', 'margin_evidence_ref'):
        if previous.get(key) is not None and previous[key] != next_row.get(key):
            raise InvalidResult('verified_fact_correction_required')
    for key in ('pnl', 'fees', 'margin', 'roi'):
        if previous.get(key) is not None and _fact_number(previous[key]) != _fact_number(next_row.get(key)):
            raise InvalidResult('verified_money_correction_required')
    if previous.get('money_verified') is True and next_row.get('money_verified') is not True:
        raise InvalidResult('money_verification_would_be_lost')
    for key in ('evidence_refs', 'money_evidence_refs', 'exit_types'):
        if any(ref not in (next_row.get(key) or []) for ref in (previous.get(key) or [])):
            raise InvalidResult('evidence_would_be_lost')
    if progressed and not any(ref not in (previous.get('evidence_refs') or [])
                              for ref in next_row['evidence_refs']):
        raise InvalidResult('progress_requires_new_evidence')


def _fact_number(value):
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise InvalidOperation
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except (InvalidOperation, ValueError) as exc:
        raise InvalidResult('invalid_quantity_or_money_fact') from exc
