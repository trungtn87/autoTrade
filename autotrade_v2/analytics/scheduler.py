"""Background read-only BingX history archive for the unified hosting app.

Runs only GET-based BingX analytics in a separate worker thread.
Never sends trading requests or blocks Dashboard HTTP routes.
A separate BingX read-only key is required for the archive.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone


LOGGER = logging.getLogger("autotrade.dashboard.archive")
POLL_SECONDS = 30
FAILURE_BACKOFF = timedelta(minutes=15)
BUSY_BACKOFF = timedelta(minutes=5)


def archive_enabled(environ=None) -> bool:
    """Always run read-only analytics on the dashboard service.

    No additional environment toggle is required. The BingX read-only
    credentials are still read securely by render_archive.run_once().
    """
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


