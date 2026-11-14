"""V3 Discord contract tests. Fake BingX/Discord only; no live orders sent."""
from __future__ import annotations

from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.l5_alert import _fmt_price, closed_message, success_message
from autotrade_v3.live.tests.fakes import build, sig


def test_live_dispatch_sends_only_one_confirmed_message_per_success():
    _, store, client, sender, alert, control, _reader, executor = build()
    strategy = L2Strategy(store, control, alert, executor)
    threads = strategy.dispatch([sig("BTC-USDT", combo=1), sig("ETH-USDT", combo=1, side="SELL")])
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(client.entry_posts) == 2
    assert sorted(sender.channels()) == ["btc", "eth"]
    assert all("| ĐẶT LỆNH THÀNH CÔNG**" in text for _, text in sender.calls)
    assert all("📡" not in text for _, text in sender.calls)
    assert len(store.signals) == 2
    assert all(r["stage"] == "protected" for r in executor.results)


def test_live_rejected_entry_only_reports_error_without_signal_or_success():
    _, _store, client, sender, alert, control, _reader, executor = build()
    client.entry_mode = "reject"
    threads = L2Strategy(_store, control, alert, executor).dispatch([sig()])
    for thread in threads:
        thread.join(timeout=10)
    assert len(client.entry_posts) == 1
    assert sender.channels() == ["error"]
    assert "📡" not in sender.calls[0][1]


def test_live_unconfirmed_sl_never_sends_false_order_success():
    _, store, client, sender, alert, control, _reader, executor = build()
    client.leg_mode["STOP_MARKET"] = "lost_absent"
    threads = L2Strategy(store, control, alert, executor).dispatch([sig()])
    for thread in threads:
        thread.join(timeout=10)
    assert len(client.entry_posts) == 1
    assert all(channel == "error" for channel in sender.channels())
    assert all("ĐẶT LỆNH THÀNH CÔNG" not in message for _, message in sender.calls)
    assert all("📡" not in message for _, message in sender.calls)


def test_confirmed_price_falls_back_to_verified_post_price_when_bingx_omits_it():
    row = {
        "symbol": "BTC-USDT", "side": "SELL", "combo": "C11",
        "avg_price": "67334.54", "tp_price": "11111.1", "sl_price": "99999.9",
        "confirmed_at_ms": 1794625249000,
        "protection": {
            "tp": {"status": "confirmed", "evidence": {
                "confirm": {"order": {"status": "NEW"}},
                "ack": {"orderId": "10"},
                "params": {"stopPrice": "66122.479"}}},
            "sl": {"status": "confirmed", "evidence": {
                "confirm": {"order": {"status": "NEW", "stopPrice": "67940.5105"}},
                "params": {"stopPrice": "67940.5"}}},
        },
    }
    message = success_message(row)
    assert "Entry **67334.5** · TP **66122.5** · SL **67940.5**" in message
    assert "11111.1" not in message and "99999.9" not in message


def test_price_formatter_never_turns_unconfirmed_stop_into_estimate():
    row = {"symbol": "BTC-USDT", "side": "SELL", "combo": "C11",
           "avg_price": "67334.5", "tp_price": "66122.4", "sl_price": "67940.5",
           "confirmed_at_ms": 1794625249000,
           "protection": {
               "tp": {"status": "unknown", "evidence": {"params": {"stopPrice": "66122.4"}}},
               "sl": {"status": "confirmed", "evidence": {"params": {"stopPrice": "67940.5"}}},
           }}
    message = success_message(row)
    assert "TP **?**" in message and "SL **67940.5**" in message
    assert _fmt_price("66122.45") == "66122.5"
    assert _fmt_price("66122.44") == "66122.4"
    assert _fmt_price("nan") == "?"


def test_close_notification_rounds_prices_but_not_quantities():
    message = closed_message({
        "symbol": "BTC-USDT", "side": "SELL", "combo": "C4", "avg_price": 67666.5,
        "entry_qty": "0.0015",
        "filled": [{"kind": "tp", "qty": "0.0008", "price": "66444.444"},
                   {"kind": "trailing", "qty": "0.0007", "price": "69440.049"}],
        "leftover": {"legs": [], "mode": "live"},
        "closed_at_ms": 1794625249000,
    })
    assert "Entry **67666.5** · KL **0.0015**" in message
    assert "TP: **0.0008 @ 66444.4**" in message
    assert "Trailing: **0.0007 @ 69440.0**" in message
    assert "Không còn lệnh dư" in message
