"""AI evidence is sourced from existing BingX archive projection, never candles."""
import unittest
from unittest.mock import Mock, patch

from app.ai.bingx_feedback import normalize_feedback, load_bingx_feedback


class BingxFeedbackTests(unittest.TestCase):
    def test_closed_with_estimated_money_stays_labelled(self):
        row = {
            "status": "CLOSED", "source": "bingx_db_roi_estimate",
            "closed_ms": 12345, "outcome": "WIN", "pnl": 1.25,
            "roi": 4.4, "reason": None, "shared_position": False,
        }
        result = normalize_feedback(row, "entry-1")
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["quality"], "BINGX_VERIFIED_EXIT_ESTIMATED_MONEY")
        self.assertEqual(result["pnl"], 1.25)

    def test_merged_closed_is_not_verified(self):
        result = normalize_feedback({
            "status": "ESTIMATED_CLOSED",
            "source": "bingx_db_merged_allocation_estimate",
            "outcome": "LOSS", "roi": -1.5, "pnl": -0.3,
            "closed_ms": 12346, "shared_position": True,
        }, "entry-2")
        self.assertEqual(result["quality"], "MERGED_POSITION_ESTIMATE")

    def test_unresolved_never_leaks_money(self):
        result = normalize_feedback({
            "status": "UNRESOLVED", "source": "bingx_db_unresolved",
            "outcome": "WIN", "roi": 20, "pnl": 100,
        }, "entry-3")
        self.assertIsNone(result["outcome"])
        self.assertIsNone(result["roi"])
        self.assertIsNone(result["pnl"])

    def test_no_signal_link_is_not_proof_of_no_trade(self):
        result = normalize_feedback(None, None)
        self.assertEqual(result["status"], "NO_CONFIRMED_ENTRY_LINK")
        self.assertIsNone(result["outcome"])

    def test_exact_signal_id_match_no_approximate_combo(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            {"signal_id": "E1", "entry_order_id": "x1", "symbol": "ETH-USDT"}
        ]
        projection = [
            {"symbol": "ETH-USDT", "entry_order_id": "x1",
             "status": "CLOSED", "source": "bingx_db_verified",
             "outcome": "WIN", "closed_ms": 1},
            {"symbol": "ETH-USDT", "entry_order_id": "x2",
             "status": "CLOSED", "source": "bingx_db_verified",
             "outcome": "LOSS", "closed_ms": 2},
        ]
        engine = Mock()
        with patch("dashboard.autotrade_live.db.get_engine", return_value=engine), patch(
            "dashboard.autotrade_live.results.load_results", return_value=projection
        ) as load:
            results = load_bingx_feedback(conn, [
                {"event_id": "E1", "symbol": "ETH-USDT"},
                {"event_id": "E2", "symbol": "ETH-USDT"},
            ])
        self.assertEqual(results["E1"]["outcome"], "WIN")
        self.assertEqual(results["E2"]["status"], "NO_CONFIRMED_ENTRY_LINK")
        self.assertEqual(results["E1"]["quality"], "BINGX_VERIFIED_EXIT")
        engine.dispose.assert_called_once()
        load.assert_called_once_with(engine)

    def test_two_links_do_not_arbitrarily_select_one(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            {"signal_id": "E1", "entry_order_id": "x1", "symbol": "ETH-USDT"},
            {"signal_id": "E1", "entry_order_id": "x2", "symbol": "ETH-USDT"},
        ]
        with patch("dashboard.autotrade_live.db.get_engine", return_value=Mock()), patch(
            "dashboard.autotrade_live.results.load_results", return_value=[]
        ):
            results = load_bingx_feedback(conn, [
                {"event_id": "E1", "symbol": "ETH-USDT"}])
        self.assertEqual(results["E1"]["status"], "MULTIPLE_LINKED_ENTRIES_UNRESOLVED")
        self.assertIsNone(results["E1"]["pnl"])

    def test_symbol_mismatch_rejected(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            {"signal_id": "E1", "entry_order_id": "x1", "symbol": "BTC-USDT"}
        ]
        with patch("dashboard.autotrade_live.db.get_engine", return_value=Mock()), patch(
            "dashboard.autotrade_live.results.load_results", return_value=[]
        ):
            results = load_bingx_feedback(conn, [
                {"event_id": "E1", "symbol": "ETH-USDT"}])
        self.assertEqual(results["E1"]["status"], "SYMBOL_MISMATCH_UNRESOLVED")
        self.assertIsNone(results["E1"]["outcome"])


if __name__ == "__main__":
    unittest.main()
