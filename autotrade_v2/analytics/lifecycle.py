"""One stoppable archive worker per V2 lifecycle, isolated from execution."""
import logging
import threading
from datetime import datetime, timedelta, timezone
from .archive import run_once
from .scheduler import ready_slot, FAILURE_BACKOFF, BUSY_BACKOFF

LOG = logging.getLogger(__name__)


class CollectorRuntime:
    def __init__(self):
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self.status = {'state': 'stopped'}

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='v2-trade-collector', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        # Never cancel a to_thread call and release its lock while HTTP work
        # continues. Refuse a new worker until the previous thread exits.
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _run(self):
        completed = None
        next_attempt = datetime.min.replace(tzinfo=timezone.utc)
        self.status = {'state': 'waiting'}
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            slot = ready_slot(now)
            if slot is not None and slot != completed and now >= next_attempt:
                self.status = {'state': 'running', 'started_at': now.isoformat()}
                try:
                    result = run_once(now)
                    if result.get('reason') == 'archive_locked':
                        next_attempt = datetime.now(timezone.utc) + BUSY_BACKOFF
                    else:
                        completed = slot
                        next_attempt = slot + timedelta(hours=1)
                    self.status = {'state': 'waiting', 'finished_at': datetime.now(timezone.utc).isoformat()}
                except Exception as exc:
                    next_attempt = datetime.now(timezone.utc) + FAILURE_BACKOFF
                    self.status = {'state': 'failed', 'error_class': type(exc).__name__}
                    LOG.error('V2_COLLECTOR_FAILED error_class=%s retry_seconds=900', type(exc).__name__)
            self._stop.wait(30)
        self.status = {'state': 'stopped'}
