"""Offline checks for the Render Free wake-up based collector scheduler."""
from datetime import datetime, timezone

from autotrade_live.web_scheduler import archive_enabled, ready_slot


def test_scheduler_always_enabled_without_extra_environment_toggle():
    assert archive_enabled({})
    assert archive_enabled({"DASHBOARD_ARCHIVE_ENABLED": "false"})
    assert archive_enabled({"BINGX_READ_API_KEY": "read", "BINGX_READ_API_SECRET": "secret"})


def test_runs_only_after_minute_07_in_utc_and_vietnam():
    # 15:07 UTC = 22:07 Vietnam. Never fire at :00 / candle close.
    assert ready_slot(datetime(2026, 10, 27, 15, 6, tzinfo=timezone.utc)) is None
    assert ready_slot(datetime(2026, 10, 27, 15, 7, tzinfo=timezone.utc)) == datetime(
        2026, 10, 27, 15, 7, tzinfo=timezone.utc
    )
    assert ready_slot(datetime(2026, 10, 27, 15, 43, tzinfo=timezone.utc)) == datetime(
        2026, 10, 27, 15, 7, tzinfo=timezone.utc
    )


def test_time_zone_is_normalized():
    from datetime import timedelta
    local = timezone(timedelta(hours=7))
    assert ready_slot(datetime(2026, 10, 27, 22, 10, tzinfo=local)) == datetime(
        2026, 10, 27, 15, 7, tzinfo=timezone.utc
    )
