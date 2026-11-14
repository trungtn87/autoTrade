"""L4 leftover-protection cleanup — MOCK tests (fake BingX, fake Discord, in-memory DB).

Fixtures only: these prove code behaviour, NOT how live BingX behaves.
"""
from __future__ import annotations

import threading

from autotrade_v3.live.l4_leftover import LeftoverCleaner, norm_id
from autotrade_v3.live.l4_control import HourlySync
from autotrade_v3.live.tests.fakes import build, sig


class Clock:
    def __init__(self):
        self.t = 1_763_000_000_000

    def __call__(self):
        return self.t


def setup(mode="live", **kw):
    s, store, client, sender, alert, control, reader, executor = build(**kw)
    clock = Clock()
    cleaner = LeftoverCleaner(s, client, store, reader, control, alert, mode=mode, busy=lambda: False, now_ms=clock)
    return s, store, client, sender, control, executor, cleaner, clock


def open_combo(executor, store, **kw):
    r = executor.execute(sig(**kw))
    assert r["stage"] == "protected"
    return store.executions[r["event_id"]]


def cleanup_state(store, ex):
    return (store.executions[ex["event_id"]].get("raw_protection") or {}).get("cleanup") or {}


def error_steps(store):
    return [r["step"] for r in store.control_logs if r["layer"] != "L5" and r.get("discord_status") != "skipped"
            and r["step"].startswith("leftover")]


def test_tp_and_trailing_fill_then_orphan_sl_is_cancelled_by_exact_id_only():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    assert ex["trailing_order_id"]
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"], price="98500")
    sender.calls.clear()
    out = cleaner.run_pass("test")
    assert out["executions"][ex["event_id"]] == "cancelled"
    assert client.cancels == [ex["sl_order_id"]]
    assert client.orders[ex["sl_order_id"]]["status"] == "CANCELED"
    assert cleanup_state(store, ex)["status"] == "done"
    assert error_steps(store) == []
    text = [c for _, c in sender.calls]
    assert len(text) == 1 and sender.channels() == ["btc"]
    assert "🏁 **BTC BUY · C1 | ĐÃ ĐÓNG**" in text[0] and "TP" in text[0] and "Trailing" in text[0] and "Đã dọn lệnh dư: SL" in text[0]
    # a second pass does nothing and sends nothing
    sender.calls.clear()
    cleaner.run_pass("again")
    assert client.cancels == [ex["sl_order_id"]] and sender.calls == []


def test_shadow_mode_records_but_never_sends_delete_and_notifies_once():
    _, store, client, sender, control, executor, cleaner, _ = setup(mode="shadow")
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"], price="98500")
    sender.calls.clear()
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "would_cancel"
    assert getattr(client, "cancels", []) == []
    assert client.orders[ex["sl_order_id"]]["status"] == "NEW"
    assert any(r["step"] == "leftover_shadow" for r in store.control_logs)
    assert len(sender.calls) == 1 and "chế độ shadow" in sender.calls[0][1]
    n_logs = len(store.control_logs)
    cleaner.run_pass()
    assert len(store.control_logs) == n_logs and len(sender.calls) == 1   # no repeat note, no repeat message
    assert cleanup_state(store, ex).get("status") != "done"


def test_partial_close_cancels_nothing_and_warns_sl_over_remaining_once():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
    sender.calls.clear()
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "open"
    assert getattr(client, "cancels", []) == []
    assert sum(1 for r in store.control_logs if r.get("error_code") == "SL_OVER_REMAINING") == 1
    cleaner.run_pass()
    assert sum(1 for r in store.control_logs if r.get("error_code") == "SL_OVER_REMAINING") == 1
    assert not any("🏁" in c for _, c in sender.calls)


def test_never_touches_another_combos_orders():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    a = open_combo(executor, store, combo=1)
    b = open_combo(executor, store, combo=2)
    client.trigger_fill(a["tp_order_id"], a["tp_qty"], price="99000")
    client.trigger_fill(a["trailing_order_id"], a["trailing_qty"], price="98500")
    cleaner.run_pass()
    assert client.cancels == [a["sl_order_id"]]
    for leg in ("sl_order_id", "tp_order_id", "trailing_order_id"):
        assert client.orders[b[leg]]["status"] == "NEW"
    assert cleanup_state(store, b).get("status") != "done"


def test_unprovable_exit_means_no_cancel_and_an_error_alert():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"])
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"])
    client.history_mode = "error"            # parent is gone and history cannot be read
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "unproven"
    assert getattr(client, "cancels", []) == []
    assert any(r["step"] == "leftover_cleanup" for r in store.control_logs)
    assert "error" in sender.channels()


def test_parent_gone_and_no_matching_child_is_unproven():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.orders[ex["tp_order_id"]]["_hidden"] = True     # vanished, no child anywhere
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"])
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "unproven"
    assert getattr(client, "cancels", []) == []


def test_over_filled_exit_total_is_an_anomaly_not_a_close():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"])
    client.trigger_fill(ex["trailing_order_id"], "0.0100")
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "anomaly"
    assert getattr(client, "cancels", []) == []


def test_cancel_rejected_is_not_assumed_cancelled_and_retries_are_bounded():
    _, store, client, sender, control, executor, cleaner, clock = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"], price="98500")
    client.cancel_mode = "reject"
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "cancel_pending"
    assert client.cancels == [ex["sl_order_id"]]
    assert cleanup_state(store, ex).get("status") != "done"
    cleaner.run_pass()                                   # inside the 60 s gap: no second DELETE
    assert len(client.cancels) == 1
    clock.t += 61_000
    cleaner.run_pass()
    assert len(client.cancels) == 2
    clock.t += 61_000
    cleaner.run_pass()                                   # exhausted: warn, stop
    assert len(client.cancels) == 2
    assert any(r.get("error_code") == "CANCEL_RETRY_EXHAUSTED" for r in store.control_logs)


def test_lost_delete_reply_is_verified_by_get_in_both_directions():
    for mode, expect_done in (("lost_applied", True), ("lost_not_applied", False)):
        _, store, client, sender, control, executor, cleaner, _ = setup()
        ex = open_combo(executor, store)
        client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
        client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"], price="98500")
        client.cancel_mode = mode
        cleaner.run_pass()
        assert (cleanup_state(store, ex).get("status") == "done") is expect_done


def test_open_orders_missing_a_leg_is_still_handled_by_exact_get():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
    client.open_hides = ("TRAILING_STOP_MARKET",)           # openOrders does not list trailing orders
    out = cleaner.run_pass()
    assert out["executions"][ex["event_id"]] == "open"       # trailing still alive => not closed
    assert any(r["step"] == "leftover_observation" for r in store.control_logs)
    assert getattr(client, "cancels", []) == []


def test_no_exchange_fill_price_means_no_discord_closed_message():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="0")
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"], price="0")
    sender.calls.clear()
    cleaner.run_pass()
    assert not any("🏁" in c for _, c in sender.calls)


def test_open_orders_failure_does_nothing_and_reports():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.open_mode = "error"
    out = cleaner.run_pass()
    assert out["executions"] == {} and getattr(client, "cancels", []) == []
    assert any(r["step"] == "leftover_open_orders" for r in store.control_logs)


def test_cleanup_never_gates_or_breaks_an_entry():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    executor.on_settled = lambda reason: (_ for _ in ()).throw(RuntimeError("cleanup boom"))
    r = executor.execute_safe(sig())
    assert r["stage"] == "protected" and len(client.entry_posts) == 1
    off = LeftoverCleaner(cleaner.settings, client, store, cleaner.reader, control, None, mode="off")
    off.request("entry")
    assert off.last["state"] == "not_run"


def test_request_runs_in_background_after_busy_threads_finish():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    ex = open_combo(executor, store)
    client.trigger_fill(ex["tp_order_id"], ex["tp_qty"], price="99000")
    client.trigger_fill(ex["trailing_order_id"], ex["trailing_qty"], price="98500")
    gate = threading.Event()
    cleaner._busy = lambda: not gate.is_set()
    cleaner.request("entry")                                # returns at once, nothing sent yet
    assert getattr(client, "cancels", []) == []
    gate.set()
    for t in threading.enumerate():
        if t.name == "v3-leftover":
            t.join(timeout=10)
    assert client.cancels == [ex["sl_order_id"]]


def test_hourly_sync_hands_over_to_cleanup_after_projection():
    _, store, client, sender, control, executor, cleaner, _ = setup()
    seen = []
    sync = HourlySync(store, control, admit=lambda: True, client=client, after_sync=seen.append)
    assert sync.run_once()["result"] == "completed"
    assert seen == ["hourly_sync"]


def test_float_rounded_ids_are_normalised_to_exact_integers():
    assert norm_id(2121453213688893440.0) == "2121453213688893440"
    assert norm_id("0") == "" and norm_id("abc") == "" and norm_id(None) == ""


def test_default_mode_is_live_and_unknown_value_falls_back_to_live(monkeypatch):
    from autotrade_v3.live.config import V3Settings
    monkeypatch.delenv("V3_LEFTOVER_CLEANUP_MODE", raising=False)
    assert V3Settings().leftover_cleanup_mode == "live"
    _, store, client, sender, control, executor, cleaner, _ = setup(mode="bogus")
    assert cleaner.mode == "live"
