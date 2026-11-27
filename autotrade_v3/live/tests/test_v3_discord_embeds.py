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


def fields(embed):
    return {f["name"]: f["value"] for f in embed["fields"]}


def test_open_uses_unrounded_confirmed_prices_and_actual_quantity():
    embed = success_embed(opened())["embeds"][0]
    f = fields(embed)
    assert embed["color"] == BLUE
    assert f["Entry"] == "1647.8" and f["Khối lượng"] == "0.06 ETH"
    assert f["Take Profit"] == "1614.8" and f["Stop Loss"] == "1658.8"
    assert f["PnL tại TP (trước phí)"] == "+1.9800 USDT"
    assert f["PnL tại SL (trước phí)"] == "-0.6600 USDT"
    assert "9999" not in str(embed) and "8888" not in str(embed)


def test_missing_protection_never_displays_success():
    r = opened()
    r["protection"]["tp"]["status"] = "unknown"
    embed = success_embed(r)["embeds"][0]
    assert embed["color"] == ORANGE
    assert fields(embed)["Take Profit"] == "?"
    assert "THÀNH CÔNG" not in embed["title"]


def test_close_is_explicitly_estimated_and_slippage_uses_verified_trigger():
    r = closed()
    embed = closed_embed(r)["embeds"][0]
    f = fields(embed)
    assert embed["color"] == RED
    assert "-0.6900 USDT" in embed["description"]
    assert "ước tính" in embed["description"]
    assert f["Exit (giá khớp bình quân)"] == "1659.3"
    assert f["ROI / Net PnL"] == "Chờ đối soát DB"
    assert f["Slippage bất lợi (âm = có lợi)"] == "+0.5000 USDT/ETH"
    assert "Không còn lệnh dư" in f["Trạng thái"]


def test_incomplete_fills_do_not_invent_pnl_or_cleanup():
    r = closed()
    r["filled"][0]["qty"] = "0.03"
    r["leftover"] = {}
    embed = closed_embed(r)["embeds"][0]
    assert "Chưa có dữ liệu" in embed["description"]
    assert fields(embed)["Exit (giá khớp bình quân)"] == "?"
    assert "Chưa có bằng chứng" in fields(embed)["Trạng thái"]


def test_tp_and_shadow_status():
    r = closed()
    r["filled"] = [{"kind": "tp", "qty": "0.06", "price": "1614.8"}]
    assert closed_embed(r)["embeds"][0]["color"] == GREEN
    r["leftover"] = {"mode": "shadow", "legs": ["sl"]}
    embed = closed_embed(r)["embeds"][0]
    assert embed["color"] == ORANGE
    assert "chưa hủy" in fields(embed)["Trạng thái"]


def test_default_sender_posts_one_embed_per_event_and_preserves_routing():
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post") as post:
        post.return_value.status_code = 204
        alert.trade_success(opened())
        alert.trade_success(opened())
        alert.trade_closed(closed())
        alert.trade_closed(closed())
        alert.error({"layer": "L1", "step": "data", "message": "missing candles"}, None)
        alert.notice("Khởi động", ["Đã kết nối"])
        assert post.call_count == 4
        assert [c.args[0] for c in post.call_args_list] == ["eth", "eth", "error", "error"]
        payloads = [c.kwargs["json"] for c in post.call_args_list]
        assert all(p["allowed_mentions"] == {"parse": []} for p in payloads)
        assert [p["embeds"][0]["color"] for p in payloads] == [BLUE, RED, YELLOW, GREY]
        assert all(len(p["embeds"]) == 1 for p in payloads)


def test_network_failure_is_isolated():
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post", side_effect=RuntimeError("offline")):
        alert.trade_success(opened())
    assert alert.failures == 1
    assert alert.sent[-1]["status"] == "failed"


def test_closed_finance_from_dashboard_projection_in_worker():
    from autotrade_v3.live.store import MemoryStore
    store = MemoryStore()
    row = closed()
    store.executions[row["event_id"]] = {"id": 1}
    store.finances[1] = {"is_fully_closed": True, "net_pnl": "-0.8123",
                         "roi_pct": "-4.12", "open_fee": "0.05", "close_fee": "0.06", "funding": None}
    alert = L5Alert(enabled=True, webhook_btc="btc", webhook_eth="eth",
                    webhook_error="error", store=store, synchronous=True)
    with patch("autotrade_v3.live.l5_alert.requests.post") as post:
        post.return_value.status_code = 204
        alert.trade_closed(row)
        embed = post.call_args.kwargs["json"]["embeds"][0]
    assert "Net PnL theo DB: -0.8123 USDT" in embed["description"]
    assert "Funding chưa phân bổ" in embed["description"]
    assert fields(embed)["ROI theo DB"] == "-4.12%"
    assert fields(embed)["Phí giao dịch"] == "0.1100 USDT"
