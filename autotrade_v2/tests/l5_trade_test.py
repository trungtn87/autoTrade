"""No-network tests for the L5 trade alert adapter (fake writer, router, threads)."""
from __future__ import annotations

import unittest

from app.control.l5_trade import L5TradeAlerts


class FakeWriter:
    def __init__(self, returning=7, error=None):
        self.returning = returning
        self.error = error
        self.writes = []
        self.marks = []

    def write(self, layer, message, **kwargs):
        self.writes.append((layer, message, kwargs))
        if self.error:
            raise self.error
        return self.returning

    def mark_discord(self, log_id, status):
        self.marks.append((log_id, status))


class FakeRouter:
    def __init__(self, result="sent", error=None):
        self.result = result
        self.error = error
        self.sends = []

    def send(self, channel, content):
        self.sends.append((channel, content))
        if self.error:
            raise self.error
        return self.result


def run_sync(target, args):
    target(*args)


def make(writer=None, router=None, start_thread=run_sync):
    writer = writer or FakeWriter()
    router = router or FakeRouter()
    return L5TradeAlerts(writer, router, start_thread=start_thread), writer, router


class L5TradeAlertsTest(unittest.TestCase):
    def test_order_complete_sends_success_to_symbol_channel_without_log(self):
        alerts, writer, router = make()
        alerts.handle("L3.EXEC.ORDER_COMPLETE", "INFO", "done",
                      symbol="BTC-USDT", event_id="e1", order_id="o1")
        self.assertEqual(len(router.sends), 1)
        channel, content = router.sends[0]
        self.assertEqual(channel, "BTC")
        self.assertIn("ĐẶT LỆNH THÀNH CÔNG", content)
        self.assertIn("BTC-USDT", content)
        self.assertIn("e1", content)
        self.assertIn("o1", content)
        self.assertEqual(writer.writes, [])
        self.assertEqual(writer.marks, [])

    def test_silent_keys_do_nothing(self):
        alerts, writer, router = make()
        for key in ("L3.EXEC.SL_PLACED", "L3.EXEC.ORDER_SENT"):
            alerts.handle(key, "CRITICAL", "x", symbol="BTC-USDT")
        self.assertEqual(router.sends, [])
        self.assertEqual(writer.writes, [])

    def test_critical_fail_logs_and_sends_error_then_marks_sent(self):
        alerts, writer, router = make()
        alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL", "stop failed",
                      symbol="BTC-USDT")
        self.assertEqual(len(writer.writes), 1)
        layer, message, kwargs = writer.writes[0]
        self.assertEqual(layer, "L3")
        self.assertEqual(kwargs["step"], "L3.EXEC.SL_FAIL")
        self.assertEqual(len(router.sends), 1)
        channel, content = router.sends[0]
        self.assertEqual(channel, "ERROR")
        self.assertTrue(content.startswith("LỖI L3.EXEC.SL_FAIL"))
        self.assertEqual(writer.marks, [(7, "sent")])

    def test_identical_errors_are_not_deduplicated(self):
        alerts, writer, router = make()
        for _ in range(2):
            alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL", "same",
                          symbol="BTC-USDT")
        self.assertEqual(len(writer.writes), 2)
        self.assertEqual([c for c, _ in router.sends], ["ERROR", "ERROR"])

    def test_error_on_eth_goes_to_error_channel_not_eth(self):
        alerts, _, router = make()
        alerts.handle("L3.EXEC.UNSAFE_OPEN_POSITION", "WARNING", "unsafe",
                      symbol="ETH-USDT")
        self.assertEqual([c for c, _ in router.sends], ["ERROR"])

    def test_failed_send_marks_failed_without_raising(self):
        alerts, writer, _ = make(router=FakeRouter(result="failed"))
        alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL", "x", symbol="BTC-USDT")
        self.assertEqual(writer.marks, [(7, "failed")])

    def test_writer_error_still_sends_error_alert(self):
        alerts, writer, router = make(writer=FakeWriter(error=RuntimeError("db down")))
        alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL", "x", symbol="BTC-USDT")
        self.assertEqual([c for c, _ in router.sends], ["ERROR"])
        self.assertEqual(writer.marks, [])

    def test_router_error_does_not_raise(self):
        alerts, writer, _ = make(router=FakeRouter(error=RuntimeError("boom")))
        alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL", "x", symbol="BTC-USDT")
        self.assertEqual(writer.marks, [(7, "failed")])

    def test_sends_are_deferred_to_start_thread(self):
        deferred = []
        alerts, writer, router = make(
            start_thread=lambda target, args: deferred.append((target, args)))
        alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL", "x", symbol="BTC-USDT")
        alerts.handle("L3.EXEC.ORDER_COMPLETE", "INFO", "y", symbol="ETH-USDT")
        self.assertEqual(router.sends, [])
        self.assertEqual(len(deferred), 2)

    def test_unknown_symbol_on_success_is_caught(self):
        alerts, writer, router = make()
        alerts.handle("L3.EXEC.ORDER_COMPLETE", "INFO", "done", symbol="DOGE-USDT")
        self.assertEqual(router.sends, [])
        self.assertEqual(writer.writes, [])

    def test_unrelated_info_key_is_ignored(self):
        alerts, writer, router = make()
        alerts.handle("L3.EXEC.SOMETHING_ELSE", "INFO", "x", symbol="BTC-USDT")
        self.assertEqual(router.sends, [])
        self.assertEqual(writer.writes, [])

    def test_urls_in_message_are_redacted(self):
        alerts, _, router = make()
        alerts.handle("L3.EXEC.SL_FAIL", "CRITICAL",
                      "see https://discord.com/api/webhooks/1/abc",
                      symbol="BTC-USDT")
        content = router.sends[0][1]
        self.assertNotIn("https://", content)
        self.assertIn("[URL]", content)


if __name__ == "__main__":
    unittest.main()
