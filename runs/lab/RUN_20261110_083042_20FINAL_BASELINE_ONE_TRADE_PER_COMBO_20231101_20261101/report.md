# AutoTrade V3 Research Lab Run Report: `RUN_20261110_083042_20FINAL_BASELINE_ONE_TRADE_PER_COMBO_20231101_20261101`

- **Execution Mode:** `ONE_TRADE_PER_COMBO`
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
- **Accepted Entries (Trades):** 4,156
- **Total Trade Legs:** 6,273
- **Skipped by Concurrency:** 1,828
- **Rejected by Margin:** 0

## 2. Performance & PnL

- **Gross PnL:** $699.84
- **Total Fees (Taker 0.05%):** $415.64
- **Net PnL:** **$284.19**
- **ROI (Reference Allocated Capital ($1,000)):** **+28.42%**
- **Note on ROI:** Reference isolated combo ROI (max 1 open trade per combo).
- **Net Profit Factor (Default):** 1.14
- **Gross Profit Factor:** 1.39
- **Trade-level Win Rate:** 39.61% (1646/4156)
- **Leg-level Win Rate:** 44.30% (2779/6273)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $44.89 (3.39%)
- **Continuous M2M Max Drawdown:** NOT_APPLICABLE (Continuous intra-trade M2M drawdown is NOT_APPLICABLE in isolated/unconstrained trade modes.)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 3 legs
- **Trailing Stop Ambiguity:** 459 legs
- **Resolution Policy:** `CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF |
|:---|---:|---:|---:|---:|---:|---:|
| **BTC-USDT** | 1,861 | 3,021 | $138.53 | 47.3% | 1.16 | 1.43 |
| **ETH-USDT** | 2,295 | 3,252 | $145.66 | 41.5% | 1.12 | 1.36 |
