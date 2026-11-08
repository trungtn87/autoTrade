"""Discord reports are informational and never influence AutoTrade orders."""
import json
import unittest
from unittest.mock import Mock, patch

from app.ai.observer import GeminiObserver
from app.contracts import TradeIntent
from app.control.events import EventReporter, SystemEvent, _ai_discord_content


class GeminiDiscordTests(unittest.TestCase):
    def setUp(self):
        self.intent = TradeIntent(
            event_id="BTC-USDT|1h|C4|BUY|3600000",
            symbol="BTC-USDT", combo=4, side="BUY", timeframe="1h",
            close_time=3600000, entry=100.5, tp=103.0, sl=99.0, smc_dir=0,
        )
        self.reported = []
        self.model = Mock()
        self.observer = GeminiObserver(
            "", enabled=True, client=self.model, key="",
            event_cb=lambda *args, **kwargs: self.reported.append((args, kwargs)),
        )

    def test_hypothetical_block_is_explicit_and_mentions_sanitized(self):
        evt = SystemEvent(
            event_key="L4.AI.PRE_REVIEW", layer="L4", severity="INFO",
            message="recorded", event_id=self.intent.event_id,
            symbol="BTC-USDT", combo=4,
            details={
                "side": "BUY", "ai_decision": "BLOCK",
                "smc_alignment": "CONFLICT", "news_status": "UNAVAILABLE",
                "reasoning_verified": "4h bearish",
                "reasoning_ai_opinion": "@everyone should not enter",
            },
        )
        body = _ai_discord_content(evt)
        self.assertIn("GIẢ ĐỊNH: KHÔNG NÊN VÀO", body)
        self.assertIn("AI không chặn lệnh", body)
        self.assertIn("Chưa có dữ liệu tin tức kiểm chứng", body)
        self.assertNotIn("@everyone", body)
        self.assertLessEqual(len(body), 1900)

    def test_post_discord_displays_original_view_without_inventing_money(self):
        evt = SystemEvent(
            event_key="L4.AI.POST_REVIEW", layer="L4", severity="INFO",
            message="recorded", event_id=self.intent.event_id,
            symbol="ETH-USDT", combo=101,
            details={
                "side": "SELL", "outcome": None, "pnl": None, "roi": None,
                "pre_decision": "BLOCK", "pre_trade_assessment": "INCONCLUSIVE",
                "explanation": "No financial evidence",
                "lessons": ["Continue testing"], "revision": 2,
            },
        )
        body = _ai_discord_content(evt)
        self.assertIn("C11 · SHORT", body)
        self.assertIn("Nhận định trước Entry: KHÔNG NÊN VÀO", body)
        self.assertIn("PnL: chưa có số liệu xác minh", body)
        self.assertIn("Chưa đủ bằng chứng", body)
        self.assertIn("Revision: 2", body)

    def test_routes_ai_info_to_symbol_webhook_not_error_or_trade_batch(self):
        reporter = object.__new__(EventReporter)
        reporter.discord_enabled = True
        reporter.webhook_btc = "https://discord.example/btc"
        reporter.webhook_eth = "https://discord.example/eth"
        reporter.webhook_error = "https://discord.example/error"
        reporter._trade_outbox = None
        reporter._last_discord = {}
        reporter._last_error = ""
        for symbol, url in (
            ("BTC-USDT", reporter.webhook_btc),
            ("ETH-USDT", reporter.webhook_eth),
        ):
            event = SystemEvent(
                event_key="L4.AI.PRE_REVIEW", layer="L4", severity="INFO",
                message="analysis recorded", event_id=self.intent.event_id,
                symbol=symbol, combo=4,
                details={"side": "BUY", "ai_decision": "ALLOW"},
            ).normalized()
            with patch.object(reporter, "_send_email_alert"), patch(
                "app.control.events.requests.post"
            ) as post:
                post.return_value.status_code = 204
                reporter._notify(event)
                post.assert_called_once()
                args, kwargs = post.call_args
                self.assertEqual(args[0], url)
                self.assertEqual(kwargs["json"]["allowed_mentions"], {"parse": []})
                self.assertIn("GEMINI", kwargs["json"]["content"])

    def test_notifies_only_for_newly_saved_valid_pre_review(self):
        model_output = {
            "decision": "BLOCK", "confidence": 0.6, "smc_alignment": "MIXED",
            "news_risk": "UNKNOWN", "risk_flags": [],
            "reasoning_verified": "Mixed directions",
            "reasoning_ai_opinion": "risk", "watch_points": [],
        }
        self.model.ask.return_value = (
            {"candidates": [{"content": {"parts": [{"text": json.dumps(model_output)}]}}]},
            {}, 9,
        )
        context = {"features": [], "news_status": "UNAVAILABLE"}
        with patch.object(self.observer, "_pre_exists", return_value=False), patch(
            "app.ai.observer.build_pre_context", return_value=context
        ), patch.object(self.observer, "_try_budget", return_value=True), patch.object(
            self.observer, "_write_pre", return_value=False
        ):
            self.observer._observe_one(self.intent, None)
            self.assertEqual(len(self.reported), 0)
        with patch.object(self.observer, "_pre_exists", return_value=False), patch(
            "app.ai.observer.build_pre_context", return_value=context
        ), patch.object(self.observer, "_try_budget", return_value=True), patch.object(
            self.observer, "_write_pre", return_value=True
        ):
            self.observer._observe_one(self.intent, None)
        self.assertEqual(len(self.reported), 1)
        args, kwargs = self.reported[0]
        self.assertEqual(args[0], "L4.AI.PRE_REVIEW")
        self.assertEqual(kwargs["details"]["ai_decision"], "BLOCK")

    def test_post_report_requires_new_verified_closed_snapshot(self):
        post_output = {
            "pre_trade_assessment": "INCORRECT", "primary_cause": "REVERSAL",
            "contributing_factors": [], "explanation": "Large upside movement",
            "lessons": ["Don't assume countertrend will continue"],
        }
        self.model.ask.return_value = (
            {"candidates": [{"content": {"parts": [{"text": json.dumps(post_output)}]}}]},
            {}, 14,
        )
        ledger_row = (
            self.intent.event_id, 2,
            {"status": "CLOSED", "symbol": "BTC-USDT", "side": "BUY", "outcome": "LOSS",
             "pnl": "-0.75", "roi": None},
            {"signal": {"combo": 4, "symbol": "BTC-USDT", "side": "BUY"}},
            {"decision": "ALLOW"},
        )
        with patch.object(self.observer, "_pending_closed", return_value=[ledger_row]), patch.object(
            self.observer, "_try_budget", return_value=True
        ), patch.object(self.observer, "_write_post", return_value=False):
            self.observer._observe_closed()
            self.assertEqual(len(self.reported), 0)
        with patch.object(self.observer, "_pending_closed", return_value=[ledger_row]), patch.object(
            self.observer, "_try_budget", return_value=True
        ), patch.object(self.observer, "_write_post", return_value=True):
            self.observer._observe_closed()
            self.assertEqual(len(self.reported), 1)
            args, kwargs = self.reported[0]
            self.assertEqual(args[0], "L4.AI.POST_REVIEW")
            self.assertEqual(kwargs["details"]["pre_decision"], "ALLOW")
            self.assertEqual(kwargs["details"]["pnl"], "-0.75")

    def test_event_callback_failure_does_not_break_observer(self):
        self.observer.event_cb = Mock(side_effect=RuntimeError("discord unavailable"))
        self.assertIsNone(
            self.observer._announce(
                "L4.AI.PRE_REVIEW", self.intent.event_id, self.intent.symbol,
                self.intent.combo, {"ai_decision": "ALLOW"},
            )
        )


if __name__ == "__main__":
    unittest.main()
