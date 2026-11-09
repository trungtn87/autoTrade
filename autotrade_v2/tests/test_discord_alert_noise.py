"""Discord incident grouping: audit and trade execution stay unchanged."""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from autotrade_v2.app.control.events import EventReporter, SystemEvent


def event(key, *, symbol="ETH-USDT", combo=105, event_id="evt-1", risks=None, attempt=None):
    details = {}
    if risks is not None:
        details["risks"] = risks
    if attempt is not None:
        details["attempt"] = attempt
        details["expected_open_time"] = 111
    return SystemEvent(
        event_key=key,
        layer=key.split(".", 1)[0],
        severity="WARNING" if key == "L1.DATA.CANDLE_STALE" else "CRITICAL",
        message="verified exposure/protection mismatch; entry not submitted",
        symbol=symbol,
        combo=combo,
        event_id=event_id,
        details=details,
    ).normalized()


class DiscordAlertNoiseTests(unittest.TestCase):
    def setUp(self):
        self.reporter = EventReporter(
            "", discord_enabled=True, webhook_error="https://example.invalid/error"
        )
        self.http = patch(
            "autotrade_v2.app.control.events.requests.post",
            return_value=SimpleNamespace(status_code=204, headers={}, text=""),
        )
        self.post = self.http.start()

    def tearDown(self):
        self.reporter.stop()
        self.http.stop()

    def test_first_missing_candle_does_not_spam_before_rest_recovery(self):
        key = "L1.DATA.CANDLE_STALE"
        self.reporter._notify(event(key, attempt=1))
        self.assertEqual(self.post.call_count, 0)
        self.reporter._notify(event(key, attempt=2))
        self.assertEqual(self.post.call_count, 1)
        self.reporter._notify(event(key, attempt=3, event_id="next-candle"))
        self.assertEqual(self.post.call_count, 1)
        self.reporter._last_discord[self.reporter._discord_dedupe_key(event(key, attempt=2))] = (
            time.monotonic() - 3601
        )
        self.reporter._notify(event(key, attempt=2))
        self.assertEqual(self.post.call_count, 2)

    def test_merged_incident_groups_all_blocked_signals_by_risk_set(self):
        key = "L3.EXEC.MERGED_RECONCILE_REQUIRED"
        risks = [
            "ETH-USDT|15m|C7|SELL|100:EXIT_HISTORY_UNVERIFIED",
            "ETH-USDT|15m|C7|SELL|100:NO_VERIFIED_ACTIVE_SL",
        ]
        first = event(key, event_id="first", combo=105, risks=risks)
        self.reporter._notify(first)
        self.reporter._notify(event(key, event_id="second", combo=106, risks=list(reversed(risks))))
        self.assertEqual(self.post.call_count, 1)
        summary = self.post.call_args.kwargs["json"]["content"]
        self.assertIn("Entry chưa được gửi", summary)
        self.assertIn("C15", summary)
        self.assertIn("NO_VERIFIED_ACTIVE_SL", summary)
        self.assertNotIn("ETH-USDT|15m|C7|SELL|100:", summary)

        changed = event(key, event_id="third", risks=risks + [
            "ETH-USDT|15m|C7|SELL|100:NO_VERIFIED_ACTIVE_TP"
        ])
        self.reporter._notify(changed)
        self.assertEqual(self.post.call_count, 2)
        self.reporter._last_discord[self.reporter._discord_dedupe_key(changed)] = (
            time.monotonic() - 3601
        )
        self.reporter._notify(changed)
        self.assertEqual(self.post.call_count, 3)

    def test_new_emergency_stays_immediate(self):
        key = "L3.EXEC.FORCE_CLOSE_UNRESOLVED"
        self.reporter._notify(event(key, event_id="emergency-1"))
        self.reporter._notify(event(key, event_id="emergency-2"))
        self.assertEqual(self.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
