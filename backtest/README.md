# AutoTrade Python Research Engine

Python research/backtest environment for the BTC/ETH system used in `autoTrade`.

## Current model

- C1/C2/C3/C4/C6: native 1H
- C5/C7/C8/C9/C10: native 15m
- Tier: native 1H with H4 context
- 11 independent combo positions; opposite directions may coexist in the simulator
- C1/C3/C6: ATR TP/SL; all other combos + Tier: percentage TP/SL
- SMC research module: confirmed pivot -> BOS -> Order Block -> optional veto
- RAW and OB variants can be run from the same data/signals

## Environment

Python 3.12 is the reference runtime.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python selftest.py
```

Docker is also supported:

```bash
docker build -t autotrade-backtest .
docker run --rm autotrade-backtest
```

## Download BingX history

Market K-lines are public; API keys are not required for this research downloader.

```bash
python bootstrap_data.py \
  --symbols BTC-USDT,ETH-USDT \
  --intervals 15m,1h,4h,6h \
  --start 2021-01-01 \
  --out-dir data
```

`data/manifest.json` records actual first/last candle, row count, duplicates, and detected missing intervals. The downloader requests BingX in batches and locally caches the result, so repeated backtests do not need to redownload candles.

## Run RAW vs Order Block research

```bash
python research_pipeline.py \
  --symbol BTCUSDT \
  --data15 data/BTCUSDT_15m.pkl \
  --data1h data/BTCUSDT_1h.pkl \
  --data4h data/BTCUSDT_4h.pkl \
  --data6h data/BTCUSDT_6h.pkl \
  --danger 0.0,0.5 \
  --out-dir results/BTCUSDT
```

Repeat for ETH. The output includes summary tables, trade logs, and veto logs.

## GitHub Actions

`.github/workflows/python-research.yml` provides a reproducible cloud environment. It:

1. installs Python 3.12 and dependencies;
2. runs the synthetic self-test;
3. downloads BingX BTC/ETH 15m/1h/4h/6h candles;
4. validates data coverage/gaps;
5. runs RAW vs OB 0.0/0.5 ATR research for both symbols;
6. uploads market-data and result artifacts.

The workflow uses public market data only and needs no BingX secrets.

## Calibration requirement

Before trusting multi-year PnL, compare a known TradingView period trade-by-trade. Pine-specific details that may need calibration include EMA warmup, PSAR, daily VWAP anchor, higher-timeframe confirmation timing, and ambiguous same-candle TP/SL ordering.
