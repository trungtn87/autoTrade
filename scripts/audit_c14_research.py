"""Independent audit script: D2 violation breakdown, full ValidationGates, real-data no-lookahead, and clean-tree repro."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry
from autotrade_v3.validation.gates import ValidationGates
from scripts.c14_filters import C14DisabledFilter, IchimokuAtrFilter, build_c14_1h_feature_table

EXPECTED_DATASET_HASH = "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628"
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

FILTER_RECON_PATHS = {
    "analysis.accounting.three_level_reconciliation_pass",
    "analysis.symbols.BTC-USDT.signals",
    "analysis.combos[3].signals",
    "analysis.reconciliation.signals.sum_by_symbol",
    "analysis.reconciliation.signals.sum_by_combo",
    "analysis.reconciliation.signals.max_absolute_diff",
    "analysis.reconciliation.reconciliation_pass",
}


def _classify_d2_violations(val_json: dict[str, Any]) -> tuple[int, int, int, int]:
    d2 = val_json.get("gates", {}).get("D2", {})
    viols = d2.get("violations", [])
    filter_cnt = 0
    m2m_1e4_cnt = 0
    other_cnt = 0
    for v in viols:
        p = v.get("path", "")
        line = v.get("line", "")
        if p in FILTER_RECON_PATHS or line == "reconciliation_parity":
            filter_cnt += 1
        elif p == "analysis.risk.m2m_max_drawdown_dollars" and abs(float(v.get("expected", 0)) - float(v.get("actual", 0))) <= 2e-4:
            m2m_1e4_cnt += 1
        else:
            other_cnt += 1
    return len(viols), filter_cnt, m2m_1e4_cnt, other_cnt


def _collect_latest_runs(runs_root: Path) -> dict[tuple[str, str], Path]:
    found: dict[tuple[str, str], Path] = {}
    for d in sorted(runs_root.glob("RUN_*")):
        mf = d / "run_manifest.json"
        if not mf.exists():
            continue
        m = json.loads(mf.read_text(encoding="utf-8"))
        found[(m["execution_mode"], m["cost_profile"])] = d
    return found


def main() -> None:
    out_dir = ROOT / "reports" / "v3-c14-loo-h2"
    out_dir.mkdir(parents=True, exist_ok=True)

    code_sha, code_dirty, _ = get_git_state()
    print(f"Audit start | sha={code_sha[:8]} | code_dirty={code_dirty}", flush=True)

    # 1. Audit all 36 runs (H0, LOO-19, H2a, H2b)
    variant_roots = {
        "H0": {k: BASELINE_RUNS_ROOT / v for k, v in BASELINE_RUN_IDS.items()},
        "LOO-19": _collect_latest_runs(ROOT / "runs" / "v3_c14_loo_19"),
        "H2a": _collect_latest_runs(ROOT / "runs" / "v3_c14_h2a"),
        "H2b": _collect_latest_runs(ROOT / "runs" / "v3_c14_h2b"),
    }

    matrix_rows: list[dict[str, Any]] = []
    d2_rows: list[dict[str, Any]] = []

    for var, run_map in variant_roots.items():
        for (mode, cp), rdir in sorted(run_map.items()):
            m = json.loads((rdir / "run_manifest.json").read_text(encoding="utf-8"))
            a = json.loads((rdir / "analysis.json").read_text(encoding="utf-8"))
            v = json.loads((rdir / "validation.json").read_text(encoding="utf-8"))
            gates = {g: r.get("status", "MISSING") for g, r in v.get("gates", {}).items()}
            total_v, filt_v, m2m_v, other_v = _classify_d2_violations(v)

            matrix_rows.append(
                {
                    "variant": var,
                    "mode": mode,
                    "cost_profile": cp,
                    "run_id": m["run_id"],
                    "code_sha": m["code_sha"][:8],
                    "code_dirty": m["code_dirty"],
                    "dataset_hash": m["dataset_hash"],
                    "total_signals": m["total_signals"],
                    "filtered_signals": m.get("filtered_signals_count", 0),
                    "entries": m["accepted_entries"],
                    "net_pnl": round(float(a["pnl"]["net_pnl"]), 4),
                    "total_fees": round(float(a["pnl"]["total_fees"]), 4),
                    "overall_status": v.get("overall_status"),
                    "A1": gates.get("A1"),
                    "A2": gates.get("A2"),
                    "B1": gates.get("B1"),
                    "B2": gates.get("B2"),
                    "B3": gates.get("B3"),
                    "C1": gates.get("C1"),
                    "C2": gates.get("C2"),
                    "C3": gates.get("C3"),
                    "C4": gates.get("C4"),
                    "C5": gates.get("C5"),
                    "D1": gates.get("D1"),
                    "D2": gates.get("D2"),
                    "D2_total_viols": total_v,
                    "D2_filter_recon_viols": filt_v,
                    "D2_m2m_1e4_viols": m2m_v,
                    "D2_other_viols": other_v,
                }
            )
            for viol in v.get("gates", {}).get("D2", {}).get("violations", []):
                d2_rows.append(
                    {
                        "variant": var,
                        "mode": mode,
                        "cost_profile": cp,
                        "run_id": m["run_id"],
                        "path": viol.get("path", ""),
                        "line": viol.get("line", ""),
                        "kind": viol.get("kind", ""),
                        "expected": viol.get("expected", ""),
                        "actual": viol.get("actual", ""),
                    }
                )

    pd.DataFrame(matrix_rows).to_csv(out_dir / "AUDIT_36_RUNS_MATRIX.csv", index=False)
    pd.DataFrame(d2_rows).to_csv(out_dir / "D2_VIOLATION_AUDIT.csv", index=False)

    # 2. Real-data Zero Look-Ahead check on BTC-USDT 1H at real C14 signal timestamps
    dl = DataLayer()
    df_1h = dl.query_timeframe("BTC-USDT", "1h", "2023-11-01", "2026-11-01", include_warmup=True)
    feat_full = build_c14_1h_feature_table(df_1h)
    base_sigs = pd.read_parquet(BASELINE_RUNS_ROOT / BASELINE_RUN_IDS[("BINGX_PORTFOLIO", "BASE")] / "signals.parquet")
    c14_sigs = base_sigs[(base_sigs["symbol"] == "BTC-USDT") & (base_sigs["combo_id"] == 104)].sort_values("signal_close_ms")
    sample_indices = np.linspace(0, len(c14_sigs) - 1, 12, dtype=int)
    max_abs_err = 0.0
    checked_real_signals = 0
    for idx in sample_indices:
        cms = int(c14_sigs.iloc[idx]["signal_close_ms"])
        df_trunc = df_1h.loc[feat_full.index <= cms].copy()
        feat_trunc = build_c14_1h_feature_table(df_trunc)
        r_full = feat_full.loc[cms]
        r_trunc = feat_trunc.loc[cms]
        for col in ("tenkan", "kijun", "span_a", "span_b", "atr14", "kumo_thickness_pct", "dist_close_kijun_atr"):
            err = abs(float(r_full[col]) - float(r_trunc[col]))
            max_abs_err = max(max_abs_err, err)
            assert err < 1e-12, f"Real-data look-ahead mismatch at {cms} {col}: {err}"
        checked_real_signals += 1

    # 3. Clean-tree Reproducibility + Full ValidationGates for LOO-19 and H2b (BINGX_PORTFOLIO BASE & STRESS)
    rm_repro = RunManager(runs_dir=ROOT / "runs" / "v3_c14_clean_repro")
    runner = ExperimentRunner(data_layer=dl, run_manager=rm_repro)
    strat = StrategyCoreRegistry.get("20FINAL_FIXED_V1")

    expected_pnls = {
        ("LOO-19", "BASE"): 331.9564,
        ("LOO-19", "STRESS"): 68.6206,
        ("H2b", "BASE"): 348.2177,
        ("H2b", "STRESS"): 66.7973,
    }
    filters_map = {
        "LOO-19": [C14DisabledFilter()],
        "H2b": [IchimokuAtrFilter(feat_full, variant="H2b")],
    }

    repro_results: list[dict[str, Any]] = []
    for var in ("LOO-19", "H2b"):
        for cp in ("BASE", "STRESS"):
            res = runner.run_experiment(
                strategy_id="20FINAL_FIXED_V1",
                mode="BINGX_PORTFOLIO",
                symbols=["BTC-USDT", "ETH-USDT"],
                start="2023-11-01",
                end="2026-11-01",
                initial_equity=1000.0,
                notional_per_trade=100.0,
                slippage_rate=0.0,
                fee_rate_taker=0.0005,
                smoke_test=False,
                is_golden=False,
                cost_profile=cp,
                ambiguity_policy="CONSERVATIVE",
                use_signal_cache=True,
                filters=filters_map[var],
                use_runstore=False,
            )
            net_pnl = round(float(res.analysis["pnl"]["net_pnl"]), 4)
            diff = round(net_pnl - expected_pnls[(var, cp)], 6)
            assert abs(diff) == 0.0, f"Repro mismatch {var} {cp}: {net_pnl} vs {expected_pnls[(var, cp)]}"

            # Run full ValidationGates with run_dir & data_layer
            full_val = ValidationGates.audit_run(
                strategy=strat,
                mode="BINGX_PORTFOLIO",
                symbols=["BTC-USDT", "ETH-USDT"],
                start="2023-11-01",
                end="2026-11-01",
                sim_result=res.sim_result,
                analysis=res.analysis,
                run_dir=res.run_dir,
                smoke_test=False,
                data_layer=dl,
            )
            gates_status = {g: r.status for g, r in full_val.gates.items()}
            repro_results.append(
                {
                    "variant": var,
                    "mode": "BINGX_PORTFOLIO",
                    "cost_profile": cp,
                    "run_id": res.run_id,
                    "code_sha": res.manifest.code_sha,
                    "code_dirty": res.manifest.code_dirty,
                    "dataset_hash": res.manifest.dataset_hash,
                    "net_pnl": net_pnl,
                    "expected_net_pnl": expected_pnls[(var, cp)],
                    "repro_diff": diff,
                    "full_val_overall": full_val.overall_status,
                    "gates": gates_status,
                }
            )
            print(f"CLEAN REPRO [{var} {cp}] dirty={res.manifest.code_dirty} pnl={net_pnl} diff={diff} gates={gates_status}", flush=True)

    audit_summary = {
        "audit_git_sha": code_sha,
        "audit_code_dirty": code_dirty,
        "real_data_lookahead_signals_checked": checked_real_signals,
        "real_data_lookahead_max_abs_err": max_abs_err,
        "clean_tree_repro": repro_results,
    }
    (out_dir / "CLEAN_TREE_REPRO_AUDIT.json").write_text(json.dumps(audit_summary, indent=2), encoding="utf-8")
    print("AUDIT COMPLETE.", flush=True)


if __name__ == "__main__":
    main()
