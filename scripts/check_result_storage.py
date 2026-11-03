"""Synthetic PostgreSQL checks, invoked only by the isolated migration runner."""
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from autotrade_v2.analytics.ledger import write_result, RevisionConflict, EvidenceConflict


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
    print('PASS: storage receipts, atomic rollback, concurrency, restart retry, outage retry, legacy overwrite guard, duplicate closure and private access')


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
