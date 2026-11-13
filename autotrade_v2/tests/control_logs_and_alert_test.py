"""No-network tests for the control-log writer and the L5 Discord router."""
from __future__ import annotations

import json
import unittest

from app.control.alert_v2 import (
    DiscordRouter,
    format_entry_result,
    format_signal,
    route_for_symbol,
)
from app.control.error_log import ControlLogWriter


class FakeDb:
    def __init__(self, returning=42, error=None):
        self.returning = returning
        self.error = error
        self.calls = []

    def execute_returning(self, sql, params):
        self.calls.append((sql, params))
        if self.error:
            raise self.error
        return self.returning

    def execute(self, sql, params):
        self.calls.append((sql, params))


class ControlLogWriterTest(unittest.TestCase):
    def test_rejects_unknown_layer(self):
        writer = ControlLogWriter(FakeDb())
        with self.assertRaises(ValueError):
            writer.write("L9", "bad layer")

    def test_sanitizes_details_before_insert(self):
        db = FakeDb(returning=42)
        writer = ControlLogWriter(db)
        details = {
            "apiSecret": "s3cr3t",
            "signature": "abc",
            "webhook_url": "https://discord.com/api/webhooks/1/x",
            "note": "posted to https://discord.com/api/webhooks/1/x",
            "nested": {"Authorization": "Bearer x", "qty": 2},
        }
        log_id = writer.write("L5", "alert", symbol="BTC-USDT", details=details)
        self.assertEqual(log_id, 42)
        sql, params = db.calls[-1]
        self.assertIn("autotrade_analytics.control_logs", sql)
        self.assertIn("RETURNING id", sql)
        payload = params[7]
        for forbidden in ("apiSecret", "signature", "webhook_url", "s3cr3t",
                          "Authorization", "Bearer", "https://discord"):
            self.assertNotIn(forbidden, payload)
        stored = json.loads(payload)
        self.assertEqual(stored["note"], "[REDACTED_URL]")
        self.assertEqual(stored["nested"], {"qty": 2})

    def test_write_returns_minus_one_on_db_error(self):
        writer = ControlLogWriter(FakeDb(error=RuntimeError("db down")))
        self.assertEqual(writer.write("L1", "boom"), -1)
        self.assertIn("db down", writer.last_error)

    def test_mark_discord_validates_status_and_updates(self):
        db = FakeDb()
        writer = ControlLogWriter(db)
        with self.assertRaises(ValueError):
            writer.mark_discord(42, "bogus")
        self.assertEqual(db.calls, [])
        writer.mark_discord(42, "sent")
        self.assertEqual(len(db.calls), 1)
        self.assertEqual(db.calls[0][1], ("sent", 42))


class DiscordRouterTest(unittest.TestCase):
    def test_send_skipped_when_webhook_missing(self):
        posted = []
        router = DiscordRouter({"BTC": ""}, lambda url, content: posted.append(url))
        self.assertEqual(router.send("BTC", "hello"), "skipped")
        self.assertEqual(posted, [])

    def test_send_failed_without_retry(self):
        calls = []

        def failing_post(url, content):
            calls.append(url)
            raise RuntimeError("discord 500")

        router = DiscordRouter({"ERROR": "https://discord.example/hook"}, failing_post)
        self.assertEqual(router.send("ERROR", "boom"), "failed")
        self.assertEqual(len(calls), 1)

    def test_route_for_symbol(self):
        self.assertEqual(route_for_symbol("BTC-USDT"), "BTC")
        self.assertEqual(route_for_symbol("ETH-USDT"), "ETH")
        with self.assertRaises(ValueError):
            route_for_symbol("DOGE-USDT")


class AlertFormatTest(unittest.TestCase):
    def test_format_signal_header_and_event_id(self):
        text = format_signal("BTC-USDT", "combo-a", "LONG", "2026-11-13 10:00",
                             "100", "110", "95", "evt-123")
        self.assertTrue(text.startswith("TÍN HIỆU — CHƯA ĐẶT LỆNH"))
        self.assertIn("evt-123", text)
        self.assertNotIn("http", text)

    def test_format_entry_result_failed_with_order_id(self):
        text = format_entry_result(
            "FAILED", "ETH-USDT", "combo-b", "SHORT", "evt-9", "ord-777",
            None, None, {}, "entry_confirm", "timeout waiting for fill",
        )
        self.assertIn("ord-777", text)
        self.assertIn("entry_confirm", text)
        self.assertNotIn("không đặt được lệnh", text)


if __name__ == "__main__":
    unittest.main()
