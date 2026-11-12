"""Regression tests for 2026-11-11 BingX SSL startup timeout incident.

All exchange operations are replaced by local doubles. Never place a live order.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from autotrade_v2.app.config import Settings
from autotrade_v2.app.contracts import ExecutionResult, TradeIntent
from autotrade_v2.app.control.events import EventReporter, SystemEvent
from autotrade_v2.app.execution.service import ExecutionService


class EmptyStore:
    def list_states(self):
        return []

    def get_state(self, event_id):
        return None

    def get(self, key, target):
        return getattr(self, "emergency", None)

    @contextmanager
    def trading_lock(self):
        yield True


class SequenceClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0

    def query_balance(self):
        answer = self.answers[self.calls]
        self.calls += 1
        if isinstance(answer, BaseException):
            raise answer
        return answer


BALANCE = {"code": 0, "data": [{"asset": "USDT"}]}


def service_with(answers):
    settings = replace(
        Settings(), execution_enabled=True, dry_run=False,
        bingx_api_key="unit-test-key", bingx_api_secret="unit-test-secret",
    )
    service = ExecutionService(settings, EmptyStore())
    client = SequenceClient(answers)
    service.executor.client = client
    return service, client


def intent():
    return TradeIntent(
        event_id="ETH-USDT|1h|C4|SELL|1794484799999",
        symbol="ETH-USDT", combo=4, side="SELL", timeframe="1h",
        close_time=1794484799999, entry=2021.84,
        tp=1981.4, sl=2038.01, smc_dir=-1,
    )


class PreflightRecoveryTests(unittest.TestCase):
    def test_ssl_timeout_then_success_without_restart(self):
        service, client = service_with([
            RuntimeError("ConnectTimeout: SSL handshake operation timed out"),
            BALANCE,
        ])
        first = service.preflight_and_recover()
        self.assertFalse(first["ok"])
        self.assertFalse(service.credentials_verified)
        blocked = service.execute(intent())
        self.assertEqual(blocked.status, "credential_preflight_required")
        self.assertFalse(blocked.details["network_called"])

        recovered = service.preflight_and_recover()
        self.assertTrue(recovered["ok"])
        self.assertTrue(service.credentials_verified)
        self.assertEqual(recovered["recovery"], [])
        self.assertEqual(client.calls, 2)

    def test_recovery_exception_keeps_new_entries_blocked(self):
        service, client = service_with([BALANCE])
        service.recover_pending = Mock(side_effect=[
            RuntimeError("incomplete reconciliation"), [],
        ])
        first = service.preflight_and_recover()
        self.assertFalse(first["ok"])
        self.assertEqual(first["phase"], "recovery")
        self.assertTrue(service.credentials_verified)
        self.assertFalse(service.entry_preflight_ready)
        self.assertEqual(service.execute(intent()).status,
                         "preflight_recovery_pending")
        second = service.preflight_and_recover()
        self.assertTrue(second["ok"])
        self.assertEqual(service.recover_pending.call_count, 2)
        self.assertTrue(service.entry_preflight_ready)
        self.assertEqual(client.calls, 1, "recovery-only retries must not recheck an already verified API")

    def test_existing_emergency_remains_allowed_during_recovery_failure(self):
        service, _ = service_with([BALANCE])
        service.credentials_verified = True
        service.entry_recovery_pending = True
        service.preflight_error = "execution recovery failed"
        service.store.emergency = {
            "phase": "in_progress",
            "initiator": intent().event_id,
            "failed_kind": "sl",
            "epoch": "test-epoch",
        }
        with patch("autotrade_v2.app.execution.service.ForceClose") as close:
            close.return_value.run.return_value = {"stage": "force_close_confirmed"}
            result = service.execute(intent())
        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "force_close_confirmed")
        self.assertEqual(close.return_value.run.call_count, 1)

    def test_fresh_entry_cannot_overtake_pending_recovery(self):
        service, _ = service_with([BALANCE])
        entered = threading.Event()
        release = threading.Event()
        entry_called = threading.Event()
        finish = []
        def slow_recovery():
            entered.set()
            if not release.wait(4.0):
                raise TimeoutError("test release timed out")
            return []
        service.recover_pending = slow_recovery
        service._execute_unlocked = Mock(side_effect=lambda trade, recovery_only=False: (
            entry_called.set() or ExecutionResult(
                event_id=trade.event_id, accepted=False,
                status="test_only_no_order", details={"network_called": False},
            )
        ))
        recovery_thread = threading.Thread(
            target=lambda: finish.append(service.preflight_and_recover()),
            daemon=True,
        )
        entry_thread = threading.Thread(
            target=lambda: service.execute(intent()),
            daemon=True,
        )
        recovery_thread.start()
        try:
            self.assertTrue(entered.wait(2.0))
            entry_thread.start()
            self.assertFalse(entry_called.wait(0.10),
                             "new Entry must wait for reconciliation lock")
        finally:
            release.set()
            recovery_thread.join(3.0)
            if entry_thread.ident is not None:
                entry_thread.join(3.0)
        self.assertFalse(recovery_thread.is_alive())
        self.assertFalse(entry_thread.is_alive())
        self.assertTrue(finish[0]["ok"])
        self.assertTrue(entry_called.is_set())


class PreflightDiscordRoutingTests(unittest.TestCase):
    def setUp(self):
        self.reporter = EventReporter(
            "", discord_enabled=True,
            webhook_eth="https://example.invalid/eth",
            webhook_btc="https://example.invalid/btc",
            webhook_error="https://example.invalid/system",
        )
        self.patch_http = patch(
            "autotrade_v2.app.control.events.requests.post",
            return_value=SimpleNamespace(status_code=204, headers={}, text=""),
        )
        self.post = self.patch_http.start()

    def tearDown(self):
        self.reporter.stop()
        self.patch_http.stop()

    @staticmethod
    def event(key, event_id="signal-1", symbol="ETH-USDT", severity="CRITICAL",
              details=None):
        return SystemEvent(
            event_key=key, layer="L3", severity=severity,
            message="BingX credentials were not verified; order blocked",
            event_id=event_id, symbol=symbol, combo=4,
            details=details or {},
        ).normalized()

    def test_preflight_block_goes_to_system_once_for_all_combos(self):
        key = "L3.EXEC.PREFLIGHT_BLOCK"
        error = "ConnectTimeout: SSL handshake operation timed out"
        self.reporter._notify(self.event(key, details={"error": error}))
        self.reporter._notify(self.event(
            key, event_id="different-signal", symbol="BTC-USDT",
            details={"error": error},
        ))
        self.assertEqual(self.post.call_count, 1)
        url = self.post.call_args.args[0]
        body = self.post.call_args.kwargs["json"]["content"]
        self.assertEqual(url, "https://example.invalid/system")
        self.assertIn("quá thời gian", body)
        self.assertNotIn("Đặt lệnh thành công", body)

    def test_persistent_incident_retries_system_webhook_without_spam(self):
        key = "L3.EXEC.PREFLIGHT_STILL_BLOCKED"
        self.reporter._notify(self.event(
            key, event_id="retry-1",
            details={"attempt": 1, "error": "SSL handshake timed out"},
        ))
        self.reporter._notify(self.event(
            key, event_id="retry-3", symbol="BTC-USDT",
            details={"attempt": 3, "error": "SSL handshake timed out"},
        ))
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.post.call_args.args[0],
                         "https://example.invalid/system")
        self.assertIn("quá thời gian",
                      self.post.call_args.kwargs["json"]["content"])

    def test_restored_notice_to_system_and_order_result_stays_trade(self):
        self.reporter._notify(self.event(
            "L3.EXEC.PREFLIGHT_RECOVERED", severity="WARNING",
            details={"attempts": 2},
        ))
        self.assertEqual(self.post.call_args.args[0],
                         "https://example.invalid/system")
        self.assertIn("KHÔI PHỤC", self.post.call_args.kwargs["json"]["content"])
        self.reporter._notify(self.event(
            "L3.EXEC.ORDER_COMPLETE",
            event_id="new-valid-signal",
            details={"side": "SELL", "entry": 100, "tp": 99, "sl": 102},
        ))
        self.assertEqual(self.post.call_args.args[0],
                         "https://example.invalid/eth")


if __name__ == "__main__":
    unittest.main()
