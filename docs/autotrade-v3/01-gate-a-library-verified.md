# AutoTrade V3-01 — Gate A evidence from Library, 10/11/2026

**Read-only examination of the user's actual ChatGPT Library files**, not inference from a conversation or synthetic tests. Source: four large BTC/ETH ZIPs + final_manifest v4 + BingX supplement + backup-verification ZIP.

## Exact archive checks
- All **4 original outer ZIPs**: historical SHA256 matches and ZIP CRC passes.
- **Final manifest v4** SHA256 matches its published `final_manifest.sha256`.
- **72/72 Binance Parquet** SHA256 equals final manifest v4, and Thrift/Parquet footer row counts equal manifest; **72/72 original Binance monthly ZIP payloads** SHA256 equals checkpoint.
- **2/2 BingX supplement Parquet** SHA256 equals supplement_manifest v2, each file has 1,440 rows.
- Backup report in Library has four reference restored partitions PASS (2 per symbol); not re-restored from remote cloud in this audit.

## Independent full DuckDB M1 scan (3,156,480 rows)
| Instrument | Binance USD-M last | BingX USDT perpetual last | Hybrid unique minutes | Missing | Duplicates | Bad timestamp / OHLCV |
|---|---:|---:|---:|---:|---:|---|
| BTCUSDT | 1,576,800 | 1,440 | 1,578,240 | 0 | 0 | 0 / 0 |
| ETHUSDT | 1,576,800 | 1,440 | 1,578,240 | 0 | 0 | 0 / 0 |

Window = **2023-11-01T00:00:00Z** inclusive to **2026-11-01T00:00:00Z** exclusive. Min/max UTC, 1-minute alignment, full continuity, positive finite OHLC, valid high/low, nonnegative volume and venue tags checked directly across all rows.

Zero-volume candles: 225 Binance BTC, 130 Binance ETH; flagged for observation, **not treated as invalid by default**. Both BingX supplements have 0 zero-volume rows.

## Gate decision
- **A-HYBRID = PASS** for historical backtest/replay research.
- **A-PROVENANCE = PASS** for recorded venue, contract and LAST price basis.
- **A-BINANCE-SOURCE = INCOMPLETE**: October 2026 had 43,200 / 44,640 Binance candles per instrument (96.774%); 1,440 BingX candles from 2026-10-28 make hybrid complete. All other 70 source-month partitions PASS.
- **BINGX-EXECUTION PARITY = NOT EVALUATED**: M1 candles alone cannot establish exact exchange triggers or fill order.
- November 2026 data **not collected**, outside scope.

## Reproducing
The raw ZIPs remain in the owner's ChatGPT Library; do not commit candle data to Git. The audit exported verified `v3_source_map_verified.json` and `v3_checksums_verified.json` for 74 Parquet files, and two reports (integrity + DuckDB M1 QA). The report pack was also supplied directly to the owner in the conversation. The M1 importer in this PR now disables DuckDB hive path inference and checks in-file venue/market/price basis against the provided provenance.

**CI synthetic tests prove the code's QA behaviors; Gate A above is additionally backed by independent direct scan of original Library bytes.**
