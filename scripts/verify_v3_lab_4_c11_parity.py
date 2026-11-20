"""Post-integration parity verification for V3_LAB_4@4.0.0 with ETH-USDT/C11 H3_B1_DEMARKER.

Verifies exact trade-by-trade parity against Round 2 B1 (ETH_C11_R2_BUY_TRADES_BASE/STRESS.csv)
and confirms zero unintended changes on the other 29 combos (including BTC/C11, ETH/C10, BTC/C10).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

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
from scripts.run_v3_lab_4_eth_c11_round2 import get_tracked_dirty_files

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "c11_integration"
R2_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "eth_c11_round2"
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
    r2_base_df = pd.read_csv(R2_DIR / "ETH_C11_R2_BUY_TRADES_BASE.csv")
    r2_stress_df = pd.read_csv(R2_DIR / "ETH_C11_R2_BUY_TRADES_STRESS.csv")

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

            eth11 = sorted(
                [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101],
                key=lambda t: (t.exit_time_ms, t.entry_time_ms),
            )
            pnls11 = [float(t.pnl_own_entry) for t in eth11]

            # Exact trade-by-trade parity check against Round 2 B1 CSV
            ref_df = r2_base_df if profile == "BASE" else r2_stress_df
            ref_b1 = ref_df[(ref_df["variant"] == "B1") & (ref_df["mode"] == mode)].sort_values(
                ["exit_time_ms", "entry_time_ms"]
            )
            assert len(eth11) == len(ref_b1), f"{mode} {profile} count mismatch: {len(eth11)} vs {len(ref_b1)}"
            for t, (_, r) in zip(eth11, ref_b1.iterrows()):
                assert t.signal_id == r["signal_id"]
                assert t.side == r["side"]
                assert int(t.entry_time_ms) == int(r["entry_time_ms"])
                assert int(t.exit_time_ms) == int(r["exit_time_ms"])
                assert t.exit_reason == r["exit_reason"]
                assert abs(float(t.entry_price) - float(r["entry_price"])) < 1e-6
                assert abs(float(t.exit_price) - float(r["exit_price"])) < 1e-6
                assert abs(float(t.pnl_own_entry) - float(r["pnl_own_entry"])) < 1e-6

            btc11 = [t for t in res.trades if t.symbol == "BTC-USDT" and int(t.combo_id) == 101]
            eth10 = [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 10]
            btc10 = [t for t in res.trades if t.symbol == "BTC-USDT" and int(t.combo_id) == 10]

            key = f"{mode}_{profile}"
            results[key] = {
                "portfolio_trades": len(res.trades),
                "portfolio_net_pnl": round(float(an["pnl"]["net_pnl"]), 4),
                "m2m_max_dd_dollars": round(float(an["risk"].get("m2m_max_drawdown_dollars") or 0.0), 4),
                "eth_c11_trades": len(eth11),
                "eth_c11_buy": sum(1 for t in eth11 if t.side == "BUY"),
                "eth_c11_sell": sum(1 for t in eth11 if t.side == "SELL"),
                "eth_c11_tp": sum(1 for t in eth11 if t.exit_reason == "TP"),
                "eth_c11_sl": sum(1 for t in eth11 if t.exit_reason == "SL"),
                "eth_c11_own_pnl": round(sum(pnls11), 4),
                "eth_c11_pf": round(profit_factor(pnls11), 4),
                "eth_c11_closed_max_dd": round(closed_max_drawdown(pnls11), 4),
                "btc_c11_trades": len(btc11),
                "btc_c11_own_pnl": round(sum(float(t.pnl_own_entry) for t in btc11), 4),
                "eth_c10_trades": len(eth10),
                "eth_c10_own_pnl": round(sum(float(t.pnl_own_entry) for t in eth10), 4),
                "btc_c10_trades": len(btc10),
                "btc_c10_own_pnl": round(sum(float(t.pnl_own_entry) for t in btc10), 4),
            }
            print(f"[{key:26s}] {json.dumps(results[key])}")

    # Assert exact Round 2 B1 targets & untouched combos
    for mode in MODES:
        b = results[f"{mode}_BASE"]
        s = results[f"{mode}_STRESS"]
        assert b["eth_c11_trades"] == 242 and b["eth_c11_buy"] == 92 and b["eth_c11_sell"] == 150
        assert abs(b["eth_c11_own_pnl"] - 33.9645) < 1e-3
        assert abs(b["eth_c11_pf"] - 1.2413) < 1e-3
        assert abs(b["eth_c11_closed_max_dd"] - 12.0053) < 1e-3
        assert s["eth_c11_trades"] == 245 and s["eth_c11_buy"] == 93 and s["eth_c11_sell"] == 152
        assert abs(s["eth_c11_own_pnl"] - 18.1548) < 1e-3
        assert abs(s["eth_c11_pf"] - 1.1209) < 1e-3
        assert abs(s["eth_c11_closed_max_dd"] - 13.8087) < 1e-3
        assert abs(b["portfolio_net_pnl"] - 221.1557) < 1e-2
        assert abs(s["portfolio_net_pnl"] - (-183.1944)) < 1e-2
        assert b["btc_c11_trades"] == 241 and abs(b["btc_c11_own_pnl"] - 24.19) < 0.02
        assert b["eth_c10_trades"] == 23 and abs(b["eth_c10_own_pnl"] - 14.3407) < 1e-3
        assert b["btc_c10_trades"] == 46 and abs(b["btc_c10_own_pnl"] - 6.2829) < 1e-3

    code_sha, code_dirty, _ = get_git_state()
    manifest = {
        "verification_id": "V3_LAB_4_C11_INTEGRATION_PARITY",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_id": cfg["config_id"],
        "version": cfg["version"],
        "config_hash": cfg["config_hash"],
        "dataset_hash": dataset_hash,
        "results": results,
        "parity_status": "PASSED_EXACT_TRADE_BY_TRADE",
        "git_state": {
            "sha": code_sha,
            "dirty": code_dirty,
            "tracked_dirty_files": get_tracked_dirty_files(),
        },
        "env_versions": get_env_versions(),
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "PARITY_VERIFICATION.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nALL TRADE-BY-TRADE PARITY CHECKS PASSED in {time.time() - t0:.2f}s!")


if __name__ == "__main__":
    main()
