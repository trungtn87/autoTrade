"""Dashboard-only estimated attribution for balanced BingX merged positions."""
import json

from sqlalchemy import create_engine
from autotrade_live import results


def _fixture(monkeypatch, missing_exit_fill=False, extra_unlinked_entry=False):
    db = create_engine("sqlite:///:memory:")
    with db.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE signal_links (entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT)")
        conn.exec_driver_sql("CREATE TABLE bingx_orders (order_id TEXT, symbol TEXT, side TEXT, "
                             "position_side TEXT, type TEXT, status TEXT, filled_qty REAL, "
                             "created_ms INTEGER, updated_ms INTEGER, raw TEXT)")
        conn.exec_driver_sql("CREATE TABLE bingx_fills (order_id TEXT, symbol TEXT, side TEXT, "
                             "position_side TEXT, qty REAL, price REAL, realized_pnl REAL, fee REAL, raw TEXT)")
        for eid, combo in (("e13", "C13"), ("e15", "C15")):
            conn.exec_driver_sql("INSERT INTO signal_links VALUES (?, 'ETH-USDT', ?, 'BUY')", (eid, combo))
            entry = dict(positionID="shared", avgPrice="2400", leverage="50X")
            conn.exec_driver_sql("INSERT INTO bingx_orders VALUES (?, 'ETH-USDT', 'BUY', 'LONG', "
                                 "'MARKET', 'FILLED', 0.04, 1000, 1100, ?)", (eid, json.dumps(entry)))
            conn.exec_driver_sql("INSERT INTO bingx_fills VALUES (?, 'ETH-USDT', 'BUY', 'LONG', "
                                 "0.04, 2400, 0, -0.04, ?)",
                                 (eid, json.dumps({"commissionAsset": "USDT"})))
        for oid, kind, qty, parent, pnl, fee in (
            ("exit13-sl", "STOP_MARKET", 0.04, "p13-sl", -0.1, -0.02),
            ("exit13-tr", "TRAILING_STOP_MARKET", 0.02, "p13-tr", 0.8, -0.02),
            ("exit15-sl", "STOP_MARKET", 0.02, "p15-sl", -0.2, -0.02),
        ):
            conn.exec_driver_sql("INSERT INTO bingx_orders VALUES (?, 'ETH-USDT', 'SELL', 'LONG', "
                                 "?, 'FILLED', ?, 1200, 1300, ?)",
                                 (oid, kind, qty, json.dumps({"positionID": "shared", "triggerOrderId": parent})))
            if not (missing_exit_fill and oid == "exit13-tr"):
                conn.exec_driver_sql("INSERT INTO bingx_fills VALUES (?, 'ETH-USDT', 'SELL', 'LONG', "
                                     "?, 2400, ?, ?, ?)",
                                     (oid, qty, pnl, fee, json.dumps({"commissionAsset": "USDT"})))
        if extra_unlinked_entry:
            conn.exec_driver_sql("INSERT INTO bingx_orders VALUES ('unknown', 'ETH-USDT', 'BUY', "
                                 "'LONG', 'MARKET', 'FILLED', 0.04, 1000, 1100, ?)",
                                 (json.dumps({"positionID": "shared"}),))
    monkeypatch.setattr(results, "_journal_exit_bindings", lambda states, orders: {
        ("ETH-USDT", "e13"): dict(position_id="shared", combo="C13", side="BUY",
                                 parents={"p13-sl": "STOP_MARKET", "p13-tr": "TRAILING_STOP_MARKET"}),
        ("ETH-USDT", "e15"): dict(position_id="shared", combo="C15", side="BUY",
                                 parents={"p15-sl": "STOP_MARKET"}),
    })
    return db


def test_balanced_merges_estimate_win_loss_roi_and_pnl(monkeypatch):
    db = _fixture(monkeypatch)
    try:
        rows = results._linked_results(db, schema=None, has_fills=True)
        assert len(rows) == 2
        found = {row["combo"]: row for row in rows}
        assert {r["status"] for r in rows} == {"ESTIMATED_CLOSED"}
        assert {r["source"] for r in rows} == {"bingx_db_merged_allocation_estimate"}
        assert found["C13"]["outcome"] == "LOSS"
        assert found["C15"]["outcome"] == "WIN"
        assert abs(found["C13"]["pnl"] - (-0.16)) < 1e-8
        assert abs(found["C15"]["pnl"] - 0.52) < 1e-8
        assert abs(found["C13"]["roi"] - (-0.16 / 1.92 * 100)) < 1e-6
        assert abs(found["C15"]["roi"] - (0.52 / 1.92 * 100)) < 1e-6
        assert abs(sum(row["pnl"] for row in rows) - 0.36) < 1e-8
        report = results.result_payload(rows)
        assert report["summary"]["closed"] == 2
        assert {r["status"] for r in report["theory"]["history"]} == {"estimated_closed"}
        assert {r["outcome"] for r in report["theory"]["history"]} == {"WIN", "LOSS"}
        assert all(c["estimated_count"] == 1 for c in report["theory"]["combos"])
        assert all(c["roi_estimated"] for c in report["theory"]["combos"])
    finally:
        db.dispose()


def test_missing_exact_exit_fill_does_not_invent_result(monkeypatch):
    db = _fixture(monkeypatch, missing_exit_fill=True)
    try:
        rows = results._linked_results(db, schema=None, has_fills=True)
        assert all(r["status"] == "UNRESOLVED" for r in rows)
        assert all(r["roi"] is None for r in rows)
    finally:
        db.dispose()


def test_unlinked_entry_on_shared_position_disables_allocation(monkeypatch):
    db = _fixture(monkeypatch, extra_unlinked_entry=True)
    try:
        rows = results._linked_results(db, schema=None, has_fills=True)
        assert all(r["status"] == "UNRESOLVED" for r in rows)
    finally:
        db.dispose()
