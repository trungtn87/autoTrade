"""V3 Live Terminal payload: pure math, open/closed split, filters, public redaction. Offline."""
from __future__ import annotations

from autotrade_v3.dashboard.payload import build_payload

DAY = 86_400_000
NOW = 100 * DAY


def _order(i, symbol="ETH-USDT", combo="C1", qty=1.0, filled=NOW - 2 * 3600_000):
    return {"id": i, "event_id": f"EV{i}", "symbol": symbol, "combo": combo, "side": "BUY",
            "executed_qty": qty, "avg_price": 100.0, "sl_price": 99.2, "tp_price": 102.0, "filled_ms": filled}


def _build(**kw):
    base = dict(orders=[], results=[], finances={}, signals=[], errors=[], candle_last_close={},
                now_ms=NOW, days=30, public=False)
    base.update(kw)
    return build_payload(**base)


def test_closed_trades_use_finance_net_and_win_loss_split() -> None:
    orders = [_order(1), _order(2, combo="C2")]
    results = [{"execution_order_id": 1, "close_type": "TP", "realized_pnl": 2.0, "fee": 0.1, "closed_ms": NOW - 1000},
               {"execution_order_id": 2, "close_type": "SL", "realized_pnl": -1.0, "fee": 0.1, "closed_ms": NOW - 2000}]
    finances = {1: {"net_pnl": 1.9, "gross_pnl": 2.0, "roi_pct": 1.9, "is_fully_closed": True},
                2: {"net_pnl": None, "gross_pnl": -1.0, "roi_pct": None, "is_fully_closed": True}}
    p = _build(orders=orders, results=results, finances=finances)
    s = p["summary"]
    assert s["closed_trades"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate_pct"] == 50.0
    # order 2 has no net_pnl in finance: falls back to realized_pnl - fee = -1.1
    assert round(s["net_pnl"], 6) == round(1.9 - 1.1, 6)
    assert [t["id"] for t in p["trades"]] == [1, 2]


def test_open_positions_are_filled_orders_without_a_closed_finance_row() -> None:
    orders = [_order(1, qty=2.0), _order(2, qty=0.0)]
    p = _build(orders=orders, finances={})
    assert p["summary"]["open_positions"] == 1
    assert p["open_positions"][0]["id"] == 1 and p["open_positions"][0]["qty"] == 2.0


def test_days_window_and_symbol_combo_filters() -> None:
    old = _order(1, filled=NOW - 40 * DAY)
    recent_eth = _order(2, symbol="ETH-USDT", combo="C3", filled=NOW - DAY)
    recent_btc = _order(3, symbol="BTC-USDT", combo="C1", filled=NOW - DAY)
    finances = {i: {"net_pnl": 1.0, "is_fully_closed": True} for i in (1, 2, 3)}
    results = [{"execution_order_id": 1, "closed_ms": NOW - 40 * DAY}]
    results += [{"execution_order_id": i, "closed_ms": NOW - DAY} for i in (2, 3)]
    p = _build(orders=[old, recent_eth, recent_btc], results=results, finances=finances, days=30)
    assert {t["id"] for t in p["trades"]} == {2, 3}  # old order is outside 30 days
    p2 = _build(orders=[old, recent_eth, recent_btc], results=results, finances=finances, days=None)
    assert len(p2["trades"]) == 3
    p3 = _build(orders=[old, recent_eth, recent_btc], results=results, finances=finances, days=None,
                symbol="eth-usdt", combo="c3")
    assert [t["id"] for t in p3["trades"]] == [2]
    assert p3["filters"] == {"symbol": "ETH-USDT", "combo": "C3", "days": None}


def test_by_combo_sorted_by_net_with_win_rate() -> None:
    orders = [_order(1, combo="C1"), _order(2, combo="C1"), _order(3, combo="C2")]
    results = [{"execution_order_id": i, "closed_ms": NOW - 1} for i in (1, 2, 3)]
    finances = {1: {"net_pnl": 3.0, "is_fully_closed": True}, 2: {"net_pnl": -1.0, "is_fully_closed": True},
                3: {"net_pnl": 5.0, "is_fully_closed": True}}
    rows = _build(orders=orders, results=results, finances=finances)["by_combo"]
    assert [r["combo"] for r in rows] == ["C2", "C1"]
    c1 = rows[1]
    assert c1["trades"] == 2 and c1["wins"] == 1 and c1["win_rate_pct"] == 50.0 and c1["net_pnl"] == 2.0


def test_public_payload_redacts_ids_and_collector_details() -> None:
    orders = [_order(1, qty=1.0)]
    results = [{"execution_order_id": 1, "close_type": "TP", "realized_pnl": 2.0, "fee": 0.0, "closed_ms": NOW - 5}]
    finances = {1: {"net_pnl": 2.0, "is_fully_closed": True}}
    errors = [{"layer": "L3", "symbol": "ETH-USDT", "error_code": "X", "message": "secret path /var/x",
               "time_ms": NOW - 10, "discord_status": "sent"}]
    p = _build(orders=orders, results=results, finances=finances, errors=errors, public=True)
    text = repr(p)
    assert "EV1" not in text and "event_id" not in text and "secret path" not in text
    assert set(p["trades"][0]) == {"symbol", "combo", "side", "entry_ms", "exit_ms", "close_type", "net_pnl", "roi_pct"}
    assert p["public"] is True


def test_private_payload_keeps_ids_and_error_message() -> None:
    orders = [_order(1)]
    results = [{"execution_order_id": 1, "close_type": "TP", "realized_pnl": 2.0, "fee": 0.0, "closed_ms": NOW - 5}]
    finances = {1: {"net_pnl": 2.0, "is_fully_closed": True}}
    errors = [{"layer": "L3", "symbol": "ETH-USDT", "error_code": "X", "message": "detail", "time_ms": NOW - 10,
               "discord_status": "sent"}]
    p = _build(orders=orders, results=results, finances=finances, errors=errors, public=False)
    assert p["trades"][0]["id"] == 1 and p["trades"][0]["event_id"] == "EV1"
    assert p["health"]["errors_last_24h"][0]["message"] == "detail"


def test_errors_older_than_24h_are_not_counted_and_candles_reported() -> None:
    errors = [{"layer": "L1", "symbol": "BTC-USDT", "error_code": "OLD", "message": "m",
               "time_ms": NOW - 2 * DAY, "discord_status": "sent"}]
    p = _build(errors=errors, candle_last_close={"ETH-USDT": NOW - 900_000, "BTC-USDT": NOW - 900_000})
    assert p["summary"]["errors_last_24h"] == 0
    assert [c["symbol"] for c in p["health"]["candles"]] == ["BTC-USDT", "ETH-USDT"]
