"""Test actual Discord JSON payload without network or live exchange orders."""
from unittest.mock import patch

from autotrade_v3.live.l5_alert import (
    L5Alert, BLUE, RED, GREEN, ORANGE, YELLOW, GREY,
    success_embed, closed_embed,
)


def opened():
    return {
        "event_id": "ETH|C6|SELL|1", "symbol": "ETH-USDT", "combo": "C6", "side": "SELL",
        "avg_price": "1647.8", "executed_qty": "0.06", "confirmed_at_ms": 1795791653000,
        "tp_price": "9999", "sl_price": "8888",
        "protection": {
            "tp": {"status": "confirmed", "evidence": {"params": {"stopPrice": "1614.8"}}},
            "sl": {"status": "confirmed", "evidence": {"confirm": {"order": {"stopPrice": "1658.8"}}}},
        },
    }


def closed():
    return {
        **opened(), "entry_qty": "0.06", "filled_at": "2026-11-27T15:00:53+00:00",
        "closed_at_ms": 1795792636000,
        "filled": [{"kind": "sl", "qty": "0.06", "price": "1659.3"}],
        "leftover": {"mode": "live", "legs": [], "verified": True},
    }


def finance():
    return {"is_fully_closed": True, "closed_qty": "0.06", "gross_pnl": "-0.6900",
            "net_pnl": "-0.8000", "roi_pct": "-4.04", "open_fee": "0.05",
            "close_fee": "0.06", "funding": None, "raw": {"discord_finance_verified": True}}


def test_open_compact_and_confirmed_only():
    embed = success_embed(opened())["embeds"][0]
    assert embed["color"] == BLUE
    assert "Entry **1647.8**" in embed["description"] and "0.06 ETH" in embed["description"]
    assert "TP **1614.8** · SL **1658.8**" in embed["description"]
    assert "PnL" not in embed["description"]
    assert "9999" not in str(embed) and "8888" not in str(embed)


def test_missing_protection_never_displays_success():
    r = opened()
    r["protection"]["tp"]["status"] = "unknown"
    embed = success_embed(r)["embeds"][0]
    assert embed["color"] == ORANGE
    assert "TP **?**" in embed["description"]
    assert "MỞ LỆNH" not in embed["title"]


def test_close_compact_uses_reconciled_net_and_roi_only():
    embed = closed_embed(closed(), finance())["embeds"][0]
    assert embed["color"] == RED
    assert "-0.8 USDT" in embed["description"] and "-4.04%" in embed["description"]
    assert "1647.8 → 1659.3" in embed["description"]
    assert "Không còn lệnh dư" in embed["description"]
    assert "Funding" not in embed["description"]
    assert "Slippage" not in str(embed) and "Phí giao dịch" not in str(embed)
    assert "ước tính trước phí" not in str(embed)


def test_tp_and_shadow_status():
    r = closed()
    r["filled"] = [{"kind": "tp", "qty": "0.06", "price": "1614.8"}]
    assert closed_embed(r, finance())["embeds"][0]["color"] == GREEN
    r["leftover"] = {"mode": "shadow", "legs": ["sl"]}
    embed = closed_embed(r, finance())["embeds"][0]
    assert embed["color"] == ORANGE and "Còn lệnh dư" in embed["description"]


def test_incomplete_or_unverified_finance_never_sends():
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post") as post:
        alert.trade_closed(closed())
        for bad in ({"raw": {}}, {"closed_qty": "0.03"}, {"open_fee": None},
                    {"net_pnl": None}, {"is_fully_closed": False}):
            alert.trade_closed(closed(), {**finance(), **bad})
        r = closed()
        r["filled"][0]["qty"] = "0.03"
        alert.trade_closed(r, finance())
        assert post.call_count == 0


def test_default_sender_posts_one_embed_per_event_and_preserves_routing():
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post") as post:
        post.return_value.status_code = 204
        alert.trade_success(opened())
        alert.trade_success(opened())
        alert.trade_closed(closed(), finance())
        alert.trade_closed(closed(), finance())
        alert.error({"layer": "L1", "step": "data", "message": "missing candles"}, None)
        alert.notice("Khởi động", ["Đã kết nối"])
        assert post.call_count == 4
        assert [c.args[0] for c in post.call_args_list] == ["eth", "eth", "error", "error"]
        payloads = [c.kwargs["json"] for c in post.call_args_list]
        assert all(p["allowed_mentions"] == {"parse": []} for p in payloads)
        assert [p["embeds"][0]["color"] for p in payloads] == [BLUE, RED, YELLOW, GREY]


def test_network_failure_is_isolated():
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post", side_effect=RuntimeError("offline")):
        alert.trade_success(opened())
    assert alert.failures == 1


def test_pending_close_survives_new_alert_instance_and_waits_for_finance():
    from autotrade_v3.live.store import MemoryStore
    store = MemoryStore()
    row = closed()
    event = row["event_id"]
    store.executions[event] = {"id": 1, "raw_protection": {
        "cleanup": {"discord_close_pending": row}}}
    def new_alert():
        return L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                       webhook_error="error", store=store, synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post") as post:
        post.return_value.status_code = 204
        new_alert().flush_reconciled()
        assert post.call_count == 0 and store.pending_discord_closes()
        store.finances[1] = finance()
        new_alert().flush_reconciled([event])
        assert post.call_count == 0
        new_alert().flush_reconciled()
        assert post.call_count == 1
        assert not store.pending_discord_closes()
        new_alert().flush_reconciled()
        assert post.call_count == 1


def test_failed_close_delivery_remains_pending():
    from autotrade_v3.live.store import MemoryStore
    store = MemoryStore()
    row = closed()
    event = row["event_id"]
    store.executions[event] = {"id": 1, "raw_protection": {
        "cleanup": {"discord_close_pending": row}}}
    store.finances[1] = finance()
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", store=store, synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post") as post:
        post.return_value.status_code = 500
        alert.flush_reconciled()
        assert store.pending_discord_closes()
        post.return_value.status_code = 204
        alert.flush_reconciled()
        assert not store.pending_discord_closes()
        assert post.call_count == 2
