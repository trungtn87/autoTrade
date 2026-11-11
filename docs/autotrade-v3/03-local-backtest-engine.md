# V3-03 — AutoTrade V3 Local Backtest Engine

Local-first, simulation-only backtest engine executing the full trading lifecycle:
`SIGNAL -> ENTRY -> POSITION OPEN -> TP / SL / TRAILING -> CLOSED`.

**STRICTLY SIMULATION ONLY**: No live orders, no BingX private trading keys, no production database access.

---

## 1. Architecture Overview

```text
autotrade_v3/
  engine/
    candle_loader.py         # M1 loading, gap & monotonicity validation, no-lookahead resampling
    signal_engine.py         # Replays exact 20FINAL strategy (14 FINAL14 + 6 NEW6) with locked parameters
    risk_manager.py          # Sizing ($100 notional), leverage (100x BTC / 50x ETH), taker fees (0.05%), slippage
    execution_simulator.py   # Full lifecycle simulation with conservative intrabar ambiguity handling
  analysis/
    metrics.py               # Net PnL, Win Rate, Profit Factor, Max Drawdown, Intrabar ambiguity stats
    regimes.py               # Development (24M), Validation (6M), Holdout (6M) window evaluations
  reports/
    reporter.py              # Exports CSV, Parquet, JSON summary, and self-contained HTML dashboard
  tests/                     # Complete offline test suite (39 unit tests)
scripts/
  run_v3_backtest.py         # Deterministic CLI backtest runner
```

---

## 2. Execution Modes

The engine strictly separates strategy evaluation from venue execution constraints:

1. **Virtual Combo Isolated Mode (`--mode virtual`)**:
   - Each combo operates as an independent virtual position.
   - Evaluates pure edge of individual combo logic without interference from other combos.
   - Respects locked exit models:
     - **NEW6 (C11, C12, C13, C14, C15, C16)**: 100% Hard TP / 100% Hard SL (no trailing stop).
     - **FINAL14 (C1..C10, TIER)**: 50% TP + 50% Trailing (T1 activation at +0.5%, callback 0.5%).

2. **Exchange-Compatible Portfolio Mode (`--mode portfolio`)**:
   - Accurately models BingX Hedge Mode constraints where concurrent positions on the same symbol and side are merged into a single position with volume-weighted average entry price.
   - Tracks portfolio-level equity, margin utilization, and prevents over-leverage.

---

## 3. Conservative Intrabar Handling

On discrete candles, when both Take-Profit (TP) and Stop-Loss (SL) are breached within the same bar (e.g. Long: `high >= TP` and `low <= SL`):
- **Conservative Policy**: Stop-Loss is assumed to trigger first (`exit_reason = "SL_INTRABAR_AMBIGUOUS"`).
- **Transparency**: Every affected trade is marked with `intrabar_ambiguous = True`.
- **Metrics**: Both the total count and percentage of intrabar-ambiguous trades are tracked in summary reports.

---

## 4. Multi-Window Regimes

To prevent overfitting, trades are automatically segmented and independently evaluated across three distinct UTC time horizons:
- **Development**: 2023-11-01 to 2025-10-31 (24 months)
- **Validation**: 2025-11-01 to 2026-04-30 (6 months)
- **Holdout**: 2026-05-01 to 2026-10-31 (6 months)

---

## 5. CLI Usage

### Running Smoke Test (Offline / CI / Fixture)
```bash
python scripts/run_v3_backtest.py --smoke-test --out-dir autotrade_v3_results
```

### Running on Historical M1 Catalog (Full 3-Year Dataset)
```bash
python scripts/run_v3_backtest.py \
    --catalog "path/to/catalog.duckdb" \
    --out-dir "runs/v3_full_run" \
    --symbols BTC-USDT ETH-USDT \
    --mode both \
    --start 2023-11-01 \
    --end 2026-11-01
```

### Generated Artifacts
- `virtual_trades.csv` / `virtual_trades.parquet`: Detailed trade ledger for isolated virtual combos.
- `portfolio_trades.csv` / `portfolio_trades.parquet`: Reconciled exchange-level trade ledger.
- `virtual_summary.json` / `portfolio_summary.json`: Complete statistical metrics and regime breakdowns.
- `virtual_report.html` / `portfolio_report.html`: Self-contained interactive visual dashboards with equity curve and monthly PnL heatmaps.
- `run_manifest.json`: Reproducibility metadata including Git SHA, execution timestamp, and parameters.
