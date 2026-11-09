"""Offline regression for BTC C14 2026-11-09: exchange has SL, WAL cannot confirm.

No exchange credentials, POST or external network. Acknowledged orderId is
ownership proof; the corresponding GET must still verify identity and parameters.
"""
import unittest
from copy import deepcopy

from autotrade_v2.app.execution.final14_executor import Final14Executor
from autotrade_v2.app.execution.protection_journal import (
    ProtectionJournal, ProtectionReconcilePending,
)


class Exchange:
    def __init__(self, **changes):
        self.order = {
            "orderId": "123456789", "symbol": "BTC-USDT",
            "side": "BUY", "positionSide": "SHORT", "positionId": "912",
            "type": "STOP_MARKET", "status": "NEW",
            "origQty": "0.0013", "executedQty": "0",
            "stopPrice": "75630.1",
        }
        self.order.update(changes)
        self.detail_reads = 0
        self.posts = 0

    def get_order_detail(self, symbol, order_id):
        self.detail_reads += 1
        return {"code": 0, "data": {"order": deepcopy(self.order)}}

    def get_open_orders(self, symbol):
        raise AssertionError("openOrders is not authoritative for acknowledged orderId")

    def get_contract_rules(self, symbol):
        return {"pricePrecision": 1}

    @staticmethod
    def extract_order(raw):
        return (raw.get("data") or {}).get("order") or raw.get("data") or {}


def state():
    return {
        "event_id": "BTC-USDT|1h|C14|SELL|1794189599999",
        "protection": {},
        "protection_attempts": {"sl": {"posts": 1}},
        "protection_journal": {"sl": {
            "phase": "prepared",
            "acknowledged_order_id": "123456789",
            "baseline_ids": [],
            "spec": {
                "kind": "sl", "symbol": "BTC-USDT",
                "side": "BUY", "position_side": "SHORT",
                "quantity": "0.0013", "trigger": "75630.06",
                "position_id": "912",
            },
        }},
    }


def make_journal(exchange, current, persisted):
    executor = Final14Executor.__new__(Final14Executor)
    executor.client = exchange
    def verify(raw, kind, pid):
        spec = current["protection_journal"][kind]["spec"]
        return executor._verify_protection(
            "BTC-USDT", raw, kind, pid,
            entry_side="SELL", expected_quantity=spec["quantity"],
            expected_trigger=spec["trigger"],
        )
    def persist(next_state):
        persisted.append(deepcopy(next_state))
        current.clear()
        current.update(deepcopy(next_state))
    return ProtectionJournal(exchange, current, persist, verify)


class C14ProtectionRecoveryTest(unittest.TestCase):
    def test_acknowledged_sl_recovers_and_never_posts_duplicate(self):
        exchange = Exchange()
        current = state()
        snapshots = []
        journal = make_journal(exchange, current, snapshots)
        result = journal.place(
            "sl", "BTC-USDT", "SELL", "0.0013", 75630.06, "912",
            send=lambda: self.fail("SL must not be sent again"),
        )
        self.assertEqual(result["order_id"], "123456789")
        self.assertEqual(exchange.posts, 0)
        self.assertEqual(current["protection"]["sl"]["order_id"], "123456789")
        self.assertNotIn("sl", current["protection_journal"])
        self.assertGreaterEqual(exchange.detail_reads, 2)

    def test_identity_or_trigger_mismatch_stays_pending_without_repost(self):
        for changes in (
            {"side": "SELL"}, {"type": "TAKE_PROFIT_MARKET"},
            {"positionId": "999"}, {"origQty": "0.0026"},
            {"stopPrice": "75640.1"}, {"orderId": "other"},
        ):
            with self.subTest(changes=changes):
                exchange = Exchange(**changes)
                current = state()
                snapshots = []
                journal = make_journal(exchange, current, snapshots)
                with self.assertRaises(ProtectionReconcilePending):
                    journal.place(
                        "sl", "BTC-USDT", "SELL", "0.0013", 75630.06, "912",
                        send=lambda: self.fail("No repeat conditional POST"),
                    )
                self.assertNotIn("sl", current.get("protection") or {})
                self.assertEqual(current["protection_journal"]["sl"]["acknowledged_order_id"],
                                 "123456789")

    def test_unacknowledged_order_remains_pending_even_if_shape_matches(self):
        exchange = Exchange()
        current = state()
        current["protection_journal"]["sl"].pop("acknowledged_order_id")
        exchange.get_open_orders = lambda symbol: [exchange.order]
        journal = make_journal(exchange, current, [])
        with self.assertRaises(ProtectionReconcilePending):
            journal.place(
                "sl", "BTC-USDT", "SELL", "0.0013", 75630.06, "912",
                send=lambda: self.fail("No duplicate when response is uncertain"),
            )

    def test_bingx_precision_mismatch_cannot_mask_wrong_price(self):
        exchange = Exchange(stopPrice="75630.2")
        executor = Final14Executor.__new__(Final14Executor)
        executor.client = exchange
        with self.assertRaisesRegex(RuntimeError, "trigger price unverified"):
            executor._verify_protection(
                "BTC-USDT", {"data": {"order": {"orderId": "123456789"}}},
                "sl", "912", entry_side="SELL",
                expected_quantity="0.0013", expected_trigger="75630.06",
            )


if __name__ == "__main__":
    unittest.main()
