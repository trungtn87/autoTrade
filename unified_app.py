"""One-port deployment for hosting plans limited to one application.

Dashboard keeps its historical root URLs and PWA paths. The trading engine is
AutoTrade V3 (autotrade_v3.live, five layers) mounted under /engine. The V2
engine (autotrade_v2.app.main) is no longer imported, so V2 neither trades nor
sends alerts; its code stays in the repository as reference. Both FastAPI lifespans are explicitly run exactly once; Starlette
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

# The unified app has one configured BingX API pair. When no dedicated
# history-read pair exists, reuse that pair for the archive's GET-only client.
# Never combine half-configured read credentials with the trading pair.
if not os.getenv("BINGX_READ_API_KEY") and not os.getenv("BINGX_READ_API_SECRET"):
    os.environ["BINGX_READ_API_KEY"] = os.getenv("BINGX_API_KEY", "")
    os.environ["BINGX_READ_API_SECRET"] = os.getenv("BINGX_API_SECRET", "")

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from autotrade_v2.app.monitor_resources import container_resource_sampler

from autotrade_v2.app.ai.page import router as ai_router, STATIC as AI_STATIC
from autotrade_v3.live.app import app as engine_app
from dashboard.autotrade_live.mobile import app as dashboard_app


@asynccontextmanager
async def combined_lifespan(_app: FastAPI):
    # Mounted FastAPI applications' lifespan events are not called by Starlette
    # automatically. The V3 engine lifespan runs L1..L5 and the hourly :07
    # read-only BingX archive; Dashboard serves HTTP only.
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
# Mount engine first; the Dashboard consumes / including /static, /login,
# /mobile and /api/dashboard without altering existing absolute PWA URLs.
@app.get("/monitor/resources", include_in_schema=False)
def monitor_resources():
    """Read-only per-container capacity; no trade or host secrets."""
    return JSONResponse(container_resource_sampler.snapshot(),
                        headers={"Cache-Control": "no-store"})


app.include_router(ai_router)
app.mount("/ai/static", StaticFiles(directory=str(AI_STATIC)), name="ai-static")
app.mount("/engine", engine_app)
app.mount("/", dashboard_app)
