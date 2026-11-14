# V3-12 Evidence Report: Unified Execution Simulator

## A. Metadata & Scope
- **PR / Branch:** `feat/v3-12-unified-simulator` (branched from `feat/v3-11-strategy-core` at `a7186ff`)
- **Base SHA:** `a7186ff`
- **Parity Checkpoint SHA:** `958f300` (code at `b147ed8`)
- **Verified Code Head SHA:** `09a3e6d0c72a985119d5ba98930312ad0110488e`
- **Signal Source:** Existing `StrategyEngine` (`20FINAL_BASELINE`) via `ExperimentRunner`
- **Strict Scope & Boundary Checks (`git diff --stat a7186ff..HEAD -- <path>`):**
  - `autotrade_v2/`: **empty** (`VERIFIED`)
  - `runs/golden/v3-08/`: **empty** (`VERIFIED`)
  - `autotrade_v3/strategy_core/`: **empty** (`VERIFIED`)
  - `autotrade_v3/analysis/`: **empty** (`VERIFIED`)
  - `autotrade_v3/validation/`: modified ONLY `context.py` (`RunBundle.signal_decisions`) and `gates_c.py` (`gate_c5` reads `signal_decisions`); C3 replay untouched; `autotrade_v3.sim` added to `FORBIDDEN_PREFIXES` in `test_validation_import_boundary.py` (`VERIFIED`)

## B. Architecture & Key Changes
1. **New `autotrade_v3/sim/` package:**
   - `autotrade_v3/sim/types.py`: `DecisionStatus` (`ACCEPTED`, `SKIPPED`, `REJECTED`, `FILTERED`, `UNFILLABLE`), `ExitRules`, `LegSpec`, `LegOutcome`, `SignalDecision`, `Event`, `M1BarArrays`.
   - `autotrade_v3/sim/leg.py`: `simulate_leg(bars_m1, entry_idx, entry_price, side, leg_spec, exit_rules) -> LegOutcome` — sole implementation of M1 exit rules (fixed TP/SL, trailing activation/peak/callback, gap-open fill, intrabar SL-first ambiguity, `MAX_BARS_HELD`, `FORCE_CLOSE` on final bar after checking TP/SL).
   - `autotrade_v3/sim/admission.py`: `AllSignals`, `OneTradePerCombo` (Mode B same-bar exit/entry concurrency enforced via `>=` in `bdc0b22`), `PortfolioAdmission` (Mode C combo concurrency + margin check). Emits one `SignalDecision` per `signal_id` in chronological order, with `blocking_trade_id` on `SKIPPED_BY_CONCURRENCY`.
   - `autotrade_v3/sim/account.py`: `AccountLedger` for Mode C merged `(symbol, side)` positions, VWAP, margin reservation, and per-M1 equity snapshots.
   - `autotrade_v3/sim/engine.py`: `UnifiedSimulator` orchestrating signal -> decision -> `simulate_leg` -> ledger -> artifacts, with `trade_id = signal_id`, `leg_id = signal_id + "#L1"/"#L2"`, monotonically increasing `Event.seq`, Mode C execution event emission, and `UNFILLABLE` (`NO_M1_BAR_AFTER_SIGNAL`) replacing the legacy look-ahead fallback.
2. **Facade & Persistence:**
   - `autotrade_v3/engine/execution_simulator.py`: legacy duplicated simulation loops removed (`-1307` lines in `edb673f`); `ExecutionSimulator` delegates directly to `UnifiedSimulator`.
   - `autotrade_v3/lab/run_manager.py`: persists `signal_decisions.parquet` (1 row per `signal_id`) alongside derived `skipped_signals` and `rejected_signals`.
   - `autotrade_v3/lab/experiment.py`: in `smoke_test=True` only, bounds `eval_signals` by each symbol's generated 75,000-bar synthetic M1 horizon so synthetic smoke runs do not emit trailing `UNFILLABLE` signals outside generated bars.

## C. Commit History (from `a7186ff`)
- `d08343c` `test(v3-12): add hand-crafted M1 leg execution specification tests`
- `8e76bc6` `feat(v3-12): implement pure M1 leg simulator in autotrade_v3/sim/leg.py`
- `b147ed8` `feat(v3-12): add admission, account, engine, signal_decisions, and C5 support`
- `958f300` `chore(v3-12): parity checkpoint — 100% match with runs/golden/v3-08 across A, B, C`
- `240c75c` `fix(v3-12): remove look-ahead entry fallback and emit UNFILLABLE`
- `bdc0b22` `feat(v3-12): enforce Mode B concurrency on same-bar exit/entry (P2-2)`
- `9c7b12b` `feat(v3-12): check TP/SL before FORCE_CLOSE on final bar in Mode C (P2-8)`
- `75467fa` `chore(v3-12): add runs/golden/v3-12 baseline and V3-12 evidence artifacts`
- `edb673f` `refactor(v3-12): replace legacy simulator methods with UnifiedSimulator facade`
- `2c7c4de` `fix(v3-12): bound smoke_test signal window to synthetic M1 horizon`
- `09a3e6d` `test(v3-12): update trailing max_bars_held=0 test to call sim.leg.simulate_leg`

## D. Parity Checkpoint & Intentional Behavior Changes
1. **Parity Checkpoint (`958f300`, vs `runs/golden/v3-08/` on real 3-year M1 data 2023-11-01..2026-11-01):**
   - Keyed by `(signal_id, leg_type)` excluding `trade_id`, `leg_id`, `event_id` (per design spec 4.5).
   - `ALL_SIGNALS`: 8,258 rows, `rows_only_in_a=0`, `rows_only_in_b=0`, `bad_cols=[]` (`VERIFIED` — see `evidence/v3-12/parity/ALL_SIGNALS.txt`).
   - `ONE_TRADE_PER_COMBO`: 6,273 rows, `rows_only_in_a=0`, `rows_only_in_b=0`, `bad_cols=[]` (`VERIFIED` — see `evidence/v3-12/parity/ONE_TRADE_PER_COMBO.txt`).
   - `BINGX_PORTFOLIO`: 6,265 rows, `rows_only_in_a=0`, `rows_only_in_b=0`, `bad_cols=[]` (`VERIFIED` — see `evidence/v3-12/parity/BINGX_PORTFOLIO.txt`).
2. **Look-ahead Removal (`240c75c`):**
   - Zero signals triggered the fallback on the 3-year real M1 dataset; ledgers remained 100% identical to `v3-08`.
3. **Intentional Change 4.7(a) — Mode B Same-Bar Concurrency (`bdc0b22`, P2-2):**
   - Exactly **11 signals** changed status (9 `ACCEPTED -> SKIPPED`, 2 `SKIPPED -> ACCEPTED`); net effect on Mode B: `accepted_entries` 4,156 -> 4,149, `skipped` 1,828 -> 1,835, `total_legs` 6,273 -> 6,265, `net_pnl_sum` `284.1945340361231` -> `282.19348403612315` (delta `-2.00105`, matching `sum(net_diff)` in `evidence/v3-12/mode_b_change.csv`).
   - Mode B vs Mode C accepted signal sets after `bdc0b22`: `only_in_b=0`, `only_in_c=0` (`VERIFIED`).
4. **Intentional Change 4.7(b) — Final Bar TP/SL Before FORCE_CLOSE (`9c7b12b`, P2-8):**
   - Golden 3-year dataset has no `FORCE_CLOSE` legs; post-B8 comparison against Mode A `v3-08`, Mode B post-B7, and Mode C `v3-08` showed `rows_only_in_a=0`, `rows_only_in_b=0`, `bad_cols=[]` (see `evidence/v3-12/golden_run_summary.json`).

## E. Golden Baseline Comparison (`runs/golden/v3-08/` vs `runs/golden/v3-12/`)

| Mode | Metric | `v3-08` | `v3-12` | Status |
| --- | --- | ---: | ---: | --- |
| `ALL_SIGNALS` | `total_signals` | 5,984 | 5,984 | `VERIFIED` |
| `ALL_SIGNALS` | `accepted_entries` | 5,984 | 5,984 | `VERIFIED` |
| `ALL_SIGNALS` | `total_legs` | 8,258 | 8,258 | `VERIFIED` |
| `ALL_SIGNALS` | `net_pnl_sum` | 410.7006752498004 | 410.7006752498003 | `VERIFIED` |
| `ONE_TRADE_PER_COMBO` | `total_signals` | 5,984 | 5,984 | `VERIFIED` |
| `ONE_TRADE_PER_COMBO` | `accepted_entries` | 4,156 | 4,149 | `VERIFIED` (P2-2) |
| `ONE_TRADE_PER_COMBO` | `skipped` | 1,828 | 1,835 | `VERIFIED` (P2-2) |
| `ONE_TRADE_PER_COMBO` | `total_legs` | 6,273 | 6,265 | `VERIFIED` (P2-2) |
| `ONE_TRADE_PER_COMBO` | `net_pnl_sum` | 284.1945340361231 | 282.19348403612315 | `VERIFIED` (P2-2) |
| `BINGX_PORTFOLIO` | `total_signals` | 5,984 | 5,984 | `VERIFIED` |
| `BINGX_PORTFOLIO` | `accepted_entries` | 4,149 | 4,149 | `VERIFIED` |
| `BINGX_PORTFOLIO` | `skipped` | 1,835 | 1,835 | `VERIFIED` |
| `BINGX_PORTFOLIO` | `total_legs` | 6,265 | 6,265 | `VERIFIED` |
| `BINGX_PORTFOLIO` | `net_pnl_sum` | 282.1934840361229 | 282.1934840361229 | `VERIFIED` |

### Large Non-Committed Golden Artifacts (`runs/golden/v3-12/BINGX_PORTFOLIO/`, > 20 MB)
- `D:\AutoTrade\runs\golden\v3-12\BINGX_PORTFOLIO\snapshots.parquet`: `23,619,083` bytes, `781,611` rows, `sha256=a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a`, `content_hash=668b94733b9a04ed70b364a9c0e0c24bd42554355be6e1cbe2bff13f7e6e1aad`
- `D:\AutoTrade\runs\golden\v3-12\BINGX_PORTFOLIO\snapshots.csv`: `81,054,899` bytes, `781,611` rows, `sha256=61840fe56609e5a49abdf68e4bab6483f3d61b94811043af4de4048e61fb1ec6`
- Read verification: `.\.venv\Scripts\python.exe -c "import pandas as pd; print(len(pd.read_parquet('runs/golden/v3-12/BINGX_PORTFOLIO/snapshots.parquet')))"`

## F. 12-Gate Validation Results (`runs/golden/v3-12/`)
- **`ALL_SIGNALS`**: `VERIFIED` — 12/12 gates `PASS` (`A1..D2`). (`evidence/v3-12/verify/ALL_SIGNALS/validation.json`)
- **`ONE_TRADE_PER_COMBO`**: `VERIFIED` — 12/12 gates `PASS` (`A1..D2`). (`evidence/v3-12/verify/ONE_TRADE_PER_COMBO/validation.json`)
- **`BINGX_PORTFOLIO`**: `FAILED` — 11/12 gates `PASS`; **`C4` (`Accounting`) `FAIL`** with `violation_count: 2328` (`kind: "EVENT_ENTRY_PRICE"`). (`evidence/v3-12/verify/BINGX_PORTFOLIO/validation.json`)
  - **Root cause (documented per spec 4.4):** Mode C now emits `ENTRY` execution events at each leg's actual M1 open fill price (`execution_events.parquet`), whereas Mode C `trades.entry_price` still holds the merged position VWAP until V3-13 adds separate entry-price PnL columns, and `validation/gates_c.py` `C4` compares `EVENT_ENTRY_PRICE` against `trades.entry_price`.

## G. Mutation Tests (M1–M5) & Timing Benchmark
- **M1–M5 (`evidence/v3-12/mutation_tests.log`):**
  - `M1` (TP before SL in temp copy of `sim/leg.py`): `test_sim_leg_spec.py` exited 1 (`2 failed, 25 passed`); `C3` failed with `EXIT_REASON,EXIT_PRICE` mismatch. `VERIFIED`
  - `M2` (`entry_time_ms = signal_close_ms` in temp copy): `C1` failed (`ENTRY_TIME`, 1 violation). `VERIFIED`
  - `M3` (delete 1 row from temp copy of `signal_decisions.parquet`): `C5` failed (`MISSING_SIGNAL_DECISION`, `MANIFEST_COUNT`, 2 violations). `VERIFIED`
  - `M4` (restore `>` in temp copy of `sim/admission.py`): `test_sim_admission_account.py` exited 1 (`1 failed, 4 passed`). `VERIFIED`
  - `M5` (mutate 1 high price in temp copy of M1 bars): `compare_ledgers` flagged exact changed leg (`matched=1`, `diff_cols=['exit_time_ms', ...]`). `VERIFIED`
- **Timing Benchmark (`evidence/v3-12/timing.md`, 3-year real M1 preloaded):**
  - Mode A (`ALL_SIGNALS`): `5.171s -> 3.073s` (`1.68x`)
  - Mode B (`ONE_TRADE_PER_COMBO`): `4.288s -> 2.273s` (`1.89x`)
  - Mode C (`BINGX_PORTFOLIO`): `32.052s -> 28.041s` (`1.14x`)

## H. Test Suite, Lint, & Environment Notes
- **Pytest (`.\.venv\Scripts\python.exe -m pytest autotrade_v3/tests --junitxml=evidence/v3-12/junit/all.xml -q` at `09a3e6d`):** `278 passed in 899.53s (0:14:59)`, exit code `0` (`evidence/v3-12/pytest_all.log`, `evidence/v3-12/junit/all.xml`). `VERIFIED`
- **Ruff (`.\.venv\Scripts\python.exe -m ruff check autotrade_v3` at `09a3e6d`):** `All checks passed!`, exit code `0`. `VERIFIED`
- **Compileall (`.\.venv\Scripts\python.exe -m compileall -q autotrade_v3` at `09a3e6d`):** exit code `0`. `VERIFIED`
- **Static Type Check (`mypy`):** `UNVERIFIED` — `mypy` is not installed in `.venv`.

## I. Verdict & Next Steps
- **Overall V3-12 Status:** `PARTIALLY_VERIFIED` — All 278 unit/integration tests, lint, compileall, M1–M5 mutation tests, parity checkpoints, P2-2/P2-8 change accounting, and Mode A/B 12-gate validations pass (`VERIFIED`). Mode C 12-gate validation reports `C4` `FAIL` (2,328 `EVENT_ENTRY_PRICE` mismatches) as an expected consequence of V3-12 requirement 4.4 (Mode C emits execution events at actual entry price while keeping VWAP `entry_price` in `trades.parquet` until V3-13).
- **Ready for Review:** Review by Claude, acceptance/merge by Owner (no self-merge performed per N10).
