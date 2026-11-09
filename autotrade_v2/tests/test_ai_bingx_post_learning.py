"""Safety tests for BingX-backed post-EXIT Gemini lessons; no live API calls."""
import json
import unittest
from unittest.mock import Mock, patch

from app.ai.bingx_post_learning import (
    eligible, evidence_payload, evidence_hash, assessment,
    prompt_for_review, parse_post_response,
)
from app.ai.observer import GeminiObserver


def _review():
    return {
        "event_id": "ETH-USDT|1h|C15|SELL|1700000000000",
        "symbol": "ETH-USDT", "combo": "C15", "side": "SELL",
        "signal_close_ms": 1700000000000, "ai_decision": "ALLOW",
        "input_json": {"signal": {"entry": 2000, "sl": 2020, "tp": 1980}},
        "output_json": {"reasoning_verified": "SMC short", "risk_flags": []},
    }


def _evidence():
    return {
        "status": "CLOSED", "quality": "BINGX_VERIFIED_EXIT",
        "entry_order_id": "1234567", "closed_ms": 1700007200000,
        "outcome": "WIN", "pnl": 4.1, "roi": 3.2,
        "source": "bingx_db_verified", "shared_position": False,
    }


def _response(value="CORRECT"):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps({
        "primary_cause": "Rủi ro biến động",
        "explanation": "Kết quả dựa trên đối soát lệnh",
        "contributing_factors": ["SMC phù hợp"],
        "lessons": ["Kiểm tra độ biến động"],
        "pre_trade_assessment": value,
    }, ensure_ascii=False)}]}}]}


class BingxPostLearningTests(unittest.TestCase):
    def test_requires_exact_closed_exchange_evidence(self):
        self.assertTrue(eligible(_review(), _evidence()))
        for change in (
            {"status": "CLOSED_PENDING"},
            {"outcome": None},
            {"entry_order_id": None},
            {"quality": "MANUAL_HISTORICAL_RECORD"},
            {"closed_ms": 1700000000000},
        ):
            e = {**_evidence(), **change}
            self.assertFalse(eligible(_review(), e))
        self.assertFalse(eligible({**_review(), "ai_decision": "TIMEOUT"}, _evidence()))

    def test_fingerprint_is_stable_and_changes_on_correction(self):
        p = evidence_payload(_review(), _evidence())
        self.assertEqual(evidence_hash(p), evidence_hash(dict(reversed(list(p.items())))))
        self.assertNotEqual(evidence_hash(p), evidence_hash({**p, "roi": "4.0"}))
        self.assertNotIn("api_key", p)
        self.assertEqual(p["entry_order_id"], "1234567")

    def test_estimated_merged_never_claims_ai_correctness(self):
        e = {**_evidence(), "quality": "MERGED_POSITION_ESTIMATE",
             "status": "ESTIMATED_CLOSED"}
        p = evidence_payload(_review(), e)
        answer = parse_post_response(_response("CORRECT"), p)
        self.assertEqual(answer["pre_trade_assessment"], "INCONCLUSIVE")
        self.assertTrue(answer["estimated"])
        self.assertEqual(assessment("ALLOW", "WIN", "BINGX_VERIFIED_EXIT"), "CORRECT")
        self.assertEqual(assessment("BLOCK", "LOSS", "BINGX_VERIFIED_EXIT"), "CORRECT")
        self.assertEqual(assessment("ALLOW", "LOSS", "BINGX_VERIFIED_EXIT"), "INCORRECT")
        self.assertEqual(assessment("ALLOW", "BREAKEVEN", "BINGX_VERIFIED_EXIT"), "INCONCLUSIVE")

    def test_bingx_post_prompt_does_not_imply_candle_fill(self):
        text = prompt_for_review(_review(), evidence_payload(_review(), _evidence()))
        self.assertIn("BingX database", text)
        self.assertIn("INCONCLUSIVE", text)
        self.assertIn("tiếng Việt", text)
        self.assertIn("20FINAL", text)

    def test_invalid_model_output_never_saved_as_ready(self):
        p = evidence_payload(_review(), _evidence())
        for reply in ({"candidates": []}, {"candidates": [{"content": {
            "parts": [{"text": '{"pre_trade_assessment":"CORRECT"}'}]}}]}):
            with self.assertRaises((ValueError, IndexError, KeyError, json.JSONDecodeError)):
                parse_post_response(reply, p)

    def test_background_post_saves_only_after_claim(self):
        client = Mock()
        client.ask.return_value = (_response(), {}, 33)
        cb = Mock()
        obs = GeminiObserver("fake", enabled=True, client=client,
                             event_cb=cb, poll_seconds=60)
        obs._bingx_candidates = Mock(return_value=[(_review(), _evidence())])
        obs._claim_bingx = Mock(return_value=1)
        obs._finish_bingx = Mock(return_value=True)
        obs._try_budget = Mock(return_value=True)
        with patch("app.ai.observer.time", return_value=_evidence()["closed_ms"] / 1000 + 10):
            obs._observe_bingx_closed()
        obs._finish_bingx.assert_called_once()
        cb.assert_called_once()
        self.assertEqual(cb.call_args.args[0], "L4.AI.BINGX_POST_REVIEW")
        self.assertEqual(cb.call_args.kwargs["details"]["pre_trade_assessment"], "CORRECT")
        client.ask.assert_called_once()

    def test_historical_backfill_writes_db_without_discord_spam(self):
        client = Mock()
        client.ask.return_value = (_response(), {}, 25)
        cb = Mock()
        obs = GeminiObserver("fake", enabled=True, client=client, event_cb=cb)
        obs._bingx_candidates = Mock(return_value=[(_review(), _evidence())])
        obs._claim_bingx = Mock(return_value=1)
        obs._finish_bingx = Mock(return_value=True)
        obs._try_budget = Mock(return_value=True)
        with patch("app.ai.observer.time", return_value=_evidence()["closed_ms"] / 1000 + 86400):
            obs._observe_bingx_closed()
        obs._finish_bingx.assert_called_once()
        cb.assert_not_called()

    def test_existing_hash_skips_and_does_not_consume_budget(self):
        client = Mock()
        obs = GeminiObserver("fake", enabled=True, client=client)
        obs._bingx_candidates = Mock(return_value=[(_review(), _evidence())])
        obs._claim_bingx = Mock(return_value=None)
        obs._try_budget = Mock()
        obs._observe_bingx_closed()
        client.ask.assert_not_called()
        obs._try_budget.assert_not_called()

    def test_no_result_if_no_evidence(self):
        client = Mock()
        obs = GeminiObserver("fake", enabled=True, client=client)
        obs._bingx_candidates = Mock(return_value=[])
        obs._observe_bingx_closed()
        client.ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
