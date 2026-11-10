#!/usr/bin/env python3
"""AutoTrade V3 Local Backtest Engine CLI.

Full simulation lifecycle: SIGNAL -> ENTRY -> POSITION OPEN -> TP / SL / TRAILING -> CLOSED.
SIMULATION ONLY — STRICTLY NO LIVE TRADING ORDERS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pandas as pd

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autotrade_v3.analysis.metrics import calculate_metrics
from autotrade_v3.analysis.regimes import evaluate_regimes
from autotrade_v3.engine.candle_loader import (
    generate_synthetic_m1,
    load_m1_from_catalog,
    resample_m1_to_timeframe,
)
from autotrade_v3.engine.execution_simulator import ExecutionSimulator
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import SignalEngine
from autotrade_v3.reports.reporter import (
    export_events_csv,
    export_events_parquet,
    export_summary_json,
    export_trades_csv,
    export_trades_parquet,
    generate_html_report,
    generate_validation_report_md,
)


def get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "UNKNOWN_COMMIT"


def run_backtest(
    catalog_path: Path | None,
    out_dir: Path,
    symbols: list[str],
    mode: str = "both",
    start: str = "2023-11-01",
    end: str = "2026-11-01",
    smoke_test: bool = False,
    initial_equity: float = 1000.0,
    notional: float = 100.0,
    slippage: float = 0.0,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    git_sha = get_git_commit()
    timestamp_now = datetime.now(timezone.utc).isoformat()

    risk_cfg = RiskConfig(
        initial_equity=initial_equity,
        notional_per_trade=notional,
        slippage_rate=slippage,
    )
    simulator = ExecutionSimulator(risk_config=risk_cfg)
    sig_engine = SignalEngine()

    all_signals = []
    symbol_frames_m1 = {}
    symbol_frames_m15 = {}

    print(f"=== AutoTrade V3 Backtest Engine ===")
    print(f"Git commit: {git_sha}")
    print(f"Symbols: {symbols} | Window: {start} to {end} | Mode: {mode}")

    # FIX P1-01: Disallow synthetic fallback in real historical backtest
    data_source_type = "SYNTHETIC_M1_FIXTURE" if smoke_test else "REAL_M1_HYBRID_BINANCE_BINGX"

    if not smoke_test:
        if catalog_path is None:
            print("[P1-01 ERROR] No DuckDB catalog path specified and --smoke-test not set.", file=sys.stderr)
            sys.exit(1)
        if not Path(catalog_path).exists():
            print(f"[P1-01 ERROR] DuckDB catalog not found at: {catalog_path}", file=sys.stderr)
            print("Historical backtest strictly forbids synthetic fallback. Aborting.", file=sys.stderr)
            sys.exit(1)

    for sym in symbols:
        print(f"\nProcessing {sym}...")
        if smoke_test:
            print(f"  [Smoke Test Mode] Generating deterministic synthetic M1 fixture (seed=42)...")
            base_px = 65000.0 if "BTC" in sym else 3500.0
            # 5000 15m bars = 75,000 M1 bars (sufficient for WARMUP=3400)
            df_m1 = generate_synthetic_m1(sym, start=start, periods=75000, base_price=base_px, seed=42)
        else:
            print(f"  Loading real M1 from DuckDB catalog '{catalog_path}'...")
            df_m1 = load_m1_from_catalog(Path(catalog_path), sym, start, end)
            if df_m1.empty:
                print(f"[P1-01 ERROR] Zero M1 candles loaded for {sym} between {start} and {end}!", file=sys.stderr)
                sys.exit(1)
            print(f"  -> Loaded {len(df_m1):,} continuous M1 candles for {sym}")

        symbol_frames_m1[sym] = df_m1

        # FIX P1-02: Signal generation strictly on M15/H1 (resampled from M1)
        print(f"  Resampling M1 to M15 (strictly no-lookahead)...")
        df_m15 = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        symbol_frames_m15[sym] = df_m15

        print(f"  Replaying 20FINAL signals on {len(df_m15):,} M15 bars...")
        sigs = sig_engine.generate_signals(sym, df_m15, only_approved=True, only_warmup_ready=True)
        print(f"  -> Generated {len(sigs):,} approved & warmup-ready signals")
        all_signals.extend(sigs)

    all_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

    manifest = {
        "status": "COMPLETED",
        "generated_at": timestamp_now,
        "git_commit": git_sha,
        "symbols": symbols,
        "window": {"start": start, "end": end},
        "mode": mode,
        "data_source": data_source_type,
        "catalog_path": str(catalog_path) if catalog_path else None,
        "smoke_test": smoke_test,
        "risk_config": {
            "initial_equity": initial_equity,
            "notional_per_trade": notional,
            "slippage_rate": slippage,
        },
        "total_approved_signals": len(all_signals),
    }

    # FIX P1-02 & FIX P1-03: Execute trade simulations strictly on raw M1 candles
    print(f"\n--- Simulating Trades on Raw M1 Bars ---")
    combined_virtual_trades = []

    for sym in symbols:
        sym_sigs = [s for s in all_signals if s.symbol == sym]
        df_m1_sym = symbol_frames_m1[sym]
        print(f"  Simulating {len(sym_sigs)} signals on {sym} with {len(df_m1_sym):,} M1 candles...")
        v_trades = simulator.simulate_virtual_trades(sym_sigs, df_m1_sym)
        combined_virtual_trades.extend(v_trades)
        print(f"  -> {sym}: generated {len(v_trades):,} trade legs")

    # Sort combined virtual trades by exit time
    combined_virtual_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))

    # FIX P1-04: Exchange Hedge Merged Portfolio Mode
    combined_portfolio_trades = []
    df_portfolio_snaps = pd.DataFrame()
    if mode in ("portfolio", "both"):
        print(f"\n--- Simulating Exchange Hedge Merged Portfolio across all symbols ---")
        combined_portfolio_trades, df_portfolio_snaps = simulator.simulate_portfolio_from_trades(combined_virtual_trades)
        combined_portfolio_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
        print(f"  -> Reconciled {len(combined_portfolio_trades):,} portfolio trade legs")

    # Retrieve all execution events (FIX P1-03 audit trail)
    all_events = simulator.get_execution_events()

    # Evaluate Virtual Mode
    v_metrics = None
    v_regimes = {}
    if combined_virtual_trades:
        v_metrics = calculate_metrics(combined_virtual_trades, initial_equity=initial_equity)
        v_regimes = evaluate_regimes(combined_virtual_trades, initial_equity=initial_equity)
        export_trades_csv(combined_virtual_trades, out_dir / "virtual_trades.csv")
        export_trades_parquet(combined_virtual_trades, out_dir / "virtual_trades.parquet")
        export_summary_json(v_metrics, v_regimes, manifest, out_dir / "virtual_summary.json")
        generate_html_report(v_metrics, v_regimes, {**manifest, "mode": "VIRTUAL_ISOLATED"}, out_dir / "virtual_report.html")

        print("\n=== Virtual Combo Isolated Results ===")
        print(f"Total Trades: {v_metrics.total_trades}")
        print(f"Win Rate: {v_metrics.win_rate_pct:.1f}%")
        print(f"Net Realized PnL: ${v_metrics.net_pnl:,.2f} (ROI: {v_metrics.roi_pct:+.2f}%)")
        print(f"Profit Factor: {v_metrics.profit_factor:.2f}")
        print(f"Max Drawdown: ${v_metrics.max_drawdown_dollars:,.2f} ({v_metrics.max_drawdown_pct:.1f}%)")
        print(f"Total Fees: ${v_metrics.total_fees:,.2f}")
        print(f"Intrabar Ambiguous Trades: {v_metrics.intrabar_ambiguous_trades} ({v_metrics.intrabar_ambiguous_pct:.1f}%)")

    # Evaluate Portfolio Mode
    p_metrics = None
    p_regimes = {}
    if combined_portfolio_trades:
        p_metrics = calculate_metrics(combined_portfolio_trades, initial_equity=initial_equity)
        p_regimes = evaluate_regimes(combined_portfolio_trades, initial_equity=initial_equity)
        export_trades_csv(combined_portfolio_trades, out_dir / "portfolio_trades.csv")
        export_trades_parquet(combined_portfolio_trades, out_dir / "portfolio_trades.parquet")
        export_summary_json(p_metrics, p_regimes, manifest, out_dir / "portfolio_summary.json")
        generate_html_report(p_metrics, p_regimes, {**manifest, "mode": "EXCHANGE_HEDGE_MERGED"}, out_dir / "portfolio_report.html")

        print("\n=== Exchange Hedge Merged Portfolio Results ===")
        print(f"Total Trades: {p_metrics.total_trades}")
        print(f"Win Rate: {p_metrics.win_rate_pct:.1f}%")
        print(f"Net Realized PnL: ${p_metrics.net_pnl:,.2f} (ROI: {p_metrics.roi_pct:+.2f}%)")
        print(f"Profit Factor: {p_metrics.profit_factor:.2f}")
        print(f"Max Drawdown: ${p_metrics.max_drawdown_dollars:,.2f} ({p_metrics.max_drawdown_pct:.1f}%)")
        print(f"Total Fees: ${p_metrics.total_fees:,.2f}")

    # Export execution events
    if all_events:
        export_events_csv(all_events, out_dir / "execution_events.csv")
        export_events_parquet(all_events, out_dir / "execution_events.parquet")
        print(f"Exported {len(all_events):,} execution events to Parquet & CSV")

    # Load Gate A and Gate B results for artifact bundle
    qa_path = REPO_ROOT / "catalog" / "v3_m1_qa.json"
    qa_data = None
    if qa_path.exists():
        qa_data = json.loads(qa_path.read_text(encoding="utf-8"))
        (out_dir / "data_validation.json").write_text(json.dumps(qa_data, indent=2) + "\n", encoding="utf-8")

    b1_path = REPO_ROOT / "reports" / "v3_b1_result.json"
    b2_path = REPO_ROOT / "reports" / "v3_b2_result.json"
    parity_bundle = {}
    if b1_path.exists():
        parity_bundle["gate_b1"] = json.loads(b1_path.read_text(encoding="utf-8"))
    if b2_path.exists():
        parity_bundle["gate_b2"] = json.loads(b2_path.read_text(encoding="utf-8"))
    if parity_bundle:
        (out_dir / "signal_parity.json").write_text(json.dumps(parity_bundle, indent=2) + "\n", encoding="utf-8")

    # Export comprehensive validation report markdown
    generate_validation_report_md(
        v_metrics=v_metrics,
        p_metrics=p_metrics,
        manifest=manifest,
        qa_data=qa_data,
        parity_data=parity_bundle,
        out_path=out_dir / "validation_report.md",
    )

    # Standard report.html
    primary_html = out_dir / "portfolio_report.html" if p_metrics else out_dir / "virtual_report.html"
    if primary_html.exists():
        shutil.copyfile(primary_html, out_dir / "report.html")

    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nAll deliverables generated successfully in: {out_dir}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="AutoTrade V3 Local Backtest Engine")
    parser.add_argument("--catalog", type=Path, default=None, help="Path to DuckDB catalog file")
    parser.add_argument("--out-dir", type=Path, default=Path("autotrade_v3_results"), help="Output directory")
    parser.add_argument("--symbols", nargs="+", default=["BTC-USDT", "ETH-USDT"], help="Symbols to test")
    parser.add_argument("--mode", choices=["virtual", "portfolio", "both"], default="both", help="Simulation mode")
    parser.add_argument("--start", default="2023-11-01", help="Start UTC date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-11-01", help="End UTC date (YYYY-MM-DD)")
    parser.add_argument("--smoke-test", action="store_true", help="Run deterministic smoke test with synthetic M1 data")
    parser.add_argument("--initial-equity", type=float, default=1000.0, help="Initial capital in USD")
    parser.add_argument("--notional", type=float, default=100.0, help="Notional position size in USD")
    parser.add_argument("--slippage", type=float, default=0.0, help="Slippage fraction (e.g. 0.0001 for 0.01%)")
    args = parser.parse_args()

    run_backtest(
        catalog_path=args.catalog,
        out_dir=args.out_dir,
        symbols=args.symbols,
        mode=args.mode,
        start=args.start,
        end=args.end,
        smoke_test=args.smoke_test,
        initial_equity=args.initial_equity,
        notional=args.notional,
        slippage=args.slippage,
    )


if __name__ == "__main__":
    main()
