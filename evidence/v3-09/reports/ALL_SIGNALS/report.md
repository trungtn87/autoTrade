# AutoTrade V3 Research Lab Run Report: `ALL_SIGNALS`

- **Execution Mode:** `ALL_SIGNALS`
- **Strategy:** `20FINAL_BASELINE` (v1.0.0)
- **Time Window:** `2023-11-01` to `2026-11-01`
- **Symbols:** BTC-USDT, ETH-USDT
- **Git SHA:** `b68933c131725f680eafc72640153b61af85741d`
- **Validation Status:** **REAL_DATA_PASS** (Real Market Data: False)
- **Data Hash:** `a18fc2e1bf598594...`

## Validation Gates Audit

| Gate | Status | Summary |
|:---|:---:|:---|

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
- **Net Profit Factor (Trade):** 1.14
- **Net Profit Factor (Leg):** 1.13
- **Gross Profit Factor (Trade):** 1.38
- **Gross Profit Factor (Leg):** 1.37
- **Trade-level Win Rate:** 38.72% (2317/5984)
- **Leg-level Win Rate:** 42.75% (3530/8258)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $74.39 (5.65%)
- **Continuous M2M Max Drawdown:** NOT_APPLICABLE (Continuous intra-trade M2M drawdown is NOT_APPLICABLE in isolated/unconstrained trade modes.)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 0 legs
- **Trailing Stop Ambiguity:** 0 legs
- **Resolution Policy:** `CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF | Net PF (Trade) | Gross PF (Trade) |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| **BTC-USDT** | 2,575 | 3,838 | $320.84 | 47.2% | 1.26 | 1.54 | 1.27 | 1.56 |
| **ETH-USDT** | 3,409 | 4,420 | $89.86 | 38.9% | 1.05 | 1.26 | 1.05 | 1.26 |

## 6. Exit Reason Breakdown

| Exit Reason | Count | Pct of Legs | Gross PnL | Net PnL |
|:---|---:|---:|---:|---:|
| **SL** | 4,478 | 54.23% | $-2,742.88 | $-3,082.13 |
| **SL_INTRABAR_AMBIGUOUS** | 3 | 0.04% | $-1.30 | $-1.45 |
| **TP** | 2,299 | 27.84% | $3,414.40 | $3,229.36 |
| **TRAIL** | 1,478 | 17.90% | $338.87 | $264.92 |

## 7. Performance by Combo

| Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL | Win Rate | Net PF (Leg) | Net PF (Trade) |
|:---|:---|---:|:---|:---|---:|---:|---:|---:|---:|---:|
| **BTC-USDT/C1** | BTC-USDT | 1 | C1 | 1h | 245 | 490 | $1.24 | 45.9% | 1.01 | 1.01 |
| **BTC-USDT/C10** | BTC-USDT | 10 | C10 | 15m | 46 | 92 | $6.61 | 63.0% | 1.5 | 1.51 |
| **BTC-USDT/C11** | BTC-USDT | 101 | C11 | 1h | 828 | 828 | $227.68 | 47.2% | 1.52 | 1.52 |
| **BTC-USDT/C14** | BTC-USDT | 104 | C14 | 1h | 316 | 316 | $19.06 | 31.0% | 1.11 | 1.11 |
| **BTC-USDT/C16** | BTC-USDT | 106 | C16 | 1h | 168 | 168 | $18.00 | 55.4% | 1.24 | 1.24 |
| **BTC-USDT/C2** | BTC-USDT | 2 | C2 | 1h | 84 | 168 | $3.09 | 53.6% | 1.1 | 1.11 |
| **BTC-USDT/C4** | BTC-USDT | 4 | C4 | 1h | 278 | 556 | $-4.71 | 48.0% | 0.96 | 0.96 |
| **BTC-USDT/C6** | BTC-USDT | 6 | C6 | 1h | 114 | 228 | $18.77 | 50.4% | 1.46 | 1.5 |
| **BTC-USDT/C7** | BTC-USDT | 7 | C7 | 15m | 227 | 454 | $4.25 | 44.5% | 1.04 | 1.05 |
| **BTC-USDT/C9** | BTC-USDT | 9 | C9 | 15m | 76 | 152 | $7.74 | 48.7% | 1.44 | 1.46 |
| **BTC-USDT/TIER** | BTC-USDT | 11 | TIER | 1h | 193 | 386 | $19.11 | 51.5% | 1.23 | 1.25 |
| **ETH-USDT/C1** | ETH-USDT | 1 | C1 | 1h | 272 | 544 | $-14.22 | 39.9% | 0.88 | 0.86 |
| **ETH-USDT/C10** | ETH-USDT | 10 | C10 | 15m | 13 | 26 | $8.37 | 76.9% | 4.33 | 5.24 |
| **ETH-USDT/C12** | ETH-USDT | 102 | C12 | 1h | 455 | 455 | $36.38 | 40.0% | 1.13 | 1.13 |
| **ETH-USDT/C13** | ETH-USDT | 103 | C13 | 1h | 297 | 297 | $15.51 | 34.0% | 1.09 | 1.09 |
| **ETH-USDT/C15** | ETH-USDT | 105 | C15 | 1h | 1,646 | 1,646 | $13.89 | 32.4% | 1.01 | 1.01 |
| **ETH-USDT/C3** | ETH-USDT | 3 | C3 | 1h | 95 | 190 | $3.32 | 43.2% | 1.09 | 1.1 |
| **ETH-USDT/C4** | ETH-USDT | 4 | C4 | 1h | 268 | 536 | $17.03 | 46.6% | 1.15 | 1.16 |
| **ETH-USDT/C6** | ETH-USDT | 6 | C6 | 1h | 75 | 150 | $0.31 | 40.0% | 1.01 | 1.01 |
| **ETH-USDT/C7** | ETH-USDT | 7 | C7 | 15m | 288 | 576 | $9.27 | 47.2% | 1.07 | 1.08 |

## 8. Reconciliation Audit

- **Three-Level Parity Pass (Run == Symbol Sum == Combo Sum):** `True`
- **Symbol-Combo Configuration Alignment:** `True`
- **Unrounded Tolerance:** `1e-09`
