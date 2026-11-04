"""Read and format V2-normalized results. No exchange or execution imports."""
import json
from datetime import datetime, timezone
from collections import defaultdict
from sqlalchemy import text, inspect
from decimal import Decimal, InvalidOperation
from sqlalchemy.exc import SQLAlchemyError



def _decimal(value):
    """Use exact stored quantities; missing/invalid provider facts are not proof."""
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    except (ValueError, TypeError, ArithmeticError):
        return None


def _raw(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except ValueError:
            pass
    return {}



def _signed_decimal(value):
    """Provider PnL and USDT commissions may be positive, zero or negative."""
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (ValueError, TypeError, ArithmeticError):
        return None


def _estimated_roi(entry, exits, fills):
    """Fee-adjusted estimate from complete exact-order fills; funding excluded.

    Only used after single-entry fully matched exit-quantity confirmation.
    """
    leverage_text = str(_raw(entry['raw']).get('leverage') or '').strip().upper()
    leverage = _decimal(leverage_text.removesuffix('X'))
    if leverage is None or leverage <= 0:
        return None
    notional = Decimal(0)
    net_pnl = Decimal(0)
    for index, order in enumerate([entry, *exits]):
        order_fills = fills.get((order['symbol'], str(order['order_id'])), [])
        expected_qty = _decimal(order['filled_qty'])
        if not order_fills or expected_qty is None or expected_qty <= 0:
            return None
        filled_qty = Decimal(0)
        for fill in order_fills:
            quantity = _decimal(fill['qty'])
            price = _decimal(fill['price'])
            realized = _signed_decimal(fill['realized_pnl'])
            fee = _signed_decimal(fill['fee'])
            asset = _raw(fill['raw']).get('commissionAsset')
            if (fill['side'] != order['side'] or fill['position_side'] != order['position_side']
                    or asset not in (None, '', 'USDT') or quantity is None or quantity <= 0
                    or price is None or price <= 0 or realized is None or fee is None):
                return None
            filled_qty += quantity
            net_pnl += realized + fee
            if index == 0:
                notional += quantity * price
        # A missing or duplicated fill must not produce a misleading ROI.
        if abs(filled_qty - expected_qty) > max(Decimal('0.00000001'), expected_qty * Decimal('0.000001')):
            return None
    margin = notional / leverage
    if margin <= 0:
        return None
    return float(net_pnl), float(margin), float(100 * net_pnl / margin)



def _journal_exit_bindings(states, orders):
    """Bind every V2 protection parent to its unique, confirmed strategy entry.

    BingX emits a *different child order ID* when a native SL/TP/trailing
    triggers. Its raw triggerOrderId identifies the original V2 protection.
    A shared positionID is not enough to allocate a fill between Combos.
    """
    from autotrade_v2.analytics.bridge import extract_link

    # Older V2 snapshots (notably 24 Oct) contain protection IDs but omit
    # state.position_id. The *exact* entry order still has the provider's
    # positionID; never infer ownership merely from a shared position group.
    entry_orders = defaultdict(list)
    for order in orders:
        if order['type'] in ('MARKET', 'LIMIT') and order['side'] in ('BUY', 'SELL'):
            entry_orders[(order['symbol'], str(order['order_id']))].append(order)
    bindings = {}
    claims = defaultdict(set)
    for record in states:
        linked = extract_link(record['payload'], record['event_id'])
        state = _raw(record['payload'])
        if not linked or not isinstance(state, dict):
            continue
        key = (linked['symbol'], linked['entry_order_id'])
        candidates = entry_orders.get(key, [])
        if len(candidates) != 1:
            continue
        exact_entry = candidates[0]
        if (exact_entry['side'] != linked['side'] or
                exact_entry['position_side'] !=
                ('LONG' if linked['side'] == 'BUY' else 'SHORT') or
                _decimal(exact_entry['filled_qty']) in (None, Decimal(0))):
            continue
        entry_provider = _raw(exact_entry['raw'])
        position_id = str(entry_provider.get('positionID') or
                          entry_provider.get('positionId') or '')
        state_position = str(state.get('position_id') or '')
        # Existing state data, when present, must agree with the provider
        # entry; a mismatch is not an opportunity to guess a replacement ID.
        if (not position_id or position_id == '0' or
                (state_position not in ('', '0') and state_position != position_id)):
            continue
        protection = state.get('protection') or {}
        if not isinstance(protection, dict):
            continue
        parents = {}
        invalid = False
        for name, kind in (('tp', 'TAKE_PROFIT_MARKET'), ('sl', 'STOP_MARKET'),
                           ('trailing', 'TRAILING_STOP_MARKET')):
            item = protection.get(name)
            if not isinstance(item, dict) or not item.get('order_id'):
                continue
            parent = str(item['order_id'])
            if parent in parents:
                invalid = True
                break
            parents[parent] = kind
        if invalid or not parents:
            continue
        binding = dict(position_id=position_id, combo=linked['combo'],
                       side=linked['side'], parents=parents)
        if key in bindings and bindings[key] != binding:
            bindings[key] = None
            continue
        bindings[key] = binding
        for parent in parents:
            claims[(linked['symbol'], parent)].add(key)
    # One native protection parent must not be claimed by multiple entries.
    ambiguous = {key for parent, keys in claims.items() if len(keys) > 1 for key in keys}
    for key in ambiguous:
        bindings[key] = None
    return bindings


def _owned_exits(group, entry_side, binding):
    """Return matching triggered child orders only, or a failed ownership proof."""
    exits = []
    seen_parents = set()
    invalid = False
    for order in group:
        if order['side'] == entry_side:
            continue
        provider = _raw(order['raw'])
        trigger_a = str(provider.get('triggerOrderId') or '')
        trigger_b = str(provider.get('triggerOrderID') or '')
        parent = trigger_a or trigger_b
        if not parent and str(order['order_id']) in binding['parents']:
            parent = str(order['order_id'])
        if parent not in binding['parents']:
            continue
        expected_kind = binding['parents'][parent]
        if ((trigger_a and trigger_b and trigger_a != trigger_b)
                or order['type'] != expected_kind
                or order['side'] not in ('BUY', 'SELL')
                or parent in seen_parents):
            invalid = True
            continue
        seen_parents.add(parent)
        exits.append(order)
    return exits, not invalid


def _linked_results(engine, schema, has_fills=False, has_states=False):
    """Read confirmed strategy entries already in DB. Never query BingX.

    The exchange may aggregate several Combo entries under one positionID.
    Such a position must not be split into invented per-Combo outcomes/ROI.
    """
    prefix = 'autotrade_analytics.' if schema else ''
    with engine.connect() as conn:
        links = conn.execute(text(
            'SELECT l.entry_order_id,l.symbol,l.combo,l.side AS link_side,'
            'o.side,o.position_side,o.created_ms,o.filled_qty,o.raw '
            'FROM ' + prefix + 'signal_links l JOIN ' + prefix + 'bingx_orders o '
            "ON o.order_id=l.entry_order_id AND o.symbol=l.symbol "
            "WHERE o.status='FILLED' AND o.type IN ('MARKET','LIMIT') AND o.filled_qty>0"
        )).mappings().all()
        orders = conn.execute(text(
            'SELECT order_id,symbol,side,position_side,type,status,filled_qty,'
            'updated_ms,raw FROM ' + prefix + "bingx_orders WHERE status='FILLED'"
        )).mappings().all()
        stored_fills = conn.execute(text(
            'SELECT order_id,symbol,side,position_side,qty,price,realized_pnl,fee,raw '
            'FROM ' + prefix + 'bingx_fills'
        )).mappings().all() if has_fills else []
        state_prefix = 'public.' if schema else ''
        states = conn.execute(text(
            'SELECT event_id,payload FROM ' + state_prefix +
            "processed_targets WHERE target='bingx_state' AND payload IS NOT NULL"
        )).mappings().all() if has_states else []

    bindings = _journal_exit_bindings(states, orders)
    fills_by_order = defaultdict(list)
    for fill in stored_fills:
        fills_by_order[(fill['symbol'], str(fill['order_id']))].append(fill)

    positions = defaultdict(list)
    for order in orders:
        provider = _raw(order['raw'])
        pid = str(provider.get('positionID') or provider.get('positionId') or '')
        if pid:
            positions[(order['symbol'], pid, order['position_side'])].append(order)

    results = []
    exit_kinds = {'STOP_MARKET':'SL', 'TAKE_PROFIT_MARKET':'TP',
                  'TRAILING_STOP_MARKET':'TRAILING', 'MARKET':'MANUAL', 'LIMIT':'MANUAL'}
    for link in links:
        entry_id, symbol = str(link['entry_order_id']), link['symbol']
        provider = _raw(link['raw'])
        pid = str(provider.get('positionID') or provider.get('positionId') or '')
        qty = _decimal(link['filled_qty'])
        entry_side = link['side']
        side_matches = link['link_side'] in (None, '', entry_side)
        expected_position_side = 'LONG' if entry_side == 'BUY' else 'SHORT' if entry_side == 'SELL' else None
        group = positions.get((symbol, pid, link['position_side']), []) if pid else []
        entries = [o for o in group if o['side'] == entry_side
                   and o['type'] in ('MARKET','LIMIT') and _decimal(o['filled_qty'])]
        # In a shared BingX position, the first entry belongs to another
        # Combo. Always use the exact linked entry for costs and margin.
        linked_entry = next((o for o in entries if str(o['order_id']) == entry_id), None)
        binding = bindings.get((symbol, entry_id))
        use_owned_exits = bool(
            binding and binding['position_id'] == pid
            and binding['combo'] == link['combo']
            and binding['side'] == entry_side)
        if use_owned_exits:
            exits, ownership_ok = _owned_exits(group, entry_side, binding)
        else:
            # Retain the old single-entry fallback only when no evidence
            # links a shared position to an individual strategy intent.
            exits = [o for o in group if o['side'] != entry_side
                     and o['type'] in exit_kinds and _decimal(o['filled_qty'])]
            ownership_ok = len(entries) == 1
        exit_quantity = sum((_decimal(o['filled_qty']) for o in exits), start=_decimal(0))
        exit_times = [int(o['updated_ms'] or 0) for o in exits]
        fully_closed = (side_matches and expected_position_side == link['position_side']
                        and qty is not None and qty > 0 and ownership_ok
                        and linked_entry is not None
                        and bool(exits) and all(o['side'] in ('BUY','SELL') for o in exits)
                        and exit_quantity == qty
                        and all(t >= int(link['created_ms'] or 0) for t in exit_times))
        # Gross profit proves W/L here, but never fabricates net ROI/fees.
        gross = []
        if fully_closed:
            for o in exits:
                try:
                    value = Decimal(str(_raw(o['raw']).get('profit')))
                    if not value.is_finite():
                        raise InvalidOperation
                    gross.append(value)
                except (ValueError, TypeError, InvalidOperation):
                    gross = []
                    break
        total_profit = sum(gross, Decimal(0)) if len(gross) == len(exits) and gross else None
        outcome = ('WIN' if total_profit > 0 else
                   'LOSS' if total_profit < 0 else 'BREAKEVEN') if total_profit is not None else None
        estimate = _estimated_roi(linked_entry, exits, fills_by_order) if fully_closed else None
        if estimate is not None:
            net_pnl = estimate[0]
            outcome = 'WIN' if net_pnl > 0 else 'LOSS' if net_pnl < 0 else 'BREAKEVEN'
        results.append(dict(
            event_id='db-entry:' + symbol + ':' + entry_id,
            entry_order_id=entry_id, symbol=symbol, combo=link['combo'],
            side=entry_side, status='CLOSED' if fully_closed else 'UNRESOLVED',
            opened_ms=int(link['created_ms'] or 0),
            closed_ms=max(exit_times) if fully_closed else None,
            outcome=outcome, roi=estimate[2] if estimate is not None else None,
            pnl=estimate[0] if estimate is not None else None,
            margin=estimate[1] if estimate is not None else None,
            source=('bingx_db_roi_estimate' if estimate is not None else
                    'bingx_db_verified' if fully_closed else 'bingx_db_unresolved'),
            exit_types=sorted({exit_kinds[o['type']] for o in exits}) if fully_closed else []
        ))
    return results


def _event_time_ms(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _v2_state_results(engine, inspector):
    """Live AutoTrade V2 already persists entry proof in public.processed_targets.

    This read-only fallback prevents a failed optional BingX archive from hiding
    new V2 trades. Its states prove an entry, NOT a closed position or ROI.
    """
    pg = engine.dialect.name == 'postgresql'
    prefix = 'public.' if pg else ''
    if not inspector.has_table('processed_targets', schema='public' if pg else None):
        return []
    with engine.connect() as conn:
        states = conn.execute(text(
            'SELECT event_id,payload,processed_at FROM ' + prefix +
            "processed_targets WHERE target='bingx_state' AND payload IS NOT NULL"
        )).mappings().all()
    filled_events = {}
    if inspector.has_table('system_events', schema='public' if pg else None):
        try:
            with engine.connect() as conn:
                events = conn.execute(text(
                    'SELECT event_id, MIN(created_at) filled_at FROM ' + prefix +
                    "system_events WHERE event_key='L3.EXEC.ORDER_FILLED' GROUP BY event_id"
                )).mappings().all()
            filled_events = {e['event_id']: _event_time_ms(e['filled_at']) for e in events}
        except SQLAlchemyError:
            # Event timestamps are optional. A missing log must not hide V2 entries.
            pass

    from autotrade_v2.analytics.bridge import extract_link
    rows = []
    for item in states:
        link = extract_link(item['payload'], item['event_id'])
        if not link:
            continue
        opened = (filled_events.get(item['event_id']) or link['signal_time_ms']
                  or _event_time_ms(item['processed_at']) or 0)
        if opened <= 0:
            continue
        rows.append(dict(
            event_id='v2-state:' + item['event_id'],
            entry_order_id=link['entry_order_id'],
            symbol=link['symbol'], combo=link['combo'], side=link['side'],
            opened_ms=int(opened), closed_ms=None, status='UNRESOLVED',
            outcome=None, roi=None, pnl=None, margin=None,
            source='v2_state_db', exit_types=[]
        ))
    return rows


def load_results(engine):
    # Dashboard-only read: an unapplied migration must not hide existing history.
    schema = 'autotrade_analytics' if engine.dialect.name == 'postgresql' else None
    inspector = inspect(engine)
    has_normalized = inspector.has_table('trade_results', schema=schema)
    has_history = 'manual_dashboard_trade_results' in inspector.get_view_names(schema=schema)
    has_links = inspector.has_table('signal_links', schema=schema)
    has_orders = inspector.has_table('bingx_orders', schema=schema)
    has_fills = inspector.has_table('bingx_fills', schema=schema)
    has_v2_states = inspector.has_table('processed_targets',
                                         schema='public' if schema else None)
    if not has_normalized and not has_history and not (has_links and has_orders) and not has_v2_states:
        return None
    rows = []
    if has_normalized:
        table = 'autotrade_analytics.trade_results' if schema else 'trade_results'
        with engine.connect() as conn:
            normalized = conn.execute(text('SELECT payload FROM ' + table)).scalars().all()
        rows = [json.loads(row) if isinstance(row, str) else row for row in normalized]
    if has_history:
        table = ('autotrade_analytics.manual_dashboard_trade_results' if schema
                 else 'manual_dashboard_trade_results')
        with engine.connect() as conn:
            history = conn.execute(text(
                "SELECT entry_order_id,symbol,combo,side,entry_time_ms,exit_ms,"
                "outcome,roi_pct FROM " + table + " WHERE status='CLOSED'"
            )).mappings().all()
        covered = {(r.get('symbol'), str(r['entry_order_id']))
                   for r in rows if r.get('status') == 'CLOSED' and r.get('entry_order_id')}
        for old in history:
            identity = (old['symbol'], str(old['entry_order_id']))
            if identity in covered or old['roi_pct'] is None or old['outcome'] not in ('W', 'L'):
                continue
            covered.add(identity)
            rows.append(dict(
                event_id='history:' + old['symbol'] + ':' + str(old['entry_order_id']),
                entry_order_id=str(old['entry_order_id']),
                symbol=old['symbol'], combo=old['combo'], side=old['side'],
                status='CLOSED', opened_ms=int(old['entry_time_ms']),
                closed_ms=int(old['exit_ms']),
                outcome='WIN' if old['outcome'] == 'W' else 'LOSS',
                roi=float(old['roi_pct']), pnl=None, margin=None,
                source='existing_history', exit_types=[]
            ))
    if has_links and has_orders:
        # Backfill visibility from the durable DB, without a new exchange API.
        # A closed historical snapshot has precedence over the same raw entry.
        known = {(r.get('symbol'), str(r.get('entry_order_id')))
                 for r in rows if r.get('entry_order_id')}
        for linked in _linked_results(engine, schema, has_fills=has_fills,
                                      has_states=has_v2_states):
            identity = (linked['symbol'], linked['entry_order_id'])
            if identity not in known:
                rows.append(linked)
                known.add(identity)
    if has_v2_states:
        # V2's durable execution state is newer than the optional BingX archive.
        # Never replace a confirmed exchange result or double-count a matched ID.
        known = {(r.get('symbol'), str(r.get('entry_order_id')))
                 for r in rows if r.get('entry_order_id')}
        try:
            for state in _v2_state_results(engine, inspector):
                identity = (state['symbol'], state['entry_order_id'])
                if identity not in known:
                    rows.append(state)
                    known.add(identity)
        except SQLAlchemyError:
            if not rows:
                raise
            # An unavailable optional source cannot blank the retained history.
    return rows


def sync_metadata(run):
    if run is None:
        return None
    try:
        details = json.loads(run.details)
    except (ValueError, TypeError):
        details = {}
    coverage = run.finished_at
    if isinstance(details, dict) and details.get('stage') == 'raw_sync' and details.get('end_ms') is not None:
        coverage = datetime.fromtimestamp(details['end_ms']/1000, timezone.utc)
    return dict(status=run.status, time=coverage.isoformat() if coverage else None,
                completed_at=run.finished_at.isoformat() if run.finished_at else None)


def result_payload(rows, last_sync=None, symbol='', combo='', start_ms=0):
    available = rows is not None
    rows = rows or []
    history = []
    groups = defaultdict(list)
    closed_count = 0
    for row in rows:
        opened = row.get('opened_ms') or 0
        is_closed = row.get('status') == 'CLOSED' and bool(row.get('closed_ms'))
        if opened < start_ms or not (opened or is_closed):
            continue
        if (symbol and row.get('symbol') != symbol) or (combo and row.get('combo') != combo):
            continue
        groups[(row['symbol'], row['combo'])].append(row)
        if is_closed:
            closed_count += 1
        history.append(dict(symbol=row['symbol'], combo=row['combo'], side=row['side'],
                            status='closed' if is_closed else (row.get('status') or 'UNRESOLVED').lower(),
                            entry_ms=opened or None,
                            exit_ms=row.get('closed_ms') if is_closed else None,
                            outcome=row.get('outcome') if is_closed else None,
                            result=row.get('outcome') if is_closed else 'UNRESOLVED',
                            roi_pct=row.get('roi') if is_closed else None,
                            recorded_roi_pct=row.get('roi') if is_closed else None,
                            pnl=row.get('pnl') if is_closed else None,
                            margin=row.get('margin') if is_closed else None,
                            source=row.get('source'), exit_types=row.get('exit_types', [])))
    combos = []
    for (sym, name), trades in sorted(groups.items(), key=lambda item: (item[0][0] or '', str(item[0][1] or ''))):
        closed = [r for r in trades if r.get('status') == 'CLOSED' and r.get('closed_ms')]
        wins = sum(t.get('outcome') == 'WIN' for t in closed)
        losses = sum(t.get('outcome') == 'LOSS' for t in closed)
        complete = bool(closed) and all(t.get('outcome') in {'WIN', 'LOSS', 'BREAKEVEN'} for t in closed)
        # Include ROI for all closed trades with data, even when other trades
        # in this Combo have not yet obtained a full set of fills.
        priced = [t for t in closed if t.get('roi') is not None]
        weighted = bool(priced) and all(
            t.get('pnl') is not None and t.get('margin') is not None
            and t['margin'] > 0 for t in priced)
        actual_roi = (100 * sum(t['pnl'] for t in priced) /
                      sum(t['margin'] for t in priced)) if weighted else (
                          sum(t['roi'] for t in priced) if priced else None)
        # Historical Excel ROI has no PnL/margin; its percentage is retained.
        combos.append(dict(symbol=sym, combo=name, total=len(trades), closed=len(closed),
                           unresolved=len(trades)-len(closed), win=wins, loss=losses,
                           tp_count=wins if complete else None,
                           sl_count=losses if complete else None,
                           win_rate=round(100*wins/len(closed), 2) if complete else None,
                           real_roi_total=round(actual_roi, 4) if actual_roi is not None else None,
                           roi_estimated=any(t.get('source') == 'bingx_db_roi_estimate' for t in priced),
                           roi_coverage=len(priced)))
    return dict(theory=dict(available=available,
                            reason=None if available else 'Existing DB results are unavailable',
                            combos=combos,
                            history=sorted(history, key=lambda t: t['exit_ms'] or t['entry_ms'] or 0,
                                           reverse=True)),
                last_sync=last_sync, trades=[],
                summary={'closed':closed_count, 'total':len(history)},
                unresolved=sum(r.get('status') != 'CLOSED' for r in rows))
