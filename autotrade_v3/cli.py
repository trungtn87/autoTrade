"""Unified CLI for AutoTrade V3 Offline Research Lab Core."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager
from autotrade_v3.strategy.registry import StrategyRegistry

SUPPORTED_MODES = [
    {
        "mode": "ALL_SIGNALS",
        "alias": ["MODE_A", "A"],
        "description": "Unconstrained signal evaluation. Every approved signal is entered as an independent trade (no concurrency skips, no margin limit).",
    },
    {
        "mode": "ONE_TRADE_PER_COMBO",
        "alias": ["MODE_B", "B", "VIRTUAL_ISOLATED"],
        "description": "Combo-isolated execution. Each combo can have at most one active trade. Overlapping signals are recorded as SKIPPED_BY_CONCURRENCY.",
    },
    {
        "mode": "BINGX_PORTFOLIO",
        "alias": ["MODE_C", "C", "EXCHANGE_HEDGE_MERGED"],
        "description": "Exchange hedge mode merged positions with VWAP entry blending, strict account margin checks, and continuous M2M intra-trade equity tracking.",
    },
]


def cmd_datasets(args: argparse.Namespace) -> None:
    """List available offline datasets and verify integrity."""
    dl = DataLayer(catalog_path=args.catalog)
    manifest = dl.get_manifest()
    print("=== AutoTrade V3 Offline Research Datasets ===")
    print(f"Name:             {manifest.name}")
    print(f"Catalog Path:     {manifest.catalog_path}")
    print(f"QA Manifest:      {manifest.qa_path}")
    print(f"Time Range:       {manifest.start_utc} to {manifest.end_utc} (3 Years)")
    print(f"Total Candles:    {manifest.total_candles:,} M1 bars")
    print(f"Partitions:       {manifest.partition_count} Parquet files")
    print(f"Data SHA256:      {manifest.data_manifest_hash}")
    print(f"Integrity Status: {manifest.status}")
    print("\nSymbols:")
    for sym, count in manifest.symbol_candle_counts.items():
        print(f"  - {sym}: {count:,} M1 candles")

    if args.verify:
        print("\nRunning comprehensive integrity audit (Gate A)...")
        res = dl.verify_integrity()
        if res["passed"]:
            print("  -> Audit Passed: 0 gaps, 0 duplicates, 0 OHLC violations.")
        else:
            print("  -> Audit FAILED:", res.get("issues"))


def cmd_strategies(args: argparse.Namespace) -> None:
    """List registered strategies."""
    strategies = StrategyRegistry.list_strategies()
    print("=== AutoTrade V3 Registered Strategies ===")
    for s in strategies:
        print(f"- Strategy ID: {s['strategy_id']} (v{s['version']})")
        print(f"  Required Timeframes: {s['required_timeframes']}")
        print(f"  Signal Spec Hash:    {s.get('signal_spec_hash', s['config_hash'])}")
        print(f"  Exit Spec Hash:      {s.get('exit_spec_hash', s['config_hash'])}")
        print(f"  Spec Hash:           {s.get('spec_hash', s['config_hash'])}")


def cmd_strategy_clone(args: argparse.Namespace) -> None:
    """Clone an existing strategy specification into a new versioned research strategy."""
    from autotrade_v3.strategy_core.registry import clone_strategy_params

    params_dir = Path(args.params_dir) if getattr(args, "params_dir", None) else None
    res = clone_strategy_params(
        from_ref=args.from_ref,
        to_ref=args.to_ref,
        params_dir=params_dir,
    )
    print(f"=== Strategy Cloned: {res['strategy_id']}@{res['version']} ===")
    print(f"- Parent:           {res['parent']}")
    print(f"- Params File:      {res['params_path']}")
    print(f"- Diff File:        {res['diff_path']}")
    print(f"- Signal Spec Hash: {res['hashes']['signal_spec_hash']}")
    print(f"- Exit Spec Hash:   {res['hashes']['exit_spec_hash']}")
    print(f"- Spec Hash:        {res['hashes']['spec_hash']}")


def cmd_modes(args: argparse.Namespace) -> None:
    """List available execution modes."""
    print("=== AutoTrade V3 Execution Modes ===")
    for m in SUPPORTED_MODES:
        print(f"\n[{m['mode']}] (Aliases: {', '.join(m['alias'])})")
        print(f"  {m['description']}")


def cmd_backtest(args: argparse.Namespace) -> None:
    """Execute backtest in selected mode."""
    is_golden = getattr(args, "golden", False)
    out_dir = args.out_dir
    if is_golden and out_dir == "runs/lab":
        out_dir = "runs/golden/v3-08"

    if is_golden:
        from autotrade_v3.lab.run_manager import get_git_state
        code_sha, code_dirty, diff_sha256 = get_git_state()
        if code_dirty or code_sha == "UNKNOWN_COMMIT" or not code_sha:
            print("ERROR: --golden run rejected: git working tree is dirty or git state cannot be determined.", file=sys.stderr)
            sys.exit(1)

    dl = DataLayer(catalog_path=args.catalog)
    rm = RunManager(runs_dir=out_dir)
    runner = ExperimentRunner(data_layer=dl, run_manager=rm)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    modes_to_run = [args.mode] if args.mode.upper() != "ALL" else ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]

    for m in modes_to_run:
        print("\n=======================================================")
        print(f"Starting Backtest | Mode: {m} | Strategy: {args.strategy}")
        print(f"Window: {args.start} to {args.end} | Symbols: {symbols}")
        print("=======================================================")

        filters = []
        filter_context = None
        if getattr(args, "annotation_source", None):
            from autotrade_v3.external.annotations import AnnotationStore
            from autotrade_v3.filters import AnnotationGuardFilter, FilterContext

            filters = [AnnotationGuardFilter(source=args.annotation_source)]
            filter_context = FilterContext(annotation_store=AnnotationStore())

        res = runner.run_experiment(
            strategy_id=args.strategy,
            mode=m,
            symbols=symbols,
            start=args.start,
            end=args.end,
            initial_equity=args.initial_equity,
            notional_per_trade=args.notional,
            slippage_rate=args.slippage,
            smoke_test=args.smoke_test,
            is_golden=is_golden,
            cost_profile=args.cost_profile,
            ambiguity_policy=args.ambiguity_policy,
            use_signal_cache=(not getattr(args, "no_cache", False)),
            filters=filters or None,
            filter_context=filter_context,
            use_runstore=(not is_golden),
        )

        pnl = res.analysis["pnl"]
        acct = res.analysis["accounting"]
        wr = res.analysis["win_rate"]
        risk = res.analysis["risk"]

        pf_display = f"{pnl['net_profit_factor']:.2f}" if isinstance(pnl.get('net_profit_factor'), (int, float)) else str(pnl.get('net_profit_factor', pnl.get('profit_factor', 'N/A')))
        gross_pf_display = f"{pnl['gross_profit_factor']:.2f}" if isinstance(pnl.get('gross_profit_factor'), (int, float)) else str(pnl.get('gross_profit_factor', 'N/A'))
        closed_dd = risk.get('closed_trade_max_drawdown_dollars', 0.0)
        m2m_dd = risk.get('m2m_max_drawdown_dollars')
        dd_display = f"${m2m_dd:,.2f} (Continuous M2M)" if m2m_dd is not None else f"${closed_dd:,.2f} (Closed-trade)"

        print(f"\nRun Complete: {res.run_id}")
        print(f"Artifacts Saved: {res.run_dir}")
        print(f"- Validation:     {res.manifest.validation_status}")
        print(f"- Total Signals:  {acct['total_signals']:,}")
        print(f"- Accepted Trades: {acct['accepted_entries']:,} ({acct['total_legs']:,} legs)")
        print(f"- Skipped:        {acct['skipped_by_concurrency']:,} (concurrency)")
        print(f"- Rejected:       {acct['rejected_by_margin']:,} (margin)")
        print(f"- Net PnL:        ${pnl['net_pnl']:,.2f} (ROI: {pnl['roi_pct']:+.2f}%)")
        print(f"- Net Profit Factor:   {pf_display}")
        print(f"- Gross Profit Factor: {gross_pf_display}")
        print(f"- Win Rate:       {wr['trade_win_rate_pct']:.1f}% (Trade), {wr['leg_win_rate_pct']:.1f}% (Leg)")
        print(f"- Max Drawdown:   {dd_display}")


def cmd_runs(args: argparse.Namespace) -> None:
    """List historical backtest runs."""
    rm = RunManager(runs_dir=args.out_dir)
    runs = rm.list_runs()
    if not runs:
        print("No lab backtest runs found.")
        return

    print("=== AutoTrade V3 Research Lab Runs ===")
    fmt = "{:<32} {:<18} {:<18} {:<10} {:<10} {:<8} {:<8}"
    print(fmt.format("Run ID", "Strategy", "Mode", "Net PnL", "ROI", "WinRate", "PF"))
    print("-" * 110)
    for r in runs:
        print(fmt.format(
            r.get("run_id", "")[:31],
            r.get("strategy_id", "")[:17],
            r.get("execution_mode", "")[:17],
            f"${r.get('net_pnl', 0.0):,.2f}",
            f"{r.get('roi_pct', 0.0):+.2f}%",
            f"{r.get('trade_win_rate_pct', 0.0):.1f}%",
            f"{r.get('profit_factor', 0.0):.2f}",
        ))


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare multiple backtest runs."""
    rm = RunManager(runs_dir=args.out_dir)
    run_dicts = []
    for rid in args.run_ids:
        r = rm.get_run(rid)
        if not r:
            print(f"Warning: Run ID '{rid}' not found in catalog.", file=sys.stderr)
            continue
        run_dicts.append(r)

    if not run_dicts:
        print("No valid runs to compare.")
        return

    comp = ResearchAnalyzer.compare_runs(run_dicts)
    table = comp["comparison_table"]

    print("=== AutoTrade V3 Research Lab Cross-Run Comparison ===")
    fmt = "{:<28} {:<18} {:<8} {:<8} {:<8} {:<10} {:<9} {:<8} {:<10}"
    print(fmt.format("Run ID", "Mode", "Signals", "Trades", "Legs", "Net PnL", "ROI", "WinRate", "Max DD"))
    print("-" * 115)
    for row in table:
        print(fmt.format(
            row["run_id"][:27],
            row["mode"][:17],
            str(row["total_signals"]),
            str(row["accepted_entries"]),
            str(row["total_legs"]),
            f"${row['net_pnl']:,.2f}",
            f"{row['roi_pct']:+.2f}%",
            f"{row['win_rate_trade_pct']:.1f}%",
            f"${row['max_drawdown_dollars']:,.2f}",
        ))


def cmd_verify(args: argparse.Namespace) -> None:
    """Verify run artifacts via the 12 independent validation gates."""
    from autotrade_v3.validation.runner import run_validation

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"Error: Run directory does not exist: {run_dir}", file=sys.stderr)
        sys.exit(1)

    no_write = getattr(args, "no_write", False)
    output_dir = getattr(args, "output_dir", None)

    print(f"Starting Independent Validation Gates verification on: {run_dir}")
    report = run_validation(
        run_dir=run_dir,
        dataset_root=args.dataset_root,
        n_cutoffs=args.cutoffs,
        seed=args.seed,
        write_result=not no_write,
        output_dir=output_dir,
    )

    print(f"\n=======================================================")
    print(f"Validation Complete | Run ID: {report.run_id}")
    print(f"Overall Status: {report.overall_status}")
    print(f"Mode: {report.execution_mode} | Real Market Data: {report.is_real_data}")
    print(f"Duration: {report.total_duration_s:.2f}s | Seed: {report.seed} | Cutoffs: {report.n_cutoffs}")
    print(f"=======================================================\n")

    fmt = "{:<6} {:<28} {:<16} {:<14} {:<12} {:<10}"
    print(fmt.format("Gate", "Name", "Status", "Checked/Total", "Violations", "Duration"))
    print("-" * 90)

    for gid, g in report.gates.items():
        chk_str = f"{g['rows_checked']:,}/{g['rows_total']:,}"
        dur_str = f"{g['duration_s']:.2f}s"
        print(fmt.format(
            gid,
            g["name"][:27],
            g["status"],
            chk_str,
            str(g["violation_count"]),
            dur_str,
        ))
        if g.get("reason"):
            print(f"       Reason: {g['reason']}")

    if report.failure_reasons:
        print("\nFailure details:")
        for fr in report.failure_reasons:
            print(f"  - {fr}")

    if not no_write:
        if output_dir:
            out_target = Path(output_dir) / "validation.json"
            print(f"\nValidation artifact written to: {out_target}")
        elif "runs/golden/v3-08" in str(run_dir).replace("\\", "/"):
            print("\nNote: Golden run validation artifact was not written to run_dir (golden runs are immutable). Pass --output-dir to save.")
        else:
            out_target = run_dir / "validation.json"
            print(f"\nValidation artifact written to: {out_target}")
    else:
        print("\nRead-only mode (--no-write): validation artifact not written.")

    if report.overall_status in ("FAILED", "UNVERIFIED_DIRTY"):
        print(f"\nVerification FAILED with status: {report.overall_status}", file=sys.stderr)
        sys.exit(1)
    elif report.overall_status == "PARTIALLY_VERIFIED":
        print(
            "\nVerification Result: PARTIALLY_VERIFIED "
            "(All executable gates passed; Gate B3 remains INCONCLUSIVE by design pending V3-11)."
        )
        sys.exit(0)
    elif report.overall_status in ("VERIFIED", "SYNTHETIC"):
        print(f"\nVerification Result: {report.overall_status}")
        sys.exit(0)
    else:
        print(f"\nVerification Result: {report.overall_status}", file=sys.stderr)
        sys.exit(1)


def cmd_report(args: argparse.Namespace) -> None:
    """View report for a specific run."""
    rm = RunManager(runs_dir=args.out_dir)
    r = rm.get_run(args.run_id)
    if not r:
        print(f"Error: Run '{args.run_id}' not found.", file=sys.stderr)
        sys.exit(1)

    rep_file = Path(r["path"]) / "report.md"
    if rep_file.exists():
        print(rep_file.read_text(encoding="utf-8"))
    else:
        print(json.dumps(r["analysis"], indent=2, default=str))


def run_uncertainty_analysis(
    *,
    strategy_id: str,
    mode: str,
    symbols: list[str],
    start: str,
    end: str,
    cost_profile: str,
    initial_equity: float = 1000.0,
    notional_per_trade: float = 100.0,
    catalog_path: str = "catalog/research.duckdb",
    smoke_test: bool = False,
) -> dict[str, Any]:
    """Run full simulation under CONSERVATIVE and OPTIMISTIC and compute uncertainty interval."""
    import pandas as pd
    from autotrade_v3.engine.candle_loader import datetime_index_to_ms, generate_synthetic_m1, resample_m1_to_timeframe
    from autotrade_v3.engine.risk_manager import RiskConfig
    from autotrade_v3.sim.costs import CostModel
    from autotrade_v3.sim.engine import UnifiedSimulator

    cm = CostModel.from_profile_name(cost_profile, is_real_data=(not smoke_test))
    strategy = StrategyRegistry.get(strategy_id)
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)

    dl = DataLayer(catalog_path=catalog_path)
    candles_m1_eval: dict[str, pd.DataFrame] = {}
    strategy_input: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if smoke_test:
            base_px = 65000.0 if "BTC" in sym else 3500.0
            df_m1 = generate_synthetic_m1(sym, start=start, periods=75000, base_price=base_px, seed=42)
            t_ms = datetime_index_to_ms(df_m1.index)
            candles_m1_eval[sym] = df_m1.loc[(t_ms >= start_ms) & (t_ms < end_ms)]
            strategy_input[sym] = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        else:
            df_m1 = dl.query_m1(sym, start, end, include_warmup=True)
            t_ms = datetime_index_to_ms(df_m1.index)
            candles_m1_eval[sym] = df_m1.loc[(t_ms >= start_ms) & (t_ms < end_ms)]
            strategy_input[sym] = dl.query_timeframe(sym, "15m", start, end, include_warmup=True)

    all_signals = strategy.generate_signals(
        data=strategy_input, symbols=symbols, only_approved=True, only_warmup_ready=True
    )
    max_m1_by_sym = (
        {s: int(datetime_index_to_ms(df.index)[-1]) for s, df in candles_m1_eval.items() if not df.empty}
        if smoke_test
        else {}
    )
    eval_signals = [
        s for s in all_signals
        if s.signal_close_ms + 1 >= start_ms
        and s.signal_close_ms + 1 < (
            min(end_ms, max_m1_by_sym[s.symbol] + 1)
            if smoke_test and s.symbol in max_m1_by_sym
            else end_ms
        )
    ]
    eval_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

    risk_cfg = RiskConfig(
        initial_equity=initial_equity,
        notional_per_trade=notional_per_trade,
        cost_profile=cm.profile.name,
        cost_model=cm,
    )
    res_con = UnifiedSimulator(risk_config=risk_cfg, cost_model=cm, ambiguity_policy="CONSERVATIVE").run(
        mode=mode, signals=eval_signals, candle_dict=candles_m1_eval
    )
    res_opt = UnifiedSimulator(risk_config=risk_cfg, cost_model=cm, ambiguity_policy="OPTIMISTIC").run(
        mode=mode, signals=eval_signals, candle_dict=candles_m1_eval
    )

    def _sig_summary(res) -> dict[str, tuple[str, float, tuple[tuple[str, str, int], ...]]]:
        by_sig_legs: dict[str, list[Any]] = {}
        for t in res.trades:
            by_sig_legs.setdefault(t.signal_id, []).append(t)
        out: dict[str, tuple[str, float, tuple[tuple[str, str, int], ...]]] = {}
        for d in res.signal_decisions:
            legs = sorted(by_sig_legs.get(d.signal_id, []), key=lambda x: x.leg_id)
            net_s = round(sum(x.net_pnl for x in legs), 10)
            sig_tup = tuple((x.leg_id, x.exit_reason, int(x.exit_time_ms)) for x in legs)
            out[d.signal_id] = (d.status, net_s, sig_tup)
        return out

    map_con = _sig_summary(res_con)
    map_opt = _sig_summary(res_opt)
    changed_signals = sorted(
        sid for sid in set(map_con) | set(map_opt)
        if map_con.get(sid) != map_opt.get(sid)
    )

    amb_types = ("TP_AND_SL", "SL_AND_TRAIL_ACT", "TRAIL_ACT_AND_CB", "TRAIL_PEAK_AND_STOP")
    amb_counts_con = {k: sum(1 for t in res_con.trades if t.ambiguity_type == k) for k in amb_types}
    amb_counts_opt = {k: sum(1 for t in res_opt.trades if t.ambiguity_type == k) for k in amb_types}
    net_con = float(sum(t.net_pnl for t in res_con.trades))
    net_opt = float(sum(t.net_pnl for t in res_opt.trades))

    return {
        "mode": res_con.mode,
        "strategy_id": strategy_id,
        "cost_profile": cm.profile.name,
        "cost_profile_hash": cm.profile.profile_hash,
        "ambiguous_legs_conservative": amb_counts_con,
        "ambiguous_legs_optimistic": amb_counts_opt,
        "net_pnl_conservative": net_con,
        "net_pnl_optimistic": net_opt,
        "net_pnl_delta": net_opt - net_con,
        "changed_signal_count": len(changed_signals),
        "changed_signal_ids": changed_signals,
    }


def cmd_uncertainty(args: argparse.Namespace) -> None:
    """Compute CONSERVATIVE vs OPTIMISTIC ambiguity uncertainty interval."""
    if not args.smoke_test and not args.cost_profile:
        print("ERROR: --cost-profile is required on real data (no implicit default).", file=sys.stderr)
        sys.exit(1)
    profile = args.cost_profile or "IDEAL"
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    out = run_uncertainty_analysis(
        strategy_id=args.strategy,
        mode=args.mode,
        symbols=symbols,
        start=args.start,
        end=args.end,
        cost_profile=profile,
        initial_equity=args.initial_equity,
        notional_per_trade=args.notional,
        catalog_path=args.catalog,
        smoke_test=args.smoke_test,
    )
    print(json.dumps({k: v for k, v in out.items() if k != "changed_signal_ids"}, indent=2))
    print(f"Changed signal_ids: {out['changed_signal_count']}")
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(json.dumps(out, indent=2), encoding="utf-8")
    if args.out_signals:
        Path(args.out_signals).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_signals).write_text("\n".join(out["changed_signal_ids"]) + "\n", encoding="utf-8")


def cmd_store_list(args: argparse.Namespace) -> None:
    from autotrade_v3.runstore import RunStore

    store = RunStore(store_dir=args.store_dir)
    rows = store.index.list_runs(limit=args.limit)
    if not rows:
        print("No runs in RunStore.")
        return
    for r in rows:
        print(
            f"{r['run_id']} | {r['execution_mode']} | {r['cost_profile'] or 'N/A'} | "
            f"{r['ambiguity_policy']} | net={r['net_pnl']:.4f} | {r['validation_status']}"
        )


def cmd_store_show(args: argparse.Namespace) -> None:
    from autotrade_v3.runstore import RunStore

    store = RunStore(store_dir=args.store_dir)
    rdir = store.resolve_run_dir(args.run_id)
    manifest = json.loads((rdir / "run_manifest.json").read_text(encoding="utf-8"))
    print(json.dumps(manifest, indent=2))


def cmd_store_reindex(args: argparse.Namespace) -> None:
    from autotrade_v3.runstore import RunStore

    store = RunStore(store_dir=args.store_dir)
    n = store.index.reindex()
    print(f"Reindexed {n} runs into {store.index.db_path}")


def cmd_diff(args: argparse.Namespace) -> None:
    from autotrade_v3.runstore import DiffInvariantError, RunStore, diff_runs

    store = RunStore(store_dir=args.store_dir)
    dir_a = store.resolve_run_dir(args.run_a)
    dir_b = store.resolve_run_dir(args.run_b)
    try:
        res = diff_runs(dir_a, dir_b, out_dir=args.out)
    except DiffInvariantError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    s = res.summary
    print(f"Diff {s['run_a']} -> {s['run_b']} | Total Signals: {s['total_signals']}")
    print(f"Net(A)={s['net_a']:.4f}  Net(B)={s['net_b']:.4f}  Delta={s['expected_delta_net']:.4f}  InvariantDiff={s['invariant_diff']:.3e}")
    for cat, stats in s["by_category"].items():
        if stats["count"] > 0 or cat == "UNEXPLAINED":
            print(f"  {cat:<12} count={stats['count']:>6}  delta_net={stats['delta_net']:>+12.4f}")
    if s["unexplained_count"] > 0:
        print(f"WARNING: UNEXPLAINED rows = {s['unexplained_count']}")


def cmd_cache_stats(args: argparse.Namespace) -> None:
    from autotrade_v3.runstore import SignalCache

    cache = SignalCache(cache_dir=args.cache_dir)
    print(json.dumps(cache.stats(), indent=2))


def cmd_annotations_import(args: argparse.Namespace) -> None:
    from autotrade_v3.external.annotations import AnnotationStore

    store = AnnotationStore(db_path=args.db_path)
    res = store.import_file(
        args.file,
        source=args.source,
        as_of_verified=(not args.unverified),
    )
    print(json.dumps(res, indent=2))


def cmd_import_ledger(args: argparse.Namespace) -> None:
    from autotrade_v3.external.ledger_import import import_external_ledger
    from autotrade_v3.runstore import RunStore

    store = RunStore(store_dir=args.store_dir)
    res = import_external_ledger(
        args.file,
        args.mapping,
        store=store,
        initial_equity=args.initial_equity,
        run_label=args.label,
    )
    print(json.dumps({"run_id": res.run_id, "run_dir": str(res.run_dir), "reproducible_hit": res.reproducible_hit}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autotrade_v3",
        description="AutoTrade V3 Offline Research Lab Core CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Datasets
    p_ds = subparsers.add_parser("datasets", help="List available offline datasets")
    p_ds.add_argument("--catalog", default="catalog/research.duckdb", help="DuckDB catalog path")
    p_ds.add_argument("--verify", action="store_true", help="Run deep integrity audit")
    p_ds.set_defaults(func=cmd_datasets)

    # Strategies
    p_st = subparsers.add_parser("strategies", help="List registered strategies")
    p_st.set_defaults(func=cmd_strategies)

    # Strategy management (clone)
    p_strat = subparsers.add_parser("strategy", help="Manage versioned strategies (e.g. clone)")
    strat_sub = p_strat.add_subparsers(dest="strategy_command", required=True)
    p_clone = strat_sub.add_parser("clone", help="Clone an existing strategy specification")
    p_clone.add_argument("--from", dest="from_ref", required=True, help="Source strategy ref (e.g. 20FINAL@1)")
    p_clone.add_argument("--to", dest="to_ref", required=True, help="Target strategy ref (e.g. EXP_01@1)")
    p_clone.add_argument("--params-dir", default=None, help="Optional parameter root directory")
    p_clone.set_defaults(func=cmd_strategy_clone)

    # Modes
    p_md = subparsers.add_parser("modes", help="List execution simulation modes")
    p_md.set_defaults(func=cmd_modes)

    # Backtest
    p_bt = subparsers.add_parser("backtest", help="Execute simulation backtest")
    p_bt.add_argument("--strategy", default="20FINAL_FIXED_V1", help="Strategy ID")
    p_bt.add_argument("--mode", default="ALL_SIGNALS", choices=["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO", "all", "ALL"], help="Execution mode")
    p_bt.add_argument("--symbols", default="BTC-USDT,ETH-USDT", help="Comma-separated symbols")
    p_bt.add_argument("--start", default="2023-11-01", help="Start date (UTC)")
    p_bt.add_argument("--end", default="2026-11-01", help="End date (UTC)")
    p_bt.add_argument("--initial-equity", type=float, default=1000.0, help="Initial account equity")
    p_bt.add_argument("--notional", type=float, default=100.0, help="Notional position size per trade ($)")
    p_bt.add_argument("--slippage", type=float, default=0.0, help="Per-side slippage rate (e.g. 0.0001 = 0.01%%)")
    p_bt.add_argument("--catalog", default="catalog/research.duckdb", help="DuckDB catalog path")
    p_bt.add_argument("--out-dir", default="runs/lab", help="Directory to store run artifacts")
    p_bt.add_argument("--smoke-test", action="store_true", help="Use synthetic fixture for fast verification")
    p_bt.add_argument("--golden", action="store_true", help="Enforce golden baseline run: require clean git working tree")
    p_bt.add_argument("--cost-profile", default=None, help="Cost profile name (required for real data, e.g. IDEAL, BASE, STRESS)")
    p_bt.add_argument("--ambiguity-policy", default="CONSERVATIVE", choices=["LEGACY", "CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH"], help="Intrabar ambiguity resolution policy")
    p_bt.add_argument("--no-cache", action="store_true", help="Disable signal cache and force recomputation")
    p_bt.add_argument("--annotation-source", default=None, help="Optional annotation source name for AnnotationGuardFilter")
    p_bt.set_defaults(func=cmd_backtest)

    # Uncertainty interval (CONSERVATIVE vs OPTIMISTIC)
    p_unc = subparsers.add_parser("uncertainty", help="Run CONSERVATIVE vs OPTIMISTIC ambiguity interval")
    p_unc.add_argument("--strategy", default="20FINAL_FIXED_V1", help="Strategy ID")
    p_unc.add_argument("--mode", default="ALL_SIGNALS", choices=["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"], help="Execution mode")
    p_unc.add_argument("--symbols", default="BTC-USDT,ETH-USDT", help="Comma-separated symbols")
    p_unc.add_argument("--start", default="2023-11-01", help="Start date (UTC)")
    p_unc.add_argument("--end", default="2026-11-01", help="End date (UTC)")
    p_unc.add_argument("--initial-equity", type=float, default=1000.0, help="Initial account equity")
    p_unc.add_argument("--notional", type=float, default=100.0, help="Notional position size per trade ($)")
    p_unc.add_argument("--catalog", default="catalog/research.duckdb", help="DuckDB catalog path")
    p_unc.add_argument("--cost-profile", default=None, help="Cost profile name (required for real data)")
    p_unc.add_argument("--smoke-test", action="store_true", help="Use synthetic fixture")
    p_unc.add_argument("--out-json", default=None, help="Optional JSON output path")
    p_unc.add_argument("--out-signals", default=None, help="Optional path for changed signal_id list")
    p_unc.set_defaults(func=cmd_uncertainty)

    # Runs
    p_rn = subparsers.add_parser("runs", help="List historical research runs")
    p_rn.add_argument("--out-dir", default="runs/lab", help="Directory where runs are stored")
    p_rn.set_defaults(func=cmd_runs)

    # Compare
    p_cp = subparsers.add_parser("compare", help="Compare multiple runs")
    p_cp.add_argument("run_ids", nargs="+", help="Run IDs to compare")
    p_cp.add_argument("--out-dir", default="runs/lab", help="Directory where runs are stored")
    p_cp.set_defaults(func=cmd_compare)

    # Report
    p_rp = subparsers.add_parser("report", help="Display run report")
    p_rp.add_argument("run_id", help="Run ID to display")
    p_rp.add_argument("--out-dir", default="runs/lab", help="Directory where runs are stored")
    p_rp.set_defaults(func=cmd_report)

    # Verify (V3-10 Independent Validation Gates)
    p_vf = subparsers.add_parser("verify", help="Verify run artifacts with 12 independent validation gates")
    p_vf.add_argument("run_dir", help="Run directory to verify (e.g. runs/golden/v3-08/ALL_SIGNALS)")
    p_vf.add_argument("--dataset-root", default=".", help="Root directory containing dataset partition files")
    p_vf.add_argument("--cutoffs", type=int, default=20, help="Number of cutoff points for B2 no-lookahead check")
    p_vf.add_argument("--seed", type=int, default=20261110, help="Seed for cutoff sampling")
    p_vf.add_argument("--output-dir", default=None, help="Directory to write validation.json (avoids writing into run_dir)")
    p_vf.add_argument("--no-write", action="store_true", help="Read-only mode: do not write validation.json anywhere")
    p_vf.set_defaults(func=cmd_verify)

    # Store (V3-14 RunStore)
    p_store = subparsers.add_parser("store", help="Manage RunStore (list, show, reindex)")
    store_sub = p_store.add_subparsers(dest="store_command", required=True)
    p_sl = store_sub.add_parser("list", help="List runs in RunStore")
    p_sl.add_argument("--store-dir", default="runs/lab/store", help="RunStore directory")
    p_sl.add_argument("--limit", type=int, default=None, help="Optional row limit")
    p_sl.set_defaults(func=cmd_store_list)

    p_ss = store_sub.add_parser("show", help="Show run_manifest.json for a run")
    p_ss.add_argument("run_id", help="Run ID or directory path")
    p_ss.add_argument("--store-dir", default="runs/lab/store", help="RunStore directory")
    p_ss.set_defaults(func=cmd_store_show)

    p_sr = store_sub.add_parser("reindex", help="Rebuild index.duckdb from store directories")
    p_sr.add_argument("--store-dir", default="runs/lab/store", help="RunStore directory")
    p_sr.set_defaults(func=cmd_store_reindex)

    # Diff (V3-14 per-signal diff)
    p_df = subparsers.add_parser("diff", help="Compare two runs per signal_id")
    p_df.add_argument("run_a", help="Run A ID or directory")
    p_df.add_argument("run_b", help="Run B ID or directory")
    p_df.add_argument("--store-dir", default="runs/lab/store", help="RunStore directory")
    p_df.add_argument("--out", default=None, help="Output directory for diff.parquet and diff_summary.json")
    p_df.set_defaults(func=cmd_diff)

    # Cache (V3-14 SignalCache)
    p_ca = subparsers.add_parser("cache", help="Inspect SignalCache")
    ca_sub = p_ca.add_subparsers(dest="cache_command", required=True)
    p_cs = ca_sub.add_parser("stats", help="Show SignalCache statistics")
    p_cs.add_argument("--cache-dir", default="runs/lab/cache/signals", help="SignalCache directory")
    p_cs.set_defaults(func=cmd_cache_stats)

    # Annotations (V3-14 offline annotation store)
    p_an = subparsers.add_parser("annotations", help="Manage offline annotations")
    an_sub = p_an.add_subparsers(dest="annotations_command", required=True)
    p_ai = an_sub.add_parser("import", help="Import local JSON/CSV annotations")
    p_ai.add_argument("file", help="Local JSON or CSV annotation file")
    p_ai.add_argument("--source", default=None, help="Annotation source name")
    p_ai.add_argument("--unverified", action="store_true", help="Mark imported rows AS_OF_UNVERIFIED")
    p_ai.add_argument("--db-path", default="runs/lab/annotations/annotations.duckdb", help="Annotation DuckDB path")
    p_ai.set_defaults(func=cmd_annotations_import)

    # External ledger import
    p_il = subparsers.add_parser("import-ledger", help="Import an external backtest ledger into RunStore")
    p_il.add_argument("file", help="External ledger CSV or Parquet file")
    p_il.add_argument("--mapping", required=True, help="JSON column mapping file (canonical -> source)")
    p_il.add_argument("--store-dir", default="runs/lab/store", help="RunStore directory")
    p_il.add_argument("--initial-equity", type=float, default=1000.0, help="Initial equity reference")
    p_il.add_argument("--label", default="external", help="Strategy/source label")
    p_il.set_defaults(func=cmd_import_ledger)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
