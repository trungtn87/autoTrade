"""In-process lifecycle worker. Own DB pool; never execution locks or BingX.

Recovery scans durable bingx_state, including terminal executions. Production
composition keeps enabled=False until PR 3 adapts the collector to this ledger.
"""
import json
import logging
import threading
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from .contract import InvalidResult
from .ledger import RevisionConflict, write_result
from .projection import digest, project_result

LOG = logging.getLogger(__name__)
PAGE_SIZE = 100


def claim_ids(state):
    if not isinstance(state, dict):
        return set()
    ids = {str(state['order_id'])} if state.get('order_id') else set()
    protection = state.get('protection')
    if isinstance(protection, dict):
        ids.update(str(item['order_id']) for item in protection.values()
                   if isinstance(item,dict) and item.get('order_id'))
    return ids


def writer_engine(database_url):
    if database_url.startswith('postgres://'):
        database_url = 'postgresql+psycopg://' + database_url[len('postgres://'):]
    elif database_url.startswith('postgresql://'):
        database_url = 'postgresql+psycopg://' + database_url[len('postgresql://'):]
    if not database_url.startswith('postgresql+psycopg://'):
        raise ValueError('postgresql_required')
    return create_engine(database_url, pool_size=1, max_overflow=0, pool_timeout=1,
                         pool_pre_ping=True, connect_args={'connect_timeout':3,'prepare_threshold':None})


class DatabaseSource:
    def __init__(self, engine):
        self.engine = engine

    def page(self, cursor):
        with self.engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            rows = conn.execute(text('''SELECT event_id, payload FROM public.processed_targets
                WHERE target='bingx_state' AND event_id > :cursor
                ORDER BY event_id LIMIT :limit'''), {'cursor':cursor,'limit':PAGE_SIZE}).all()
        decoded = []
        for eid,payload in rows:
            try:
                state = json.loads(payload) if isinstance(payload,str) else payload
            except (ValueError,TypeError):
                state = None
            decoded.append((str(eid),state))
        return decoded

    def ambiguous(self, states):
        ids = set().union(*(claim_ids(state) for _,state in states))
        if not ids:
            return set()
        # PG 17 JSON predicate protects against malformed legacy TEXT payloads.
        # Check ownership across all pages, not just the current page.
        query = text('''WITH states AS (
            SELECT event_id, CASE WHEN payload IS JSON OBJECT THEN payload::jsonb
                ELSE '{}'::jsonb END AS state
            FROM public.processed_targets WHERE target='bingx_state'
        ), claims AS (
            SELECT event_id, state->>'order_id' AS oid FROM states
            UNION ALL
            SELECT event_id, p.value->>'order_id' FROM states
            CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(state->'protection')='object'
                THEN state->'protection' ELSE '{}'::jsonb END) p
        ) SELECT oid FROM claims WHERE oid IN :ids
          GROUP BY oid HAVING count(DISTINCT event_id)>1''').bindparams(bindparam('ids', expanding=True))
        with self.engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            return set(conn.execute(query, {'ids':sorted(ids)}).scalars())

    def current(self, event_id):
        with self.engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            row = conn.execute(text('''SELECT revision,payload FROM autotrade_analytics.trade_results
                WHERE event_id=:eid'''), {'eid':event_id}).mappings().first()
            return (row['revision'],row['payload']) if row else (0,None)


class LifecycleWriter:
    def __init__(self, engine, source=None):
        self.engine = engine
        self.source = source or DatabaseSource(engine)

    def process(self, event_id, state, ambiguous=False):
        # Bounded CAS retry: recompute against the winning snapshot, never
        # blind-overwrite. Other errors retry on a later worker pass.
        for attempt in range(3):
            revision, previous = self.source.current(event_id)
            row = project_result(event_id, state, previous, ambiguous=ambiguous)
            if row is None:
                return 'skipped'
            if row == previous:
                return 'unchanged'
            try:
                _, change = write_result(self.engine, row, source='v2-lifecycle-state',
                    evidence_id=digest({'event_id':event_id,'snapshot':row}), expected_revision=revision)
                return change
            except RevisionConflict:
                if attempt == 2:
                    raise


class LifecycleWriterRuntime:
    def __init__(self, database_url='', *, enabled=False, notify=None, factory=None,
                 interval=5, retry_seconds=15):
        self.enabled = enabled
        self.database_url = database_url
        self.notify = notify
        self.factory = factory
        self.interval, self.retry_seconds = interval, retry_seconds
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._cursor = ''
        self._last_notice = None
        self.status = {'state':'disabled','reason':'collector_contract_pending'}

    def start(self):
        if not self.enabled:
            return  # No thread, engine, DB or provider work while gated.
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._cursor = ''  # Every restart scans all unfinished lifecycle data.
            self._last_notice = None
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='v2-lifecycle-writer',daemon=True)
            self._thread.start()

    def request_stop(self):
        self._stop.set()

    def stop(self):
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _publish(self, state):
        self.status = state
        notice = (state.get('state'),state.get('error_class'),state.get('last_record_error'))
        if notice == self._last_notice:
            return
        if self.notify:
            try:
                self.notify(dict(state))
            except Exception as exc:
                LOG.error('V2_LIFECYCLE_NOTIFICATION_FAILED error_class=%s',type(exc).__name__)
                return
        self._last_notice = notice

    def run_page(self, writer):
        states = writer.source.page(self._cursor)
        collisions = writer.source.ambiguous(states)
        counts = dict(inserted=0,updated=0,unchanged=0,skipped=0,failed=0)
        last_error = None
        for eid,state in states:
            if self._stop.is_set():
                break
            try:
                counts[writer.process(eid,state,ambiguous=bool(collisions.intersection(claim_ids(state))))] += 1
            except SQLAlchemyError:
                raise  # One outage/schema failure => bounded pass backoff, not N connection attempts.
            except Exception as exc:
                counts['failed'] += 1
                last_error = str(exc) if isinstance(exc,InvalidResult) else type(exc).__name__
                # Never include provider payload, event/order IDs or DB URL.
                LOG.error('V2_LIFECYCLE_RECORD_FAILED error_class=%s',type(exc).__name__)
        # Poison records cannot starve later intents; failed records reappear
        # on the next full scan, and a restart starts at the first page.
        self._cursor = states[-1][0] if states else ''
        self._publish({'state':'degraded' if counts['failed'] else 'waiting',
                       'counts':counts,'last_record_error':last_error})
        return counts

    def _run(self):
        engine = None
        writer = None
        try:
            while not self._stop.is_set():
                delay = self.interval
                try:
                    if writer is None:
                        if self.factory:
                            writer = self.factory()
                        else:
                            engine = writer_engine(self.database_url)
                            writer = LifecycleWriter(engine)
                    self.run_page(writer)
                except Exception as exc:
                    delay = self.retry_seconds
                    self._publish({'state':'failed','error_class':type(exc).__name__,
                                   'retry_seconds':delay})
                    LOG.error('V2_LIFECYCLE_PASS_FAILED error_class=%s',type(exc).__name__)
                self._stop.wait(delay)
        finally:
            if engine is not None:
                engine.dispose()
            self.status = {'state':'stopped'}
