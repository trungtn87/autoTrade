"""AI Analyst private authentication, CSRF, and no-trading regression tests."""
from __future__ import annotations
import os
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.ai import page


class AnalystPageTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AI_ANALYST_PASSWORD": "StrongTestPassword123!",
            "GEMINI_API_KEY": "fake-offline-key",
            "AI_CHAT_DAILY_MAX_CALLS": "30",
        })
        self.env.start()
        page._login_failures.clear()
        app = FastAPI()
        app.include_router(page.router)
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()
        page._login_failures.clear()

    def login(self):
        response = self.client.post("/ai/login", data={"password": "StrongTestPassword123!"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/ai")
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("secure", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        return response

    def test_password_env_required_and_public_dashboard_cookie_is_not_enough(self):
        self.assertEqual(self.client.get("/ai").status_code, 200)
        self.assertEqual(self.client.get("/ai/api/state").status_code, 401)
        with patch.dict(os.environ, {"AI_ANALYST_PASSWORD": ""}):
            self.assertEqual(self.client.get("/ai").status_code, 503)
            self.assertEqual(self.client.get("/ai/api/state").status_code, 503)

    def test_password_minimum_exactly_ten_characters(self):
        with patch.dict(os.environ, {"AI_ANALYST_PASSWORD": "abcdefghi"}):
            self.assertEqual(self.client.get("/ai").status_code, 503)
            self.assertEqual(self.client.post("/ai/login", data={"password": "abcdefghi"}).status_code, 503)
        with patch.dict(os.environ, {"AI_ANALYST_PASSWORD": "abcdefghij"}):
            response = self.client.get("/ai")
            self.assertEqual(response.status_code, 200)
            self.assertIn('minlength="10"', response.text)
            login = self.client.post("/ai/login", data={"password": "abcdefghij"})
            self.assertEqual(login.status_code, 303)
            self.assertEqual(self.client.get("/ai").status_code, 200)

    def test_wrong_password_rate_limits(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/ai/login", data={"password": "wrong"}).status_code, 401)
        self.assertEqual(
            self.client.post("/ai/login", data={"password": "StrongTestPassword123!"}).status_code,
            429
        )

    def test_login_requires_csrf_for_chat(self):
        self.login()
        page_html = self.client.get("/ai")
        self.assertEqual(page_html.status_code, 200)
        self.assertIn("AutoTrade AI Analyst", page_html.text)
        self.assertEqual(
            self.client.post("/ai/api/chat", json={
                "message": "Why was C10 blocked?", "request_id": str(uuid4())
            }).status_code, 403
        )

    def test_api_history_stays_private(self):
        self.assertEqual(self.client.get("/ai/api/thread/" + str(uuid4())).status_code, 401)
        self.login()
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.return_value = {"ready": False}
        with patch.object(page, "_db", return_value=db), patch.object(
            page, "_reviews", return_value=[]
        ), patch.object(page, "_sessions", return_value=[]), patch.object(
            page, "_summary", return_value=[]
        ):
            res = self.client.get("/ai/api/state")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["observe_only"])
        self.assertFalse(res.json()["post_ready"])
        self.assertTrue(res.json()["csrf"])
        self.assertNotIn(os.environ["GEMINI_API_KEY"], res.text)

    def test_chat_only_reads_grounded_context_and_persists_a_response(self):
        self.login()
        sid = uuid4()
        db = MagicMock()
        db.__enter__.return_value.execute.return_value.fetchone.return_value = {"ready": False}
        with patch.object(page, "_db", return_value=db), patch.object(
            page, "_reviews", return_value=[]
        ), patch.object(page, "_sessions", return_value=[]), patch.object(
            page, "_summary", return_value=[]
        ):
            csrf = self.client.get("/ai/api/state").json()["csrf"]
        with patch.object(page, "_db", return_value=db), patch.object(
            page, "_reserve_message", return_value=(sid, None, False)
        ) as reserve, patch.object(
            page, "_chat_context", return_value=(
                [{"role": "user", "content": "Check BOS on BTC"}],
                {"recent_reviews": [], "verified_post_rule": "No source = unknown"},
            )
        ), patch.object(page, "_gemini_reply", return_value="Chưa có bằng chứng") as ask:
            res = self.client.post("/ai/api/chat", headers={"X-AI-CSRF": csrf}, json={
                "message": "Check BOS on BTC", "request_id": str(uuid4())
            })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["observe_only"])
        self.assertEqual(res.json()["reply"], "Chưa có bằng chứng")
        reserve.assert_called_once()
        ask.assert_called_once()

    def test_gemini_uses_only_given_source_and_no_bingx(self):
        context = {"selected_review": None, "recent_reviews": [],
                   "combo_statistics": [], "verified_post_rule": "Only confirmed CLOSED"}
        response = MagicMock()
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Chưa có dữ liệu."}]}}]
        }
        with patch("app.ai.page.requests.post", return_value=response) as send:
            result = page._gemini_reply(
                [{"role": "user", "content": "Lệnh gần nhất?"}], context
            )
        self.assertEqual(result, "Chưa có dữ liệu.")
        instruction = send.call_args.kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        self.assertIn("Không bịa lệnh", instruction)
        self.assertTrue(send.call_args.args[0].startswith("https://generativelanguage.googleapis.com/"))

    def test_password_rotation_revokes_old_session(self):
        self.login()
        with patch.dict(os.environ, {"AI_ANALYST_PASSWORD": "SomeOtherStrongPassword2026"}):
            self.assertEqual(self.client.get("/ai/api/state").status_code, 401)


if __name__ == "__main__":
    unittest.main()
