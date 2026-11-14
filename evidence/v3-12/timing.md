# V3-12 Execution Timing Benchmark (3-Year Real M1 Data: 2023-11-01 to 2026-11-01)

## Simulator-Only Timing (Preloaded M1 + Signals)

| Mode | Legacy Simulator (s) | Unified Simulator (s) | Speedup |
| --- | ---: | ---: | ---: |
| ALL_SIGNALS (Mode A) | 5.171 | 3.073 | 1.68x |
| ONE_TRADE_PER_COMBO (Mode B) | 4.288 | 2.273 | 1.89x |
| BINGX_PORTFOLIO (Mode C) | 32.052 | 28.041 | 1.14x |

## Full Golden Pipeline Timing (Data Load + Signal Gen + Sim + Analysis + Persistence)

| Mode | Full Run Elapsed (s) |
| --- | ---: |
| ALL_SIGNALS (Mode A) | 762.603 |
| ONE_TRADE_PER_COMBO (Mode B) | 128.152 |
| BINGX_PORTFOLIO (Mode C) | 202.045 |
