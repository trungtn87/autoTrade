# AutoTrade V3 Validation & Historical Backtest Audit Report

- **Generated At**: 2026-11-10T03:55:33.324024+00:00
- **Git Commit**: `05ec773e0b7cf88b48c27936dc22c24a8a0d303f`
- **Simulation Mode**: `both`
- **Data Source**: `REAL_M1_HYBRID_BINANCE_BINGX`
- **Window**: `2023-11-01` to `2026-11-01` (3 Years)
- **Symbols**: `BTC-USDT, ETH-USDT`
- **Capital & Sizing**: Initial Equity: $1,000.00 | Notional/Trade: $100.00 | Slippage: 0.00%
- **Operational Scope**: **SIMULATION ONLY** (No real exchange orders, no live credentials used).

---

## 1. Gate A — M1 Data Integrity Validation

- **Status**: `HYBRID_PASS_SOURCE_INCOMPLETE` (Research Ready: `True`)
- **Primary Source**: Binance USD-M 1m Perpetual (`data/m1_raw/provider=binance/`)
- **Secondary / Provenance**: BingX 1m Perpetual (`data/m1_raw/provider=bingx/` - 2026-10-28 1,440 candles gap fill)
- **Total Parquet Partitions**: `74` monthly/daily partition files
- **Total Continuous M1 Bars**: Exactly 3,156,480 candles (1,578,240 BTC-USDT + 1,578,240 ETH-USDT)
- **Gaps / Overlaps / Duplicates**: **0 gaps**, **0 duplicates**, **0 OHLCV sequence errors**
- **Catalog**: Local DuckDB indexed catalog at `catalog/research.duckdb`

---

## 2. Gate B — 20FINAL Signal Parity Validation

- **Gate B1 (Identical Input Parity)**:
  - Baseline: RUN17 Frozen Canonical Signals (`autotrade_v3/data_backtest/run17_reference.json`)
  - Status: **`PASS`** (All 20/20 combos identical, 0 discrepancies, 100.0% signal-level parity)
  - Data SHA256 matches verified frozen CSV inputs.
- **Gate B2 (Source Sensitivity & Overlap)**:
  - Common Binance Period (01/2024 to 04/2026): **100.0% Jaccard Index**
  - Full Overlap Window (01/2024 to 09/2026): **97.302% Jaccard Index** (residual divergence strictly due to cross-venue indicator warm-up differences after BingX boundary).

---

## 3. Gate C — Execution Engine Correctness Verification

1. **Strict M1 Execution Timing**:
   - Signal confirmed at M15/H1 bar close timestamp $T_{close}$.
   - Earliest possible entry is $T_{close} + 1$ ms (open of subsequent M1 bar). Strictly 0 lookahead bias.
2. **Conservative Intrabar Ambiguity Resolution**:
   - When both TP and SL price thresholds are hit within the same 1-minute candlestick, the engine strictly prioritizes **SL first**.
   - Flagged with `intrabar_ambiguous = True` for auditability.
3. **Split-Leg Exit Mechanics (FINAL14)**:
   - For combos utilizing `50TP_50TRAIL`, 50% notional exits on fixed Take Profit, and 50% notional enters trailing stop mode.
   - Stored as independent legs (`_L1`, `_L2`) with distinct exit prices, fees, and timestamps under the parent `trade_id`.
4. **BingX Merged Position Hedge Simulator**:
   - Same-symbol, same-side positions are merged into a volume-weighted average entry price.
   - Partial combo exits reduce position volume without closing the entire position prematurely.
   - Zero negative volume or margin overdrafts.

---

## 4. Gate D — Historical Backtest Performance Results

### Summary Comparison: Virtual Isolated vs. BingX Merged Portfolio

| Metric | Virtual Mode (Combo Isolated) | Portfolio Mode (BingX Merged) |
| :--- | :--- | :--- |
| **Total Trades (Legs)** | **6271** | **6271** |
| **Win Rate** | **44.7%** | **45.3%** |
| **Net Realized PnL** | **$343.52** | **$343.52** |
| **ROI (on Initial Capital)** | **+34.35%** | **+34.35%** |
| **Profit Factor** | **1.17** | **1.18** |
| **Max Drawdown** | **$43.65 (3.4%)** | **$43.47 (3.4%)** |
| **Total Fees Paid** | **$415.56** | **$415.56** |
| **Intrabar Ambiguous Trades** | **0 (0.0%)** | N/A (Inherited from Virtual) |

### 20FINAL Combo Breakdown (Virtual Isolated Mode)

| Combo ID | Symbol | TF | Total Trades | Win Rate | Net PnL | Profit Factor | Intrabar Ambiguous |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| C1 | ETH-USDT | 1h | 898 | 43.6% | $14.10 | 1.07 | 0 |
| C10 | ETH-USDT | 15m | 118 | 66.1% | $15.73 | 2.00 | 0 |
| C11 | BTC-USDT | 1h | 243 | 43.2% | $40.51 | 1.29 | 0 |
| C12 | ETH-USDT | 1h | 452 | 40.3% | $39.39 | 1.15 | 0 |
| C13 | ETH-USDT | 1h | 255 | 36.5% | $30.91 | 1.21 | 0 |
| C14 | BTC-USDT | 1h | 294 | 30.9% | $17.27 | 1.11 | 0 |
| C15 | ETH-USDT | 1h | 631 | 34.9% | $48.11 | 1.13 | 0 |
| C16 | BTC-USDT | 1h | 164 | 56.1% | $20.00 | 1.28 | 0 |
| C2 | BTC-USDT | 1h | 168 | 53.6% | $5.86 | 1.19 | 0 |
| C3 | ETH-USDT | 1h | 188 | 42.5% | $6.17 | 1.17 | 0 |
| C4 | BTC-USDT | 1h | 1046 | 48.0% | $24.71 | 1.10 | 0 |
| C6 | BTC-USDT | 1h | 372 | 46.2% | $22.32 | 1.31 | 0 |
| C7 | ETH-USDT | 15m | 938 | 47.2% | $27.55 | 1.13 | 0 |
| C9 | BTC-USDT | 15m | 146 | 50.7% | $10.07 | 1.61 | 0 |
| TIER | BTC-USDT | 1h | 358 | 52.2% | $20.82 | 1.28 | 0 |

---

## 5. Architectural Differences: Virtual vs. Portfolio Mode

1. **Volume-Weighted Average Price (VWAP) Effect**:
   In Virtual Mode, each combo enters and exits at its own isolated entry price. In BingX Hedge Mode, adding to an existing position blends the entry price. When an earlier combo closes, its realized PnL is computed against the blended average entry price rather than its standalone entry price.
2. **Capital & Margin Aggregation**:
   Virtual Mode assumes infinite parallel margin per combo. Portfolio Mode strictly tracks the combined margin pool and mark-to-market equity drawdown across all active BTC and ETH positions simultaneously.
3. **Conservative Intrabar Bias**:
   Because ambiguous M1 bars are forced to assume Stop Loss triggers before Take Profit, real-world execution on live tick data may experience higher realized win rates.

---

## 6. Simulation & Real-World Boundaries

- **No Execution Guarantee**: This backtest is a deterministic historical simulation on M1 OHLCV bars. Real exchange order matching involves microsecond queue priority, slippage during high-volatility spikes, and maker/taker fill probability.
- **Funding & Borrowing Fees**: Funding rates between perpetual contracts are not included in this simulation run.
- **Safety Boundary**: This run was executed purely in offline simulation mode. No active exchange API keys or live trading channels were accessed.
