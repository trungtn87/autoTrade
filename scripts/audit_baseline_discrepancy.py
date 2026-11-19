#!/usr/bin/env python3
"""Independent Baseline Discrepancy Audit: 690d92ed vs eec2c9de vs 18d287eb."""

from __future__ import annotations

import hashlib
import json
import pickle
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(r"D:\AutoTrade")
OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "r2_integration_verification"
R1_CACHE = ROOT / "runs" / "lab" / "v3_lab_4" / "weakest10_18variants_20261119_004500" / "cache" / "weakest10_immutable_cache_v1.pkl"

EVAL_START_MS = int(pd.Timestamp("2023-11-01 00:00:00", tz="UTC").timestamp() * 1000)
EVAL_END_MS = int(pd.Timestamp("2026-11-01 00:00:00", tz="UTC").timestamp() * 1000)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def worker_run(repo_dir: Path, out_pq: Path, out_meta: Path, version_arg: str | None = None) -> None:
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))

    from autotrade_v3.lab.v3_lab_4_loader import load_v3_lab_4_config
    from autotrade_v3.sim.costs import CostModel
    from autotrade_v3.sim.engine import RiskConfig
    from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy
    from round2_engine import Round2ResearchSimulator

    cfg = load_v3_lab_4_config(version=version_arg) if version_arg else load_v3_lab_4_config()
    strat = V3Lab4Strategy(version=version_arg) if version_arg else V3Lab4Strategy()

    with R1_CACHE.open("rb") as f:
        cache = pickle.load(f)
    m15_dict = cache["m15_dict"]
    m1_arrays = cache["m1_arrays_eval"]

    sigs = [s for s in strat.generate_signals(m15_dict) if EVAL_START_MS <= int(s.signal_close_ms) < EVAL_END_MS]
    ctrl = strat.build_c15_h3_controller(m15_dict["ETH-USDT"])
    cm = CostModel.from_profile_name("BASE", is_real_data=True)
    risk = RiskConfig(initial_equity=1000.0, notional_per_trade=100.0, cost_profile="BASE", cost_model=cm)
    sim = Round2ResearchSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE", c15_controller=ctrl)
    res = sim.run_with_arrays(mode="BINGX_PORTFOLIO", signals=sigs, arrays_by_sym=m1_arrays)

    df = pd.DataFrame([asdict(t) for t in res.trades])
    df.to_parquet(out_pq, index=False)

    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()
    meta = {
        "repo_dir": str(repo_dir),
        "git_sha": git_sha,
        "config_version": cfg.get("version"),
        "config_hash_field": cfg.get("config_hash"),
        "v3_lab_4_config_sha256": sha256_file(repo_dir / "autotrade_v3" / "lab" / "v3_lab_4_config.json"),
        "v3_lab_4_py_sha256": sha256_file(repo_dir / "autotrade_v3" / "strategy" / "v3_lab_4.py"),
        "signal_spec_hash": strat.get_signal_spec_hash(),
        "spec_hash": strat.get_spec_hash(),
        "trades": len(df),
        "net_pnl": round(float(df["net_pnl"].sum()), 4),
        "own_net_pnl_sum": round(float(df["pnl_own_entry"].sum()), 4),
        "m2m_max_dd_usd": float(res.summary_dict.get("m2m_max_drawdown_dollars", 0.0)),
    }
    out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def main() -> None:
    if "--worker" in sys.argv:
        i = sys.argv.index("--worker")
        repo_dir = Path(sys.argv[i + 1])
        out_pq = Path(sys.argv[i + 2])
        out_meta = Path(sys.argv[i + 3])
        ver = sys.argv[i + 4] if len(sys.argv) > i + 4 else None
        worker_run(repo_dir, out_pq, out_meta, ver)
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wt690 = Path(r"D:\AutoTrade\_wt_690d92ed")
    wteec = Path(r"D:\AutoTrade\_wt_eec2c9de")

    runs = [
        ("690d92ed", wt690, None),
        ("eec2c9de", wteec, None),
        ("18d287eb_v400", ROOT, "4.0.0"),
    ]
    metas = {}
    dfs = {}
    for label, rdir, ver in runs:
        pq = OUT_DIR / f"_audit_{label}.parquet"
        mj = OUT_DIR / f"_audit_{label}.json"
        cmd = [sys.executable, str(__file__), "--worker", str(rdir), str(pq), str(mj)]
        if ver:
            cmd.append(ver)
        subprocess.run(cmd, cwd=rdir, check=True)
        metas[label] = json.loads(mj.read_text(encoding="utf-8"))
        dfs[label] = pd.read_parquet(pq)
        print(label, "-> trades:", metas[label]["trades"], "net_pnl:", metas[label]["net_pnl"], "config_hash:", metas[label]["config_hash_field"])

    # 1. Check 18d287eb_v400 vs eec2c9de (must be 0.0 diff -> base.py/types.py/engine.py/leg.py zero impact)
    df_eec = dfs["eec2c9de"].sort_values(["entry_time_ms", "symbol", "combo_id", "side"]).reset_index(drop=True)
    df_18d = dfs["18d287eb_v400"].sort_values(["entry_time_ms", "symbol", "combo_id", "side"]).reset_index(drop=True)
    assert len(df_eec) == len(df_18d)
    max_diff_engine = float(np.max(np.abs(df_eec["net_pnl"].values - df_18d["net_pnl"].values)))
    print("Engine changes impact check (18d287eb_v400 vs eec2c9de): max_abs_pnl_diff =", max_diff_engine)

    # 2. Combo-by-combo diff between 690d92ed (5932 trades) and eec2c9de (5339 trades)
    df_690 = dfs["690d92ed"]
    g690 = df_690.groupby(["symbol", "combo_id", "combo_name"]).agg(
        trades_690=("net_pnl", "count"),
        own_pnl_690=("pnl_own_entry", "sum"),
        vwap_pnl_690=("net_pnl", "sum"),
    )
    geec = df_eec.groupby(["symbol", "combo_id", "combo_name"]).agg(
        trades_eec=("net_pnl", "count"),
        own_pnl_eec=("pnl_own_entry", "sum"),
        vwap_pnl_eec=("net_pnl", "sum"),
    )
    comp = g690.join(geec, how="outer").fillna(0).reset_index()
    comp["delta_trades"] = comp["trades_eec"].astype(int) - comp["trades_690"].astype(int)
    comp["delta_own_pnl"] = (comp["own_pnl_eec"] - comp["own_pnl_690"]).round(4)
    comp["delta_vwap_pnl"] = (comp["vwap_pnl_eec"] - comp["vwap_pnl_690"]).round(4)
    comp.to_csv(OUT_DIR / "BASELINE_COMBO_DIFF.csv", index=False)

    print("\nCombos with trade count or Own PnL differences (690d92ed -> eec2c9de):")
    diff_c = comp[(comp["delta_trades"] != 0) | (comp["delta_own_pnl"].abs() > 1e-6)]
    print(diff_c.to_string(index=False))
    print("Sum delta_trades:", int(comp["delta_trades"].sum()), "Sum delta_own_pnl:", round(float(comp["delta_own_pnl"].sum()), 4))

    # 3. Trade-level diff (signal_ids removed/added)
    s690 = set(df_690["signal_id"])
    seec = set(df_eec["signal_id"])
    removed = df_690[df_690["signal_id"].isin(s690 - seec)][["signal_id", "symbol", "combo_id", "combo_name", "side", "entry_time_dt", "exit_reason", "pnl_own_entry"]].copy()
    removed["diff_type"] = "REMOVED_IN_EEC2C9DE"
    added = df_eec[df_eec["signal_id"].isin(seec - s690)][["signal_id", "symbol", "combo_id", "combo_name", "side", "entry_time_dt", "exit_reason", "pnl_own_entry"]].copy()
    added["diff_type"] = "ADDED_IN_EEC2C9DE"
    tdiff = pd.concat([removed, added], ignore_index=True)
    tdiff.to_csv(OUT_DIR / "BASELINE_TRADE_DIFF_593.csv", index=False)

    summary = {
        "metas": metas,
        "engine_zero_impact_max_diff": max_diff_engine,
        "total_delta_trades": int(comp["delta_trades"].sum()),
        "removed_trades_count": len(removed),
        "added_trades_count": len(added),
        "changed_combos_count": len(diff_c),
    }
    (OUT_DIR / "BASELINE_DISCREPANCY_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
