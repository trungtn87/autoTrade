"""Synthetic PostgreSQL checks, invoked only by the isolated migration runner."""
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from autotrade_v2.analytics.ledger import write_result, RevisionConflict, EvidenceConflict
from autotrade_v2.analytics.contract import InvalidResult, validate_result


def verify_storage(engine):
    row = dict(contract_version=1, event_id='storage-1', symbol='ETH-USDT', combo='C2',
               side='SELL', entry_order_id='storage-entry', opened_ms=1000, closed_ms=None,
               status='OPEN', outcome=None, exit_types=[], opened_qty='2', closed_qty='0',
               pnl=None, fees=None, margin=None, roi=None,
               verification_source='V2_CONFIRMED_ORDER', evidence_refs=['entry:storage-entry'])

    def write(payload, evidence='entry', expected=0):
        return write_result(engine, payload, source='synthetic', evidence_id=evidence,
                            expected_revision=expected)

    assert write(row) == (1, 'inserted')
    # Retry a committed observation after simulated process restart / lost ACK.
    assert write(dict(row)) == (1, 'unchanged')
    expect(EvidenceConflict, lambda:write({**row, 'combo':'C3'}))
    expect(EvidenceConflict, lambda:write({**row, 'event_id':'another-intent'}))
    partial = {**row, 'status':'PARTIAL', 'closed_qty':'1', 'exit_types':['TP'],
               'evidence_refs':row['evidence_refs'] + ['fill:tp']}
    expect(RevisionConflict, lambda:write(partial, 'partial', 0))
    assert write(partial, 'partial', 1) == (2, 'updated')
    # Old evidence replay must not roll PARTIAL back to OPEN.
    assert write(row) == (1, 'unchanged')
    assert scalar(engine, "SELECT status FROM autotrade_analytics.trade_results WHERE event_id='storage-1'") == 'PARTIAL'
    final = {**partial, 'status':'CLOSED', 'closed_qty':'2', 'closed_ms':2000,
             'exit_types':['TP','TRAILING'],
             'evidence_refs':partial['evidence_refs'] + ['fill:trailing']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, final, 'final', 2) for _ in range(2)]
        assert sorted(f.result()[1] for f in futures) == ['unchanged','updated']
    assert scalar(engine, "SELECT count(*) FROM autotrade_analytics.trade_results WHERE event_id='storage-1'") == 1
    assert scalar(engine, "SELECT count(*) FROM autotrade_analytics.trade_result_receipts WHERE event_id='storage-1'") == 3
    # A different intent cannot count the same exchange entry a second time.
    expect(DBAPIError, lambda:write({**final,'event_id':'duplicate-entry'}, 'duplicate-entry', 0))
    assert scalar(engine, "SELECT count(*) FROM autotrade_analytics.trade_result_receipts WHERE evidence_id='duplicate-entry'") == 0
    # Legacy collector UPDATE without a new revision must not erase V2 results.
    try:
        execute(engine, "UPDATE autotrade_analytics.trade_results SET payload=payload WHERE event_id='storage-1'")
    except DBAPIError as exc:
        assert exc.orig.sqlstate == '40001'
    else:
        raise AssertionError('Legacy collector bypassed revision guard')
    # Constraint rejection must roll back the snapshot AND its receipt.
    for patch in ({'money_verified':False}, {'fees':None}, {'roi':'10'}, {'outcome':'WIN'},
                  {'contract_version':None}, {'evidence_refs':None}, {'margin_evidence_ref':None}):
        money = {**final, 'pnl':'-1', 'fees':'0.1', 'margin':'10', 'roi':'-10',
                 'outcome':'LOSS', 'money_verified':True,
                 'money_evidence_refs':['money:1'], 'margin_evidence_ref':'margin:1',
                 'evidence_refs':final['evidence_refs'] + ['money:1','margin:1'], **patch}
        # Deliberately bypass Python validation to exercise PostgreSQL constraints.
        import json
        def invalid_write():
            with engine.begin() as conn:
                conn.execute(text('''UPDATE autotrade_analytics.trade_results
                    SET payload=CAST(:payload AS jsonb), revision=4 WHERE event_id='storage-1' '''),
                             {'payload':json.dumps(money)})
        expect(DBAPIError, invalid_write)
    for role in ('anon', 'authenticated'):
        def forbidden():
            with engine.begin() as conn:
                conn.execute(text('SET LOCAL ROLE ' + role))
                conn.execute(text('SELECT * FROM autotrade_analytics.trade_result_receipts'))
        expect(DBAPIError, forbidden)
    # Separate stale writers race: exactly one revision may commit.
    def race(i):
        try:
            return write(final, 'reconcile-' + str(i), 3)[1]
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(race, range(2))) == ['conflict','updated']
    # Inject a connection acquisition outage, then retry with a fresh connection.
    from unittest.mock import Mock
    offline = Mock(dialect=engine.dialect)
    offline.begin.side_effect = OperationalError('synthetic outage', {}, Exception())
    expect(OperationalError, lambda:write_result(offline, final, source='synthetic',
           evidence_id='after-outage', expected_revision=4))
    assert write(final, 'after-outage', 4) == (5, 'updated')
    assert scalar(engine, "SELECT count(*) FROM autotrade_analytics.trade_result_receipts WHERE event_id='storage-1'") == 5
    # Fail after snapshot UPDATE, before receipt INSERT: both must roll back.
    from sqlalchemy import event
    def fail_receipt(conn, cursor, statement, parameters, context, executemany):
        if 'INSERT INTO autotrade_analytics.trade_result_receipts' in statement:
            raise OperationalError('synthetic mid-transaction outage', {}, Exception())
    event.listen(engine, 'before_cursor_execute', fail_receipt)
    try:
        expect(OperationalError, lambda:write(final, 'mid-transaction', 5))
    finally:
        event.remove(engine, 'before_cursor_execute', fail_receipt)
    assert scalar(engine, "SELECT revision FROM autotrade_analytics.trade_results WHERE event_id='storage-1'") == 5
    assert scalar(engine, "SELECT count(*) FROM autotrade_analytics.trade_result_receipts WHERE evidence_id='mid-transaction'") == 0
    money = {**final, 'pnl':'-1', 'fees':'0.1', 'margin':'10', 'roi':'-10',
             'outcome':'LOSS', 'money_verified':True,
             'money_evidence_refs':['money:1'], 'margin_evidence_ref':'margin:1',
             'evidence_refs':final['evidence_refs'] + ['money:1','margin:1']}
    assert write(money, 'mid-transaction', 5) == (6, 'updated')
    assert scalar(engine, "SELECT outcome||'|'||roi::text FROM autotrade_analytics.trade_results WHERE event_id='storage-1'") == 'LOSS|-10'
    verify_transitions(engine, row, money)
    print('PASS: storage receipts, atomic rollback, concurrency, restart retry, outage retry, legacy overwrite guard, duplicate closure and private access')


def verify_transitions(engine, base, money):
    """Probe independently valid snapshots through API AND direct SQL updates."""
    import json
    states = {
        'UNRESOLVED': dict(status='UNRESOLVED', closed_qty='0', closed_ms=None, exit_types=[]),
        'OPEN': dict(status='OPEN', closed_qty='0', closed_ms=None, exit_types=[]),
        'PARTIAL': dict(status='PARTIAL', closed_qty='1', closed_ms=None, exit_types=['TP']),
        'CLOSED': dict(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP']),
    }
    allowed = {'UNRESOLVED': {'UNRESOLVED','OPEN','PARTIAL','CLOSED'},
               'OPEN': {'OPEN','PARTIAL','CLOSED'},
               'PARTIAL': {'PARTIAL','CLOSED'}, 'CLOSED': {'CLOSED'}}

    def probe(label, before, after, accepted=False):
        eid = 'transition-' + label
        before = validate_result({**before, 'event_id':eid, 'entry_order_id':eid})
        after = validate_result({**after, 'event_id':eid, 'entry_order_id':eid})
        def write(value, suffix, revision):
            return write_result(engine, value, source='transition-test',
                                evidence_id=eid + ':' + suffix, expected_revision=revision)
        assert write(before, 'before', 0) == (1, 'inserted')
        def read():
            with engine.connect() as conn:
                current = conn.execute(text('''SELECT payload, revision, updated_at
                    FROM autotrade_analytics.trade_results WHERE event_id=:eid'''), {'eid':eid}).one()
                receipts = conn.execute(text('''SELECT revision, snapshot
                    FROM autotrade_analytics.trade_result_receipts
                    WHERE event_id=:eid ORDER BY revision'''), {'eid':eid}).all()
                return current, receipts
        original = read()
        # Direct SQL intentionally bypasses validate_transition; revision alone
        # and a payload claiming correction approval cannot bypass the DB guard.
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(text('''UPDATE autotrade_analytics.trade_results
                    SET payload=CAST(:payload AS jsonb), revision=2, updated_at=now()
                    WHERE event_id=:eid'''), {'payload':json.dumps(after), 'eid':eid})
            except DBAPIError as exc:
                if accepted:
                    raise
                assert exc.orig.sqlstate == '23514', (label, exc.orig.sqlstate)
                if after['status'] not in allowed[before['status']]:
                    assert exc.orig.diag.message_primary == 'status_regression_correction_required'
            else:
                assert accepted, 'SQL accepted regression: ' + label
            finally:
                tx.rollback()
        assert read() == original, label
        if accepted:
            assert write(after, 'after', 1) == (2, 'updated')
            current, receipts = read()
            assert current.payload == after and current.revision == 2
            assert [(r.revision, r.snapshot) for r in receipts] == [(1,before),(2,after)]
            assert write(after, 'after', 1) == (2, 'unchanged')
        else:
            expect(InvalidResult, lambda:write(after, 'after', 1))
            assert read() == original, 'Rejected write changed snapshot/history: ' + label
            assert write(before, 'before', 0) == (1, 'unchanged')

    for old, fields in states.items():
        before = {**base, **fields}
        for new, new_fields in states.items():
            after = {**before, **new_fields,
                     'exit_types':sorted(set(before['exit_types'] + new_fields['exit_types'])),
                     'evidence_refs':before['evidence_refs'] + ['new-observation']}
            probe(old + '-' + new, before, after, new in allowed[old])
    partial = {**base, **states['PARTIAL']}
    for label, changes in (
        ('closed-qty-decrease', {'closed_qty':'0.5'}),
        ('closed-qty-tiny-decrease', {'closed_qty':'0.99999999999999999999'}),
        ('opened-qty-decrease', {'opened_qty':'1.5'}),
        ('exit-evidence-loss', {'exit_types':['SL']}),
    ):
        probe(label, partial, {**partial, **changes,
            'evidence_refs':partial['evidence_refs'] + ['correction-proof'],
            'correction':{'approved':True,'reason':'provider correction'}})
    unresolved = {**partial,'status':'UNRESOLVED'}
    for key in ('opened_qty','closed_qty'):
        probe(key + '-null', unresolved, {**unresolved,key:None})
    closed = {**base, **states['CLOSED']}
    for label, changes in (
        ('closed-volume-increase', {'opened_qty':'3','closed_qty':'3'}),
        ('closed-volume-decrease', {'opened_qty':'1','closed_qty':'1'}),
        ('opened-time-change', {'opened_ms':900}),
        ('closed-time-change', {'closed_ms':3000}),
    ):
        probe(label, closed, {**closed, **changes,'evidence_refs':closed['evidence_refs'] + ['new-proof']})
    for label, changes in (
        ('known-pnl-change', {'pnl':'-2','roi':'-20'}),
        ('known-pnl-erase', {'pnl':None,'roi':None,'outcome':None}),
        ('known-fees-change', {'fees':'0.2'}),
        ('known-margin-change', {'margin':'20','roi':'-5'}),
        ('known-result-erase', {'outcome':None}),
        ('known-roi-erase', {'roi':None}),
        ('known-money-refs-replace', {'money_evidence_refs':['new-proof']}),
        ('known-margin-ref-replace', {'margin_evidence_ref':'new-proof'}),
        ('known-money-sign-flip', {'pnl':'1','roi':'10','outcome':'WIN'}),
    ):
        probe(label, money, {**money, **changes,'evidence_refs':money['evidence_refs'] + ['new-proof']})
    zero = {**money,'pnl':0,'fees':0,'roi':0,'outcome':'BREAKEVEN'}
    probe('known-zero-change', zero, {**zero,'pnl':'1','roi':'10','outcome':'WIN'})
    probe('verified-source-change', base, {**base,'verification_source':'UNVERIFIED'})
    pending = {**base,'money_verified':True,'money_evidence_refs':['money-proof'],
               'evidence_refs':base['evidence_refs'] + ['money-proof']}
    probe('verification-flag-loss', pending, {**pending,'money_verified':False})
    probe('pending-money-refs-loss', pending, {**pending,'money_evidence_refs':None})
    probe('progress-no-new-evidence', base, {**base,**states['PARTIAL']})
    probe('partial-progress', partial, {**partial,'closed_qty':'1.5',
          'evidence_refs':partial['evidence_refs'] + ['fill-2']}, True)
    probe('numeric-equivalence', money, {**money,'pnl':-1.0,'fees':0.1,'margin':10,'roi':-10}, True)
    probe('null-money-enrichment', {**closed,'exit_types':money['exit_types']}, money, True)
    print('PASS: all 16 state transitions, monotone fills, frozen CLOSED/money/results/proofs, correction bypass rejection, SQL guards and unchanged rejected revisions/receipts')


def execute(engine, sql):
    with engine.begin() as conn:
        conn.execute(text(sql))


def scalar(engine, sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def expect(kind, action):
    try:
        action()
    except kind:
        return
    raise AssertionError('Expected ' + kind.__name__)
