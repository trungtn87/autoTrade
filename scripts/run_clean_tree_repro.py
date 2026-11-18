"""Clean-tree reproducibility & dual-validation (Standard + Filter-Aware) runner for H0, LOO-19, H2b."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.filter_aware_validation import EXPECTED_DATASET_HASH, validate_filtered_run
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.sim.engine import SIMULATOR_VERSION
from autotrade_v3.validation.runner import run_validation
from scripts.c14_filters import C14DisabledFilter, IchimokuAtrFilter, build_c14_1h_feature_table

MODES = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
COST_PROFILES = ["IDEAL", "BASE", "STRESS"]


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_sha256(df: pd.DataFrame) -> str:
    if df.empty:
        return hashlib.sha256(b"EMPTY").hexdigest()
    cols = sorted(df.columns.tolist())
    norm = df[cols].sort_values([c for c in ("exit_time_ms", "entry_time_ms", "signal_id") if c in cols]).reset_index(drop=True)
    payload = norm.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    # 1. Strict clean-tree preflight
    raw_status = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    code_sha, code_dirty, _ = get_git_state()
    if code_dirty:
        raise RuntimeError(f"Clean-tree check FAILED before run: code_dirty={code_dirty}, status={raw_status}")

    print(f"CLEAN-TREE PREFLIGHT OK | sha={code_sha} | code_dirty={code_dirty}", flush=True)

    runs_root = ROOT / "runs" / "v3_c14_clean_acceptance"
    runs_root.mkdir(parents=True, exist_ok=True)
    golden_dm = ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json"
    if not golden_dm.exists():
        raise FileNotFoundError(f"Missing golden dataset_manifest.json: {golden_dm}")

    # Clear any stale signal cache inside this worktree so signals are computed fresh from M1/M15
    sig_cache_dir = ROOT / "runs" / "signal_cache"
    if sig_cache_dir.exists():
        shutil.rmtree(sig_cache_dir)

    dl = DataLayer()
    df_1h = dl.query_timeframe("BTC-USDT", "1h", "2023-11-01", "2026-11-01", include_warmup=True)
    feat_1h = build_c14_1h_feature_table(df_1h)
    kumo_by_ms = {int(cms): float(val) for cms, val in feat_1h["kumo_thickness_pct"].items()}

    def _oracle_loo(row: pd.Series) -> tuple[bool, str]:
        return True, "LOO_C14_DISABLED"

    def _oracle_h2b(row: pd.Series) -> tuple[bool, str]:
        cms = int(str(row["signal_id"]).rsplit("_", 1)[-1])
        kp = kumo_by_ms.get(cms, float("nan"))
        if not pd.notna(kp) or kp < 0.005:
            return True, "H2b_THIN_KUMO"
        return False, ""

    variants: list[tuple[str, list[Any], Any]] = [
        ("H0", [], None),
        ("LOO-19", [C14DisabledFilter()], _oracle_loo),
        ("H2b", [IchimokuAtrFilter(feat_1h, variant="H2b")], _oracle_h2b),
    ]

    manifest_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    baseline_signals_df: pd.DataFrame | None = None

    for var_name, flt_list, oracle_fn in variants:
        v_runs_dir = runs_root / var_name.lower().replace("-", "_")
        v_runs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(golden_dm, v_runs_dir / "dataset_manifest.json")

        rm = RunManager(runs_dir=v_runs_dir)
        runner = ExperimentRunner(data_layer=dl, run_manager=rm)

        for mode in MODES:
            for cp in COST_PROFILES:
                key = f"{var_name}__{mode}__{cp}"
                t0 = time.perf_counter()
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
                    cost_profile=cp,
                    ambiguity_policy="CONSERVATIVE",
                    use_signal_cache=True,
                    filters=flt_list,
                    use_runstore=False,
                )
                assert res.manifest.code_dirty is False, f"Dirty tree detected during {key}!"
                assert res.manifest.dataset_hash == EXPECTED_DATASET_HASH

                if baseline_signals_df is None:
                    baseline_signals_df = pd.read_parquet(res.run_dir / "signals.parquet")

                # Run full standard 12-gate validation with dataset_manifest.json present
                std_rep = run_validation(res.run_dir, write_result=True)
                # Run independent Filter-Aware validation (FA1..FA5)
                fa_rep = validate_filtered_run(
                    res.run_dir,
                    variant=var_name,
                    baseline_signals_df=baseline_signals_df,
                    expected_drop_fn=oracle_fn,
                )

                trades_df = pd.read_parquet(res.run_dir / "trades.parquet")
                c14_tr = trades_df[(trades_df["symbol"] == "BTC-USDT") & (trades_df["combo_id"] == 104)]
                tp_cnt = int((trades_df["exit_reason"] == "TP").sum()) if not trades_df.empty else 0
                sl_cnt = int((trades_df["exit_reason"] == "SL").sum()) if not trades_df.empty else 0
                c14_tp = int((c14_tr["exit_reason"] == "TP").sum()) if not c14_tr.empty else 0
                c14_sl = int((c14_tr["exit_reason"] == "SL").sum()) if not c14_tr.empty else 0

                dt = time.perf_counter() - t0
                gates_std = {g: r["status"] for g, r in std_rep.gates.items()}
                gates_fa = {g: r["status"] for g, r in fa_rep.gates.items()}

                manifest_rows.append(
                    {
                        "variant": var_name,
                        "mode": mode,
                        "cost_profile": cp,
                        "run_id": res.run_id,
                        "git_sha": res.manifest.code_sha,
                        "code_dirty": res.manifest.code_dirty,
                        "dataset_hash": res.manifest.dataset_hash,
                        "spec_hash": res.manifest.spec_hash,
                        "simulator_version": SIMULATOR_VERSION,
                        "signal_source": res.manifest.signal_source,
                        "trades_sha256": _file_sha256(res.run_dir / "trades.parquet"),
                        "trades_content_sha256": _content_sha256(trades_df),
                        "orig_signals": fa_rep.summary["orig_signals"],
                        "kept_signals": fa_rep.summary["kept_signals"],
                        "dropped_signals": fa_rep.summary["dropped_signals"],
                        "entries": res.manifest.accepted_entries,
                        "tp": tp_cnt,
                        "sl": sl_cnt,
                        "c14_trades": int(len(c14_tr)),
                        "c14_tp": c14_tp,
                        "c14_sl": c14_sl,
                        "c14_net_pnl": round(float(c14_tr["net_pnl"].sum()), 4) if not c14_tr.empty else 0.0,
                        "port_net_pnl": round(float(res.analysis["pnl"]["net_pnl"]), 4),
                        "total_fees": round(float(res.analysis["pnl"]["total_fees"]), 4),
                        "closed_dd": round(float(res.analysis["risk"]["closed_trade_max_drawdown_dollars"]), 4),
                        "m2m_dd": res.analysis["risk"].get("m2m_max_drawdown_dollars"),
                        "duration_s": round(dt, 2),
                    }
                )
                gate_rows.append(
                    {
                        "variant": var_name,
                        "mode": mode,
                        "cost_profile": cp,
                        "run_id": res.run_id,
                        "code_dirty": res.manifest.code_dirty,
                        "std_overall": std_rep.overall_status,
                        **gates_std,
                        "fa_overall": fa_rep.overall_status,
                        **gates_fa,
                        "d2_filter_recon_viols": fa_rep.summary["d2_filter_recon_viols"],
                        "d2_m2m_1e4_viols": fa_rep.summary["d2_m2m_1e4_viols"],
                    }
                )
                print(
                    f"<<< [{key}] pnl={res.analysis['pnl']['net_pnl']:+.4f} "
                    f"std={std_rep.overall_status} fa={fa_rep.overall_status} "
                    f"C1={gates_std.get('C1')} D2={gates_std.get('D2')} ({dt:.1f}s)",
                    flush=True,
                )

    stage_dir = runs_root / "acceptance_stage"
    stage_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(manifest_rows).to_csv(stage_dir / "RUN_MANIFESTS_AND_HASHES.csv", index=False)
    pd.DataFrame(gate_rows).to_csv(stage_dir / "FINAL_GATE_MATRIX.csv", index=False)
    print("CLEAN-TREE REPRODUCTION COMPLETE. Staged at:", stage_dir, flush=True)


if __name__ == "__main__":
    main()
