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
    export_summary_json,
    export_trades_csv,
    export_trades_parquet,
    generate_html_report,
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
    symbol_frames = {}

    print(f"=== AutoTrade V3 Backtest Engine ===")
    print(f"Git commit: {git_sha}")
    print(f"Symbols: {symbols} | Window: {start} to {end} | Mode: {mode}")

    for sym in symbols:
        print(f"\nProcessing {sym}...")
        if smoke_test or catalog_path is None or not catalog_path.exists():
            print(f"  [Notice] Using deterministic synthetic M1 fixture for smoke test...")
            base_px = 65000.0 if "BTC" in sym else 3500.0
            # 5000 15m bars = 75,000 M1 bars (sufficient for WARMUP=3400)
            df_m1 = generate_synthetic_m1(sym, start=start, periods=75000, base_price=base_px, seed=42)
        else:
            print(f"  Loading M1 from catalog {catalog_path}...")
            df_m1 = load_m1_from_catalog(catalog_path, sym, start, end)

        print(f"  Resampling M1 to M15 (strictly no-lookahead)...")
        df_m15 = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        symbol_frames[sym] = df_m15

        print(f"  Replaying 20FINAL signals on {len(df_m15)} M15 bars...")
        sigs = sig_engine.generate_signals(sym, df_m15, only_approved=True, only_warmup_ready=True)
        print(f"  -> Generated {len(sigs)} approved & warmup-ready signals")
        all_signals.extend(sigs)

    all_signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

    manifest = {
        "status": "COMPLETED",
        "generated_at": timestamp_now,
        "git_commit": git_sha,
        "symbols": symbols,
        "window": {"start": start, "end": end},
        "mode": mode,
        "risk_config": {
            "initial_equity": initial_equity,
            "notional_per_trade": notional,
            "slippage_rate": slippage,
        },
        "total_approved_signals": len(all_signals),
    }

    # Execute simulation per symbol or combined
    combined_virtual_trades = []
    combined_portfolio_trades = []

    for sym in symbols:
        sym_sigs = [s for s in all_signals if s.symbol == sym]
        df_bars = symbol_frames[sym]

        if mode in ("virtual", "both"):
            v_trades = simulator.simulate_virtual_trades(sym_sigs, df_bars)
            combined_virtual_trades.extend(v_trades)

        if mode in ("portfolio", "both"):
            p_trades, _ = simulator.simulate_portfolio_trades(sym_sigs, df_bars)
            combined_portfolio_trades.extend(p_trades)

    # Sort combined trades by exit time
    combined_virtual_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id))
    combined_portfolio_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id))

    # Evaluate Virtual Mode
    if combined_virtual_trades:
        v_metrics = calculate_metrics(combined_virtual_trades, initial_equity=initial_equity)
        v_regimes = evaluate_regimes(combined_virtual_trades, initial_equity=initial_equity)
        export_trades_csv(combined_virtual_trades, out_dir / "virtual_trades.csv")
        export_trades_parquet(combined_virtual_trades, out_dir / "virtual_trades.parquet")
        export_summary_json(v_metrics, v_regimes, manifest, out_dir / "virtual_summary.json")
        generate_html_report(v_metrics, v_regimes, {**manifest, "mode": "VIRTUAL_ISOLATED"}, out_dir / "virtual_report.html")

        print("\n--- Virtual Combo Isolated Results ---")
        print(f"Total Trades: {v_metrics.total_trades}")
        print(f"Win Rate: {v_metrics.win_rate_pct:.1f}%")
        print(f"Net Realized PnL: ${v_metrics.net_pnl:,.2f} (ROI: {v_metrics.roi_pct:+.2f}%)")
        print(f"Profit Factor: {v_metrics.profit_factor:.2f}")
        print(f"Max Drawdown: ${v_metrics.max_drawdown_dollars:,.2f} ({v_metrics.max_drawdown_pct:.1f}%)")
        print(f"Total Fees: ${v_metrics.total_fees:,.2f}")
        print(f"Intrabar Ambiguous Trades: {v_metrics.intrabar_ambiguous_trades} ({v_metrics.intrabar_ambiguous_pct:.1f}%)")

    # Evaluate Portfolio Mode
    if combined_portfolio_trades:
        p_metrics = calculate_metrics(combined_portfolio_trades, initial_equity=initial_equity)
        p_regimes = evaluate_regimes(combined_portfolio_trades, initial_equity=initial_equity)
        export_trades_csv(combined_portfolio_trades, out_dir / "portfolio_trades.csv")
        export_trades_parquet(combined_portfolio_trades, out_dir / "portfolio_trades.parquet")
        export_summary_json(p_metrics, p_regimes, manifest, out_dir / "portfolio_summary.json")
        generate_html_report(p_metrics, p_regimes, {**manifest, "mode": "EXCHANGE_HEDGE_MERGED"}, out_dir / "portfolio_report.html")

        print("\n--- Exchange Hedge Merged Portfolio Results ---")
        print(f"Total Trades: {p_metrics.total_trades}")
        print(f"Win Rate: {p_metrics.win_rate_pct:.1f}%")
        print(f"Net Realized PnL: ${p_metrics.net_pnl:,.2f} (ROI: {p_metrics.roi_pct:+.2f}%)")
        print(f"Profit Factor: {p_metrics.profit_factor:.2f}")
        print(f"Max Drawdown: ${p_metrics.max_drawdown_dollars:,.2f} ({p_metrics.max_drawdown_pct:.1f}%)")

    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nAll reports generated successfully in {out_dir}")
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
