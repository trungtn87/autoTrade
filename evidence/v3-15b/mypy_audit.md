# Mypy Audit & Classification (`autotrade_v3`)

- **Date**: 2026-11-15
- **Scope**: `mypy autotrade_v3` (150 source files)

## 1. Error Counts (Before vs After)
- **Before (no `[tool.mypy]` config)**: **200 errors in 89 files** (149 source files)
  - Missing third-party stubs (`[import-untyped]`): **93 errors** (`pandas`: 78, `pyarrow`: 9, `plotly`: 6)
  - Internal type/annotation errors: **107 errors**
- **After (`pyproject.toml` `[tool.mypy.overrides]` + annotation fixes in `scan_hardcoded_metrics.py` & `gates_c.py`)**: **102 errors in 24 files** (150 source files)
  - Missing third-party stubs: **0 errors** (suppressed via `ignore_missing_imports = true` for `pandas`, `pyarrow`, `plotly`)
  - `autotrade_v3/dashboard/`: **0 errors**
  - `autotrade_v3/runstore/`: **0 errors**
  - `autotrade_v3/tools/scan_hardcoded_metrics.py`: **0 errors** (fixed `Sequence[str | Path] | None`)
  - `autotrade_v3/validation/gates_c.py`: **0 errors** (fixed 4 annotation/dict-narrowing issues)

## 2. Remaining Internal Errors (Not Modified per Scope Rules)
- **`autotrade_v3/strategy_core/strategies/final20/logic.py` (8 errors)**: `SignalEvent(side=str)` vs `Literal['BUY', 'SELL']` (lines 614, 2709); `int(int | Any | None)` (line 1943); `float(Any | None)` (lines 1952–1956). *Not modified per rule: do not touch `strategy_core/` logic or `20FINAL`.*
- **Other legacy/non-target modules (94 errors across 23 files)**: `validation/gates_b.py` (14), `data_backtest/reconcile_live.py` (13), `tests/test_runstore.py` (13), `data_backtest/simulate_execution.py` (8), `scripts/run_v3_backtest.py` (6), `audit.py` (5), `data_backtest/verify_bingx_exits.py` (5), `analysis/metrics.py` (5), `validation/gates_d.py` (4), `legacy/reconcile_runs.py` (3), `external/annotations.py` (2), `strategy/baseline_20final.py` (2), `tests/test_sim_leg_spec.py` (2), `tests/test_exit_policy_simulator.py` (2), `analysis/regimes.py` (2), and 1 each in `engine/risk_manager.py`, `engine/candle_loader.py`, `tests/test_signal_cache.py`, `strategy/registry.py`, `tests/test_sim_mode_c_accounting.py`, `tests/test_strategy_core_v311.py`, `lab/run_manager.py`, `tests/test_reporting_corrections_v309.py`.

## 3. Branch History Note (V3-11 vs V3-12)
- `feat/v3-12-unified-simulator` (`ad74e29`) was branched prior to the final docs commit on `feat/v3-11-strategy-core` (`596cb62` `docs(v3-11): fix dataset hash and clarify CI M1 skip behavior in EVIDENCE_REPORT`); recorded as-is without rebasing (per N10 / Nhiệm vụ 4).
