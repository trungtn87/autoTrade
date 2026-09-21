"""Durable FINAL14 order lifecycle; uncertain submissions never trigger resends."""
from __future__ import annotations
import logging
from .final14_config import case_name

log = logging.getLogger(__name__)

def case_id(symbol, combo):
    return f"{symbol.upper()}|{case_name(combo)}"

def is_case_active(state, symbol, combo):
    return any(r['case_id'] == case_id(symbol,combo) for r in state.final14_records(symbol))

def snapshot(state):
    return state.final14_records()

def refresh_symbol(state, executor, symbol):
    records = state.final14_records(symbol)
    closed, unresolved = [], []
    for rec in records:
        event = rec['event_id']
        try:
            detail = executor.query_entry(rec)
            status = str(detail.get('status') or '').upper()
            qty = float(detail.get('executedQty') or 0)
            changes = {'entry_order_id': str(detail.get('orderId') or detail.get('orderID') or rec.get('entry_order_id') or '')}
            if status in {'CANCELED','CANCELLED','REJECTED','EXPIRED'} and qty == 0:
                state.update_final14(event,{**changes,'status':'rejected'})
                closed.append(rec['case_id'])
                continue
            if qty <= 0:
                unresolved.append(rec['case_id'])
                continue
            pid = str(detail.get('positionId') or detail.get('positionID') or rec.get('position_id') or '')
            if pid == '0': pid = ''
            changes.update(qty=qty,avg_price=float(detail.get('avgPrice') or 0),position_id=pid)
            if not pid:
                state.update_final14(event,{**changes,'status':'position_id_pending'})
                unresolved.append(rec['case_id'])
                continue
            # Read positions AFTER the order query, never infer a fill was closed
            # from a position snapshot taken before its fill.
            positions = executor._positions(symbol)
            current = [p for p in positions if str(p.get('positionId') or p.get('positionID') or '') == pid and abs(float(p.get('positionAmt') or 0)) > 0]
            if not current and status in {'FILLED','CANCELED','CANCELLED','EXPIRED'}:
                # Confirm absence again within this scan, allowing same-bar
                # re-entry at the signal close exactly as the reference model.
                again = executor._positions(symbol)
                still_open = any(str(p.get('positionId') or p.get('positionID') or '') == pid
                                 and abs(float(p.get('positionAmt') or 0)) > 0 for p in again)
                if still_open:
                    unresolved.append(rec['case_id'])
                else:
                    state.update_final14(event,{**changes,'status':'closed'})
                    closed.append(rec['case_id'])
            elif current:
                verified = executor.protection_matches(detail,rec)
                state.update_final14(event,{**changes,'absent_checks':0,'status':'active' if verified else 'protection_unverified','protection_verified':verified})
                if not verified:
                    unresolved.append(rec['case_id'])
                    log.error('FINAL14_PROTECTION_UNVERIFIED symbol=%s case=%s position_id=%s',symbol,rec['case_id'],pid)
            else:
                unresolved.append(rec['case_id'])
        except Exception as exc:
            state.update_final14(event,{'reconcile_error':str(exc)})
            unresolved.append(rec['case_id'])
            log.warning('FINAL14_RECONCILE_PENDING case=%s error=%s',rec['case_id'],exc)
    # Detect positions from older engines/manual trading before adding exposure.
    positions = executor._positions(symbol)
    claimed = {r.get('position_id') for r in state.final14_records(symbol) if r.get('position_id')}
    unmanaged = [str(p.get('positionId') or p.get('positionID') or '') for p in positions
                 if abs(float(p.get('positionAmt') or 0)) > 0 and str(p.get('positionId') or p.get('positionID') or '') not in claimed]
    return {'symbol':symbol,'active':len(state.final14_records(symbol)), 'closed':closed,
            'unresolved':unresolved,'unmanaged_position_ids':unmanaged,
            'entry_blocked':bool(unmanaged)}
