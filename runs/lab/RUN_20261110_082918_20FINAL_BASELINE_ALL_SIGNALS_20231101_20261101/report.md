# AutoTrade V3 Research Lab Run Report: `RUN_20261110_082918_20FINAL_BASELINE_ALL_SIGNALS_20231101_20261101`

- **Execution Mode:** `ALL_SIGNALS`
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
- **Accepted Entries (Trades):** 5,984
- **Total Trade Legs:** 8,258
- **Skipped by Concurrency:** 0
- **Rejected by Margin:** 0

## 2. Performance & PnL

- **Gross PnL:** $1,009.09
- **Total Fees (Taker 0.05%):** $598.39
- **Net PnL:** **$410.70**
- **ROI (Theoretical Baseline Capital ($1,000 reference)):** **+41.07%**
- **Note on ROI:** Reference unconstrained signal ROI. NOT an executable finite account ROI.
- **Net Profit Factor (Default):** 1.13
- **Gross Profit Factor:** 1.37
- **Trade-level Win Rate:** 38.72% (2317/5984)
- **Leg-level Win Rate:** 42.75% (3530/8258)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $74.39 (5.65%)
- **Continuous M2M Max Drawdown:** NOT_APPLICABLE (Continuous intra-trade M2M drawdown is NOT_APPLICABLE in isolated/unconstrained trade modes.)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 3 legs
- **Trailing Stop Ambiguity:** 486 legs
- **Resolution Policy:** `CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF |
|:---|---:|---:|---:|---:|---:|---:|
| **BTC-USDT** | 2,575 | 3,838 | $320.84 | 47.2% | 1.26 | 1.54 |
| **ETH-USDT** | 3,409 | 4,420 | $89.86 | 38.9% | 1.05 | 1.26 |
