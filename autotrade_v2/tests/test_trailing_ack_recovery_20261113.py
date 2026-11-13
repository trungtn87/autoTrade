"""Offline regression using the observed 2026-11-13 C4 trailing quantities and
historical BingX GET field omissions. No exchange access or live mutations.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from autotrade_v2.app.execution.final14_executor import Final14Executor
from autotrade_v2.app.execution.protection_journal import (
    ProtectionJournal, ProtectionReconcilePending,
)
from autotrade_v2.app.execution.service import ExecutionService


ORDER_ID = "2121090868580028416"


class TrailingExchange:
    def __init__(self, **changes):
        self.detail = {
            "orderID": ORDER_ID, "symbol": "ETH-USDT",
            "side": "BUY", "positionSide": "SHORT", "positionId": "912",
            "type": "TRAILING_STOP_MARKET", "status": "NEW",
            "origQty": "0.02", "executedQty": "0.00",
            # Historical BingX archive GET-style detail: no activationPrice,
            # and actPrice/priceRate empty. Keep it separate from POST ACK.
            "actPrice": "", "priceRate": "",
        }
        self.detail.update(changes)
        self.gets = 0

    def get_order_detail(self, symbol, order_id):
        self.gets += 1
        assert symbol == "ETH-USDT" and order_id == ORDER_ID
        return {"code": 0, "data": {"order": deepcopy(self.detail)}}

    def get_open_orders(self, symbol):
        raise AssertionError("acknowledged ID must not trigger shape-based adoption")

    def get_contract_rules(self, symbol):
        return {"pricePrecision": 2}

    @staticmethod
    def extract_order(raw):
        return (raw.get("data") or {}).get("order") or raw.get("data") or {}


def order_ack():
    return {"code": 0, "data": {"order": {
        "orderID": ORDER_ID, "symbol": "ETH-USDT", "side": "BUY",
        "positionSide": "SHORT", "type": "TRAILING_STOP_MARKET",
        "status": "NEW", "quantity": 0.02,
        "activationPrice": 1923.57, "priceRate": 0.005,
    }}}


def state():
    return {
        "protection": {},
        "protection_attempts": {"trailing": {"posts": 1}},
        "protection_journal": {"trailing": {
            "phase": "prepared", "baseline_ids": [],
            "spec": {"kind": "trailing", "symbol": "ETH-USDT",
                     "side": "BUY", "position_side": "SHORT",
                     "quantity": "0.02", "trigger": "1923.57",
                     "position_id": "912", "callback_rate": "0.005"},
        }},
    }


def make_journal(exchange, current, snapshots):
    executor = Final14Executor.__new__(Final14Executor)
    executor.client = exchange

    def verify(raw, kind, position_id):
        prepared = current["protection_journal"][kind]
        return executor._verify_protection(
            "ETH-USDT", raw, kind, position_id,
            entry_side="SELL", expected_quantity=prepared["spec"]["quantity"],
            expected_trigger=prepared["spec"]["trigger"],
            expected_callback=prepared["spec"]["callback_rate"],
            acknowledged_parameters=prepared.get("acknowledged_parameters"),
        )

    def persist(next_state):
        snapshots.append(deepcopy(next_state))
        current.clear()
        current.update(deepcopy(next_state))

    return ProtectionJournal(exchange, current, persist, verify)


class TrailingAckRecoveryRegression(unittest.TestCase):
    def test_post_ack_persisted_before_get_and_reused_when_get_omits_params(self):
        exchange = TrailingExchange()
        current = state()
        snapshots = []
        result = make_journal(exchange, current, snapshots)._confirm(
            "trailing", order_ack(), "912",
        )
        self.assertEqual(result["order_id"], ORDER_ID)
        self.assertEqual(result["parameter_sources"],
                         {"activationPrice": "POST_ACK", "priceRate": "POST_ACK"})
        self.assertEqual(snapshots[0]["protection_journal"]["trailing"]
                         ["acknowledged_parameters"]["activationPrice"], 1923.57)
        self.assertNotIn("trailing", current["protection_journal"])

    def test_restart_recovers_exact_ack_without_resending(self):
        exchange = TrailingExchange()
        current = state()
        current["protection_journal"]["trailing"]["acknowledged_order_id"] = ORDER_ID
        current["protection_journal"]["trailing"]["acknowledged_parameters"] = (
            order_ack()["data"]["order"]
        )
        journal = make_journal(exchange, current, [])
        recovered = journal.place(
            "trailing", "ETH-USDT", "SELL", "0.02", 1923.57, "912",
            send=lambda: self.fail("Must not POST the trailing again"),
            callback_rate=0.005,
        )
        self.assertEqual(recovered["order_id"], ORDER_ID)
        self.assertGreaterEqual(exchange.gets, 2)
        self.assertNotIn("trailing", current["protection_journal"])

    def test_legacy_ack_without_post_params_is_explicitly_pending_not_decimal_crash(self):
        exchange = TrailingExchange()
        current = state()
        current["protection_journal"]["trailing"]["acknowledged_order_id"] = ORDER_ID
        journal = make_journal(exchange, current, [])
        with self.assertRaisesRegex(ProtectionReconcilePending,
                                    "exact POST ACK evidence unavailable"):
            journal.place(
                "trailing", "ETH-USDT", "SELL", "0.02", 1923.57, "912",
                send=lambda: self.fail("Never duplicate accepted order"),
                callback_rate=0.005,
            )
        self.assertEqual(current["protection_journal"]["trailing"]
                         ["acknowledged_order_id"], ORDER_ID)

    def test_get_explicit_mismatch_never_overridden_by_post(self):
        exchange = TrailingExchange(activationPrice="1923.58")
        current = state()
        current["protection_journal"]["trailing"]["acknowledged_order_id"] = ORDER_ID
        current["protection_journal"]["trailing"]["acknowledged_parameters"] = (
            order_ack()["data"]["order"]
        )
        journal = make_journal(exchange, current, [])
        with self.assertRaisesRegex(ProtectionReconcilePending,
                                    "trigger price unverified"):
            journal.place(
                "trailing", "ETH-USDT", "SELL", "0.02", 1923.57, "912",
                send=lambda: self.fail("Never duplicate accepted order"),
                callback_rate=0.005,
            )

    def test_wrong_order_identity_remains_pending(self):
        exchange = TrailingExchange(orderID="9988")
        current = state()
        current["protection_journal"]["trailing"]["acknowledged_order_id"] = ORDER_ID
        current["protection_journal"]["trailing"]["acknowledged_parameters"] = (
            order_ack()["data"]["order"]
        )
        journal = make_journal(exchange, current, [])
        with self.assertRaises(ProtectionReconcilePending):
            journal.place(
                "trailing", "ETH-USDT", "SELL", "0.02", 1923.57, "912",
                send=lambda: self.fail("Never duplicate accepted order"),
                callback_rate=0.005,
            )


class RecoveryAfterEightAttempts(unittest.TestCase):
    def test_eight_failures_do_not_disable_automatic_recovery(self):
        event_id = "ETH-USDT|1h|C4|SELL|1794542399999"
        current = {
            "terminal": False,
            "intent": {"event_id": event_id, "symbol": "ETH-USDT", "combo": 4,
                       "side": "SELL", "timeframe": "1h",
                       "close_time": 1794542399999,
                       "entry": 1933.24, "tp": 1920.0, "sl": 1940.0,
                       "smc_dir": -1},
            "recovery": {"attempts": 8, "next_at": 0, "manual_required": True},
        }

        class Store:
            def list_states(self):
                return [(event_id, current)]

            def get(self, *args):
                return None

        service = ExecutionService.__new__(ExecutionService)
        service.settings = SimpleNamespace(execution_enabled=True, dry_run=False)
        service.credentials_verified = True
        service.store = Store()
        invocations = []
        def execute(intent, recovery_only=False):
            invocations.append((intent.event_id, recovery_only))
            return SimpleNamespace(status="recovery_pending", accepted=True)
        service.execute = execute

        recovered = service.recover_pending()
        self.assertEqual(invocations, [(event_id, True)])
        self.assertEqual(recovered[0]["status"], "recovery_pending")


    def test_unrelated_symbol_has_no_side_reconcile_blocker(self):
        event_id = "ETH-USDT|1h|C4|SELL|1794542399999"
        class Store:
            def list_states(self):
                return [(event_id, {
                    "intent": {"symbol": "ETH-USDT", "side": "SELL"},
                    "terminal": False, "safety_status": "RECONCILE_REQUIRED",
                })]

            def get(self, *args):
                return None

        class Client:
            def get_positions(self, symbol):
                self._check(symbol)
                return []

            def get_open_orders(self, symbol):
                self._check(symbol)
                return []

            @staticmethod
            def _check(symbol):
                assert symbol == "BTC-USDT"

        service = ExecutionService.__new__(ExecutionService)
        service.store = Store()
        service.executor = SimpleNamespace(client=Client())
        intent = SimpleNamespace(
            event_id="BTC-USDT|1h|C4|SELL|1794542399999",
            symbol="BTC-USDT", side="SELL",
        )
        baseline, report = service._entry_admission(intent)
        self.assertEqual(baseline, {})
        self.assertEqual(report["blocking_risks"], [])


if __name__ == "__main__":
    unittest.main()
