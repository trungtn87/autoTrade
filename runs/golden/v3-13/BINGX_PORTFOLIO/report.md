# AutoTrade V3 Research Lab Run Report: `BINGX_PORTFOLIO`

- **Execution Mode:** `BINGX_PORTFOLIO`
- **Strategy:** `20FINAL_BASELINE` (v1.0.0)
- **Cost Profile:** `IDEAL` (hash: `c670e9dc806a8b04...`)
- **Ambiguity Policy:** `CONSERVATIVE`
- **Time Window:** `2023-11-01` to `2026-11-01`
- **Symbols:** BTC-USDT, ETH-USDT
- **Git SHA:** `03ec6b1e30e450780461208a14c1c9252b860205`
- **Validation Status:** **FAILED** (Real Market Data: True)
- **Data Hash:** `a18fc2e1bf598594...`

## Validation Gates Audit

| Gate | Status | Summary |
|:---|:---:|:---|
| **GATE A DATA** | `INCONCLUSIVE` | A1=INCONCLUSIVE; A2=INCONCLUSIVE |
| **GATE B STRATEGY** | `INCONCLUSIVE` | B1=PASS; B2=INCONCLUSIVE; B3=INCONCLUSIVE |
| **GATE C EXECUTION** | `INCONCLUSIVE` | C1=INCONCLUSIVE; C2=PASS; C3=INCONCLUSIVE; C4=PASS; C5=PASS |
| **GATE D RESULT** | `FAIL` | D1=PASS; D2=FAIL |

## 1. Accounting & Signals

- **Total Signals Generated:** 5,984
- **Accepted Entries (Trades):** 4,149
- **Total Trade Legs:** 6,265
- **Skipped by Concurrency:** 1,835
- **Rejected by Margin:** 0

## 2. Performance & PnL

- **Gross PnL (Fill):** $697.14
- **Gross PnL (Raw/Trigger):** $697.14
- **Entry Fee:** $207.4500 | **Exit Fee:** $207.4953 (Total: $414.9453)
- **Spread Cost:** $0.0000 | **Slippage Cost:** $0.0000 | **Funding Cost:** $0.0000
- **Net PnL (IDEAL):** **$282.19**
- **ROI (Account Initial Equity ($1,000)):** **+28.22%**
- **Note on ROI:** True finite-capital portfolio ROI after margin, fees, and VWAP blending.
- **Net Profit Factor (Default):** 1.14
- **Gross Profit Factor:** 1.4
- **Net Profit Factor (Trade):** 1.15
- **Net Profit Factor (Leg):** 1.14
- **Gross Profit Factor (Trade):** 1.43
- **Gross Profit Factor (Leg):** 1.4
- **Trade-level Win Rate:** 40.85% (1695/4149)
- **Leg-level Win Rate:** 45.06% (2823/6265)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $45.71 (3.46%)
- **Continuous M2M Max Drawdown:** $49.01 (3.70%)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 3 legs
- **Trailing Stop Ambiguity:** 463 legs
- **Resolution Policy:** `CONSERVATIVE`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF | Net PF (Trade) | Gross PF (Trade) |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| **BTC-USDT** | 1,861 | 3,021 | $138.53 | 47.9% | 1.17 | 1.46 | 1.18 | 1.49 |
| **ETH-USDT** | 2,288 | 3,244 | $143.66 | 42.4% | 1.13 | 1.37 | 1.13 | 1.38 |

## 6. Exit Reason Breakdown

| Exit Reason | Count | Pct of Legs | Gross PnL | Net PnL |
|:---|---:|---:|---:|---:|
| **SL** | 3,255 | 51.96% | $-1,717.04 | $-1,942.35 |
| **SL_INTRABAR_AMBIGUOUS** | 3 | 0.05% | $-1.10 | $-1.25 |
| **TP** | 1,626 | 25.95% | $2,058.00 | $1,937.61 |
| **TRAIL** | 1,381 | 22.04% | $357.28 | $288.18 |

## 7. Performance by Combo

> Note: Mode C per-combo PnL attribution uses `pnl_own_entry` (individual leg entry price), not merged VWAP.

| Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL (own_entry) | Win Rate | Net PF (Leg) | Net PF (Trade) |
|:---|:---|---:|:---|:---|---:|---:|---:|---:|---:|---:|
| **BTC-USDT/C1** | BTC-USDT | 1 | C1 | 1h | 196 | 392 | $12.89 | 49.2% | 1.16 | 1.18 |
| **BTC-USDT/C10** | BTC-USDT | 10 | C10 | 15m | 46 | 92 | $6.61 | 63.0% | 1.4 | 1.42 |
| **BTC-USDT/C11** | BTC-USDT | 101 | C11 | 1h | 243 | 243 | $40.51 | 43.2% | 1.29 | 1.29 |
| **BTC-USDT/C14** | BTC-USDT | 104 | C14 | 1h | 294 | 294 | $17.27 | 30.9% | 0.94 | 0.94 |
| **BTC-USDT/C16** | BTC-USDT | 106 | C16 | 1h | 164 | 164 | $20.00 | 56.1% | 1.27 | 1.27 |
| **BTC-USDT/C2** | BTC-USDT | 2 | C2 | 1h | 84 | 168 | $3.09 | 54.2% | 1.34 | 1.36 |
| **BTC-USDT/C4** | BTC-USDT | 4 | C4 | 1h | 264 | 528 | $-7.28 | 48.3% | 1.0 | 1.0 |
| **BTC-USDT/C6** | BTC-USDT | 6 | C6 | 1h | 112 | 224 | $16.69 | 50.5% | 1.47 | 1.54 |
| **BTC-USDT/C7** | BTC-USDT | 7 | C7 | 15m | 206 | 412 | $1.66 | 45.6% | 1.11 | 1.12 |
| **BTC-USDT/C9** | BTC-USDT | 9 | C9 | 15m | 73 | 146 | $8.70 | 48.0% | 1.25 | 1.26 |
| **BTC-USDT/TIER** | BTC-USDT | 11 | TIER | 1h | 179 | 358 | $18.39 | 53.4% | 1.47 | 1.53 |
| **ETH-USDT/C1** | ETH-USDT | 1 | C1 | 1h | 254 | 508 | $-14.59 | 41.1% | 0.91 | 0.9 |
| **ETH-USDT/C10** | ETH-USDT | 10 | C10 | 15m | 13 | 26 | $8.37 | 76.9% | 4.63 | 5.53 |
| **ETH-USDT/C12** | ETH-USDT | 102 | C12 | 1h | 452 | 452 | $39.39 | 40.3% | 1.09 | 1.09 |
| **ETH-USDT/C13** | ETH-USDT | 103 | C13 | 1h | 254 | 254 | $29.01 | 36.2% | 1.21 | 1.21 |
| **ETH-USDT/C15** | ETH-USDT | 105 | C15 | 1h | 626 | 626 | $47.01 | 34.8% | 1.1 | 1.1 |
| **ETH-USDT/C3** | ETH-USDT | 3 | C3 | 1h | 94 | 188 | $3.32 | 45.2% | 1.51 | 1.54 |
| **ETH-USDT/C4** | ETH-USDT | 4 | C4 | 1h | 259 | 518 | $21.29 | 47.7% | 1.15 | 1.16 |
| **ETH-USDT/C6** | ETH-USDT | 6 | C6 | 1h | 74 | 148 | $1.07 | 40.5% | 1.06 | 1.06 |
| **ETH-USDT/C7** | ETH-USDT | 7 | C7 | 15m | 262 | 524 | $8.79 | 50.2% | 1.2 | 1.22 |

## 8. Reconciliation Audit

- **Three-Level Parity Pass (Run == Symbol Sum == Combo Sum):** `True`
- **Symbol-Combo Configuration Alignment:** `True`
- **Unrounded Tolerance:** `1e-09`
