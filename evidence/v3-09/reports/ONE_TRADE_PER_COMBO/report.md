# AutoTrade V3 Research Lab Run Report: `ONE_TRADE_PER_COMBO`

- **Execution Mode:** `ONE_TRADE_PER_COMBO`
- **Strategy:** `20FINAL_BASELINE` (v1.0.0)
- **Time Window:** `2023-11-01` to `2026-11-01`
- **Symbols:** BTC-USDT, ETH-USDT
- **Git SHA:** `b68933c131725f680eafc72640153b61af85741d`
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
- **Net Profit Factor (Trade):** 1.14
- **Net Profit Factor (Leg):** 1.14
- **Gross Profit Factor (Trade):** 1.41
- **Gross Profit Factor (Leg):** 1.39
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

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Net PF | Gross PF | Net PF (Trade) | Gross PF (Trade) |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| **BTC-USDT** | 1,861 | 3,021 | $138.53 | 47.3% | 1.16 | 1.43 | 1.17 | 1.46 |
| **ETH-USDT** | 2,295 | 3,252 | $145.66 | 41.5% | 1.12 | 1.36 | 1.13 | 1.37 |

## 6. Exit Reason Breakdown

| Exit Reason | Count | Pct of Legs | Gross PnL | Net PnL |
|:---|---:|---:|---:|---:|
| **SL** | 3,260 | 51.97% | $-1,805.23 | $-2,030.94 |
| **SL_INTRABAR_AMBIGUOUS** | 3 | 0.05% | $-1.30 | $-1.45 |
| **TP** | 1,629 | 25.97% | $2,188.90 | $2,068.21 |
| **TRAIL** | 1,381 | 22.01% | $317.47 | $248.37 |

## 7. Performance by Combo

| Combo Key | Symbol | ID | Name | Timeframe | Entries | Legs | Net PnL | Win Rate | Net PF (Leg) | Net PF (Trade) |
|:---|:---|---:|:---|:---|---:|---:|---:|---:|---:|---:|
| **BTC-USDT/C1** | BTC-USDT | 1 | C1 | 1h | 196 | 392 | $12.89 | 48.0% | 1.15 | 1.17 |
| **BTC-USDT/C10** | BTC-USDT | 10 | C10 | 15m | 46 | 92 | $6.61 | 63.0% | 1.5 | 1.51 |
| **BTC-USDT/C11** | BTC-USDT | 101 | C11 | 1h | 243 | 243 | $40.51 | 43.2% | 1.29 | 1.29 |
| **BTC-USDT/C14** | BTC-USDT | 104 | C14 | 1h | 294 | 294 | $17.27 | 30.9% | 1.11 | 1.11 |
| **BTC-USDT/C16** | BTC-USDT | 106 | C16 | 1h | 164 | 164 | $20.00 | 56.1% | 1.28 | 1.28 |
| **BTC-USDT/C2** | BTC-USDT | 2 | C2 | 1h | 84 | 168 | $3.09 | 53.6% | 1.1 | 1.11 |
| **BTC-USDT/C4** | BTC-USDT | 4 | C4 | 1h | 264 | 528 | $-7.28 | 47.5% | 0.94 | 0.94 |
| **BTC-USDT/C6** | BTC-USDT | 6 | C6 | 1h | 112 | 224 | $16.69 | 49.5% | 1.41 | 1.45 |
| **BTC-USDT/C7** | BTC-USDT | 7 | C7 | 15m | 206 | 412 | $1.66 | 44.2% | 1.02 | 1.02 |
| **BTC-USDT/C9** | BTC-USDT | 9 | C9 | 15m | 73 | 146 | $8.70 | 50.0% | 1.53 | 1.55 |
| **BTC-USDT/TIER** | BTC-USDT | 11 | TIER | 1h | 179 | 358 | $18.39 | 52.2% | 1.24 | 1.27 |
| **ETH-USDT/C1** | ETH-USDT | 1 | C1 | 1h | 254 | 508 | $-14.59 | 39.4% | 0.86 | 0.85 |
| **ETH-USDT/C10** | ETH-USDT | 10 | C10 | 15m | 13 | 26 | $8.37 | 76.9% | 4.33 | 5.24 |
| **ETH-USDT/C12** | ETH-USDT | 102 | C12 | 1h | 452 | 452 | $39.39 | 40.3% | 1.15 | 1.15 |
| **ETH-USDT/C13** | ETH-USDT | 103 | C13 | 1h | 255 | 255 | $30.91 | 36.5% | 1.21 | 1.21 |
| **ETH-USDT/C15** | ETH-USDT | 105 | C15 | 1h | 631 | 631 | $48.11 | 34.9% | 1.13 | 1.13 |
| **ETH-USDT/C3** | ETH-USDT | 3 | C3 | 1h | 94 | 188 | $3.32 | 43.1% | 1.09 | 1.1 |
| **ETH-USDT/C4** | ETH-USDT | 4 | C4 | 1h | 259 | 518 | $21.29 | 47.7% | 1.2 | 1.21 |
| **ETH-USDT/C6** | ETH-USDT | 6 | C6 | 1h | 74 | 148 | $1.07 | 40.5% | 1.04 | 1.04 |
| **ETH-USDT/C7** | ETH-USDT | 7 | C7 | 15m | 263 | 526 | $7.79 | 47.1% | 1.07 | 1.07 |

## 8. Reconciliation Audit

- **Three-Level Parity Pass (Run == Symbol Sum == Combo Sum):** `True`
- **Symbol-Combo Configuration Alignment:** `True`
- **Unrounded Tolerance:** `1e-09`
