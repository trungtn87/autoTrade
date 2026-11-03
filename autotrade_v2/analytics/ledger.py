"""Transactional PostgreSQL storage, staged for the V2 lifecycle writer.

No automatic DDL, background thread, execution imports, or exchange calls.
Call off the execution/protection path. Connection failures propagate to the
worker, which must retry from durable execution state with the same evidence ID.
"""
import json
from sqlalchemy import text
from .contract import validate_result, validate_transition


class RevisionConflict(RuntimeError):
    """Reload current snapshot and reconcile evidence before retrying."""


class EvidenceConflict(RuntimeError):
    """An evidence identifier was reused with a different snapshot/intent."""


def write_result(engine, payload, *, source, evidence_id, expected_revision):
    """Atomically store snapshot + receipt; return (revision, inserted/updated/unchanged).

    Evidence IDs must be namespaced by account/symbol/provider event or durable
    V2 state revision. Exact retries work even when expected_revision is stale.
    A changed snapshot always needs a fresh receipt and the current revision.
    """
    row = validate_result(payload)
    if (not isinstance(source, str) or not 1 <= len(source) <= 80
            or not isinstance(evidence_id, str) or not 1 <= len(evidence_id) <= 240
            or type(expected_revision) is not int or expected_revision < 0):
        raise ValueError('invalid_storage_identity')
    if engine.dialect.name != 'postgresql':
        raise ValueError('postgresql_required')
    data = json.dumps(row, sort_keys=True, allow_nan=False)
    args = dict(event_id=row['event_id'], source=source, evidence_id=evidence_id,
                payload=data, expected=expected_revision)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '1s'"))
        conn.execute(text("SET LOCAL statement_timeout = '5s'"))
        # Serialize same evidence across different intents, then same intent
        # across different evidence IDs. No locks are shared with execution.
        conn.execute(text('SELECT pg_advisory_xact_lock(2708, hashtext(:key))'),
                     {'key': source + ':' + evidence_id})
        conn.execute(text('SELECT pg_advisory_xact_lock(2709, hashtext(:key))'),
                     {'key': row['event_id']})
        old = conn.execute(text('''SELECT event_id, revision, snapshot
            FROM autotrade_analytics.trade_result_receipts
            WHERE source=:source AND evidence_id=:evidence_id'''), args).mappings().first()
        if old:
            if old['event_id'] != row['event_id'] or old['snapshot'] != row:
                raise EvidenceConflict('evidence_identity_conflict')
            return old['revision'], 'unchanged'
        current = conn.execute(text('''SELECT revision, payload FROM autotrade_analytics.trade_results
            WHERE event_id=:event_id FOR UPDATE'''), args).mappings().first()
        if (current['revision'] if current else 0) != expected_revision:
            raise RevisionConflict('result_revision_conflict')
        if current:
            validate_transition(current['payload'], row)
        revision = (current['revision'] if current else 0) + 1
        args['revision'] = revision
        if current is None:
            conn.execute(text('''INSERT INTO autotrade_analytics.trade_results
                (event_id, payload, revision) VALUES (:event_id, CAST(:payload AS jsonb), :revision)'''), args)
        else:
            conn.execute(text('''UPDATE autotrade_analytics.trade_results
                SET payload=CAST(:payload AS jsonb), revision=:revision, updated_at=now()
                WHERE event_id=:event_id'''), args)
        conn.execute(text('''INSERT INTO autotrade_analytics.trade_result_receipts
            (source, evidence_id, event_id, revision, snapshot)
            VALUES (:source, :evidence_id, :event_id, :revision, CAST(:payload AS jsonb))'''), args)
        return revision, 'inserted' if current is None else 'updated'
