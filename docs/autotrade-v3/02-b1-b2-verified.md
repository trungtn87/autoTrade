# AutoTrade V3-02 — Verified B1 and B2, 2026-11-10

## 1. Evidence from real datasets

The V3-02 replay has been executed locally on the user's 74 verified M1 Parquet files (2023-11-01 to 2026-11-01 UTC, BTC+ETH, 3,156,480 M1 rows).

- 6,752 raw signal events; 6,199 layer2-approved events; 5,984 approved events after the 3,400-M15 warmup.
- BTC 2,704 approved; ETH 3,495 approved.
- Counts are **strategy candidates**, NOT independent trades/entries or wins. NEW6 live StrategyEngine also models a hard-TP/SL single-position state, which is **not** represented by the raw/approved batch count. Simulator V3-03 will estimate executable entries and conflicts.
- Cross-venue source at signal closing bar: 6,198 Binance; 1 BingX. That does not imply the entire indicator lookback only contains Binance bars.

See /Autotrade/AutoTrade_V3_Replay_20FINAL_2023_2026.zip in the user's ChatGPT Library for original event CSV/report.

## 2. B1 same-input EXACT parity — PASS

Reused the canonical RUN17 M15 DataFrames already loaded by the existing GitHub parity CI and exported **safe CSV** without any new pickle decoder in V3 code. Replayed 200,544 M15 bars per symbol with the exact V3 batch runner.

- BTC: 11/11 combo raw+passed counts and SHA-256 digests match frozen RUN17 reference exactly.
- ETH: 9/9 combo raw+passed counts and SHA-256 digests match frozen RUN17 reference exactly.
- Across all 20 combo-symbol cases: 0 mismatches for all four parity fields (raw count/hash, passed count/hash).
- Safe CSV ZIP SHA-256: 9acea0dcd333c85d534a75df18b98a3f1f458d4a4d642ef5ca4ef307e5cd123c, stored in Library /Autotrade/AutoTrade_V3_RUN17_M15_Safe_CSV.zip.
- The frozen reference SHA-256 file is tracked at autotrade_v3/data_backtest/run17_reference.json.

Reproduce on local:
  python -m autotrade_v3.data_backtest.verify_run17 --btc BTCUSDT_RUN17_M15.csv --eth ETHUSDT_RUN17_M15.csv --output v3_b1_result.json

This is exact **research-signal parity**, not a proof of accurate live order execution.

## 3. B2 cross-venue sensitivity — MEASURED, not forced to 100%

Compare accepted event identity (symbol, combo, BUY/SELL, exact 15m native close timestamp), matched periods and clear source boundary:

| Window UTC | RUN17 approved | M1-derived approved | Both | RUN17-only | M1-only | Jaccard |
|---|---:|---:|---:|---:|---:|---:|
| Jan 2024–Mar 2026, both Binance futures | 4,692 | 4,692 | 4,692 | 0 | 0 | **100.000%** |
| Apr–Sep 2026, RUN17 BingX vs M1 Binance | 973 | 1,001 | 898 | 75 | 103 | **83.457%** |
| Full common window Jan 2024–Sep 2026 | 5,665 | 5,693 | 5,590 | 75 | 103 | **96.914%** |

Both series were derived using exactly the same 20FINAL code and native time-close semantics. Difference on cross-venue interval should **not** be labeled strategy regression. It highlights the importance of separate venue/provenance tags and sensitivity reports. Comparison excludes the early M1 warmup/early start difference by beginning 2024-01-01.

Reproduce locally from event CSV:
  python -m autotrade_v3.data_backtest.compare_sources --run17-btc BTCUSDT_run17_events.csv --run17-eth ETHUSDT_run17_events.csv --hybrid-events signal_events.csv --out v3_b2_result.json

## 4. Safety / limitations

All processing local-first. No new network, order authorization, production DB mutation or live engine code change. These measurements are not exit fill/PnL verification (Gate C). Candidate frequency is not a statistically independent trade sample, particularly for ETH C10.

**Gate B1 = PASS; Gate B2 = REPORTED. The next step is V3-03 realistic M1 execution simulation, with uncertain TP/SL intraminute handled as bounds.**
