"""No-network, no-DB tests for L5 startup wiring."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from app.control.l5_trade import L5TradeAlerts
from app.control.l5_wiring import PsycopgControlDb, build_l5, discord_post


WEBHOOK_KEYS = ("DISCORD_WEBHOOK_BTC", "DISCORD_WEBHOOK_ETH", "DISCORD_WEBHOOK_ERROR")


class L5WiringTest(unittest.TestCase):
    def test_build_l5_without_database_url_returns_none(self):
        settings = SimpleNamespace(database_url="")
        self.assertIsNone(build_l5(settings))

    def test_build_l5_with_database_url_returns_l5_adapter(self):
        settings = SimpleNamespace(database_url="postgresql://x")
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in WEBHOOK_KEYS:
                os.environ.pop(key, None)
            result = build_l5(settings)
        self.assertIsInstance(result, L5TradeAlerts)

    def test_discord_post_error_does_not_leak_url(self):
        url = "https://discord.com/api/webhooks/SECRET"
        with mock.patch("requests.post",
                        side_effect=requests.ConnectionError("boom " + url)):
            with self.assertRaises(RuntimeError) as ctx:
                discord_post(url, "content")
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_psycopg_control_db_construction_does_not_connect(self):
        with mock.patch("psycopg.connect") as connect:
            db = PsycopgControlDb("postgresql://bogus:bogus@127.0.0.1:1/none")
        connect.assert_not_called()
        self.assertEqual(db.database_url, "postgresql://bogus:bogus@127.0.0.1:1/none")


if __name__ == "__main__":
    unittest.main()
