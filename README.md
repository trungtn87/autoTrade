# AutoTrade — production baseline

**One GitHub branch (`main`) → one Docker container → one port (8000)**.
The single process runs the Binance? **No: BingX** V2 trading engine and the
mobile Live Terminal. It uses the existing Supabase database.

## Runtime URLs

| URL | Function |
| --- | --- |
| `/` or `/mobile` | Mobile Live Terminal, open directly (public read-only summary) |
| `/health` | Dashboard process health only |
| `/engine/status` | V2 runtime / last strategy execution |
| `/engine/health` | V2 candle/WS, execution, Discord, cleanup health |
| `/api/dashboard` | Redacted read-only Dashboard analytics |

The Dashboard is publicly viewable to anyone with its URL. It redacts raw fill
and order IDs; do not confuse read-only with private access. The Dashboard's
BingX credentials must be a *separate read-only key*.

## Build and deploy

Use `trungtn87/autoTrade` branch `main`, repository root `/`, root
`Dockerfile` (preferred) or root `railpack.json`, container port 8000.
Only one worker may run the engine. Root `requirements.txt` is self-contained
for Railpack. Both Docker and Railpack start:

```sh
python -m uvicorn unified_app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
```

All required configuration, safety checks and cutover steps:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
Functional snapshot and release acceptance:
[docs/BASELINE.md](docs/BASELINE.md).

The old Render/Flask server, Streamlit UI and two-service build configurations
were deliberately removed. Original commit history and relevant tests remain.
The code may **not** be deployed on multiple exchange-writing servers
simultaneously; there is no cross-host single-writer lease.

## Tests and history

- Production CI checks both engine and dashboard offline, single-container
  startup, mobile PWA and the same strategy parity against saved history.
- Dashboard's in-app history scheduler runs read-only BingX archival checks
  hourly at `:07`, with retry/backoff and PostgreSQL advisory locking.
- GitHub Actions manual history backfill is retained for exceptional use;
  **not** a second live collector schedule.
- Strategy files, execution journal, safe native TP/SL/trailing, conservative
  orphan-order cleanup, Discord notifications and Supabase schema SQL stay in
  source control.

This repository contains no live API keys or webhook URLs.
