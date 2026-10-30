# AutoTrade — V2 engine + Live Dashboard

Two independent Python/FastAPI applications share this repository and the existing
Supabase PostgreSQL database. Building or starting one does not start the other.

| Service | Build root/context | ASGI application | Port | Runtime credentials |
| --- | --- | --- | --- | --- |
| V2 trading engine | `autotrade_v2` | `app.main:app` | `PORT`, fallback 8000 | Existing engine settings only |
| Live Terminal / PWA | `dashboard` | `autotrade_live.mobile:app` | `PORT`, fallback 8000 | Dashboard login and separate BingX GET-only key |

See [deployment settings, environment and startup commands](docs/DEPLOYMENT.md)
before creating either service. Environment must be configured **before the first
start**. No production deployment or database migration is part of this integration.

- [V2 architecture and strategy](autotrade_v2/README.md)
- [Dashboard tools and reporting](dashboard/README.md)
- [Integration evidence and source preservation](docs/INTEGRATION.md)

## Local offline verification

Use separate virtual environments. Install `autotrade_v2/requirements.txt` or
`dashboard/requirements.txt`, plus `pytest httpx socksio`, then run respectively:

```sh
python scripts/check-offline.py autotrade_v2
python scripts/check-offline.py dashboard
```

Tests sanitize production settings and block external sockets. The independent
build workflow also builds both Docker contexts and starts each image with no
network and execution disabled. Full-history signal parity has its own workflow
and requires the original RUN17 dataset artifact; it must not be replaced with an
old passing run or a synthetic dataset.

Root `bingx_server.py`, `requirements.txt` and `render.yaml` are retained legacy
files from main for compatibility, **not** the entrypoint of either new service.
Do not point a new service at the repository root or this legacy Render manifest.
