# FINAL14 BingX Autotrade Engine

Production engine for BTC-USDT and ETH-USDT.

## Production topology

Only one Render service is part of the current project:

- `bingx-combo-smc-engine`
- Root directory: `signal_engine`
- Branch: `signal-engine-v1`

Legacy root-level BingX webhook/order service has been removed from the repository and must not be deployed.

## Data path

Each scheduled scan fetches one latest 15m window per symbol:

- `limit=1000`
- no `startTime` / `endTime`
- upsert into persistent candle state
- no automatic network gap-recovery request
- validation fails closed if data is incomplete

FINAL14 calculations use the persisted 3400-candle window.

## Render

Build command:

    pip install -r requirements.txt

Start command:

    uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1

Health check:

    /health

Required secrets/state:

- `BINGX_API_KEY`
- `BINGX_API_SECRET`
- `DATABASE_URL`
- Discord webhooks as needed

## Endpoints

- `GET /health`
- `GET /status`
- `POST /preview`
- `POST /scan`
- `GET /market-check`
- `GET /kline-check`

All manual market/scan endpoints are guarded by `SCAN_TOKEN`.
