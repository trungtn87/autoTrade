# V3-15 UI-to-Query-to-Store Mapping (B2)

Every number, table, and data series displayed on the 7 screens of the Research Lab Dashboard is produced by a single pure read-only function in `autotrade_v3/dashboard/queries.py` and traced to its exact source in `runs/lab/store/` (or read-only `catalog/`, `strategy_core/`, `runs/lab/notes.duckdb`).

| Screen | UI Element | `queries.py` Function | Store / Source Artifact |
|---|---|---|---|
| **1. Datasets** | Dataset list, time range, total candles, `dataset_hash`, `partition_count` | `get_datasets_overview(catalog_path, qa_path, store_dir)` | `catalog/research.duckdb` (read-only), `catalog/v3_m1_qa.json` |
| **1. Datasets** | Symbol candle counts & provider breakdown (%) | `get_dataset_provider_breakdown(catalog_path)` | `catalog/research.duckdb` (`candles` table, read-only) |
| **1. Datasets** | Latest Gate A1 & A2 status | `get_last_gate_a_status(store_dir, qa_path)` | `runs/lab/store/*/validation.json` (`gates.A1`, `gates.A2`), `runs/lab/dataset_verify_last.json` |
| **2. Strategies** | Strategy & version list, `parent`, 3 hashes, `is_locked` | `list_strategies_info(params_dir)` | `autotrade_v3/strategy_core/strategies/final20/params/` (`20final_v1.json`, `20final_v1.lock`, `<slug>/params.json`) |
| **2. Strategies** | Combo table (entry, filters, ExitPolicy) | `get_strategy_detail(strategy_ref, params_dir)` | `params.json` (`combos`) via `StrategyCoreRegistry` |
| **2. Strategies** | Parameter diff vs parent | `get_strategy_param_diff(strategy_ref, params_dir)` | `<slug>/param_diff.json` or `compute_param_diff(parent, child)` |
| **3. New Run** | Selectable datasets, strategy versions, modes, ambiguity policies | `get_new_run_options(catalog_path, params_dir, annotations_db)` | `catalog/v3_m1_qa.json`, `StrategyCoreRegistry`, `runs/lab/annotations/annotations.duckdb` (read-only) |
| **3. New Run** | Cost profiles & `UNCONFIRMED` real-data lock | `list_cost_profiles_for_ui()` | `autotrade_v3/sim/cost_profiles.json` (`load_cost_profiles()`) |
| **3. New Run** | Action queue / recent action logs | `get_action_log_entries(log_path)` | `runs/lab/dashboard_actions.log` |
| **4. Run Detail** | Run selector list | `list_store_runs(store_dir)` | `runs/lab/store/index.duckdb` (`read_only=True`) + `validation.json` |
| **4. Run Detail** | Summary KPI cards (Net PnL, PF trade/leg, Win Rate trade/leg, Drawdown, Signal counts, mode, cost profile, ambiguity policy, validation status) | `get_run_summary(run_id, store_dir)` | `run_manifest.json`, `validation.json`, `legs.parquet`, `signals.parquet`, `signal_decisions.parquet`, `equity.parquet` |
| **4. Run Detail** | Equity & drawdown chart (`M1_MTM` vs `CLOSED_TRADE`) | `get_run_equity_curve(run_id, store_dir)` | `equity.parquet` (or `legs.parquet` fallback) |
| **4. Run Detail** | BTC / ETH symbol breakdown tabs | `get_run_symbol_breakdown(run_id, store_dir)` | `legs.parquet`, `signals.parquet`, `signal_decisions.parquet` |
| **4. Run Detail** | Combo table keyed by `(symbol, combo_name)` (with Mode C `net_pnl` & `pnl_own_entry`) | `get_run_combo_breakdown(run_id, store_dir)` | `legs.parquet`, `signals.parquet`, `run_manifest.json` |
| **4. Run Detail** | Monthly breakdown table | `get_run_monthly_breakdown(run_id, store_dir)` | `legs.parquet` |
| **4. Run Detail** | Subwindow breakdown table (24M / 6M / 6M) | `get_run_subwindows_breakdown(run_id, store_dir)` | `legs.parquet` |
| **4. Run Detail** | Exit reason distribution | `get_run_exit_reasons(run_id, store_dir)` | `legs.parquet` |
| **4. Run Detail** | Execution cost component breakdown | `get_run_cost_breakdown(run_id, store_dir)` | `legs.parquet`, `run_manifest.json` |
| **4. Run Detail** | Ambiguity uncertainty interval (if present) | `get_run_uncertainty(run_id, store_dir)` | `uncertainty.json` (in `run_dir` or `runs/lab/uncertainty_<run_id>.json`) |
| **4. Run Detail** | Leg-level drill-down for any aggregate number (`sum == aggregate`) | `get_run_legs_drilldown(run_id, store_dir, ...)` | `legs.parquet` |
| **4. Run Detail** | Decision-level drill-down (`ACCEPTED`, `SKIPPED`, `REJECTED`, `FILTERED`) | `get_run_decisions_drilldown(run_id, store_dir, status)` | `signal_decisions.parquet` |
| **5. Signal Explorer** | All signals table with status, reason, leg count, PnL, ambiguity flag | `list_run_signals(run_id, store_dir, ...)` | `signals.parquet`, `signal_decisions.parquet`, `legs.parquet` |
| **5. Signal Explorer** | Signal chart data (M15 candles, M1 path, Entry, TP, SL, trailing activation, trailing stop curve, leg exits, ambiguity) | `get_signal_detail_and_chart(run_id, signal_id, store_dir, catalog_path)` | `signals.parquet`, `signal_decisions.parquet`, `legs.parquet`, `events.parquet`, `catalog/research.duckdb` (read-only M1) |
| **6. Compare** | Side-by-side metrics & mismatch warnings (dataset, window, cost profile, mode) | `compare_runs_summary(run_ids, store_dir)` | `run_manifest.json`, `validation.json`, `legs.parquet`, `signal_decisions.parquet`, `equity.parquet` |
| **6. Compare** | Overlaid equity curves | `get_compare_equity_curves(run_ids, store_dir)` | `equity.parquet` |
| **6. Compare** | Combo net PnL & delta table keyed by `(symbol, combo_name)` | `get_compare_combo_deltas(run_ids, store_dir)` | `legs.parquet` |
| **6. Compare** | Per-`signal_id` diff summary & rows | `get_compare_signal_diff(run_a, run_b, store_dir)` | `autotrade_v3.runstore.diff.diff_runs` (in-memory, `out_dir=None`) |
| **7. Research Log** | Run list with tags, notes, pinned baseline, validation status | `list_research_log(store_dir, notes_db_path)` | `runs/lab/store/index.duckdb` (`read_only=True`), `validation.json`, `runs/lab/notes.duckdb` |
| **7. Research Log** | Exportable markdown report for a run | `get_run_report_text(run_id, store_dir)` | `report.md` (in `run_dir`) or synthesized from `get_run_summary` |
