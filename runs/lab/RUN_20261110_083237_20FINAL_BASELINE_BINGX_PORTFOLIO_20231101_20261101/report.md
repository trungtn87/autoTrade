# AutoTrade V3 Research Lab Run Report: `RUN_20261110_083237_20FINAL_BASELINE_BINGX_PORTFOLIO_20231101_20261101`

- **Execution Mode:** `BINGX_PORTFOLIO`
- **Strategy:** `20FINAL_BASELINE` (v1.0.0)
- **Time Window:** `2023-11-01` to `2026-11-01`
- **Symbols:** BTC-USDT, ETH-USDT
- **Git SHA:** `e9081b72ff3817a2b5480fbd465dd13f4ddac4e7`
- **Validation Status:** **REAL_DATA_PASS** (Real Market Data: True)
- **Data Hash:** `a18fc2e1bf598594...`

## Validation Gates Audit

| Gate | Status | Summary |
|:---|:---:|:---|
| **Gate A — DATA** | `PASS` | All raw data partitions, DuckDB catalog, and coverage verified. |
| **Gate B — STRATEGY** | `PASS` | Strategy configuration, no-lookahead timestamps, and signal parity verified. |
| **Gate C — EXECUTION** | `PASS` | Causal entries, frozen ExitPolicy, and mathematical balance verified. |
| **Gate D — RESULT** | `PASS` | Report metrics derived from ledger, drawdown cleanly separated, and deliverables complete. |

## 1. Accounting & Signals

- **Total Signals Generated:** 5,984
- **Accepted Entries (Trades):** 4,149
- **Total Trade Legs:** 6,265
- **Skipped by Concurrency:** 1,835
- **Rejected by Margin:** 0

## 2. Performance & PnL

- **Gross PnL:** $697.14
- **Total Fees (Taker 0.05%):** $414.95
- **Net PnL:** **$282.19**
- **ROI (Account Initial Equity ($1,000)):** **+28.22%**
- **Note on ROI:** True finite-capital portfolio ROI after margin, fees, and VWAP blending.
- **Net Profit Factor (Default):** 1.14
- **Gross Profit Factor:** 1.41
- **Trade-level Win Rate:** 40.85% (1695/4149)
- **Leg-level Win Rate:** 45.06% (2823/6265)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $45.71 (3.46%)
- **Continuous M2M Max Drawdown:** $49.01 (3.70%)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 3 legs
- **Trailing Stop Ambiguity:** 459 legs
- **Resolution Policy:** `CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF |
|:---|---:|---:|---:|---:|---:|---:|
| **BTC-USDT** | 1,861 | 3,021 | $138.53 | 47.9% | 1.17 | 1.46 |
| **ETH-USDT** | 2,288 | 3,244 | $143.66 | 42.4% | 1.13 | 1.37 |
