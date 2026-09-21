# Backtest Standard v1.1

Locked date: 2026-09-21

This document defines the canonical research backtest workflow. Exploratory runs may remain scratch-only. A configuration becomes a locked reference result only after its final run bundle contains the manifest, exact config, code revision, data checksums, environment and trade ledger required below.

## 1. Market data

- Symbols are evaluated separately: BTCUSDT and ETHUSDT.
- Source: BingX public perpetual-futures market K-lines only. No account API key, secret, balance, position or order endpoint is permitted.
- Canonical stored timeframe: 15m only.
- Timezone: UTC.
- Range convention: half-open [start, end).
- No forward fill and no synthetic replacement of missing 15m candles.
- Reject duplicate timestamps, gaps, out-of-range rows and invalid OHLCV.
- 1h, 4h and 6h research frames are derived deterministically from the canonical 15m dataset using epoch-aligned, left-closed, left-labelled aggregation.
- Every run records SHA-256 for the exact 15m source file used.

## 2. Execution model

Canonical account profile:

- initial_equity_usd: 1000
- margin_mode: isolated
- margin_per_trade_usd: 1
- leverage: 100
- notional_per_trade_usd: 100
- default execution fee: taker 0.05% of notional per side
- maker reference fee: 0.02% per side, used only by a test explicitly modelling maker fills

Every combo owns an independent position in the research engine. Different combos may hold opposite directions simultaneously.

Current isolated-margin risk model uses a bankruptcy proxy at approximately 1/leverage adverse price movement. It is NOT yet an exact BingX liquidation-price model. Every run must record this limitation in its manifest. Exact liquidation modelling is a future calibration item and must create a new standard version rather than silently changing v1.1.

## 3. Signal/execution timing

- Native 15m combos enter from confirmed 15m events.
- Native 1h combos use the last confirmed 1h event mapped to the final 15m candle of that hour, matching the current Python research implementation.
- Entry price is the selected 15m candle close.
- Existing positions are evaluated for exit before new entries on the same 15m row.
- A position opened on a candle is not allowed to hit TP/SL on that same candle in the current engine.
- Same-15m-candle TP/SL ambiguity defaults to stop_first unless a run explicitly declares another policy.

Any future change to these assumptions requires a new standard version.

## 4. Fees and PnL

Fees are charged on notional at entry and exit. Taker backtests use 0.05% per side.

For a $100 notional round trip with unchanged notional, fees are approximately $0.10 before funding/slippage.

Funding and slippage are not included in Standard v1.1 unless explicitly enabled and recorded in a run config.

## 5. Reproducibility

Every valid run must retain:

1. run_manifest.json
2. run_config.json
3. exact Git commit SHA
4. Python and dependency versions
5. canonical 15m dataset filename, coverage and SHA-256
6. strategy/signals source revision
7. summary CSV/JSON
8. complete closed-trade ledger CSV
9. veto ledger when applicable
10. equity curve
11. open positions at end of test
12. stdout/log reference when available

Run identity format:

YYYYMMDDTHHMMSSZ__SYMBOL__COMBO_OR_PORTFOLIO__SHORT_GIT_SHA

A run is never overwritten. A rerun with identical inputs receives its own run ID, while the manifest allows exact comparison.

## 6. Validation / optimisation discipline

- BTC and ETH are analysed separately.
- Train, validation and out-of-sample windows must be written to run_config.json.
- Entry logic, exit logic, OB veto and risk parameters are changed independently where possible.
- A candidate is not promoted from a short sample solely because it maximises full-period PnL.
- Parameter changes must be checked for neighbourhood stability and later re-run on the long-history 15m dataset.
- CORE/WATCH/OFF classification is a downstream decision and is not embedded in raw performance metrics.

## 7. Storage

GitHub research branch stores code and this standard.

Google Drive root: Trade Backtest

- 00_STANDARD: locked standards/config references
- 01_DATA_15M: canonical validated 15m datasets and data manifests
- 02_RUNS: immutable per-run bundles
- 03_REFERENCE: reference Pine sources and comparison material

For ChatGPT-orchestrated backtests, uploading the completed run bundle to Google Drive/02_RUNS is a mandatory final step.

A GitHub Actions run by itself cannot use the ChatGPT Google Drive connector. Fully autonomous GitHub-to-Drive upload requires separate Google credentials configured in GitHub Secrets; until that is deliberately configured, ChatGPT copies the completed artifact to Drive after each backtest it runs.
