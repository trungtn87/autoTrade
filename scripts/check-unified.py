"""Offline verification for one-port deployment and both startup lifespans."""
from __future__ import annotations

import os

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

    login = client.get("/login")
    assert login.status_code == 200, login.text
    assert "AUTOTRADE" in login.text

    home = client.get("/")
    assert home.status_code == 303
    assert home.headers["location"] == "/login"

    mobile = client.get("/mobile")
    assert mobile.status_code == 303
    assert mobile.headers["location"] == "/login"

    protected = client.get("/api/dashboard")
    assert protected.status_code == 401, protected.text

    engine = client.get("/engine/status")
    assert engine.status_code == 200, engine.text
    assert engine.json()["runtime"]["execution_enabled"] is False

    candle = client.get("/engine/health")
    assert candle.status_code in (200, 503), candle.text
    assert candle.json()["engine"] == "autotrade-v2"

    static = client.get("/static/manifest.webmanifest")
    assert static.status_code == 200, static.text

print("PASS: one public port, login/PWA, protected Dashboard API, live Engine lifespan/status")
