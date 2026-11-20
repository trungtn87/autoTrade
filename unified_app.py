"""One-port deployment for hosting plans limited to one application.

The Live Terminal (autotrade_v3.dashboard, read-only over the autotrade_v3 tables) keeps the
historical root URLs and PWA paths. The trading engine is AutoTrade V3 (autotrade_v3.live, five layers)
mounted under /engine. The V2 engine and the old V2-backed dashboard are no longer imported. Both FastAPI
lifespans are run exactly once; Starlette does not start mounted sub-app lifespans automatically.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

# The original Live Terminal opens directly. Preserve that behavior in the
# single-service deployment without requiring another hosting Environment key.
# Public mode redacts raw order IDs, raw fills and collector diagnostics.
# Explicit DASHBOARD_PUBLIC_READONLY=false keeps the optional private login.
os.environ.setdefault("DASHBOARD_PUBLIC_READONLY", "true")

from fastapi import FastAPI
from autotrade_v3.live.app import app as engine_app
from autotrade_v3.dashboard.app import app as dashboard_app


@asynccontextmanager
async def combined_lifespan(_app: FastAPI):
    # Mounted FastAPI applications' lifespan events are not called by Starlette
    # automatically. The V3 engine lifespan runs L1..L5; the Live Terminal serves HTTP only.
    async with engine_app.router.lifespan_context(engine_app):
        async with dashboard_app.router.lifespan_context(dashboard_app):
            yield


app = FastAPI(
    title="Autotrade Unified Engine V3 + Live Terminal",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=combined_lifespan,
)
# Mount engine first; the Live Terminal serves / including /static, /login,
# /mobile and /api/v3/dashboard, keeping the PWA URLs.
app.mount("/engine", engine_app)
app.mount("/", dashboard_app)
