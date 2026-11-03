"""Pure V2 :07 schedule helpers; no worker or Dashboard lifecycle here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


POLL_SECONDS = 30
FAILURE_BACKOFF = timedelta(minutes=15)
BUSY_BACKOFF = timedelta(minutes=5)


def archive_enabled(environ=None) -> bool:
    """Legacy compatibility flag; the Dashboard never consumes this helper."""
    return True


def ready_slot(now: datetime) -> datetime | None:
    """Current :07 UTC slot, or None before this hour's :07.

    Vietnam is UTC+7, so every UTC :07 is also VN :07.
    Skip the first 7 minutes after candle close by design.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    slot = now.replace(minute=7, second=0, microsecond=0)
    return slot if now >= slot else None


