# AutoTrade V3 Research Lab Run Report: `RUN_20261110_073426_20FINAL_BASELINE_ALL_SIGNALS_20231101_20261101`

- **Execution Mode:** `ALL_SIGNALS`
- **Strategy:** `20FINAL_BASELINE` (v1.0.0)
- **Time Window:** `2023-11-01` to `2026-11-01`
- **Symbols:** BTC-USDT, ETH-USDT
- **Git SHA:** `0543ba0aa13b02d39c42806ecac3396757de4f39`
- **Validation Status:** **PASS**
- **Data Hash:** `a18fc2e1bf598594...`

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
- **Note on ROI:** Unconstrained signal edge. NOT an executable finite account ROI (all overlapping signals taken).
- **Profit Factor:** 1.37
- **Trade-level Win Rate:** 38.72% (2317/5984)
- **Leg-level Win Rate:** 42.75% (3530/8258)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $74.39 (5.65%)
- **Continuous M2M Max Drawdown:** $74.39 (5.65%)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 3 legs
- **Trailing Stop Ambiguity:** 486 legs
- **Resolution Policy:** `CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Profit Factor |
|:---|---:|---:|---:|---:|---:|
| **BTC-USDT** | 2,575 | 3,838 | $320.84 | 47.2% | 1.54 |
| **ETH-USDT** | 3,409 | 4,420 | $89.86 | 38.9% | 1.26 |
