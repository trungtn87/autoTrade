"""One stoppable archive worker per V2 lifecycle, isolated from execution."""
import logging
import threading
from datetime import datetime, timedelta, timezone
from .archive import run_once
from .admission import ArchiveDeferred, archive_policy
from .errors import ArchiveConfigurationError, ArchiveRateLimited
from .scheduler import ready_slot, FAILURE_BACKOFF, BUSY_BACKOFF

LOG = logging.getLogger(__name__)


class CollectorRuntime:
    def __init__(self, admit=lambda: True, notify=None):
        self.notify = notify
        self.admit = admit
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self.status = {'state': 'stopped'}

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker, name='v2-trade-collector', daemon=True)
            self._thread.start()

    def request_stop(self):
        self._stop.set()

    def stop(self):
        self.request_stop()
        # Never cancel a to_thread call and release its lock while HTTP work
        # continues. Refuse a new worker until the previous thread exits.
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _publish(self):
        if self.notify is not None and not self._stop.is_set():
            try:
                self.notify(dict(self.status))
            except Exception as exc:
                LOG.error('V2_COLLECTOR_NOTIFICATION_FAILED error_class=%s', type(exc).__name__)

    def _worker(self):
        with archive_policy(self.admit, self._stop):
            self._run()

    def _run(self):
        completed = None
        next_attempt = datetime.min.replace(tzinfo=timezone.utc)
        self.status = {'state': 'waiting'}
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            slot = ready_slot(now)
            if slot is not None and slot != completed and now >= next_attempt:
                self.status = {'state': 'running', 'started_at': now.isoformat()}
                self._publish()
                try:
                    result = run_once(now)
                    if result.get('reason') == 'archive_locked':
                        next_attempt = datetime.now(timezone.utc) + BUSY_BACKOFF
                    elif (result.get('bingx') or {}).get('end_ms', int(slot.timestamp()*1000)) < int(slot.timestamp()*1000):
                        # A resumed window is complete, but new data arrived
                        # while it was paused. Catch that tail up next minute.
                        next_attempt = datetime.now(timezone.utc) + timedelta(minutes=1)
                    else:
                        completed = slot
                        next_attempt = slot + timedelta(hours=1)
                    self.status = {'state': 'waiting', 'result':'skipped' if result.get('skipped') else 'completed',
                                   'finished_at': datetime.now(timezone.utc).isoformat(),
                                   'counts': {k:v for k,v in (result.get('bingx') or {}).items() if k in {'orders','fills','income','forced','positions','changes'}}}
                except ArchiveRateLimited as exc:
                    next_attempt = datetime.now(timezone.utc) + timedelta(seconds=exc.retry_seconds)
                    self.status = {'state':'cooldown', 'retry_at':next_attempt.isoformat(), 'error_class':'ArchiveRateLimited'}
                except ArchiveDeferred:
                    next_attempt = datetime.now(timezone.utc) + BUSY_BACKOFF
                    self.status = {'state': 'deferred', 'reason': 'execution_priority_or_shutdown'}
                except Exception as exc:
                    next_attempt = datetime.now(timezone.utc) + FAILURE_BACKOFF
                    self.status = {'state': 'failed', 'error_class': type(exc).__name__}
                    if isinstance(exc, ArchiveConfigurationError):
                        self.status['configuration_code'] = exc.code
                    LOG.error('V2_COLLECTOR_FAILED error_class=%s retry_seconds=900', type(exc).__name__)
                self._publish()
            self._stop.wait(30)
        self.status = {'state': 'stopped'}
