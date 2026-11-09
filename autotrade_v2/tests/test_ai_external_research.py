"""Read-only Gemini external research / independent SMC regression tests."""
import json
import unittest
from unittest.mock import Mock, patch

import requests

from app.ai.external_research import BinanceFuturesPublic
from app.ai.independent_smc import analyze_structure
from app.ai.observer import GeminiClient, GeminiObserver, _pre_prompt
from app.contracts import TradeIntent


class IndependentResearchTests(unittest.TestCase):
    def test_independent_smc_excludes_future_bars(self):
        bars = [{
            "open_ms": i * 900_000, "close_ms": (i+1) * 900_000-1,
            "open": 100+i/4, "high": 101+i/4, "low": 99+i/4,
            "close": 100+i/4, "volume": 20,
        } for i in range(32)]
        as_of = bars[26]["close_ms"]
        a = analyze_structure(bars, timeframe="15m", as_of_ms=as_of)
        mutated = [dict(x) for x in bars]
        for row in mutated[27:]:
            row["high"] = 1e8
            row["low"] = 0
            row["close"] = 100000
        b = analyze_structure(mutated, timeframe="15m", as_of_ms=as_of)
        self.assertEqual(a, b)
        self.assertEqual(a["bar_count"], 27)
        self.assertEqual(a["status"], "AVAILABLE")

    def test_binance_as_of_structure_never_uses_future_candle(self):
        end = 30 * 900_000-1
        rows = [[i*900_000, "100", "101", "99", "100", "25",
                 (i+1)*900_000-1] for i in range(35)]
        transport = Mock()
        transport.return_value.json.return_value = rows
        public = BinanceFuturesPublic(get=transport)
        got = public.structure_as_of("ETH-USDT", end)
        self.assertEqual(got["status"], "AVAILABLE")
        for tf in ("15m", "1h", "4h"):
            self.assertEqual(got["timeframes"][tf]["bar_count"], 30)
            self.assertEqual(got["timeframes"][tf]["as_of_ms"], end)
        self.assertEqual(transport.call_count, 3)
        self.assertTrue(all(c.kwargs["params"]["endTime"] == end
                            for c in transport.call_args_list))

    def test_binance_timeout_keeps_partial_or_unavailable_and_never_blocks(self):
        transport = Mock(side_effect=requests.Timeout("network"))
        public = BinanceFuturesPublic(get=transport)
        got = public.capture("BTC-USDT", 1000)
        self.assertEqual(got["status"], "UNAVAILABLE")
        self.assertEqual(public.structure_as_of("BTC-USDT", 1000)["status"],
                         "UNAVAILABLE")
        self.assertEqual(public.exit_window("BTC-USDT", 1000, 2000)["status"],
                         "UNAVAILABLE")

    def test_news_requires_grounded_sources_and_is_not_pre_signal_evidence(self):
        response = Mock()
        response.json.return_value = {
            "candidates": [{
                "content": {"parts": [{"text": "Tin có dẫn nguồn."}]},
                "groundingMetadata": {"groundingChunks": [
                    {"web": {"uri": "https://example.com/article", "title": "Headline"}}]},
            }]
        }
        client = GeminiClient("key", "gemini-3.6-flash",
                              transport=Mock(return_value=response))
        data = client.research_news("BTC-USDT", 1000)
        self.assertEqual(data["status"], "GROUNDED_REVIEW_TIME")
        self.assertFalse(data["publication_time_verified"])
        self.assertIsNone(data["sources"][0]["published_ms"])
        self.assertEqual(client.transport.call_args.kwargs["json"]["tools"],
                         [{"google_search": {}}])
        self.assertNotIn("responseMimeType",
                         client.transport.call_args.kwargs["json"]["generationConfig"])

    def test_observer_separates_bot_and_independent_smc(self):
        intent = TradeIntent(
            "ETH-USDT|1h|C15|SELL|7200000", "ETH-USDT", 105,
            "SELL", "1h", 7200000, 2100.0, 2058.0, 2116.8, -1,
        )
        ai = GeminiObserver("", enabled=True, client=Mock(), key="")
        ai.market_enabled = True
        ai.news_enabled = False
        ai.market = Mock()
        ai.market.capture.return_value = {"status": "UNAVAILABLE"}
        ai.market.structure_as_of.return_value = {
            "status": "PARTIAL", "timeframes": {"1h": {
                "status": "AVAILABLE", "trend": "BEARISH", "last_break": None,
            }},
        }
        ctx = {"features": [], "news_status": "UNAVAILABLE"}
        ai._external_pre(intent, ctx)
        self.assertEqual(ctx["strategy_config"]["combo_config"]["layer2"], "OB")
        self.assertEqual(ctx["independent_smc"]["status"], "PARTIAL")
        self.assertEqual(ctx["features"][0]["id"], "EXT-BINANCE-SMC-1h")
        prompt = _pre_prompt(ctx)
        self.assertIn("independent_smc", prompt)
        self.assertIn("4h OB cannot", prompt)
        self.assertIn("after the signal", prompt.lower())


if __name__ == "__main__":
    unittest.main()
