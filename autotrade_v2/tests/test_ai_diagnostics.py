"""Owner-only /ai diagnostics: no secrets, no engine, no automatic provider calls."""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai import diagnostics, page


class AiDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AI_ANALYST_PASSWORD": "password-for-owner-10",
            "DATABASE_URL": "postgresql://secret-user:secret-password@db.example.test/test",
            "GEMINI_API_KEY": "private-gemini-key-not-public",
            "GEMINI_MODEL": "gemini-3.8-flash",
            "AI_OBSERVE_ENABLED": "true",
            "DISCORD_ENABLED": "true",
            "DISCORD_WEBHOOK_BTC": "https://discord.com/api/webhooks/123456789/private_BTC_token",
            "DISCORD_WEBHOOK_ETH": "https://discord.com/api/webhooks/987654321/private_ETH_token",
        })
        self.env.start()
        page._login_failures.clear()
        diagnostics._NEXT_PROBE.clear()
        app = FastAPI()
        app.include_router(page.router)
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        diagnostics._NEXT_PROBE.clear()
        page._login_failures.clear()
        self.env.stop()

    def login(self):
        res = self.client.post("/ai/login", data={"password": "password-for-owner-10"})
        self.assertEqual(res.status_code, 303, res.text)
        return self.client.get("/ai/api/session").json()["csrf"]

    def test_private_diagnostics_do_not_call_gemini_or_discord_when_opened(self):
        self.assertEqual(self.client.get("/ai/api/diagnostics").status_code, 401)
        self.assertEqual(self.client.post("/ai/api/diagnostics/gemini").status_code, 401)
        self.assertEqual(self.client.post("/ai/api/diagnostics/discord/BTC").status_code, 401)
        self.login()
        with patch.object(page, "_db", side_effect=RuntimeError("DATABASE_URL=secret")), patch(
            "app.ai.diagnostics.requests.post"
        ) as network:
            res = self.client.get("/ai/api/diagnostics")
        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["database"]["code"], "DB_CONNECTION")
        self.assertTrue(payload["gemini"]["observer_enabled"])
        self.assertEqual(payload["gemini"]["status"], "configured")
        self.assertEqual(payload["discord"]["BTC"]["status"], "configured")
        self.assertEqual(payload["discord"]["ETH"]["status"], "configured")
        network.assert_not_called()
        for secret in ("secret-password", "private-gemini-key", "private_BTC_token", "private_ETH_token"):
            self.assertNotIn(secret, res.text)
        self.assertEqual(res.headers["cache-control"], "no-store, private")
        self.assertEqual(res.headers["x-robots-tag"], "noindex, nofollow, noarchive")

    def test_missing_db_url_classifies_without_leaking(self):
        self.login()
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            data = self.client.get("/ai/api/diagnostics").json()
        self.assertEqual(data["database"]["code"], "MISSING_DATABASE_URL")

    def test_real_table_probe_returns_counts_and_result_readiness(self):
        self.login()
        class Conn:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def execute(self, query):
                self.last_query = query
                return self
            def fetchone(self):
                if "to_regclass" in self.last_query:
                    return dict(reviews=True, sessions=True, messages=True, trade_results=False)
                return dict(reviews=7, chat_messages=4, post_reviews=0)
        with patch.object(page, "_db", return_value=Conn()):
            data = self.client.get("/ai/api/diagnostics").json()
        self.assertEqual(data["database"]["status"], "ok")
        self.assertEqual(data["database"]["reviews"], 7)
        self.assertEqual(data["database"]["chat_messages"], 4)
        self.assertFalse(data["database"]["post_trade_ready"])

    def test_gemini_requires_real_csrf_and_does_not_echo_provider_data(self):
        csrf = self.login()
        with patch("app.ai.diagnostics.requests.post") as post:
            response = self.client.post("/ai/api/diagnostics/gemini")
            self.assertEqual(response.status_code, 403)
            post.assert_not_called()
            post.return_value.status_code = 404
            post.return_value.text = "key=private-gemini-key-not-public"
            result = self.client.post("/ai/api/diagnostics/gemini", headers={"x-ai-csrf": csrf})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["code"], "MODEL_NOT_FOUND")
        self.assertEqual(result.json()["http_status"], 404)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["timeout"], diagnostics.DIAGNOSTIC_TIMEOUT_SECONDS)
        self.assertEqual(
            post.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "low"}
        )
        self.assertNotIn("private-gemini-key", result.text)
        # Throttled requests must not cost another Gemini call.
        with patch("app.ai.diagnostics.requests.post") as post:
            res = self.client.post("/ai/api/diagnostics/gemini", headers={"x-ai-csrf": csrf})
            self.assertEqual(res.json()["code"], "COOLDOWN")
            post.assert_not_called()

    def test_gemini_success_and_timeout_are_distinct(self):
        csrf = self.login()
        with patch("app.ai.diagnostics.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}
            success = self.client.post("/ai/api/diagnostics/gemini", headers={"x-ai-csrf": csrf})
        self.assertEqual(success.json()["status"], "ok")
        self.assertEqual(success.json()["code"], "GENERATE_OK")
        diagnostics._NEXT_PROBE.clear()
        with patch("app.ai.diagnostics.requests.post", side_effect=diagnostics.requests.Timeout):
            failed = self.client.post("/ai/api/diagnostics/gemini", headers={"x-ai-csrf": csrf})
        self.assertEqual(failed.json()["code"], "TIMEOUT")
        self.assertIn("25 giây", failed.json()["detail"])
        diagnostics._NEXT_PROBE.clear()
        with patch("app.ai.diagnostics.requests.post", side_effect=diagnostics.requests.ConnectTimeout):
            unreachable = self.client.post("/ai/api/diagnostics/gemini", headers={"x-ai-csrf": csrf})
        self.assertEqual(unreachable.json()["code"], "CONNECT_TIMEOUT")

    def test_discord_opt_in_uses_only_existing_symbol_webhooks(self):
        csrf = self.login()
        with patch("app.ai.diagnostics.requests.post") as post:
            post.return_value.status_code = 204
            for pair in ("BTC", "ETH"):
                res = self.client.post("/ai/api/diagnostics/discord/" + pair,
                                       headers={"x-ai-csrf": csrf})
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json()["code"], "DISCORD_DELIVERED")
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args_list[0].args[0], os.environ["DISCORD_WEBHOOK_BTC"])
            self.assertEqual(post.call_args_list[1].args[0], os.environ["DISCORD_WEBHOOK_ETH"])
            for entry in post.call_args_list:
                self.assertIn("KIỂM TRA", entry.kwargs["json"]["content"])
                self.assertIn("Không phải tín hiệu giao dịch", entry.kwargs["json"]["content"])
                self.assertEqual(entry.kwargs["json"]["allowed_mentions"], {"parse": []})
        # No second message sent within cooldown.
        with patch("app.ai.diagnostics.requests.post") as post:
            res = self.client.post("/ai/api/diagnostics/discord/BTC", headers={"x-ai-csrf": csrf})
            self.assertEqual(res.json()["code"], "COOLDOWN")
            post.assert_not_called()

    def test_rejects_arbitrary_webhook_url_and_missing_csrf(self):
        csrf = self.login()
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_BTC": "https://attacker.example.test/api/webhooks/123/abc"}):
            with patch("app.ai.diagnostics.requests.post") as post:
                res = self.client.post("/ai/api/diagnostics/discord/BTC", headers={"x-ai-csrf": csrf})
                self.assertEqual(res.json()["code"], "INVALID_WEBHOOK_URL")
                post.assert_not_called()
        with patch("app.ai.diagnostics.requests.post") as post:
            self.assertEqual(
                self.client.post("/ai/api/diagnostics/discord/BTC", headers={"x-ai-csrf": "bad"}).status_code,
                403
            )
            self.assertEqual(self.client.post("/ai/api/diagnostics/discord/OTHER", headers={"x-ai-csrf": csrf}).status_code, 404)
            post.assert_not_called()

    def test_discord_can_be_disabled_without_exposing_secret(self):
        self.login()
        with patch.dict(os.environ, {"DISCORD_ENABLED": "false"}):
            info = self.client.get("/ai/api/diagnostics").json()
            self.assertEqual(info["discord"]["BTC"]["code"], "DISCORD_DISABLED")
            self.assertEqual(info["discord"]["ETH"]["code"], "DISCORD_DISABLED")


if __name__ == "__main__":
    unittest.main()
