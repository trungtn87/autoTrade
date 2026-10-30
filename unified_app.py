"""One-port deployment for hosting plans limited to one application.

Dashboard keeps its historical root URLs and PWA paths. Engine is mounted
under /engine. Both FastAPI lifespans are explicitly run exactly once; Starlette
does not automatically start mounted sub-app lifespans.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

# Supabase Dashboard analytics tables have always lived in this schema. The
# unified service eliminates the need to configure DB_SCHEMA manually, without
# changing the standalone dashboard or local SQLite test behavior.
if os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
    os.environ.setdefault("DB_SCHEMA", "autotrade_analytics")

# The original Live Terminal opens directly. Preserve that behavior in the
# single-service deployment without requiring another hosting Environment key.
# Public mode redacts raw order IDs, raw fills and collector diagnostics.
# Explicit DASHBOARD_PUBLIC_READONLY=false keeps the optional private login.
os.environ.setdefault("DASHBOARD_PUBLIC_READONLY", "true")

from fastapi import FastAPI

from autotrade_v2.app.main import app as engine_app
from dashboard.autotrade_live.mobile import app as dashboard_app


@asynccontextmanager
async def combined_lifespan(_app: FastAPI):
    # Mounted FastAPI applications' lifespan events are not called by Starlette
    # automatically. Both runtime loops are essential: V2 websocket/order
    # reconciliation and Dashboard's read-only BingX archive scheduler.
    async with engine_app.router.lifespan_context(engine_app):
        async with dashboard_app.router.lifespan_context(dashboard_app):
            yield


app = FastAPI(
    title="Autotrade Unified Engine + Live Terminal",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=combined_lifespan,
)
# Mount engine first; the Dashboard consumes / including /static, /login,
# /mobile and /api/dashboard without altering existing absolute PWA URLs.
app.mount("/engine", engine_app)
app.mount("/", dashboard_app)
