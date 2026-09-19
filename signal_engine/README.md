# BingX Signal Engine v1

Signal engine for BTC-USDT and ETH-USDT perpetual data from BingX.

## Safe validation mode

Keep these settings while validating:

- DRY_RUN=true
- AUTO_SCHEDULER=false
- ORDER_WEBHOOK_1 and ORDER_WEBHOOK_2 blank

The engine will read public BingX market data and calculate the 10 Combo + SMC signals, but it will not place an order.

## Render deployment

Use repository branch: signal-engine-v1

Set Root Directory:

    signal_engine

Build Command:

    pip install -r requirements.txt

Start Command:

    uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1

Health Check Path:

    /health

Then copy the values from .env.example into Render Environment.

## Endpoints

- GET /health
- GET /status
- POST /preview
- POST /scan

First call /health, then POST /preview from /docs.

Do not set DRY_RUN=false until the generated signals have been compared with TradingView/BingX and the persistent idempotency state has been addressed.
