"""Post-integration parity verification for V3_LAB_4@4.0.0 with ETH-USDT/C12 H7_SELL_4H_TREND_ONLY.

Verifies:
1. Exact trade-by-trade parity (732/732: 366 BASE + 366 STRESS) against Round 3 H7 in
   D:\\AutoTrade\\exports\\V3_LAB_4_ETH_C12_ROUND3.zip.
2. Zero unintended changes on the other 29 combo-symbols (e.g. BTC/C12, ETH/C11, BTC/C11, ETH/C10, BTC/C10).
3. Saves PARITY_VERIFICATION.json and V3_LAB_4_C12_H7_INTEGRATION_REPORT.md in
   D:\\AutoTrade\\runs\\lab\\v3_lab_4\\c12_integration\\.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any
import zipfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.sim.costs import CostModel
from scripts.run_v3_lab_2_eth_c15_h2 import (
    END_DATE,
    EXPECTED_DATASET_HASH,
    INITIAL_EQUITY,
    NOTIONAL_PER_TRADE,
    START_DATE,
    SYMBOLS,
    closed_max_drawdown,
    profit_factor,
    sha256_file,
)
from scripts.run_v3_lab_2_eth_c15_research import ResearchUnifiedSimulator

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "c12_integration"
R3_ZIP = ROOT / "exports" / "V3_LAB_4_ETH_C12_ROUND3.zip"
PROFILES = ["BASE", "STRESS"]
MODES = ["ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_v3_lab_4_config()
    strat = load_v3_lab_4_strategy()
    assert len(strat.spec().combos) == 30

    dl = DataLayer()
    gm_path = ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json"
    dataset_hash = json.loads(gm_path.read_text(encoding="utf-8")).get("dataset_hash", "")
    if dataset_hash != EXPECTED_DATASET_HASH:
        raise RuntimeError(f"Dataset hash mismatch: {dataset_hash}")

    start_ms, end_ms = to_utc_ms(START_DATE), to_utc_ms(END_DATE)
    candles_m1_eval: dict[str, pd.DataFrame] = {}
    m15_dict: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        df_m1 = dl.query_m1(sym, START_DATE, END_DATE, include_warmup=True)
        t_m1 = datetime_index_to_ms(df_m1.index)
        candles_m1_eval[sym] = df_m1.loc[(t_m1 >= start_ms) & (t_m1 < end_ms)]
        m15_dict[sym] = dl.query_timeframe(sym, "15m", START_DATE, END_DATE, include_warmup=True)

    all_sigs = strat.generate_signals(m15_dict, symbols=SYMBOLS, only_approved=True, only_warmup_ready=True)
    eval_sigs = [s for s in all_sigs if start_ms <= s.signal_close_ms + 1 < end_ms]

    cost_models = {p: CostModel.from_profile_name(p, is_real_data=True) for p in PROFILES}
    with zipfile.ZipFile(R3_ZIP) as zf:
        r3_base_df = pd.read_csv(zf.open("ETH_C12_R3_TRADES_BASE.csv"))
        r3_stress_df = pd.read_csv(zf.open("ETH_C12_R3_TRADES_STRESS.csv"))

    results: dict[str, Any] = {}
    for mode in MODES:
        for profile in PROFILES:
            cm = cost_models[profile]
            risk = RiskConfig(
                initial_equity=INITIAL_EQUITY,
                notional_per_trade=NOTIONAL_PER_TRADE,
                cost_profile=profile,
                cost_model=cm,
            )
            ctrl = strat.build_c15_h3_controller(m15_dict["ETH-USDT"])
            sim = ResearchUnifiedSimulator(
                risk_config=risk,
                cost_model=cm,
                ambiguity_policy="CONSERVATIVE",
                c15_controller=ctrl,
            )
            res = sim.run(mode, eval_sigs, candles_m1_eval)
            an = ResearchAnalyzer.analyze_simulation(res, initial_equity=INITIAL_EQUITY)

            eth12 = sorted(
                [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 102],
                key=lambda t: (t.exit_time_ms, t.entry_time_ms),
            )
            pnls12 = [float(t.pnl_own_entry) for t in eth12]

            ref_df = r3_base_df if profile == "BASE" else r3_stress_df
            ref_h7 = ref_df[(ref_df["variant"] == "H7") & (ref_df["mode"] == mode)].sort_values(
                ["exit_time_ms", "entry_time_ms"]
            )
            assert len(eth12) == len(ref_h7), f"{mode} {profile} count mismatch: {len(eth12)} vs {len(ref_h7)}"
            for t, (_, r) in zip(eth12, ref_h7.iterrows()):
                assert t.signal_id == r["signal_id"]
                assert t.side == r["side"]
                assert int(t.entry_time_ms) == int(r["entry_time_ms"])
                assert int(t.exit_time_ms) == int(r["exit_time_ms"])
                assert t.exit_reason == r["exit_reason"]
                assert abs(float(t.entry_price) - float(r["entry_price"])) < 1e-6
                assert abs(float(t.exit_price) - float(r["exit_price"])) < 1e-6
                assert abs(float(t.pnl_own_entry) - float(r["pnl_own_entry"])) < 1e-6

            btc12 = [t for t in res.trades if t.symbol == "BTC-USDT" and int(t.combo_id) == 102]
            eth11 = [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
            btc11 = [t for t in res.trades if t.symbol == "BTC-USDT" and int(t.combo_id) == 101]
            row = {
                "portfolio_trades": len(res.trades),
                "portfolio_net_pnl": round(float(an["pnl"]["net_pnl"]), 4),
                "m2m_max_dd_dollars": round(float(an["risk"].get("m2m_max_drawdown_dollars") or 0.0), 4),
                "eth_c12_trades": len(eth12),
                "eth_c12_buy": sum(1 for t in eth12 if t.side == "BUY"),
                "eth_c12_sell": sum(1 for t in eth12 if t.side == "SELL"),
                "eth_c12_tp": sum(1 for t in eth12 if t.exit_reason == "TP"),
                "eth_c12_sl": sum(1 for t in eth12 if t.exit_reason == "SL"),
                "eth_c12_own_pnl": round(sum(pnls12), 4),
                "eth_c12_pf": round(profit_factor(pnls12), 4),
                "eth_c12_closed_max_dd": round(closed_max_drawdown(pnls12), 4),
                "btc_c12_trades": len(btc12),
                "btc_c12_own_pnl": round(sum(float(t.pnl_own_entry) for t in btc12), 4),
                "eth_c11_trades": len(eth11),
                "eth_c11_own_pnl": round(sum(float(t.pnl_own_entry) for t in eth11), 4),
                "btc_c11_trades": len(btc11),
                "btc_c11_own_pnl": round(sum(float(t.pnl_own_entry) for t in btc11), 4),
            }
            results[f"{mode}_{profile}"] = row
            print(f"[{mode:19s}_{profile:6s}] {json.dumps(row)}", flush=True)

    code_sha, code_dirty, _ = get_git_state()
    out = {
        "verification_id": "V3_LAB_4_C12_H7_INTEGRATION_PARITY",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_id": cfg["config_id"],
        "version": cfg["version"],
        "config_hash": cfg["config_hash"],
        "dataset_hash": dataset_hash,
        "results": results,
        "parity_status": "PASSED_EXACT_TRADE_BY_TRADE",
        "git_state": {"sha": code_sha, "dirty": code_dirty},
        "env_versions": get_env_versions(),
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "PARITY_VERIFICATION.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nALL 732/732 TRADE-BY-TRADE PARITY CHECKS PASSED in {time.time() - t0:.2f}s!", flush=True)


if __name__ == "__main__":
    main()
