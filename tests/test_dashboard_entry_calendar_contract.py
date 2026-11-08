"""Regression guard for the read-only Dashboard entry-day calendar.

The browser app is deliberately framework-free; assert that its grouping,
day sorting and filtering use Entry rather than Exit as their primary clock.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "dashboard" / "autotrade_live" / "static" / "app.js"
HTML = ROOT / "dashboard" / "autotrade_live" / "static" / "index.html"


def test_history_calendar_uses_entry_day():
    source = JS.read_text(encoding="utf-8")
    assert "const tradeDate = row => dateParts(row.entry_ms || row.exit_ms)?.key;" in source
    assert "const tradeDate = row => dateParts(row.exit_ms || row.entry_ms)?.key;" not in source
    assert "valid(row.entry_ms || row.exit_ms)" in source
    assert "dayRows(selectedDay)" in source
    assert "number(b.entry_ms || b.exit_ms)" in source


def test_history_shows_entry_hour_and_preserves_exit_timestamp():
    source = JS.read_text(encoding="utf-8")
    page = HTML.read_text(encoding="utf-8")
    assert "[dateParts(row.entry_ms || row.exit_ms)?.time || '—', '']" in source
    assert "tr.cells[0].title" in source
    assert "dateParts(row.exit_ms)" in source
    assert "theo ngày mở lệnh (Entry)" in page
    assert "Giờ mở" in page


def test_no_exchange_or_engine_changes_in_calendar_contract():
    source = JS.read_text(encoding="utf-8")
    # The browser reads DB-backed analytics only.
    assert "fetch('/api/dashboard?days=all'" in source
    assert "fetch('/engine/" not in source
