"""Archive admission only: execution never waits for this policy."""
from contextlib import contextmanager
from contextvars import ContextVar

_policy = ContextVar('v2_archive_admission', default=None)


class ArchiveDeferred(RuntimeError):
    """Yield archive work to execution or application shutdown."""


@contextmanager
def archive_policy(admit, stop):
    token = _policy.set((admit, stop))
    try:
        yield
    finally:
        _policy.reset(token)


def check_admission(heartbeat=None):
    policy = _policy.get()
    if policy is not None:
        admit, stop = policy
        while not stop.is_set():
            if admit():
                return
            # Yield in place rather than replaying a long archive from its
            # beginning at every candle. Keep the advisory transaction alive.
            if heartbeat is not None:
                heartbeat()
            stop.wait(5)
        raise ArchiveDeferred('Archive stopped during shutdown')
