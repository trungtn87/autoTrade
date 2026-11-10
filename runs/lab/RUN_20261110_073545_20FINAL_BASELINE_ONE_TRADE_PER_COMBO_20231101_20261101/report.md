# AutoTrade V3 Research Lab Run Report: `RUN_20261110_073545_20FINAL_BASELINE_ONE_TRADE_PER_COMBO_20231101_20261101`

- **Execution Mode:** `ONE_TRADE_PER_COMBO`
- **Strategy:** `20FINAL_BASELINE` (v1.0.0)
- **Time Window:** `2023-11-01` to `2026-11-01`
- **Symbols:** BTC-USDT, ETH-USDT
- **Git SHA:** `0543ba0aa13b02d39c42806ecac3396757de4f39`
- **Validation Status:** **PASS**
- **Data Hash:** `a18fc2e1bf598594...`

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
- **Note on ROI:** Virtual isolated combo performance (max 1 open trade per combo).
- **Profit Factor:** 1.39
- **Trade-level Win Rate:** 39.61% (1646/4156)
- **Leg-level Win Rate:** 44.30% (2779/6273)

## 3. Risk & Drawdown

- **Closed-Trade Max Drawdown:** $44.89 (3.39%)
- **Continuous M2M Max Drawdown:** $44.89 (3.39%)

## 4. Intrabar Ambiguity

- **TP/SL Same-Candle Ambiguity:** 3 legs
- **Trailing Stop Ambiguity:** 459 legs
- **Resolution Policy:** `CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)`

## 5. Performance by Symbol

| Symbol | Entries | Legs | Net PnL | Win Rate (Leg) | Profit Factor |
|:---|---:|---:|---:|---:|---:|
| **BTC-USDT** | 1,861 | 3,021 | $138.53 | 47.3% | 1.43 |
| **ETH-USDT** | 2,295 | 3,252 | $145.66 | 41.5% | 1.36 |
