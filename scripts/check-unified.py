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

    # The V2 AI page was removed from the unified app: no route, no static mount.
    assert client.get("/ai").status_code == 404
    assert client.get("/ai/api/state").status_code == 404
    assert client.get("/ai/static/app.js").status_code == 404

    mobile = client.get("/mobile")
    assert mobile.status_code == 200, mobile.text
    assert "Đăng xuất" not in mobile.text
    assert "LIVE TERMINAL" in mobile.text
    assert 'Chỉ xem' in mobile.text


    # The public JSON is read-only and redacted: no event IDs, order IDs or collector details.
    import autotrade_v3.dashboard.app as live_terminal
    previous_load, previous_engine = live_terminal.load_snapshot, live_terminal._get_engine
    live_terminal.load_snapshot = lambda engine: {
        "now_ms": 1_700_000_000_000, "orders": [], "results": [], "finances": {},
        "signals": [], "errors": [], "candle_last_close": {},
    }
    live_terminal._get_engine = lambda: None
    try:
        summary = client.get("/api/v3/dashboard")
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["public"] is True
        assert "trades" in body and "by_combo" in body and "health" in body
        assert "event_id" not in summary.text
    finally:
        live_terminal.load_snapshot, live_terminal._get_engine = previous_load, previous_engine

    # The V2 resource monitor was removed from the unified app.
    assert client.get("/monitor/resources").status_code == 404

    engine = client.get("/engine/status")
    assert engine.status_code == 200, engine.text
    assert engine.json()["engine"] == "autotrade-v3"
    assert engine.json()["runtime"]["execution_enabled"] is False
    assert engine.json()["legacy_v2_trading"] is False

    candle = client.get("/engine/health")
    assert candle.status_code in (200, 503), candle.text
    assert candle.json()["engine"] == "autotrade-v3"

    # The V2 engine is not imported, so it can neither trade nor alert.
    assert "autotrade_v2.app.main" not in sys.modules

    static = client.get("/static/manifest.webmanifest")
    assert static.status_code == 200, static.text
    worker = client.get("/sw.js")
    assert worker.status_code == 200, worker.text
    assert worker.headers["service-worker-allowed"] == "/"
    assert "serviceWorker" not in worker.text or "fetch" in worker.text

print("PASS: one public port, direct PWA Dashboard, redacted read-only API, V3 Engine lifespan/status")
