"""Offline schedule tests; no exchange credentials or network required."""
from datetime import datetime, timedelta, timezone
from autotrade_live.render_archive import plan_archive


def utc(hour, minute=7, day=27):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def test_normal_hourly_three_hour_overlap():
    # 21:07 VN = 14:07 UTC, last sync 20:43 VN.
    assert plan_archive(utc(14), utc(13, 43), utc(3, 18)) == ('hours', 3)


def test_manual_sync_is_not_repeated():
    assert plan_archive(utc(15, 45), utc(15, 43), utc(3, 18)) == ('skip', 0)


def test_stale_hours_expand_bounded_window():
    assert plan_archive(utc(15), utc(10), utc(3, 18)) == ('hours', 6)


def test_daily_three_day_reconciliation():
    assert plan_archive(utc(3), utc(2, 23), utc(3, 18, 26)) == ('days', 3)
    assert plan_archive(utc(3, 23), utc(3, 12), utc(3, 18)) == ('skip', 0)


def test_catch_up_after_long_downtime():
    assert plan_archive(utc(15), utc(10, day=25), utc(3, 18)) == ('days', 3)


def test_empty_database_rechecks_three_days():
    assert plan_archive(utc(15), None, None) == ('days', 3)
