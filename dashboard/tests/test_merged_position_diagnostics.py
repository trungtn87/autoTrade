"""Merged-position audit is read-only; never allocate a shared exit's PnL."""
import json

from sqlalchemy import create_engine

from autotrade_live import results


def test_merged_bingx_position_keeps_mismatched_combos_unresolved(monkeypatch):
    db = create_engine("sqlite:///:memory:")
    with db.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE signal_links (entry_order_id TEXT, symbol TEXT, combo TEXT, side TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE bingx_orders (order_id TEXT, symbol TEXT, side TEXT, "
            "position_side TEXT, type TEXT, status TEXT, filled_qty REAL, "
            "created_ms INTEGER, updated_ms INTEGER, raw TEXT)"
        )

        for entry_id, combo in (("e13", "C13"), ("e15", "C15")):
            conn.exec_driver_sql(
                "INSERT INTO signal_links VALUES (?, 'ETH-USDT', ?, 'BUY')",
                (entry_id, combo),
            )
            conn.exec_driver_sql(
                "INSERT INTO bingx_orders VALUES (?, 'ETH-USDT', 'BUY', 'LONG', "
                "'MARKET', 'FILLED', 0.04, 1000, 1100, ?)",
                (entry_id, json.dumps({"positionID": "shared"})),
            )

        for oid, kind, qty, parent in (
            ("exit13-sl", "STOP_MARKET", 0.04, "p13-sl"),
            ("exit13-tr", "TRAILING_STOP_MARKET", 0.02, "p13-tr"),
            ("exit15-sl", "STOP_MARKET", 0.02, "p15-sl"),
        ):
            conn.exec_driver_sql(
                "INSERT INTO bingx_orders VALUES (?, 'ETH-USDT', 'SELL', 'LONG', "
                "?, 'FILLED', ?, 1200, 1300, ?)",
                (oid, kind, qty,
                 json.dumps({"positionID": "shared", "triggerOrderId": parent})),
            )

    monkeypatch.setattr(
        results, "_journal_exit_bindings",
        lambda states, orders: {
            ("ETH-USDT", "e13"): {
                "position_id": "shared", "combo": "C13", "side": "BUY",
                "parents": {"p13-sl": "STOP_MARKET", "p13-tr": "TRAILING_STOP_MARKET"},
            },
            ("ETH-USDT", "e15"): {
                "position_id": "shared", "combo": "C15", "side": "BUY",
                "parents": {"p15-sl": "STOP_MARKET"},
            },
        },
    )
    try:
        rows = results._linked_results(db, schema=None)
        by_combo = {row["combo"]: row for row in rows}
        assert len(rows) == 2
        assert by_combo["C13"]["reason"] == "merged_excess_exit"
        assert by_combo["C15"]["reason"] == "merged_short_exit"
        assert by_combo["C13"]["matched_exit_qty"] == 0.06
        assert by_combo["C15"]["matched_exit_qty"] == 0.02
        assert all(r["shared_position"] and r["entry_qty"] == 0.04 for r in rows)
        assert all(r["status"] == "UNRESOLVED" and r["roi"] is None
                   and r["outcome"] is None for r in rows)

        history = results.result_payload(rows)["theory"]["history"]
        assert {r["reason"] for r in history} == {
            "merged_excess_exit", "merged_short_exit"
        }
        assert all(r["shared_position"] for r in history)
    finally:
        db.dispose()
