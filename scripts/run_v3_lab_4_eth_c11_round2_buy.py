"""ETH-USDT/C11 Round 2 Phase B & C: BUY DeMarker(60) Threshold Study (SELL fixed < 0.20).

Tests 4 BUY DeMarker(60) thresholds with SELL fixed at DeMarker(60) < 0.20 (H3 from Phase A),
SMC_STRICT_OB, Fixed TP 1.8% / SL 0.9%, V3_LAB_4@4.0.0:
- B0: BUY > 0.75, SELL < 0.20 (same as H3)
- B1: BUY > 0.78, SELL < 0.20
- B2: BUY > 0.80, SELL < 0.20 (symmetric to SELL < 0.20)
- B3: BUY > 0.82, SELL < 0.20

Saves outputs to D:\\AutoTrade\\runs\\lab\\v3_lab_4\\eth_c11_round2\\
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
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.sim.costs import CostModel
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
from scripts.run_v3_lab_4_eth_c11_round1 import (
    compute_c11_stats,
    verify_v3_lab_4_preconditions,
)
from scripts.run_v3_lab_4_eth_c11_round2 import (
    build_demarker_map,
    get_tracked_dirty_files,
)

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "eth_c11_round2"
BUY_VARIANTS: list[tuple[str, float, float]] = [
    ("B0", 0.75, 0.20),
    ("B1", 0.78, 0.20),
    ("B2", 0.80, 0.20),
    ("B3", 0.82, 0.20),
]
OTPC_PROFILES = ["IDEAL", "BASE", "STRESS"]
PORT_PROFILES = ["BASE", "STRESS"]


def filter_c11_buy_sell_demarker(
    h0_sigs: list[Any],
    buy_gt: float,
    sell_lt: float,
    dem_by_close_ms: dict[int, float],
) -> list[Any]:
    out = []
    for s in h0_sigs:
        if s.symbol == "ETH-USDT" and int(s.combo_id) == 101:
            val = dem_by_close_ms.get(int(s.signal_close_ms))
            if val is None:
                continue
            if s.side == "BUY" and not (val > buy_gt):
                continue
            if s.side == "SELL" and not (val < sell_lt):
                continue
        out.append(s)
    return out


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg, strat = verify_v3_lab_4_preconditions()
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
    print(f"Loaded M1/M15 data in {time.time() - t0:.2f}s")

    all_sigs = strat.generate_signals(m15_dict, symbols=SYMBOLS, only_approved=True, only_warmup_ready=True)
    h0_eval_sigs = [s for s in all_sigs if start_ms <= s.signal_close_ms + 1 < end_ms]
    dem_map = build_demarker_map(m15_dict["ETH-USDT"])
    cost_models = {p: CostModel.from_profile_name(p, is_real_data=True) for p in OTPC_PROFILES}

    sim_results: dict[tuple[str, str, str], tuple[Any, dict[str, Any]]] = {}
    for vname, buy_gt, sell_lt in BUY_VARIANTS:
        v_sigs = filter_c11_buy_sell_demarker(h0_eval_sigs, buy_gt, sell_lt, dem_map)
        c11_s = [s for s in v_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 101]
        print(
            f"[{vname:2s} BUY>{buy_gt:.2f}, SELL<{sell_lt:.2f}] ETH/C11 signals={len(c11_s)} "
            f"(BUY={sum(1 for s in c11_s if s.side == 'BUY')}, SELL={sum(1 for s in c11_s if s.side == 'SELL')})"
        )
        for mode, profiles in [("ONE_TRADE_PER_COMBO", OTPC_PROFILES), ("BINGX_PORTFOLIO", PORT_PROFILES)]:
            for profile in profiles:
                t_r0 = time.time()
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
                res = sim.run(mode, v_sigs, candles_m1_eval)
                an = ResearchAnalyzer.analyze_simulation(res, initial_equity=INITIAL_EQUITY)
                sim_results[(vname, mode, profile)] = (res, an)
                s_c11 = compute_c11_stats(res.trades)
                print(
                    f"  {mode:19s} | {profile:6s} | port_net={an['pnl']['net_pnl']:+8.2f} "
                    f"dd={an['risk'].get('m2m_max_drawdown_dollars') or 0.0:6.2f} "
                    f"| c11={s_c11['trades']:3d} TP={s_c11['tp']:3d} SL={s_c11['sl']:3d} "
                    f"own={s_c11['own_net_pnl']:+7.4f} ({time.time()-t_r0:.2f}s)"
                )

    b0_otpc_map = {
        p: {t.signal_id: t for t in sim_results[("B0", "ONE_TRADE_PER_COMBO", p)][0].trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101}
        for p in OTPC_PROFILES
    }
    comp_rows, yearly_rows, monthly_rows, conc_rows, port_rows = [], [], [], [], []
    trades_base_dfs, trades_stress_dfs = [], []

    for vname, buy_gt, sell_lt in BUY_VARIANTS:
        for p in OTPC_PROFILES:
            tr = sim_results[(vname, "ONE_TRADE_PER_COMBO", p)][0].trades
            c11_tr = [t for t in tr if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
            s_all = compute_c11_stats(tr)
            s_buy = compute_c11_stats(tr, "BUY")
            s_sell = compute_c11_stats(tr, "SELL")
            v_map = {t.signal_id: t for t in c11_tr}
            b0_map = b0_otpc_map[p]
            rem_buy = {sid for sid in (set(b0_map) - set(v_map)) if b0_map[sid].side == "BUY"}
            new_all = set(v_map) - set(b0_map)
            b0_own = compute_c11_stats(sim_results[("B0", "ONE_TRADE_PER_COMBO", p)][0].trades)["own_net_pnl"]
            comp_rows.append(
                {
                    "variant": vname,
                    "buy_demarker_gt": buy_gt,
                    "sell_demarker_lt": sell_lt,
                    "cost_profile": p,
                    "trades": s_all["trades"],
                    "buy_trades": s_buy["trades"],
                    "sell_trades": s_sell["trades"],
                    "tp": s_all["tp"],
                    "sl": s_all["sl"],
                    "win_rate_pct": s_all["win_rate_pct"],
                    "buy_tp": s_buy["tp"],
                    "buy_sl": s_buy["sl"],
                    "buy_wr_pct": s_buy["win_rate_pct"],
                    "buy_own_pnl": s_buy["own_net_pnl"],
                    "sell_tp": s_sell["tp"],
                    "sell_sl": s_sell["sl"],
                    "sell_wr_pct": s_sell["win_rate_pct"],
                    "sell_own_pnl": s_sell["own_net_pnl"],
                    "own_net_pnl": s_all["own_net_pnl"],
                    "delta_own_pnl_vs_b0": round(s_all["own_net_pnl"] - b0_own, 4),
                    "pf": s_all["pf"],
                    "closed_max_dd": s_all["closed_max_dd"],
                    "removed_buy_from_b0": len(rem_buy),
                    "removed_b0_buy_tp": sum(1 for sid in rem_buy if b0_map[sid].exit_reason == "TP"),
                    "removed_b0_buy_sl": sum(1 for sid in rem_buy if b0_map[sid].exit_reason == "SL"),
                    "new_concurrency_trades": len(new_all),
                }
            )
            if p in ("BASE", "STRESS"):
                for yr in ["2023", "2024", "2025", "2026"]:
                    sub_y = [t for t in c11_tr if str(t.entry_time_dt).startswith(yr)]
                    tp_y = sum(1 for t in sub_y if t.exit_reason == "TP")
                    yearly_rows.append(
                        {
                            "variant": vname,
                            "cost_profile": p,
                            "year": yr,
                            "trades": len(sub_y),
                            "tp": tp_y,
                            "sl": len(sub_y) - tp_y,
                            "own_net_pnl": round(sum(float(t.pnl_own_entry) for t in sub_y), 4),
                        }
                    )
                by_m: dict[str, list[Any]] = {}
                for t in c11_tr:
                    by_m.setdefault(str(t.entry_time_dt)[:7], []).append(t)
                for m, sub_m in sorted(by_m.items()):
                    tp_m = sum(1 for t in sub_m if t.exit_reason == "TP")
                    monthly_rows.append(
                        {
                            "variant": vname,
                            "cost_profile": p,
                            "month": m,
                            "trades": len(sub_m),
                            "tp": tp_m,
                            "sl": len(sub_m) - tp_m,
                            "own_net_pnl": round(sum(float(t.pnl_own_entry) for t in sub_m), 4),
                        }
                    )
                if vname != "B0":
                    for sid in sorted(new_all):
                        tv = v_map[sid]
                        conc_rows.append(
                            {
                                "variant": vname,
                                "cost_profile": p,
                                "signal_id": sid,
                                "side": tv.side,
                                "entry_time_dt": str(tv.entry_time_dt),
                                "exit_reason": tv.exit_reason,
                                "own_net_pnl": round(float(tv.pnl_own_entry), 4),
                            }
                        )

        for p in PORT_PROFILES:
            res_p, an_p = sim_results[(vname, "BINGX_PORTFOLIO", p)]
            c11_p = [t for t in res_p.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
            port_rows.append(
                {
                    "variant": vname,
                    "buy_demarker_gt": buy_gt,
                    "sell_demarker_lt": sell_lt,
                    "cost_profile": p,
                    "portfolio_trades": len(res_p.trades),
                    "portfolio_net_pnl": round(float(an_p["pnl"]["net_pnl"]), 4),
                    "portfolio_roi_pct": round(float(an_p["pnl"]["roi_pct"]), 2),
                    "m2m_max_dd_dollars": round(float(an_p["risk"].get("m2m_max_drawdown_dollars") or 0.0), 4),
                    "m2m_max_dd_pct": round(float(an_p["risk"].get("m2m_max_drawdown_pct") or 0.0), 2),
                    "closed_max_dd_dollars": round(float(an_p["risk"].get("closed_trade_max_drawdown_dollars") or 0.0), 4),
                    "eth_c11_port_trades": len(c11_p),
                    "eth_c11_port_own_pnl": round(sum(float(t.pnl_own_entry) for t in c11_p), 4),
                    "eth_c11_port_vwap_pnl": round(sum(float(t.pnl_vwap) for t in c11_p), 4),
                }
            )
            for mode_lbl in ["ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]:
                tr_m = [t for t in sim_results[(vname, mode_lbl, p)][0].trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
                if tr_m:
                    df_t = pd.DataFrame([t.to_dict() for t in tr_m])
                    df_t.insert(0, "variant", vname)
                    df_t.insert(1, "mode", mode_lbl)
                    if p == "BASE":
                        trades_base_dfs.append(df_t)
                    else:
                        trades_stress_dfs.append(df_t)

    pd.DataFrame(comp_rows).to_csv(OUT_DIR / "ETH_C11_R2_BUY_COMPARISON.csv", index=False)
    pd.DataFrame(yearly_rows).to_csv(OUT_DIR / "ETH_C11_R2_BUY_YEARLY_PNL.csv", index=False)
    pd.DataFrame(monthly_rows).to_csv(OUT_DIR / "ETH_C11_R2_BUY_MONTHLY_PNL.csv", index=False)
    pd.DataFrame(port_rows).to_csv(OUT_DIR / "ETH_C11_R2_BUY_PORTFOLIO_IMPACT.csv", index=False)
    pd.DataFrame(conc_rows).to_csv(OUT_DIR / "ETH_C11_R2_BUY_CONCURRENCY_DIFF.csv", index=False)
    pd.concat(trades_base_dfs, ignore_index=True).to_csv(OUT_DIR / "ETH_C11_R2_BUY_TRADES_BASE.csv", index=False)
    pd.concat(trades_stress_dfs, ignore_index=True).to_csv(OUT_DIR / "ETH_C11_R2_BUY_TRADES_STRESS.csv", index=False)

    print(f"\nCompleted Phase B in {time.time() - t0:.2f}s! Outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()
