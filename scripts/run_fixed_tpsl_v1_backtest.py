"""Run the 9-configuration 20FINAL_FIXED_V1 backtest suite and export all deliverables."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.sim.engine import SIMULATOR_VERSION

EXPECTED_DATASET_HASH = "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628"
AB_BENCHMARK = {
    ("BINGX_PORTFOLIO", "IDEAL"): 516.3583,
    ("BINGX_PORTFOLIO", "BASE"): 338.01,
    ("BINGX_PORTFOLIO", "STRESS"): 57.2547,
}

MODES = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
COST_PROFILES = ["IDEAL", "BASE", "STRESS"]


def _profit_factor(pnls: pd.Series) -> float:
    wins = float(pnls[pnls > 0].sum())
    losses = float(-pnls[pnls < 0].sum())
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def _group_stats(df: pd.DataFrame, group_cols: list[str]) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for keys, g in df.groupby(group_cols, sort=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        rec: dict[str, Any] = {col: val for col, val in zip(group_cols, keys)}
        n = len(g)
        wins = int((g["net_pnl"] > 0).sum())
        rec.update(
            {
                "trades": int(n),
                "wins": wins,
                "win_rate_pct": round((wins / n) * 100.0, 4) if n > 0 else 0.0,
                "gross_pnl": round(float(g["gross_pnl"].sum()), 4),
                "total_fees": round(float(g["fee_total"].sum()), 4),
                "slippage_cost": round(float(g["slippage_cost"].sum()), 4),
                "net_pnl": round(float(g["net_pnl"].sum()), 4),
                "profit_factor": round(_profit_factor(g["net_pnl"]), 4),
            }
        )
        rows.append(rec)
    return rows


def main() -> None:
    runs_dir = ROOT / "runs" / "fixed_tpsl_v1"
    out_dir = ROOT / "reports" / "fixed_tpsl_v1"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    golden_dm = ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json"
    if golden_dm.exists() and not (runs_dir / "dataset_manifest.json").exists():
        (runs_dir / "dataset_manifest.json").write_text(golden_dm.read_text(encoding="utf-8"), encoding="utf-8")

    dl = DataLayer()
    rm = RunManager(runs_dir=runs_dir)
    runner = ExperimentRunner(data_layer=dl, run_manager=rm)

    code_sha, code_dirty, _ = get_git_state()
    print(f"Running 20FINAL_FIXED_V1 (9 configs) | sim={SIMULATOR_VERSION} | sha={code_sha[:8]} dirty={code_dirty}")

    portfolio_rows: list[dict[str, Any]] = []
    combo_rows: list[dict[str, Any]] = []
    validation_results: dict[str, Any] = {
        "strategy_id": "20FINAL_FIXED_V1",
        "strategy_version": "1",
        "simulator_version": SIMULATOR_VERSION,
        "expected_dataset_hash": EXPECTED_DATASET_HASH,
        "git_sha": code_sha,
        "code_dirty": code_dirty,
        "parity_status": "PARITY_PENDING_LIVE_VERIFICATION",
        "runs": {},
    }
    breakdowns_by_run: dict[str, Any] = {}
    reconciliation_df: pd.DataFrame | None = None

    for mode in MODES:
        for cost_profile in COST_PROFILES:
            t0 = time.perf_counter()
            key = f"{mode}__{cost_profile}"
            print(f">>> [{key}] running...", flush=True)
            res = runner.run_experiment(
                strategy_id="20FINAL_FIXED_V1",
                mode=mode,
                symbols=["BTC-USDT", "ETH-USDT"],
                start="2023-11-01",
                end="2026-11-01",
                initial_equity=1000.0,
                notional_per_trade=100.0,
                slippage_rate=0.0,
                fee_rate_taker=0.0005,
                smoke_test=False,
                is_golden=False,
                cost_profile=cost_profile,
                ambiguity_policy="CONSERVATIVE",
                use_signal_cache=True,
                use_runstore=False,
            )
            dt = time.perf_counter() - t0
            acct = res.analysis["accounting"]
            pnl = res.analysis["pnl"]
            risk = res.analysis["risk"]
            manifest = res.manifest

            assert manifest.dataset_hash == EXPECTED_DATASET_HASH, (
                f"Dataset hash mismatch: {manifest.dataset_hash} != {EXPECTED_DATASET_HASH}"
            )
            assert all(t.leg_type == "SINGLE" for t in res.sim_result.trades), "Found non-SINGLE leg in FIXED_TP_SL!"

            trades_df = pd.DataFrame([t.to_dict() for t in res.sim_result.trades])
            slippage_total = float(trades_df["slippage_cost"].sum()) if not trades_df.empty else 0.0
            n_trades = len(trades_df)
            wins = int((trades_df["net_pnl"] > 0).sum()) if n_trades else 0
            pf = _profit_factor(trades_df["net_pnl"]) if n_trades else 0.0

            bench = AB_BENCHMARK.get((mode, cost_profile))
            portfolio_rows.append(
                {
                    "strategy_id": manifest.strategy_id,
                    "strategy_version": manifest.strategy_version,
                    "mode": mode,
                    "cost_profile": cost_profile,
                    "run_id": res.run_id,
                    "signals": acct["total_signals"],
                    "entries": acct["accepted_entries"],
                    "exits": acct["total_legs"],
                    "skipped_concurrency": acct["skipped_by_concurrency"],
                    "rejected_margin": acct["rejected_by_margin"],
                    "gross_pnl": round(float(pnl["gross_pnl"]), 4),
                    "total_fees": round(float(pnl["total_fees"]), 4),
                    "slippage_cost": round(slippage_total, 4),
                    "net_pnl": round(float(pnl["net_pnl"]), 4),
                    "roi_pct": round(float(pnl["roi_pct"]), 4),
                    "win_rate_pct": round((wins / n_trades) * 100.0, 4) if n_trades else 0.0,
                    "profit_factor": round(pf, 4),
                    "closed_dd_dollars": round(float(risk["closed_trade_max_drawdown_dollars"]), 4),
                    "closed_dd_pct": round(float(risk["closed_trade_max_drawdown_pct"]), 4),
                    "m2m_dd_dollars": round(float(risk.get("m2m_max_drawdown_dollars", 0.0)), 4)
                    if mode == "BINGX_PORTFOLIO"
                    else None,
                    "m2m_dd_pct": round(float(risk.get("m2m_max_drawdown_pct", 0.0)), 4)
                    if mode == "BINGX_PORTFOLIO"
                    else None,
                    "ab_benchmark_net_pnl": bench,
                    "diff_vs_ab_benchmark": round(float(pnl["net_pnl"]) - bench, 6) if bench is not None else None,
                    "validation_status": manifest.validation_status,
                    "parity_status": manifest.parity_status,
                    "duration_s": round(dt, 2),
                }
            )

            # Combo breakdown
            if not trades_df.empty:
                trades_df["year"] = pd.to_datetime(trades_df["entry_time_dt"], utc=True).dt.strftime("%Y")
                trades_df["month"] = pd.to_datetime(trades_df["entry_time_dt"], utc=True).dt.strftime("%Y-%m")
                for c_row in _group_stats(trades_df, ["symbol", "combo_id", "combo_name", "timeframe", "side"]):
                    c_row["mode"] = mode
                    c_row["cost_profile"] = cost_profile
                    combo_rows.append(c_row)

                # Decision reasons breakdown
                dec_df = pd.DataFrame([d.to_dict() for d in res.sim_result.signal_decisions])
                dec_counts = dec_df.groupby(["status", "reason"]).size().to_dict() if not dec_df.empty else {}
                exit_counts = trades_df["exit_reason"].value_counts().to_dict()

                breakdowns_by_run[key] = {
                    "by_symbol": _group_stats(trades_df, ["symbol"]),
                    "by_side": _group_stats(trades_df, ["side"]),
                    "by_year": _group_stats(trades_df, ["year"]),
                    "by_month": _group_stats(trades_df, ["month"]),
                    "exit_reasons": {str(k): int(v) for k, v in exit_counts.items()},
                    "decision_reasons": {f"{k[0]}|{k[1]}": int(v) for k, v in dec_counts.items()},
                }

            # Validation JSON
            val_path = res.run_dir / "validation.json"
            val_data = json.loads(val_path.read_text(encoding="utf-8")) if val_path.exists() else {}
            validation_results["runs"][key] = {
                "run_id": res.run_id,
                "strategy_config_hash": manifest.spec_hash,
                "dataset_hash": manifest.dataset_hash,
                "validation_status": manifest.validation_status,
                "overall_status": val_data.get("overall_status", manifest.validation_status),
                "gates": val_data.get("gates", {}),
            }

            if mode == "BINGX_PORTFOLIO" and cost_profile == "BASE" and not trades_df.empty:
                cols = [
                    "signal_id",
                    "trade_id",
                    "leg_id",
                    "symbol",
                    "combo_id",
                    "combo_name",
                    "timeframe",
                    "side",
                    "leg_type",
                    "entry_px_raw",
                    "entry_px_fill",
                    "entry_price",
                    "qty",
                    "notional",
                    "tp_pct",
                    "sl_pct",
                    "tp_price",
                    "sl_price",
                    "entry_time_ms",
                    "entry_time_dt",
                    "exit_time_ms",
                    "exit_time_dt",
                    "exit_px_trigger",
                    "exit_px_fill",
                    "exit_price",
                    "exit_reason",
                    "fee_entry",
                    "fee_exit",
                    "fee_total",
                    "slippage_cost",
                    "gross_pnl",
                    "net_pnl",
                    "pnl_own_entry",
                    "pnl_vwap",
                ]
                reconciliation_df = trades_df[[c for c in cols if c in trades_df.columns]].copy()

            print(
                f"    Done in {dt:.1f}s | sig={acct['total_signals']} ent={acct['accepted_entries']} "
                f"net=${pnl['net_pnl']:.2f} val={manifest.validation_status}",
                flush=True,
            )

    pd.DataFrame(portfolio_rows).to_csv(out_dir / "comparison_portfolio.csv", index=False)
    pd.DataFrame(combo_rows).to_csv(out_dir / "comparison_20_combos.csv", index=False)
    if reconciliation_df is not None:
        reconciliation_df.to_csv(out_dir / "trade_reconciliation.csv", index=False)
    (out_dir / "breakdowns.json").write_text(json.dumps(breakdowns_by_run, indent=2), encoding="utf-8")
    (out_dir / "validation_results.json").write_text(json.dumps(validation_results, indent=2), encoding="utf-8")
    print(f"\nAll 9 runs & exports complete in {out_dir}")


if __name__ == "__main__":
    main()
