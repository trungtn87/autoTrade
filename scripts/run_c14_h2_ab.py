"""Nhiệm vụ C: Run A/B Test H2a & H2b across all 9 configurations and compute stability splits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.sim.engine import SIMULATOR_VERSION
from scripts.c14_filters import IchimokuAtrFilter, build_c14_1h_feature_table

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


def _c14_breakdown(c14_df: pd.DataFrame, feat_1h: pd.DataFrame) -> dict[str, Any]:
    if c14_df.empty:
        return {"trades": 0}
    d = c14_df.copy()
    dt = pd.to_datetime(d["entry_time_ms"], unit="ms", utc=True)
    d["year"] = dt.dt.year.astype(str)
    d["quarter"] = dt.dt.tz_convert(None).dt.to_period("Q").astype(str)
    d["signal_close_ms"] = d["signal_id"].str.rsplit("_", n=1).str[-1].astype("int64")
    d = d.merge(feat_1h[["atr14_pct"]], left_on="signal_close_ms", right_index=True, how="left")

    # Overfitting windows (historical robustness / research OOS)
    t_24m_end = pd.Timestamp("2025-11-01", tz="UTC")
    t_6m1_end = pd.Timestamp("2026-05-01", tz="UTC")
    d["window"] = np.where(
        dt < t_24m_end,
        "W1_24M_202311_202510",
        np.where(dt < t_6m1_end, "W2_6M_202511_202604", "W3_6M_202605_202610"),
    )

    def _agg(col: str) -> list[dict[str, Any]]:
        rows = []
        for k, g in d.groupby(col, sort=True):
            n = int(len(g))
            tp = int((g["exit_reason"] == "TP").sum())
            rows.append(
                {
                    col: str(k),
                    "trades": n,
                    "tp": tp,
                    "sl": n - tp,
                    "win_rate_pct": round((tp / n) * 100.0, 2) if n else 0.0,
                    "net_pnl": round(float(g["net_pnl"].sum()), 4),
                    "pf": round(_profit_factor(g["net_pnl"]), 4),
                }
            )
        return rows

    return {
        "trades": int(len(d)),
        "by_side": _agg("side"),
        "by_window": _agg("window"),
        "by_year": _agg("year"),
        "by_quarter": _agg("quarter"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="+", default=["H2a", "H2b"])
    args = parser.parse_args()

    out_dir = ROOT / "reports" / "v3-c14-loo-h2"
    out_dir.mkdir(parents=True, exist_ok=True)
    loo_df = pd.read_csv(out_dir / "LOO_19_COMPARISON.csv")
    loo_lookup = {
        (r["mode"], r["cost_profile"]): r for _, r in loo_df.iterrows()
    }

    dl = DataLayer()
    df_1h = dl.query_timeframe("BTC-USDT", "1h", "2023-11-01", "2026-11-01", include_warmup=True)
    feat_1h = build_c14_1h_feature_table(df_1h)

    code_sha, code_dirty, _ = get_git_state()
    print(f"Running H2 A/B variants={args.variants} | sim={SIMULATOR_VERSION} | sha={code_sha[:8]}", flush=True)

    ab_rows: list[dict[str, Any]] = []
    breakdowns: dict[str, Any] = {}

    # Also include H0 baseline breakdown for reference
    for mode in MODES:
        for cp in COST_PROFILES:
            base_dir = BASELINE_RUNS_ROOT / BASELINE_RUN_IDS[(mode, cp)]
            b_trades = pd.read_parquet(base_dir / "trades.parquet")
            b_c14 = b_trades[(b_trades["symbol"] == "BTC-USDT") & (b_trades["combo_id"] == 104)]
            breakdowns[f"H0__{mode}__{cp}"] = _c14_breakdown(b_c14, feat_1h)

    for variant in args.variants:
        runs_dir = ROOT / "runs" / f"v3_c14_{variant.lower()}"
        runs_dir.mkdir(parents=True, exist_ok=True)
        rm = RunManager(runs_dir=runs_dir)
        runner = ExperimentRunner(data_layer=dl, run_manager=rm)
        flt = IchimokuAtrFilter(feat_1h, variant=variant)

        for mode in MODES:
            for cost_profile in COST_PROFILES:
                key = f"{variant}__{mode}__{cost_profile}"
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
                    cost_profile=cost_profile,
                    ambiguity_policy="CONSERVATIVE",
                    use_signal_cache=True,
                    filters=[flt],
                    use_runstore=False,
                )
                dt = time.perf_counter() - t0
                assert res.manifest.dataset_hash == EXPECTED_DATASET_HASH
                assert all(t.leg_type == "SINGLE" for t in res.sim_result.trades)

                trades_df = pd.DataFrame([t.to_dict() for t in res.sim_result.trades])
                decs_df = pd.DataFrame([d.__dict__ for d in res.sim_result.signal_decisions])
                c14_tr = trades_df[(trades_df["symbol"] == "BTC-USDT") & (trades_df["combo_id"] == 104)].copy()

                base_dir = BASELINE_RUNS_ROOT / BASELINE_RUN_IDS[(mode, cost_profile)]
                b_trades = pd.read_parquet(base_dir / "trades.parquet")
                b_c14 = b_trades[(b_trades["symbol"] == "BTC-USDT") & (b_trades["combo_id"] == 104)].copy()

                # New entries accepted due to concurrency changes
                b_c14_sids = set(b_c14["signal_id"].tolist())
                v_c14_sids = set(c14_tr["signal_id"].tolist())
                new_concurrency_entries = len(v_c14_sids - b_c14_sids)

                acct = res.analysis["accounting"]
                pnl = res.analysis["pnl"]
                risk = res.analysis["risk"]
                loo_row = loo_lookup[(mode, cost_profile)]

                port_net = float(pnl["net_pnl"])
                h0_net = float(loo_row["base_net_pnl"])
                loo_net = float(loo_row["loo_net_pnl"])
                marginal_vs_loo = port_net - loo_net
                diff_vs_h0 = port_net - h0_net

                c14_n = int(len(c14_tr))
                c14_tp = int((c14_tr["exit_reason"] == "TP").sum()) if c14_n else 0
                c14_wr = (c14_tp / c14_n) * 100.0 if c14_n else 0.0
                c14_reported_pnl = float(c14_tr["net_pnl"].sum()) if c14_n else 0.0
                c14_pf = _profit_factor(c14_tr["net_pnl"]) if c14_n else 0.0

                port_pf = _profit_factor(trades_df["net_pnl"]) if not trades_df.empty else 0.0
                m2m_dd = risk.get("m2m_max_drawdown_dollars")
                m2m_dd_pct = risk.get("m2m_max_drawdown_pct")
                filtered_cnt = int((decs_df["status"] == "FILTERED").sum()) if not decs_df.empty else 0

                ab_rows.append(
                    {
                        "variant": variant,
                        "mode": mode,
                        "cost_profile": cost_profile,
                        "run_id": res.run_id,
                        "c14_signals_dropped": filtered_cnt,
                        "c14_trades_remaining": c14_n,
                        "c14_new_concurrency_entries": new_concurrency_entries,
                        "c14_tp": c14_tp,
                        "c14_sl": c14_n - c14_tp,
                        "c14_win_rate_pct": round(c14_wr, 2),
                        "c14_reported_net_pnl": round(c14_reported_pnl, 4),
                        "c14_pf": round(c14_pf, 4),
                        "port_entries": acct["accepted_entries"],
                        "port_net_pnl": round(port_net, 4),
                        "port_roi_pct": round(float(pnl["roi_pct"]), 4),
                        "port_pf": round(port_pf, 4),
                        "port_closed_dd": round(float(risk["closed_trade_max_drawdown_dollars"]), 4),
                        "port_closed_dd_pct": round(float(risk["closed_trade_max_drawdown_pct"]), 4),
                        "port_m2m_dd": round(float(m2m_dd), 4) if m2m_dd is not None else None,
                        "port_m2m_dd_pct": round(float(m2m_dd_pct), 4) if m2m_dd_pct is not None else None,
                        "h0_port_net_pnl": round(h0_net, 4),
                        "loo19_port_net_pnl": round(loo_net, 4),
                        "diff_vs_h0": round(diff_vs_h0, 4),
                        "marginal_vs_loo19": round(marginal_vs_loo, 4),
                        "duration_s": round(dt, 2),
                    }
                )
                breakdowns[key] = _c14_breakdown(c14_tr, feat_1h)
                print(
                    f"<<< [{key}] c14_trades={c14_n} (dropped={filtered_cnt}, new_conc={new_concurrency_entries}), "
                    f"c14_pnl={c14_reported_pnl:+.4f}, port_pnl={port_net:+.4f}, "
                    f"vs_H0={diff_vs_h0:+.4f}, marginal_vs_LOO19={marginal_vs_loo:+.4f} ({dt:.1f}s)",
                    flush=True,
                )

    csv_path = out_dir / "C14_H2_AB_RESULTS.csv"
    json_path = out_dir / "c14_h2_breakdowns.json"
    if csv_path.exists() and set(args.variants) == {"H2ab"}:
        old_df = pd.read_csv(csv_path)
        old_df = old_df[old_df["variant"] != "H2ab"]
        new_df = pd.concat([old_df, pd.DataFrame(ab_rows)], ignore_index=True)
        new_df.to_csv(csv_path, index=False)
        old_bd = json.loads(json_path.read_text(encoding="utf-8"))
        old_bd.update(breakdowns)
        json_path.write_text(json.dumps(old_bd, indent=2), encoding="utf-8")
    else:
        pd.DataFrame(ab_rows).to_csv(csv_path, index=False)
        json_path.write_text(json.dumps(breakdowns, indent=2), encoding="utf-8")
    print("H2 A/B COMPLETE.", flush=True)


if __name__ == "__main__":
    main()
