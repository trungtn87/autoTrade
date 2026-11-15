"""Issue #121: L4 exact-ID SL resize tests with fake BingX only (never live)."""
from __future__ import annotations

import pytest
from decimal import Decimal

from autotrade_v3.live.l4_leftover import LeftoverCleaner
from autotrade_v3.live.tests.fakes import build, sig


def case(*, resize_mode="live", exchange_mode="ok"):
    settings, store, client, sender, alert, control, reader, executor = build(sl_resize_mode=resize_mode)
    client.amend_mode = exchange_mode
    ex = executor.execute(sig(combo=1, side="BUY"))
    assert ex["stage"] == "protected"
    row = store.executions[ex["event_id"]]
    cleaner = LeftoverCleaner(settings, client, store, reader, control, alert,
                              mode="live", busy=lambda: False)
    client.trigger_fill(row["tp_order_id"], row["tp_qty"], price="102000")
    sender.calls.clear()
    return store, client, sender, cleaner, row


def latest_resize(store, ex):
    raw = store.executions[ex["event_id"]]["raw_protection"]
    return raw.get("cleanup", {}).get("sl_resizes", {})


def errors(store, code):
    return [x for x in store.control_logs if x.get("error_code") == code]


def test_live_amend_shrinks_only_own_sl_and_get_verifies_quantity():
    store, client, _sender, cleaner, ex = case()
    old_qty = ex["sl_qty"]
    expected = str(Decimal(str(ex["executed_qty"])) - Decimal(str(ex["tp_qty"])))
    before_posts = list(client.order_posts)
    before_entries = list(client.entry_posts)
    result = cleaner.run_pass("sl_resize_test")
    assert result["executions"][ex["event_id"]] == "open"
    assert client.amends == [(ex["sl_order_id"], expected)]
    assert client.orders[ex["sl_order_id"]]["origQty"] == expected
    assert client.order_posts == before_posts  # no replacement SL/TP
    assert client.entry_posts == before_entries  # never resends Entry
    assert getattr(client, "cancels", []) == []
    assert not errors(store, "SL_OVER_REMAINING")
    events = latest_resize(store, ex)
    assert events[ex["sl_order_id"] + ":" + expected]["state"] == "confirmed"
    assert ex["sl_qty"] == old_qty  # exact original placed quantity is preserved for audit
    cleaner.run_pass("again")
    assert len(client.amends) == 1


def test_other_combo_sl_untouched_during_same_symbol_hedge_position():
    settings, store, client, sender, alert, control, reader, executor = build(sl_resize_mode="live")
    a = executor.execute(sig(combo=1, side="BUY"))
    b = executor.execute(sig(combo=2, side="BUY"))
    exa, exb = store.executions[a["event_id"]], store.executions[b["event_id"]]
    old_second = client.orders[exb["sl_order_id"]]["origQty"]
    client.trigger_fill(exa["tp_order_id"], exa["tp_qty"])
    cleaner = LeftoverCleaner(settings, client, store, reader, control, alert,
                              mode="live", busy=lambda: False)
    cleaner.run_pass()
    assert client.amends and [oid for oid, qty in client.amends] == [exa["sl_order_id"]]
    assert client.orders[exb["sl_order_id"]]["origQty"] == old_second
    assert getattr(client, "cancels", []) == []


@pytest.mark.parametrize("mode", ["shadow", "off"])
def test_shadow_or_off_does_not_send_amend(mode):
    store, client, sender, cleaner, ex = case(resize_mode=mode)
    cleaner.run_pass()
    assert getattr(client, "amends", []) == []
    assert client.orders[ex["sl_order_id"]]["origQty"] == ex["sl_qty"]
    assert len(errors(store, "SL_OVER_REMAINING")) == 1
    if mode == "shadow":
        events = latest_resize(store, ex)
        assert events and next(iter(events.values()))["state"] == "shadow"


@pytest.mark.parametrize("mode", ["reject", "lost_not_applied", "triggered_before"])
def test_rejection_loss_or_trigger_race_never_assumes_amend_succeeded(mode):
    store, client, sender, cleaner, ex = case(exchange_mode=mode)
    cleaner.run_pass()
    assert len(client.amends) == 1
    assert len(errors(store, "SL_RESIZE_NOT_CONFIRMED")) == 1
    assert len(errors(store, "SL_OVER_REMAINING")) == 1
    assert not getattr(client, "cancels", [])
    cleaner.run_pass()
    assert len(client.amends) == 1  # no blind retry after ambiguous response


def test_lost_success_reply_get_confirms_amend_without_duplicate_post():
    store, client, sender, cleaner, ex = case(exchange_mode="lost_applied")
    cleaner.run_pass()
    assert len(client.amends) == 1
    assert next(iter(latest_resize(store, ex).values()))["state"] == "confirmed"
    assert not errors(store, "SL_RESIZE_NOT_CONFIRMED")
    cleaner.run_pass()
    assert len(client.amends) == 1


def test_missing_exact_original_sl_client_id_refuses_amend():
    store, client, sender, cleaner, ex = case()
    ex["raw_protection"]["sl"]["evidence"]["params"]["clientOrderId"] = "tampered-id"
    cleaner.run_pass()
    assert not getattr(client, "amends", [])
    assert errors(store, "SL_RESIZE_IDENTITY")
    assert errors(store, "SL_OVER_REMAINING")


def test_write_ahead_failure_prevents_exchange_sl_modification():
    store, client, sender, cleaner, ex = case()
    store.fail.add("update_execution")
    cleaner.run_pass()
    assert not getattr(client, "amends", [])
    assert errors(store, "SL_RESIZE_STORE_FAILED")
    assert not getattr(client, "cancels", [])


def test_default_settings_never_enable_live_sl_amend(monkeypatch):
    from autotrade_v3.live.config import V3Settings

    monkeypatch.delenv("V3_SL_RESIZE_MODE", raising=False)
    assert V3Settings().sl_resize_mode == "shadow"
