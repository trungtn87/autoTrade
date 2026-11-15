"""L4 shared-position attribution regression with exact BingX-style evidence (offline)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from autotrade_v3.live.l4_allocation import (
    UnverifiableAllocation, allocate_group, reconcile_shared_positions,
)
from autotrade_v3.live.store import MemoryStore

POSITION = "2121740044943446018"
EVENT13 = "ETH-USDT|1h|C13|SELL|1794697199999"
EVENT15 = "ETH-USDT|1h|C15|SELL|1794697199999"


def entries():
    out = []
    for num, event, eid, entry_id, sl, trail, commission in [
        (12, EVENT13, "12", "2121740044922474496", "2121740085858242560",
         "2121740151444574208", "-0.043674"),
        (13, EVENT15, "13", "2121740050475732992", "2121740091071762432",
         "2121740161590595584", "-0.043698"),
    ]:
        out.append({
            "id": num, "event_id": event, "symbol": "ETH-USDT", "side": "SELL",
            "combo": "C13" if num == 12 else "C15",
            "entry_order_id": entry_id, "executed_qty": "0.05", "avg_price": "1747.0",
            "leverage": 50, "sl_order_id": sl, "tp_order_id": str(31000 + num),
            "trailing_order_id": trail, "raw_entry": {
                "confirm": {"order": {"orderId": entry_id, "positionID": POSITION,
                                     "commission": commission}}
            },
            "existing_open_fee": abs(Decimal(commission)),
        })
    return out


def close(ex, kind, order_id, qty, price, pnl, fee, ms):
    parent = ex[{"SL": "sl_order_id", "TRAILING": "trailing_order_id"}[kind]]
    order = {"symbol": "ETH-USDT", "positionSide": "SHORT", "side": "BUY",
             "orderId": order_id, "positionID": POSITION, "status": "FILLED",
             "triggerOrderId": parent, "executedQty": qty, "avgPrice": price,
             "profit": pnl, "commission": fee, "updateTime": ms}
    return {"event_id": ex["event_id"], "close_type": kind, "close_order_id": order_id,
            "closed_qty": qty, "raw": {"order": order}}


def exits(exs):
    a, b = exs
    return [
        close(a, "TRAILING", "2121741957172776960", "0.02", "1744.72", "0.0542", "-0.017447", 1794697677000),
        close(b, "TRAILING", "2121741956816261120", "0.02", "1744.86", "0.0514", "-0.017449", 1794697677000),
        close(a, "SL", "2121751452460470272", "0.05", "1759.43", "-0.6000", "-0.043986", 1794699941000),
        close(b, "SL", "2121751500204232704", "0.01", "1762.78", "-0.1535", "-0.008814", 1794699953000),
    ]


def D(v):
    return Decimal(str(v))


def test_exact_c13_c15_position_overfill_is_allocated_without_mutating_raw_fills():
    exs = entries()
    fills = exits(exs)
    before = [(r["event_id"], r["closed_qty"], r["close_order_id"]) for r in fills]
    allocated = allocate_group(exs, fills)
    assert [D(allocated[e]["closed_qty"]) for e in (EVENT13, EVENT15)] == [D("0.05"), D("0.05")]
    assert all(allocated[e]["is_fully_closed"] for e in (EVENT13, EVENT15))
    assert D(allocated[EVENT13]["gross_pnl"]) == D("-0.3058")
    assert D(allocated[EVENT15]["gross_pnl"]) == D("-0.3421")
    assert sum((D(v["gross_pnl"]) for v in allocated.values()), D(0)) == D("-0.6479")
    assert sum((D(v["close_fee"]) for v in allocated.values()), D(0)) == D("0.087696")
    sources = allocated[EVENT15]["raw"]["sources"]
    assert any(s["source_event"] == EVENT13 and s["allocated_qty"] == "0.02"
               and s["close_type"] == "SL" for s in sources)
    assert allocated[EVENT15]["raw"]["exchange_owned_exit_qty"] == "0.03"
    assert allocated[EVENT15]["raw"]["exchange_position_closed"] == "NOT_PROVEN"
    assert before == [(r["event_id"], r["closed_qty"], r["close_order_id"]) for r in fills]


def test_only_partial_shared_exit_before_overfill_preserves_normal_original_finances():
    exs = entries()
    assert allocate_group(exs, exits(exs)[:2]) == {}


def test_exact_ids_and_proof_are_mandatory():
    exs, fills = entries(), exits(entries())
    fills[2]["raw"]["order"]["triggerOrderId"] = "9999999"
    with pytest.raises(UnverifiableAllocation, match="triggerOrderId"):
        allocate_group(exs, fills)
    fills = exits(exs)
    fills[2]["raw"]["order"]["positionID"] = "19999"
    with pytest.raises(UnverifiableAllocation, match="position ID mismatch"):
        allocate_group(exs, fills)
    fills = exits(exs)
    fills[2]["raw"]["order"]["orderId"] = float(fills[2]["close_order_id"])
    with pytest.raises(UnverifiableAllocation, match="floating-point"):
        allocate_group(exs, fills)


def test_exits_above_all_known_entries_are_not_rebalanced():
    exs, fills = entries(), exits(entries())
    fills.append(close(exs[0], "SL", "2121751700000000000", "0.01", "1760", "0", "0", 1794699970000))
    with pytest.raises(UnverifiableAllocation, match="exceed"):
        allocate_group(exs, fills)


def test_memory_store_reconciler_updates_finance_only_not_actual_exit_records():
    store = MemoryStore()
    exs = entries()
    rows = exits(exs)
    for ex in exs:
        store.executions[ex["event_id"]] = ex.copy()
        store.finances[ex["id"]] = {"open_fee": str(ex["existing_open_fee"])}
    for row in rows:
        store.results[row["close_order_id"]] = row.copy()

    class NoAlerts:
        def report(self, *args, **kwargs):
            raise AssertionError("validated shared fill should not alert")

    summary = reconcile_shared_positions(store, NoAlerts())
    assert summary == {"groups_examined": 1, "groups_adjusted": 1, "finances_adjusted": 2, "unverified": 0}
    assert store.finances[12]["closed_qty"] == "0.05"
    assert store.finances[13]["closed_qty"] == "0.05"
    assert all(store.finances[n]["is_fully_closed"] for n in (12, 13))
    assert len(store.results) == 4
    assert [(r["event_id"], r["closed_qty"], r["close_order_id"]) for r in store.results.values()] == [
        (r["event_id"], r["closed_qty"], r["close_order_id"]) for r in rows
    ]


def test_l4_cleanup_marks_both_terminal_shared_combos_done_without_any_exchange_delete():
    from autotrade_v3.live.l4_leftover import LeftoverCleaner
    from autotrade_v3.live.tests.fakes import build

    settings, store, client, sender, alert, control, reader, _executor = build()
    exs = entries()
    allocated = allocate_group(exs, exits(exs))
    for ex in exs:
        source = ex["event_id"]
        # The three original SL/TP/trailing orders are already terminal on BingX.
        # C13 raw own fills = 0.07 > Entry, C15 raw own fills = 0.03 < Entry.
        own_qty = {"C13": ("0.05", "0", "0.02"), "C15": ("0.01", "0", "0.02")}[ex["combo"]]
        leg_keys = [ex["sl_order_id"], ex["tp_order_id"], ex["trailing_order_id"]]
        ex["raw_protection"] = {"cleanup": {"legs": {
            oid: {"status": "TERMINAL", "executed": q, "source": "triggered_child",
                  "price": 1744.7 if q != "0" else None}
            for oid, q in zip(leg_keys, own_qty)
        }, "warnings": [], "attempts": {}, "shadow_logged": []}}
        ex["stage"] = "protected"
        store.executions[source] = ex
        store.finances[ex["id"]] = allocated[source]
    cleaner = LeftoverCleaner(settings, client, store, reader, control, alert, mode="live",
                              busy=lambda: False)
    outputs = [cleaner._handle(ex, {}) for ex in exs]
    assert outputs == ["shared_closed", "shared_closed"]
    assert all(store.executions[ex["event_id"]]["raw_protection"]["cleanup"]["status"] == "done"
               for ex in exs)
    assert getattr(client, "cancels", []) == []
    assert not any("🏁" in msg for _, msg in sender.calls)
    assert not any(row.get("error_code") for row in store.control_logs)


def test_l4_never_marks_shared_combo_done_with_mismatched_position_proof():
    from autotrade_v3.live.l4_leftover import LeftoverCleaner
    from autotrade_v3.live.tests.fakes import build

    settings, store, client, sender, alert, control, reader, _executor = build()
    exs = entries()
    ex = exs[0]
    financial = allocate_group(exs, exits(exs))[EVENT13]
    financial["raw"]["position_id"] = "99999"
    store.finances[ex["id"]] = financial
    for col, qty in [("sl_order_id", "0.05"), ("tp_order_id", "0"), ("trailing_order_id", "0.02")]:
        ex.setdefault("raw_protection", {}).setdefault("cleanup", {}).setdefault("legs", {})[ex[col]] = {
            "status": "TERMINAL", "executed": qty, "price": 1759.0,
        }
    store.executions[ex["event_id"]] = ex
    cleaner = LeftoverCleaner(settings, client, store, reader, control, alert, mode="live",
                              busy=lambda: False)
    assert cleaner._handle(ex, {}) == "anomaly"
    assert store.executions[ex["event_id"]]["raw_protection"]["cleanup"].get("status") != "done"
    assert getattr(client, "cancels", []) == []
    assert any(r.get("error_code") == "LEFTOVER_FILL_OVER_ENTRY" for r in store.control_logs)
