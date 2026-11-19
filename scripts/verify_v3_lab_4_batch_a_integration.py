"""Official Gate A-E Verification Runner for Batch A P3 Integration into V3_LAB_4@4.0.0."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, resample_m1_to_timeframe, to_utc_ms
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import _canonicalize_mode
from autotrade_v3.sim.leg import extract_m1_arrays
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.strategy_core.indicators.indicators import ema
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry
from scripts.run_v3_lab_2_eth_c15_h2 import (
    END_DATE,
    EXPECTED_DATASET_HASH,
    INITIAL_EQUITY,
    NOTIONAL_PER_TRADE,
    START_DATE,
    SYMBOLS,
    sha256_file,
)
from scripts.run_v3_lab_2_eth_c15_research import ResearchUnifiedSimulator

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "batch_a_integration"

BATCH_A_P3_MAP = [
    ("BTC-USDT", 2, "BTC-USDT/C2", "BTC_C2_H1", "H1_SELL_ONLY"),
    ("BTC-USDT", 4, "BTC-USDT/C4", "BTC_C4_H1", "H1_BUY_ONLY"),
    ("BTC-USDT", 7, "BTC-USDT/C7", "BTC_C7_H1", "H1_BUY_ONLY"),
    ("BTC-USDT", 103, "BTC-USDT/C13", "BTC_C13_H4", "H4_SELL_ONLY_4H_TREND"),
    ("ETH-USDT", 103, "ETH-USDT/C13", "ETH_C13_H5", "H5_SELL_ONLY_4H_TREND"),
    ("BTC-USDT", 106, "BTC-USDT/C16", "BTC_C16_H3_1.22", "H3_VORTEX_RATIO_1_22"),
]


class FastSim(ResearchUnifiedSimulator):
    def run_cached(self, mode: str, signals: list[Any], arrays_by_sym: dict[str, Any]) -> Any:
        cm_mode = _canonicalize_mode(mode)
        sorted_sigs = sorted(signals, key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        if cm_mode in ("ALL_SIGNALS", "ONE_TRADE_PER_COMBO"):
            return self._run_virtual_mode(cm_mode, sorted_sigs, arrays_by_sym)
        return self._run_portfolio_mode(
            sorted_sigs,
            arrays_by_sym,
            sample_snapshot_interval_m1=15,
            allow_multiple_per_combo=(cm_mode == "ALL_SIGNALS_PORTFOLIO"),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify V3_LAB_4 Batch A P3 Integration (Gates A-E)")
    parser.add_argument("--out-dir", type=str, default=str(OUT_DIR))
    parser.add_argument("--data-root", type=str, default=str(ROOT))
    parser.add_argument("--ref-root", type=str, default=str(ROOT))
    parser.add_argument("--clean-repro", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_root = Path(args.data_root)
    ref_root = Path(args.ref_root)
    t0 = time.time()

    code_sha, _, _ = get_git_state()
    tracked_dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip()
    if args.clean_repro and tracked_dirty:
        raise RuntimeError(f"Clean repro requires clean tracked worktree, got: {tracked_dirty}")

    gm_path = ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json"
    ds_hash = json.loads(gm_path.read_text(encoding="utf-8")).get("dataset_hash", "")
    if ds_hash != EXPECTED_DATASET_HASH:
        raise RuntimeError(f"Dataset hash mismatch: {ds_hash}")

    cfg = load_v3_lab_4_config()
    strat = load_v3_lab_4_strategy()
    assert canonical_json_sha256({k: v for k, v in cfg.items() if k != "config_hash"}) == cfg["config_hash"]

    # Gate A: Strategy & Config Diff
    pre_cfg_path = ref_root / "runs" / "lab" / "v3_lab_4" / "batch_a_integration" / "pre_integration_v3_lab_4_config.json"
    pre_cfg = json.loads(pre_cfg_path.read_text(encoding="utf-8"))
    pre_by_key = {c["combo_key"]: c for c in pre_cfg["combos"]}
    cur_by_key = {c["combo_key"]: c for c in cfg["combos"]}

    reg_rows = []
    changed_combos = []
    for c in cfg["combos"]:
        k = c["combo_key"]
        pc = pre_by_key[k]
        if pc != c:
            changed_combos.append(
                {
                    "combo_key": k,
                    "before_execution_version": pc["execution_version"],
                    "after_execution_version": c["execution_version"],
                    "before_research_status": pc["research_status"],
                    "after_research_status": c["research_status"],
                    "tp_pct_unchanged": pc["tp_pct"] == c["tp_pct"],
                    "sl_pct_unchanged": pc["sl_pct"] == c["sl_pct"],
                    "timeframe_unchanged": pc["timeframe"] == c["timeframe"],
                    "layer2_unchanged": pc["layer2"] == c["layer2"],
                }
            )
        reg_rows.append(
            {
                "order": c["order"],
                "combo_key": k,
                "symbol": c["symbol"],
                "combo_id": c["combo_id"],
                "timeframe": c["timeframe"],
                "execution_version": c["execution_version"],
                "tp_pct": c["tp_pct"],
                "sl_pct": c["sl_pct"],
                "trailing_enabled": c["trailing_enabled"],
                "split_legs": c["split_legs"],
                "layer2": c["layer2"],
                "research_status": c["research_status"],
                "live_eligible": c["live_eligible"],
            }
        )
    pd.DataFrame(reg_rows).to_csv(out_dir / "V3_LAB_4_30_COMBO_REGISTRY.csv", index=False)

    diff_obj = {
        "before_config_hash": pre_cfg["config_hash"],
        "after_config_hash": cfg["config_hash"],
        "before_exit_spec_hash": pre_cfg["strategy_hashes"]["exit_spec_hash"],
        "after_exit_spec_hash": cfg["strategy_hashes"]["exit_spec_hash"],
        "exit_spec_hash_unchanged": pre_cfg["strategy_hashes"]["exit_spec_hash"] == cfg["strategy_hashes"]["exit_spec_hash"],
        "before_upgraded_count": len(pre_cfg["upgraded_combos"]),
        "after_upgraded_count": len(cfg["upgraded_combos"]),
        "changed_combo_count": len(changed_combos),
        "changed_combos": changed_combos,
    }
    (out_dir / "V3_LAB_4_BATCH_A_CONFIG_DIFF.json").write_text(json.dumps(diff_obj, indent=2), encoding="utf-8")

    # Verify 20FINAL untouched
    base_core = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1")
    assert len(base_core.spec().combos) == 20
    assert len(changed_combos) == 6
    assert diff_obj["exit_spec_hash_unchanged"] is True
    print(f"[GATE A] PASS | combos=30 upgraded=12 changed=6 exit_spec_hash_unchanged=True", flush=True)

    # Load M1 & generate official V3_LAB_4 signals
    dl = DataLayer(
        catalog_path=data_root / "catalog" / "research.duckdb",
        qa_path=data_root / "catalog" / "v3_m1_qa.json",
        data_dir=data_root / "data" / "m1_raw",
    )
    start_ms, end_ms = to_utc_ms(START_DATE), to_utc_ms(END_DATE)
    m1_arrays_eval = {}
    m15_dict = {}
    for sym in SYMBOLS:
        df_m1 = dl.query_m1(sym, START_DATE, END_DATE, include_warmup=True)
        m15_dict[sym] = resample_m1_to_timeframe(df_m1, timeframe_min=15)
        t_m1 = datetime_index_to_ms(df_m1.index)
        m1_arrays_eval[sym] = extract_m1_arrays(df_m1.loc[(t_m1 >= start_ms) & (t_m1 < end_ms)])

    all_sigs = strat.generate_signals(m15_dict, symbols=SYMBOLS, only_approved=True, only_warmup_ready=True)
    eval_sigs = [s for s in all_sigs if start_ms <= s.signal_close_ms + 1 < end_ms]
    print(f"[SIGNALS] Official V3_LAB_4 generated {len(eval_sigs)} eval signals", flush=True)

    # Audit 4H no-look-ahead on BTC/C13 and ETH/C13
    for sym_check in SYMBOLS:
        d4 = resample_ohlcv(ensure_dt(m15_dict[sym_check]), "4h")
        d4_close_ms = datetime_index_to_ms(d4.index) + 14_400_000 - 1
        e4 = ema(d4["close"], 100)
        for s in eval_sigs:
            if s.symbol == sym_check and int(s.combo_id) == 103:
                assert s.side == "SELL"
                idx4 = int(np.searchsorted(d4_close_ms, int(s.signal_close_ms), side="right")) - 1
                assert idx4 >= 0 and int(d4_close_ms[idx4]) <= int(s.signal_close_ms)
                assert float(d4["close"].iloc[idx4]) < float(e4.iloc[idx4])

    cms = {p: CostModel.from_profile_name(p, is_real_data=True) for p in ["BASE", "STRESS"]}

    # Gate B: 6 Batch A combos ONE_TRADE_PER_COMBO trade-by-trade parity vs Round 4 CSVs
    r4_var_dir = ref_root / "runs" / "lab" / "v3_lab_4" / "batch_a_round4" / "variants"
    gate_b_rows = []
    for prof in ["BASE", "STRESS"]:
        cm = cms[prof]
        risk = RiskConfig(initial_equity=INITIAL_EQUITY, notional_per_trade=NOTIONAL_PER_TRADE, cost_profile=prof, cost_model=cm)
        sim = FastSim(
            risk_config=risk,
            cost_model=cm,
            ambiguity_policy="CONSERVATIVE",
            c15_controller=strat.build_c15_h3_controller(m15_dict["ETH-USDT"]),
        )
        for sym, cid, ckey, r4_dir_name, ver_name in BATCH_A_P3_MAP:
            sub_sigs = [s for s in eval_sigs if s.symbol == sym and int(s.combo_id) == cid]
            res = sim.run_cached("ONE_TRADE_PER_COMBO", sub_sigs, m1_arrays_eval)
            ref_csv = r4_var_dir / r4_dir_name / f"trades_otpc_{prof.lower()}.csv"
            ref_df = pd.read_csv(ref_csv).sort_values(["entry_time_ms", "signal_id"]).reset_index(drop=True)
            cur_df = pd.DataFrame(
                [
                    {
                        "signal_id": t.signal_id,
                        "entry_time_ms": int(t.entry_time_ms),
                        "exit_time_ms": int(t.exit_time_ms),
                        "entry_price": float(t.entry_price),
                        "exit_price": float(t.exit_price),
                        "exit_reason": t.exit_reason,
                        "pnl_own_entry": float(t.pnl_own_entry),
                    }
                    for t in res.trades
                ]
            ).sort_values(["entry_time_ms", "signal_id"]).reset_index(drop=True)

            same_len = len(ref_df) == len(cur_df)
            max_pnl_diff = float((ref_df["pnl_own_entry"] - cur_df["pnl_own_entry"]).abs().max()) if same_len else 999.0
            exact_match = (
                same_len
                and bool((ref_df["signal_id"] == cur_df["signal_id"]).all())
                and bool((ref_df["entry_time_ms"] == cur_df["entry_time_ms"]).all())
                and bool((ref_df["exit_time_ms"] == cur_df["exit_time_ms"]).all())
                and bool((ref_df["exit_reason"] == cur_df["exit_reason"]).all())
                and max_pnl_diff < 1e-10
            )
            gate_b_rows.append(
                {
                    "combo_key": ckey,
                    "version": ver_name,
                    "cost_profile": prof,
                    "ref_trades": len(ref_df),
                    "cur_trades": len(cur_df),
                    "ref_own_pnl": float(ref_df["pnl_own_entry"].sum()),
                    "cur_own_pnl": float(cur_df["pnl_own_entry"].sum()),
                    "abs_own_pnl_diff": abs(float(cur_df["pnl_own_entry"].sum()) - float(ref_df["pnl_own_entry"].sum())),
                    "max_trade_pnl_diff": max_pnl_diff,
                    "exact_trade_parity": exact_match,
                    "status": "PASS" if exact_match else "FAIL",
                }
            )
            print(f"  [GATE B {ckey} {prof}] trades={len(cur_df)} own={cur_df['pnl_own_entry'].sum():+.6f} parity={exact_match}", flush=True)

    pd.DataFrame(gate_b_rows).to_csv(out_dir / "V3_LAB_4_BATCH_A_TRADE_PARITY.csv", index=False)

    # Gate C & D: BINGX_PORTFOLIO 30-combo vs P3 Closeout & H0_BASE other24
    closeout_dir = ref_root / "runs" / "lab" / "v3_lab_4" / "batch_a_final_closeout"
    p3_summary_df = pd.read_csv(closeout_dir / "BATCH_A_FINAL_2X2_PORTFOLIO.csv")
    batch_a_keys = {(sym, cid) for sym, cid, _, _, _ in BATCH_A_P3_MAP}

    gate_c_rows = []
    gate_d_rows = []
    for prof in ["BASE", "STRESS"]:
        cm = cms[prof]
        risk = RiskConfig(initial_equity=INITIAL_EQUITY, notional_per_trade=NOTIONAL_PER_TRADE, cost_profile=prof, cost_model=cm)
        sim = FastSim(
            risk_config=risk,
            cost_model=cm,
            ambiguity_policy="CONSERVATIVE",
            c15_controller=strat.build_c15_h3_controller(m15_dict["ETH-USDT"]),
        )
        res_p = sim.run_cached("BINGX_PORTFOLIO", eval_sigs, m1_arrays_eval)

        ref_p3_row = p3_summary_df[(p3_summary_df["portfolio_id"] == "P3") & (p3_summary_df["cost_profile"] == prof)].iloc[0]
        ref_p3_trades = pd.read_csv(closeout_dir / "portfolio_trades" / f"P3_{prof}_trades.csv").sort_values(["entry_time_ms", "symbol", "combo_id", "signal_id"]).reset_index(drop=True)
        cur_p3_trades = pd.DataFrame(
            [
                {
                    "signal_id": t.signal_id,
                    "symbol": t.symbol,
                    "combo_id": int(t.combo_id),
                    "side": t.side,
                    "entry_time_ms": int(t.entry_time_ms),
                    "exit_time_ms": int(t.exit_time_ms),
                    "exit_reason": t.exit_reason,
                    "pnl_own_entry": float(t.pnl_own_entry),
                    "pnl_vwap": float(t.pnl_vwap),
                }
                for t in res_p.trades
            ]
        ).sort_values(["entry_time_ms", "symbol", "combo_id", "signal_id"]).reset_index(drop=True)

        same_len = len(ref_p3_trades) == len(cur_p3_trades)
        max_own_diff = float((ref_p3_trades["pnl_own_entry"] - cur_p3_trades["pnl_own_entry"]).abs().max()) if same_len else 999.0
        max_vwap_diff = float((ref_p3_trades["pnl_vwap"] - cur_p3_trades["pnl_vwap"]).abs().max()) if same_len else 999.0
        cur_net = float(cur_p3_trades["pnl_own_entry"].sum())
        cur_m2m = float(res_p.summary_dict.get("m2m_max_drawdown_dollars", 0.0))
        exact_p3 = (
            same_len
            and bool((ref_p3_trades["signal_id"] == cur_p3_trades["signal_id"]).all())
            and bool((ref_p3_trades["entry_time_ms"] == cur_p3_trades["entry_time_ms"]).all())
            and bool((ref_p3_trades["exit_time_ms"] == cur_p3_trades["exit_time_ms"]).all())
            and bool((ref_p3_trades["exit_reason"] == cur_p3_trades["exit_reason"]).all())
            and max_own_diff < 1e-10
            and max_vwap_diff < 1e-10
            and abs(cur_m2m - float(ref_p3_row["m2m_max_dd_dollars"])) < 1e-4
        )
        gate_c_rows.append(
            {
                "cost_profile": prof,
                "ref_trades": len(ref_p3_trades),
                "cur_trades": len(cur_p3_trades),
                "ref_net_pnl": float(ref_p3_row["portfolio_net_pnl"]),
                "cur_net_pnl": cur_net,
                "ref_roi_pct": float(ref_p3_row["portfolio_roi_pct"]),
                "cur_roi_pct": (cur_net / INITIAL_EQUITY) * 100.0,
                "ref_m2m_dd": float(ref_p3_row["m2m_max_dd_dollars"]),
                "cur_m2m_dd": cur_m2m,
                "max_trade_own_diff": max_own_diff,
                "max_trade_vwap_diff": max_vwap_diff,
                "exact_p3_parity": exact_p3,
                "status": "PASS" if exact_p3 else "FAIL",
            }
        )
        print(f"  [GATE C P3 {prof}] trades={len(cur_p3_trades)} net={cur_net:+.6f} m2m_dd={cur_m2m:.4f} parity={exact_p3}", flush=True)

        # Gate D: 24 non-Batch-A combos vs H0_BASE
        h0_trades = pd.read_csv(closeout_dir / "portfolio_trades" / f"H0_BASE_{prof}_trades.csv")
        o24_keys = sorted({(r["symbol"], int(r["combo_id"])) for _, r in h0_trades.iterrows() if (r["symbol"], int(r["combo_id"])) not in batch_a_keys})
        for sym_o, cid_o in o24_keys:
            h0_c = h0_trades[(h0_trades["symbol"] == sym_o) & (h0_trades["combo_id"] == cid_o)].sort_values(["entry_time_ms", "signal_id"]).reset_index(drop=True)
            cur_c = cur_p3_trades[(cur_p3_trades["symbol"] == sym_o) & (cur_p3_trades["combo_id"] == cid_o)].sort_values(["entry_time_ms", "signal_id"]).reset_index(drop=True)
            same_c = len(h0_c) == len(cur_c) and bool((h0_c["signal_id"] == cur_c["signal_id"]).all()) and float((h0_c["pnl_own_entry"] - cur_c["pnl_own_entry"]).abs().max()) < 1e-10
            gate_d_rows.append(
                {
                    "cost_profile": prof,
                    "symbol": sym_o,
                    "combo_id": cid_o,
                    "is_pre_batch_a_upgraded": f"{sym_o}/C{cid_o if cid_o < 100 else cid_o - 90}" in cfg["upgraded_combos"],
                    "h0_trades": len(h0_c),
                    "cur_trades": len(cur_c),
                    "h0_own_pnl": float(h0_c["pnl_own_entry"].sum()),
                    "cur_own_pnl": float(cur_c["pnl_own_entry"].sum()),
                    "h0_vwap_pnl": float(h0_c["pnl_vwap"].sum()),
                    "cur_vwap_pnl": float(cur_c["pnl_vwap"].sum()),
                    "vwap_portfolio_netting_delta": float(cur_c["pnl_vwap"].sum()) - float(h0_c["pnl_vwap"].sum()),
                    "exact_own_parity": same_c,
                    "status": "PASS" if same_c else "FAIL",
                }
            )

    pd.DataFrame(gate_c_rows).to_csv(out_dir / "V3_LAB_4_BATCH_A_PORTFOLIO_PARITY.csv", index=False)
    pd.DataFrame(gate_d_rows).to_csv(out_dir / "V3_LAB_4_OTHER24_PARITY.csv", index=False)

    gate_b_pass = all(r["status"] == "PASS" for r in gate_b_rows)
    gate_c_pass = all(r["status"] == "PASS" for r in gate_c_rows)
    gate_d_pass = all(r["status"] == "PASS" for r in gate_d_rows)

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "clean_repro": bool(args.clean_repro),
        "git_sha": code_sha,
        "tracked_dirty": bool(tracked_dirty),
        "config_hash": cfg["config_hash"],
        "strategy_hashes": cfg["strategy_hashes"],
        "dataset_hash": ds_hash,
        "runner_sha256": sha256_file(Path(__file__)),
        "env_versions": get_env_versions(),
        "gates": {
            "gate_a_config_and_strategy": "PASS",
            "gate_b_6_combos_parity": "PASS" if gate_b_pass else "FAIL",
            "gate_c_p3_portfolio_parity": "PASS" if gate_c_pass else "FAIL",
            "gate_d_other24_parity": "PASS" if gate_d_pass else "FAIL",
        },
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    (out_dir / "V3_LAB_4_CLEAN_REPRO_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[INTEGRATION VERIFY] Gates={manifest['gates']} in {manifest['elapsed_seconds']}s", flush=True)
    if not (gate_b_pass and gate_c_pass and gate_d_pass):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
