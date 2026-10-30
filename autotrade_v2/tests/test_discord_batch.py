"""Regression tests for durable order-summary batching."""
from __future__ import annotations
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from autotrade_v2.app.control.discord_batch import (
    DiscordTradeOutbox, _batch_key, _format_vn_time, _one_message, _retry_seconds,
)

@dataclass
class Event:
    event_key: str = "L3.EXEC.ORDER_COMPLETE"
    event_id: str = "BTC-USDT|1h|C1|BUY|1791734399999"
    symbol: str = "BTC-USDT"
    combo: int = 1
    order_id: str = "ORDER123"
    message: str = "entry and protections reconciled"
    created_at: str = "2026-10-11T16:00:27+00:00"
    details: dict | None = None

class Cursor:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
    def fetchall(self):
        return self.replies.pop(0)

class Conn:
    def __init__(self, cursor):
        self.cursor_obj = cursor
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def cursor(self):
        return self.cursor_obj

class Response:
    status_code = 204
    headers = {}

class LimitedResponse:
    status_code = 429
    headers = {"Retry-After": "48992"}

class TradeBatchTests(unittest.TestCase):
    def test_group_same_candle_different_combos_and_timeframes(self):
        a = Event()
        b = Event(event_id="BTC-USDT|15m|C7|BUY|1791734399999",combo=7)
        c = Event(symbol="ETH-USDT",event_id="ETH-USDT|1h|C1|BUY|1791734399999")
        self.assertEqual(_batch_key(a), _batch_key(b))
        self.assertNotEqual(_batch_key(a), _batch_key(c))

    def test_message_contains_complete_fields_in_one_send(self):
        rows = [
            (1, {"event_key":"L3.EXEC.ORDER_COMPLETE","combo":1,"side":"BUY",
                 "entry":84135.1,"tp":85782.71,"sl":83427.89,
                 "order_id":"A1","timeframe":"1h"}),
            (2, {"event_key":"L3.EXEC.ORDER_COMPLETE","combo":4,"side":"BUY",
                 "entry":84135.2,"tp":85800,"sl":83200,
                 "order_id":"A2","timeframe":"1h"}),
        ]
        message, ids = _one_message("BTC-USDT",rows)
        self.assertEqual(ids,[1,2])
        for expected in ("✅ Đặt lệnh", "BTC-USDT BUY", "📊 Combo C1",
                         "📊 Combo C4", "84135.1", "85782.71", "83427.89"):
            self.assertIn(expected,message)
        self.assertEqual(message.count("✅ Đặt lệnh thành công"), 2)
        self.assertNotIn("📦", message)
        self.assertNotIn("Order:", message)

    def test_order_time_is_original_event_time_in_vietnam(self):
        # Discord may post at 22:35, but the order was confirmed at 22:00.
        self.assertEqual(
            _format_vn_time("2026-10-24T15:00:24+00:00"),
            "22:00:24 24/10/2026",
        )
        self.assertEqual(
            _format_vn_time("2026-10-24T15:00:24Z"),
            "22:00:24 24/10/2026",
        )
        self.assertEqual(_format_vn_time("2026-10-24T15:00:24"), "")
        self.assertEqual(_format_vn_time("invalid"), "")

    def test_single_order_uses_exact_classic_discord_format(self):
        """Old screenshot: no batch heading, no order ID or technical event key."""
        content, ids = _one_message("BTC-USDT", [
            (1, {"event_key": "L3.EXEC.ORDER_COMPLETE", "combo": 11,
                 "side": "BUY", "entry": 86612.6,
                 "tp": 88171.6268, "sl": 85833.0866,
                 "order_time": "2026-10-24T15:00:24+00:00"}),
        ])
        self.assertEqual(ids, [1])
        self.assertEqual(content,
            "✅ Đặt lệnh\n"
            "BTC-USDT BUY\n\n"
            "📊 Combo TIER\n"
            "Entry: 86612.6\n\n"
            "TP : 88171.6268\n"
            "SL : 85833.0866\n\n"
            "🕒 Giờ xác nhận lệnh (VN): 22:00:24 24/10/2026\n\n"
            "✅ Đặt lệnh thành công")

    def test_failure_preserves_old_error_layout(self):
        content, ids = _one_message("ETH-USDT", [
            (1, {"event_key": "L3.EXEC.ORDER_FAIL", "combo": 104,
                 "side": "SELL", "entry": 2400, "tp": 2300, "sl": 2450,
                 "error": "BingX không khớp lệnh",
                 "order_time": "2026-10-24T15:00:24+00:00"})
        ])
        self.assertEqual(ids, [1])
        self.assertIn("❌ Đặt lệnh\nETH-USDT SELL", content)
        self.assertIn("📊 Combo C14", content)
        self.assertIn("🕒 Giờ xử lý lệnh (VN): 22:00:24 24/10/2026", content)
        self.assertIn("❌ Đặt lệnh thất bại\nLý do: BingX không khớp lệnh", content)

    def test_long_batch_does_not_drop_unformatted_rows(self):
        rows = [(i, {"event_key":"L3.EXEC.ORDER_FAIL","combo":i,
                     "error":"Failure "+("X" * 150)}) for i in range(1,30)]
        text, ids = _one_message("BTC-USDT",rows)
        self.assertTrue(ids)
        self.assertLessEqual(len(text),1900)
        self.assertLess(len(ids),len(rows))

    def test_retry_after_respected(self):
        self.assertGreaterEqual(_retry_seconds(LimitedResponse(),1),48992)

    def test_stage_persists_final_outcome_before_send(self):
        cur = Cursor([])
        ev = Event(details={"entry":84000,"tp":85500,"sl":83300,"side":"BUY"})
        outbox = DiscordTradeOutbox("test://db")
        with patch("autotrade_v2.app.control.discord_batch.psycopg.connect",return_value=Conn(cur)):
            outbox.stage(ev)
        self.assertIn("ON CONFLICT",cur.calls[0][0])
        self.assertIn('"entry": 84000',cur.calls[0][1][4])
        self.assertIn('"order_time": "2026-10-11T16:00:27+00:00"',cur.calls[0][1][4])

    def test_two_orders_send_in_one_http_call(self):
        payload = {"event_key":"L3.EXEC.ORDER_COMPLETE","combo":1,"side":"BUY",
                   "entry":84000,"tp":85000,"sl":83000}
        cur = Cursor([[("BTC-USDT|1791734399999","BTC-USDT")], [(1,payload,0),(2,dict(payload,combo=4),0)]])
        outbox = DiscordTradeOutbox("test://db")
        with patch("autotrade_v2.app.control.discord_batch.psycopg.connect",return_value=Conn(cur)), \
             patch("autotrade_v2.app.control.discord_batch.requests.post",return_value=Response()) as post:
            self.assertTrue(outbox.flush_one({"BTC-USDT":"https://example.invalid/secret"},{}))
        self.assertEqual(post.call_count,1)
        self.assertIn("📊 Combo C1", post.call_args.kwargs["json"]["content"])
        self.assertIn("📊 Combo C4", post.call_args.kwargs["json"]["content"])
        self.assertEqual(post.call_args.kwargs["json"]["content"].count("BTC-USDT BUY"), 2)
        self.assertIn("SET sent_at=now()",cur.calls[-1][0])

    def test_1015_defers_every_pending_order_without_fast_retry(self):
        payload = {"event_key":"L3.EXEC.ORDER_COMPLETE","combo":1}
        cur = Cursor([[("BTC-USDT|1791734399999","BTC-USDT")], [(1,payload,0)]])
        outbox = DiscordTradeOutbox("test://db")
        with patch("autotrade_v2.app.control.discord_batch.psycopg.connect",return_value=Conn(cur)), \
             patch("autotrade_v2.app.control.discord_batch.requests.post",return_value=LimitedResponse()) as post:
            self.assertTrue(outbox.flush_one({"BTC-USDT":"https://example.invalid/secret"},{}))
        self.assertEqual(post.call_count,1)
        self.assertIn("WHERE sent_at IS NULL",cur.calls[-1][0])
        self.assertGreaterEqual(cur.calls[-1][1][0],48992)

if __name__ == "__main__":
    unittest.main()
