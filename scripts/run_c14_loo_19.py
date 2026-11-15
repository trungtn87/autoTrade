"""Run LOO-19 (C14_DISABLED) across all 9 configurations and reconcile against 20FINAL_FIXED_V1 baseline."""
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
from scripts.c14_filters import C14DisabledFilter

EXPECTED_DATASET_HASH = "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628"
MODES = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
COST_PROFILES = ["IDEAL", "BASE", "STRESS"]

BASELINE_RUNS_ROOT = Path("d:/AutoTrade/runs/fixed_tpsl_v1")
BASELINE_RUN_IDS = {
    ("ALL_SIGNALS", "IDEAL"): "RUN_20261115_040644_20FINAL_FIXED_V1_ALL_SIGNALS_20231101_20261101",
    ("ALL_SIGNALS", "BASE"): "RUN_20261115_041845_20FINAL_FIXED_V1_ALL_SIGNALS_20231101_20261101",
    ("ALL_SIGNALS", "STRESS"): "RUN_20261115_042024_20FINAL_FIXED_V1_ALL_SIGNALS_20231101_20261101",
    ("ONE_TRADE_PER_COMBO", "IDEAL"): "RUN_20261115_042203_20FINAL_FIXED_V1_ONE_TRADE_PER_COMBO_20231101_20261101",
    ("ONE_TRADE_PER_COMBO", "BASE"): "RUN_20261115_042341_20FINAL_FIXED_V1_ONE_TRADE_PER_COMBO_20231101_20261101",
    ("ONE_TRADE_PER_COMBO", "STRESS"): "RUN_20261115_042519_20FINAL_FIXED_V1_ONE_TRADE_PER_COMBO_20231101_20261101",
    ("BINGX_PORTFOLIO", "IDEAL"): "RUN_20261115_042721_20FINAL_FIXED_V1_BINGX_PORTFOLIO_20231101_20261101",
    ("BINGX_PORTFOLIO", "BASE"): "RUN_20261115_043025_20FINAL_FIXED_V1_BINGX_PORTFOLIO_20231101_20261101",
    ("BINGX_PORTFOLIO", "STRESS"): "RUN_20261115_043328_20FINAL_FIXED_V1_BINGX_PORTFOLIO_20231101_20261101",
}


def _profit_factor(pnls: pd.Series) -> float:
    wins = float(pnls[pnls > 0].sum())
    losses = float(-pnls[pnls < 0].sum())
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def _exit_counts(trades_df: pd.DataFrame) -> tuple[int, int]:
    if trades_df.empty or "exit_reason" not in trades_df.columns:
        return 0, 0
    tp = int((trades_df["exit_reason"] == "TP").sum())
    sl = int((trades_df["exit_reason"] == "SL").sum())
    return tp, sl


def main() -> None:
    runs_dir = ROOT / "runs" / "v3_c14_loo_19"
    out_dir = ROOT / "reports" / "v3-c14-loo-h2"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    dl = DataLayer()
    rm = RunManager(runs_dir=runs_dir)
    runner = ExperimentRunner(data_layer=dl, run_manager=rm)
    loo_filter = C14DisabledFilter()

    code_sha, code_dirty, _ = get_git_state()
    print(f"Running LOO-19 (C14_DISABLED) | sim={SIMULATOR_VERSION} | sha={code_sha[:8]} dirty={code_dirty}", flush=True)

    comparison_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    combo_shift_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    val_summary: dict[str, Any] = {
        "experiment": "LOO-19 (C14_DISABLED)",
        "simulator_version": SIMULATOR_VERSION,
        "git_sha": code_sha,
        "code_dirty": code_dirty,
        "expected_dataset_hash": EXPECTED_DATASET_HASH,
        "runs": {},
    }

    for mode in MODES:
        for cost_profile in COST_PROFILES:
            key = f"{mode}__{cost_profile}"
            t0 = time.perf_counter()
            print(f">>> [LOO-19 {key}] running...", flush=True)
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
                filters=[loo_filter],
                use_runstore=False,
            )
            dt = time.perf_counter() - t0
            assert res.manifest.dataset_hash == EXPECTED_DATASET_HASH, (
                f"Dataset hash mismatch: {res.manifest.dataset_hash}"
            )
            assert all(t.leg_type == "SINGLE" for t in res.sim_result.trades), "Non-SINGLE leg found"

            # Load corresponding Baseline 20 run
            base_dir = BASELINE_RUNS_ROOT / BASELINE_RUN_IDS[(mode, cost_profile)]
            base_analysis = json.loads((base_dir / "analysis.json").read_text(encoding="utf-8"))
            base_trades = pd.read_parquet(base_dir / "trades.parquet")
            base_decs = pd.read_parquet(base_dir / "signal_decisions.parquet")

            loo_trades = pd.DataFrame([t.to_dict() for t in res.sim_result.trades])
            loo_decs = pd.DataFrame([d.__dict__ for d in res.sim_result.signal_decisions])

            acct = res.analysis["accounting"]
            pnl = res.analysis["pnl"]
            risk = res.analysis["risk"]
            b_acct = base_analysis["accounting"]
            b_pnl = base_analysis["pnl"]
            b_risk = base_analysis["risk"]

            b_tp, b_sl = _exit_counts(base_trades)
            l_tp, l_sl = _exit_counts(loo_trades)

            b_slip = float(base_trades["slippage_cost"].sum()) if not base_trades.empty else 0.0
            l_slip = float(loo_trades["slippage_cost"].sum()) if not loo_trades.empty else 0.0
            b_pf = _profit_factor(base_trades["net_pnl"]) if not base_trades.empty else 0.0
            l_pf = _profit_factor(loo_trades["net_pnl"]) if not loo_trades.empty else 0.0

            b_net = float(b_pnl["net_pnl"])
            l_net = float(pnl["net_pnl"])
            marginal_c14 = b_net - l_net  # Net PnL Portfolio 20 - Net PnL Portfolio 19

            b_c14_trades = base_trades[(base_trades["symbol"] == "BTC-USDT") & (base_trades["combo_id"] == 104)]
            b_c14_pnl = float(b_c14_trades["net_pnl"].sum()) if not b_c14_trades.empty else 0.0

            m2m_b = b_risk.get("m2m_max_drawdown_dollars")
            m2m_l = risk.get("m2m_max_drawdown_dollars")
            m2m_b_pct = b_risk.get("m2m_max_drawdown_pct")
            m2m_l_pct = risk.get("m2m_max_drawdown_pct")

            comparison_rows.append(
                {
                    "mode": mode,
                    "cost_profile": cost_profile,
                    "loo_run_id": res.run_id,
                    "baseline_run_id": BASELINE_RUN_IDS[(mode, cost_profile)],
                    "base_signals": b_acct["total_signals"],
                    "loo_total_signals": acct["total_signals"],
                    "loo_filtered_c14": int((loo_decs["status"] == "FILTERED").sum()) if not loo_decs.empty else 0,
                    "base_entries": b_acct["accepted_entries"],
                    "loo_entries": acct["accepted_entries"],
                    "base_skipped": b_acct["skipped_by_concurrency"],
                    "loo_skipped": acct["skipped_by_concurrency"],
                    "base_rejected": b_acct["rejected_by_margin"],
                    "loo_rejected": acct["rejected_by_margin"],
                    "base_tp": b_tp,
                    "base_sl": b_sl,
                    "loo_tp": l_tp,
                    "loo_sl": l_sl,
                    "base_fees": round(float(b_pnl["total_fees"]), 4),
                    "loo_fees": round(float(pnl["total_fees"]), 4),
                    "base_slippage": round(b_slip, 4),
                    "loo_slippage": round(l_slip, 4),
                    "base_net_pnl": round(b_net, 4),
                    "loo_net_pnl": round(l_net, 4),
                    "loo_minus_base_pnl": round(l_net - b_net, 4),
                    "marginal_c14_contribution": round(marginal_c14, 4),
                    "base_c14_reported_pnl": round(b_c14_pnl, 4),
                    "base_roi_pct": round(float(b_pnl["roi_pct"]), 4),
                    "loo_roi_pct": round(float(pnl["roi_pct"]), 4),
                    "base_pf": round(b_pf, 4),
                    "loo_pf": round(l_pf, 4),
                    "base_closed_dd": round(float(b_risk["closed_trade_max_drawdown_dollars"]), 4),
                    "loo_closed_dd": round(float(risk["closed_trade_max_drawdown_dollars"]), 4),
                    "base_closed_dd_pct": round(float(b_risk["closed_trade_max_drawdown_pct"]), 4),
                    "loo_closed_dd_pct": round(float(risk["closed_trade_max_drawdown_pct"]), 4),
                    "base_m2m_dd": round(float(m2m_b), 4) if m2m_b is not None else None,
                    "loo_m2m_dd": round(float(m2m_l), 4) if m2m_l is not None else None,
                    "base_m2m_dd_pct": round(float(m2m_b_pct), 4) if m2m_b_pct is not None else None,
                    "loo_m2m_dd_pct": round(float(m2m_l_pct), 4) if m2m_l_pct is not None else None,
                    "validation_status": res.validation_report.overall_status if res.validation_report else "UNKNOWN",
                    "duration_s": round(dt, 2),
                }
            )

            # Reconcile 19 other combos: signal_decisions & trades
            b19_decs = base_decs[~((base_decs["symbol"] == "BTC-USDT") & (base_decs["combo_id"] == 104))].copy()
            l19_decs = loo_decs[~((loo_decs["symbol"] == "BTC-USDT") & (loo_decs["combo_id"] == 104))].copy()
            merged_decs = b19_decs.merge(
                l19_decs,
                on="signal_id",
                how="outer",
                suffixes=("_base", "_loo"),
            )
            diff_decs = merged_decs[
                (merged_decs["status_base"] != merged_decs["status_loo"])
                | (merged_decs["reason_base"].fillna("") != merged_decs["reason_loo"].fillna(""))
            ]
            for _, dr in diff_decs.iterrows():
                ledger_rows.append(
                    {
                        "mode": mode,
                        "cost_profile": cost_profile,
                        "type": "SIGNAL_DECISION_DIFF",
                        "signal_id": dr["signal_id"],
                        "symbol": dr.get("symbol_base") or dr.get("symbol_loo"),
                        "combo_id": dr.get("combo_id_base") or dr.get("combo_id_loo"),
                        "detail": f"base=({dr.get('status_base')},{dr.get('reason_base')}) vs loo=({dr.get('status_loo')},{dr.get('reason_loo')})",
                    }
                )

            b19_tr = base_trades[~((base_trades["symbol"] == "BTC-USDT") & (base_trades["combo_id"] == 104))].copy()
            l19_tr = loo_trades[~((loo_trades["symbol"] == "BTC-USDT") & (loo_trades["combo_id"] == 104))].copy()
            merged_tr = b19_tr.merge(
                l19_tr,
                on="signal_id",
                how="outer",
                suffixes=("_base", "_loo"),
            )
            diff_tr = merged_tr[
                (merged_tr["entry_time_ms_base"].fillna(-1) != merged_tr["entry_time_ms_loo"].fillna(-1))
                | (merged_tr["exit_time_ms_base"].fillna(-1) != merged_tr["exit_time_ms_loo"].fillna(-1))
                | ((merged_tr["net_pnl_base"].fillna(0.0) - merged_tr["net_pnl_loo"].fillna(0.0)).abs() > 1e-6)
            ]
            for _, tr in diff_tr.iterrows():
                ledger_rows.append(
                    {
                        "mode": mode,
                        "cost_profile": cost_profile,
                        "type": "TRADE_DIFF",
                        "signal_id": tr["signal_id"],
                        "symbol": tr.get("symbol_base") or tr.get("symbol_loo"),
                        "combo_id": tr.get("combo_id_base") or tr.get("combo_id_loo"),
                        "detail": (
                            f"base_pnl={tr.get('net_pnl_base')}, loo_pnl={tr.get('net_pnl_loo')}, "
                            f"entry=({tr.get('entry_time_ms_base')} vs {tr.get('entry_time_ms_loo')}), "
                            f"exit=({tr.get('exit_time_ms_base')} vs {tr.get('exit_time_ms_loo')})"
                        ),
                    }
                )

            # Combo-level comparison (all 20 combos)
            b_grp = base_trades.groupby(["symbol", "combo_id", "combo_name"], as_index=False).agg(
                base_trades=("net_pnl", "count"),
                base_net_pnl=("net_pnl", "sum"),
                base_gross_pnl=("gross_pnl", "sum"),
                base_fees=("fee_total", "sum"),
                base_slip=("slippage_cost", "sum"),
            )
            l_grp = loo_trades.groupby(["symbol", "combo_id", "combo_name"], as_index=False).agg(
                loo_trades=("net_pnl", "count"),
                loo_net_pnl=("net_pnl", "sum"),
                loo_gross_pnl=("gross_pnl", "sum"),
                loo_fees=("fee_total", "sum"),
                loo_slip=("slippage_cost", "sum"),
            )
            c_m = b_grp.merge(l_grp, on=["symbol", "combo_id", "combo_name"], how="outer").fillna(0.0)
            c_m["mode"] = mode
            c_m["cost_profile"] = cost_profile
            c_m["diff_loo_minus_base"] = c_m["loo_net_pnl"] - c_m["base_net_pnl"]
            for _, cr in c_m.iterrows():
                combo_shift_rows.append(cr.to_dict())

            # Symbol-level comparison
            for sym in ["BTC-USDT", "ETH-USDT"]:
                bs = base_trades[base_trades["symbol"] == sym]
                ls = loo_trades[loo_trades["symbol"] == sym]
                symbol_rows.append(
                    {
                        "mode": mode,
                        "cost_profile": cost_profile,
                        "symbol": sym,
                        "base_trades": int(len(bs)),
                        "loo_trades": int(len(ls)),
                        "base_net_pnl": round(float(bs["net_pnl"].sum()), 4),
                        "loo_net_pnl": round(float(ls["net_pnl"].sum()), 4),
                        "diff_loo_minus_base": round(float(ls["net_pnl"].sum() - bs["net_pnl"].sum()), 4),
                        "base_pf": round(_profit_factor(bs["net_pnl"]), 4),
                        "loo_pf": round(_profit_factor(ls["net_pnl"]), 4),
                    }
                )

            if res.validation_report:
                val_summary["runs"][key] = {
                    "run_id": res.run_id,
                    "overall_status": res.validation_report.overall_status,
                    "gates": {g: r.status for g, r in res.validation_report.gates.items()},
                }

            print(
                f"<<< [LOO-19 {key}] base={b_net:+.4f}, loo={l_net:+.4f}, "
                f"marginal_C14={marginal_c14:+.4f}, diff_decs={len(diff_decs)}, diff_trades={len(diff_tr)} ({dt:.1f}s)",
                flush=True,
            )

    pd.DataFrame(comparison_rows).to_csv(out_dir / "LOO_19_COMPARISON.csv", index=False)
    pd.DataFrame(ledger_rows).to_csv(out_dir / "loo_19_trade_reconciliation_ledger.csv", index=False)
    pd.DataFrame(combo_shift_rows).to_csv(out_dir / "loo_19_combo_vwap_shift.csv", index=False)
    pd.DataFrame(symbol_rows).to_csv(out_dir / "loo_19_symbol_summary.csv", index=False)
    (out_dir / "loo_19_validation_summary.json").write_text(json.dumps(val_summary, indent=2), encoding="utf-8")
    print("LOO-19 COMPLETE.", flush=True)


if __name__ == "__main__":
    main()
