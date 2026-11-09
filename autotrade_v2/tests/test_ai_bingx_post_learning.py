"""Safety tests for BingX-backed post-EXIT Gemini lessons; no live API calls."""
import json
import unittest
from unittest.mock import Mock, MagicMock, patch

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

    def test_prior_lessons_sql_excludes_future_feedback(self):
        from types import SimpleNamespace
        obs = GeminiObserver("fake", enabled=True, client=Mock())
        con = Mock()
        con.execute.side_effect = [
            Mock(fetchone=Mock(return_value=("autotrade_analytics.ai_bingx_post_reviews",))),
            Mock(fetchall=Mock(return_value=[
                ("INCONCLUSIVE", ["Xem lại OB"], "MERGED_POSITION_ESTIMATE")
            ])),
        ]
        manager = MagicMock()
        manager.__enter__.return_value = con
        obs._connection = Mock(return_value=manager)
        lessons = obs._prior_lessons(SimpleNamespace(
            symbol="ETH-USDT", combo=105, close_time=1700000000000
        ))
        self.assertEqual(lessons[0]["quality"], "MERGED_POSITION_ESTIMATE")
        sql = con.execute.call_args_list[1].args[0]
        self.assertIn("closed_ms < %s", sql)
        self.assertIn("completed_at <= to_timestamp(%s / 1000.0)", sql)
        self.assertIn("DISTINCT ON (event_id)", sql)
        self.assertEqual(con.execute.call_args_list[1].args[1],
                         ("ETH-USDT", "C15", 1700000000000,
                          1700000000000, 1700000000000))

    def test_ai_page_uses_dict_row_and_revision_fingerprint(self):
        from datetime import datetime, timezone
        from app.ai import page
        review = {**_review(), "bingx_post": _evidence()}
        payload = evidence_payload(review, review["bingx_post"])
        record = {
            "event_id": review["event_id"],
            "evidence_hash": evidence_hash(payload),
            "assessment": "CORRECT",
            "analysis_json": {"lessons": ["Cẩn thận khi short"]},
            "evidence_quality": "BINGX_VERIFIED_EXIT",
            "completed_at": datetime(2026, 11, 9, tzinfo=timezone.utc),
        }
        conn = MagicMock()
        table = Mock()
        table.fetchone.return_value = {"table_ref": "autotrade_analytics.ai_bingx_post_reviews"}
        rows = Mock()
        rows.fetchall.return_value = [record]
        conn.execute.side_effect = [table, rows]
        ready = page._attach_bingx_learning(conn, [review])[0]
        self.assertEqual(ready["post_learning"]["assessment"], "CORRECT")
        self.assertIn("2026-11-09", ready["post_learning"]["completed_at"])
        self.assertEqual(conn.execute.call_count, 2)
        self.assertIn("AS table_ref", conn.execute.call_args_list[0].args[0])
        # A corrected ROI must invalidate the old lesson's fingerprint.
        conn.execute.reset_mock()
        conn.execute.side_effect = [table, rows]
        revised = {**_review(), "bingx_post": {**_evidence(), "roi": 99}}
        self.assertIsNone(page._attach_bingx_learning(conn, [revised])[0]["post_learning"])

    def test_scans_older_signal_pages_instead_of_latest_100_forever(self):
        obs = GeminiObserver("fake", enabled=True, client=Mock())
        conn = MagicMock()
        conn.__enter__.return_value = conn
        exists = Mock()
        exists.fetchone.return_value = ("autotrade_analytics.ai_bingx_post_reviews",)
        conn.execute.return_value = exists
        cur = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        old = {**_review(), "event_id": "E2", "signal_close_ms": 1600000000000}
        new = {**_review(), "event_id": "E3", "signal_close_ms": 1700000000000}
        cur.execute.return_value.fetchall.side_effect = [[new], [old], []]
        obs._connection = Mock(return_value=conn)
        with patch("app.ai.observer.load_bingx_feedback",
                   return_value={"E2": _evidence(), "E3": _evidence()}):
            obs._bingx_candidates()
            self.assertEqual(obs._bingx_scan_before, (1700000000000, "E3"))
            obs._bingx_candidates()
            self.assertEqual(obs._bingx_scan_before, (1600000000000, "E2"))
            obs._bingx_candidates()
        self.assertIsNone(obs._bingx_scan_before)
        sql = cur.execute.call_args_list[0].args[0]
        self.assertIn("(signal_close_ms,event_id) <", sql)

    def test_no_result_if_no_evidence(self):
        client = Mock()
        obs = GeminiObserver("fake", enabled=True, client=client)
        obs._bingx_candidates = Mock(return_value=[])
        obs._observe_bingx_closed()
        client.ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
