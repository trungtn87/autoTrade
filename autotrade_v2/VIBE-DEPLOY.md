# Nhân Hòa Vibe Hosting — AutoTrade V2 runtime check

This is a **startup-command-only** configuration; there are no changes to strategy,
BingX execution, TP/SL, state store, or WebSocket logic.

## Settings for the test branch

- Repository: `https://github.com/trungtn87/autoTrade.git`
- Branch: `fix/vibe-v2-railpack-start-20261030`
- Build path: `/autotrade_v2` (do not point to the repo root)
- Build type: Railpack
- Railpack file: `autotrade_v2/railpack.json` (found as `railpack.json` at build root)
- Domain internal **Container Port**: `8000`
- Number of workers: exactly one

The config file pins the process to:
`python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`

Remove previous `RAILPACK_START_CMD` and `PORT` overrides if they conflict
with this pinned startup configuration.

## Required environment variables before enabling live trading

Reuse existing secrets **inside the hosting platform only** (do not commit or
print them). The application validates the settings at import time.

- `DATABASE_URL`: current Supabase PostgreSQL URL (required when WebSocket is on)
- `WEBSOCKET_ENABLED=true`
- `BOOTSTRAP_ENABLED=false`
- `EXECUTION_ENABLED=true` and `DRY_RUN=false` **only if this is the unique
  live engine** (do not enable simultaneously with another engine)
- `BINGX_API_KEY` and `BINGX_API_SECRET`: required for live execution
- `TP_TRAILING_SPLIT_ENABLED=true`
- `TP_TRAILING_ACTIVATION_PCT=0.005`
- `TP_TRAILING_CALLBACK_RATE=0.005`
- Other existing Discord/recovery flags must match the live service.

If required settings are missing, `validate_settings()` raises on app import
and the proxy may show 502 even though the image built successfully.

## Verify

1. Build successful does **not** mean application running.
2. Check application runtime/container logs for
   `Uvicorn running on http://0.0.0.0:8000` and `Application startup complete`.
   If absent, identify the runtime exception (not only the Railpack build log).
3. Browse `/status` to see runtime status. `/health` returns 503 when candle
   freshness/3400-candle requirements aren't met, which is different from 502.
4. Confirm Supabase, BingX WebSocket and credential preflight statuses before
   any production cutover.
5. Keep exactly one execution writer for a BingX account. Switch or disable
   the old live engine deliberately; do not assume a billing suspension prevents
   unexpected recovery of the old service.

This branch is for diagnosing deployment only. Do not deploy live without
verifying the full production configuration and anti-duplicate engine gate.
