"""Background read-only BingX history archive for the unified hosting app.

Runs only GET-based BingX analytics in a separate worker thread.
Never sends trading requests or blocks Dashboard HTTP routes.
A separate BingX read-only key is required for the archive.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .render_archive import run_once

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


async def archive_scheduler_loop():
    """Run GET-only archive in a thread so the dashboard stays responsive.

    If the host stops this process, the next startup resumes bounded catch-up.
    A failed sync is retried at most once per 15 minutes, never tight-looped.
    """
    last_completed_slot = None
    next_attempt_at = datetime.min.replace(tzinfo=timezone.utc)
    LOGGER.info("Read-only BingX archive scheduler enabled")
    while True:
        now = datetime.now(timezone.utc)
        slot = ready_slot(now)
        if slot is not None and slot != last_completed_slot and now >= next_attempt_at:
            try:
                result = await asyncio.to_thread(run_once, now)
                if result.get("skipped") and result.get("reason") == "archive_locked":
                    next_attempt_at = datetime.now(timezone.utc) + BUSY_BACKOFF
                    LOGGER.info("Archive already running; next check in 5 minutes")
                else:
                    # A successful run or an already-fresh Supabase sync.
                    last_completed_slot = slot
                    next_attempt_at = slot + timedelta(hours=1)
                    LOGGER.info("Archive check completed for UTC slot %s", slot.isoformat())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never include BingX API responses, credentials or account
                # details in web-service logs.
                next_attempt_at = datetime.now(timezone.utc) + FAILURE_BACKOFF
                LOGGER.warning("Archive sync failed: %s; retry in 15m",
                               type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)
