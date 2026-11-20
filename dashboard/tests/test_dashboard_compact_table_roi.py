"""Regression coverage for the seven-column phone dashboard and ROI data integrity."""
from pathlib import Path
import re

import pytest

from dashboard.autotrade_live.results import result_payload


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("directory", [
    "dashboard/autotrade_live/static",
    "autotrade_v3/dashboard/static",
])
def test_mobile_combo_table_fits_seven_columns_without_sideways_scroll(directory):
    css = (ROOT / directory / "app.css").read_text(encoding="utf-8")
    html = (ROOT / directory / "index.html").read_text(encoding="utf-8")
    assert ".performance-table{min-width:0;table-layout:fixed}" in css
    assert "#combos .table-scroll{overflow-x:hidden}" in css
    assert "min-width:450px" not in css
    assert "min-width:345px" not in css
    widths = [int(v) for v in re.findall(
        r"\.performance-table th:nth-child\(\d\)\{width:(\d+)%\}", css
    )]
    # TP and SL share a CSS rule; regex captures the second (8%) only.
    assert ".performance-table th:nth-child(3),.performance-table th:nth-child(4){width:8%}" in css
    assert sum(widths) + 8 == 100
    assert '<span class="label-short" aria-hidden="true">Win%</span>' in html
    assert '<span class="label-short" aria-hidden="true">PnL</span>' in html



@pytest.mark.parametrize("directory", [
    "dashboard/autotrade_live/static",
    "autotrade_v3/dashboard/static",
])
def test_mobile_journal_shows_six_columns_and_no_explanation_paragraphs(directory):
    css = (ROOT / directory / "app.css").read_text(encoding="utf-8")
    html = (ROOT / directory / "index.html").read_text(encoding="utf-8")
    js = (ROOT / directory / "app.js").read_text(encoding="utf-8")
    journal = html.split('<table class="history-table">', 1)[1].split("</table>", 1)[0]
    heading = journal.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert heading.count('<th scope="col"') == 6
    assert "Kết quả" not in heading
    assert 'id="history-list"' in journal
    assert 'colspan="6"' in journal
    assert ".history-table{min-width:0;table-layout:fixed}" in css
    assert "#history .table-scroll{overflow-x:hidden}" in css
    widths = [int(v) for v in re.findall(
        r"\.history-table th:nth-child\(\d\)\{width:(\d+)%\}", css
    )]
    assert sum(widths) == 100
    assert "[status, status === 'THẮNG'" not in js
    assert "emptyRow('history-list', 6" in js
    assert 'class="calendar-hint"' not in html
    assert 'id="roi-note"' not in html
    assert 'id="combo-roi-hint"' not in html
    assert 'id="pnl-chart-note" hidden' in html
    assert "Dashboard chỉ đọc dữ liệu đã lưu" not in html

def _trade(**changes):
    row = dict(
        symbol="ETH-USDT", combo="C4", side="BUY", status="CLOSED",
        opened_ms=1799999900000, closed_ms=1800000000000,
        outcome="WIN", roi=None, pnl=0.424, margin=2.0,
        source="bingx_db_roi_estimate",
    )
    row.update(changes)
    return row


def test_roi_recovered_only_from_known_pnl_and_margin():
    output = result_payload([_trade()])
    combo = output["theory"]["combos"][0]
    history = output["theory"]["history"][0]
    assert combo["real_roi_total"] == pytest.approx(21.2)
    assert combo["roi_coverage"] == 1
    assert combo["pnl_total"] == pytest.approx(0.424)
    assert history["recorded_roi_pct"] == pytest.approx(21.2)


def test_missing_margin_keeps_roi_unknown_but_preserves_actual_pnl():
    output = result_payload([_trade(margin=None)])
    combo = output["theory"]["combos"][0]
    history = output["theory"]["history"][0]
    assert combo["real_roi_total"] is None
    assert combo["roi_coverage"] == 0
    assert combo["pnl_total"] == pytest.approx(0.424)
    assert combo["pnl_coverage"] == 1
    assert history["recorded_roi_pct"] is None


def test_recorded_historical_roi_remains_valid_without_margin():
    output = result_payload([_trade(roi=-3.5, margin=None, pnl=None, outcome="LOSS")])
    combo = output["theory"]["combos"][0]
    assert combo["real_roi_total"] == pytest.approx(-3.5)
    assert combo["pnl_total"] is None
