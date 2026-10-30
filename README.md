# AutoTrade — one service from main

The default deployment runs **Autotrade V2 and Live Dashboard together** in one
hosting application, one Python/Uvicorn worker and one public port. The existing
standalone app packages are kept intact.

## Deploy on a host that allows only ONE application

- Git repository: `trungtn87/autoTrade`, branch `main`
- Build/root directory: `/` (repository root)
- Builder: root `Dockerfile` (recommended), or `Railpack` using root
  `railpack.json`
- Start (automatically configured):
  `python -m uvicorn unified_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1`
- Container/domain port: `8000` unless host explicitly supplies `PORT`

| Path | Service |
| --- | --- |
| `/`, `/mobile` | Live Dashboard (direct PWA access, no login) |
| `/health` | Dashboard process health |
| `/api/dashboard` | Authenticated Dashboard data |
| `/engine/status` | Autotrade V2 runtime state |
| `/engine/health` | Engine candle/WS readiness (503 when candles stale) |

The old root `bingx_server.py` is retained for history, but is **not** the
default start command. The root `requirements.txt` now installs both apps; the
root `railpack.json` selects the combined entrypoint explicitly. Do not select
`bingx_server.py` as startup.

Read [the Environment requirements and cutover checklist](docs/DEPLOYMENT.md)
before adding BingX trading credentials. Do not run this new Engine alongside
an old Engine with the same account credentials. Combined service and rolling
releases can overlap during deploy; take the old writer offline before cutover.
Unified Dashboard defaults to public read-only access, redacting raw order IDs,
fills and diagnostic details. Anyone with its URL can read summaries and PnL.
Private login remains available only if expressly configured, but is not
required for the original Dashboard behavior.

A single process means there is no strong runtime secret isolation between
Engine and Dashboard: the Dashboard's BingX client still uses separate GET-only
keys, and its endpoints do not provide order-writing functionality.

## Independently deployed services (optional)

If hosting supports two separate services, the original layouts still work:
`/autotrade_v2` runs `app.main:app`; `/dashboard` runs
`autotrade_live.mobile:app`. **These are alternatives**, not additional
deployments required by the unified approach.

## Offline verification

```sh
python -m pip install -r requirements.txt httpx socksio pytest
python scripts/check-unified.py
python scripts/check-offline.py autotrade_v2
python scripts/check-offline.py dashboard
docker build -t autotrade-unified .
```

All live exchange requests must be disabled by absence of production credentials
in local/CI tests. The GitHub CI also checks both original services independently.
No merge, production deploy or credential change follows from these docs alone.
