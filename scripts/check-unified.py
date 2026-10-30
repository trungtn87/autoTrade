"""Offline verification for one-port deployment and both startup lifespans."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for key in (
    "DATABASE_URL", "BINGX_API_KEY", "BINGX_API_SECRET",
    "BINGX_READ_API_KEY", "BINGX_READ_API_SECRET",
    "DISCORD_WEBHOOK_BTC", "DISCORD_WEBHOOK_ETH", "DISCORD_WEBHOOK_ERROR",
    "DASHBOARD_PUBLIC_READONLY",
):
    assert not os.getenv(key), f"Offline suite must not receive production setting {key}"

from fastapi.testclient import TestClient
from unified_app import app

with TestClient(app, follow_redirects=False) as client:
    dash = client.get("/health")
    assert dash.status_code == 200, dash.text
    assert dash.json()["service"] == "autotrade-dashboard"
    assert dash.json()["trade_api"] is False

    # Restore original direct-open Live Terminal; no private login values.
    assert os.getenv("DASHBOARD_PUBLIC_READONLY") == "true"
    login = client.get("/login")
    assert login.status_code == 303, login.text
    assert login.headers["location"] == "/mobile"

    home = client.get("/")
    assert home.status_code == 303
    assert home.headers["location"] == "/mobile"

    mobile = client.get("/mobile")
    assert mobile.status_code == 200, mobile.text
    assert "Đăng xuất" not in mobile.text
    assert "LIVE TERMINAL" in mobile.text

    # The public JSON remains redacted and never requests trade privileges.
    from dashboard.autotrade_live import mobile as web
    previous_records = web._db_records
    web._db_records = lambda: {
        "orders": [], "fills": [], "links": [], "forces": [],
        "incomes": [], "runs": [], "positions": [],
    }
    try:
        summary = client.get("/api/dashboard")
        assert summary.status_code == 200, summary.text
        assert "trades" in summary.json()
    finally:
        web._db_records = previous_records

    engine = client.get("/engine/status")
    assert engine.status_code == 200, engine.text
    assert engine.json()["runtime"]["execution_enabled"] is False

    candle = client.get("/engine/health")
    assert candle.status_code in (200, 503), candle.text
    assert candle.json()["engine"] == "autotrade-v2"

    static = client.get("/static/manifest.webmanifest")
    assert static.status_code == 200, static.text

print("PASS: one public port, direct PWA Dashboard, redacted read-only API, Engine lifespan/status")
