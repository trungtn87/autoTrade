"""ETH-USDT/C11 Round 2: DeMarker(60) Sensitivity, Stability & Reproducibility on V3_LAB_4@4.0.0.

Tests 4 SELL DeMarker(60) thresholds (keeping BUY > 0.75, SMC_STRICT_OB, TP 1.8% / SL 0.9%):
- H0:  DeMarker(60) < 0.25 (baseline)
- H3a: DeMarker(60) < 0.18
- H3:  DeMarker(60) < 0.20
- H3b: DeMarker(60) < 0.22

Also verifies:
1. Equivalence between research filter (DeMarker < 0.20) and strategy-core ComboSpec entry={"period": 60, "long_min": 0.75, "short_max": 0.20}.
2. Zero unintended changes on the other 29 combo-symbols.
3. Sensitivity excluding 2024-08 & 2025-05, monthly win/loss vs H0, and STRESS improvement distribution.

Saves outputs to D:\\AutoTrade\\runs\\lab\\v3_lab_4\\eth_c11_round2\\
and packages exports/V3_LAB_4_ETH_C11_ROUND2.zip.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import datetime, timezone
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

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy, _build_v3_lab_4_core
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.strategies.final20.logic import _demarker
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
from scripts.run_v3_lab_4_eth_c11_round1 import (
    compute_c11_stats,
    verify_v3_lab_4_preconditions,
)

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "eth_c11_round2"
VARIANTS: list[tuple[str, float]] = [
    ("H0", 0.25),
    ("H3a", 0.18),
    ("H3", 0.20),
    ("H3b", 0.22),
]
OTPC_PROFILES = ["IDEAL", "BASE", "STRESS"]
PORT_PROFILES = ["BASE", "STRESS"]


def build_demarker_map(df_eth_m15: pd.DataFrame) -> dict[int, float]:
    d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
    dem = _demarker(d1["high"].to_numpy(float), d1["low"].to_numpy(float), 60)
    close_ms = datetime_index_to_ms(d1.index) + 3_600_000 - 1
    return {int(close_ms[i]): float(dem[i]) for i in range(len(d1)) if np.isfinite(dem[i])}


def filter_c11_sell_demarker(
    h0_sigs: list[Any],
    short_max: float,
    dem_by_close_ms: dict[int, float],
) -> list[Any]:
    if abs(short_max - 0.25) < 1e-12:
        return list(h0_sigs)
    out = []
    for s in h0_sigs:
        if s.symbol == "ETH-USDT" and int(s.combo_id) == 101 and s.side == "SELL":
            val = dem_by_close_ms.get(int(s.signal_close_ms))
            if val is None or not (val < short_max):
                continue
        out.append(s)
    return out


def generate_h3_via_strategy_config(m15_dict: dict[str, pd.DataFrame], start_ms: int, end_ms: int) -> list[Any]:
    """Build V3Lab4Strategy with ETH/C11 ComboSpec entry.short_max=0.20 (no post-hoc filter)."""
    strat_cfg = V3Lab4Strategy()
    combos = list(strat_cfg._core.spec().combos)
    new_combos = []
    for c in combos:
        if c.symbol == "ETH-USDT" and int(c.combo_id) == 101:
            new_entry = dict(c.entry)
            new_entry["short_max"] = 0.20
            c = dataclasses.replace(c, entry=new_entry)
        new_combos.append(c)
    strat_cfg._core._spec = dataclasses.replace(strat_cfg._core.spec(), combos=tuple(new_combos))
    strat_cfg._core._inner._spec = strat_cfg._core._spec
    sigs = strat_cfg.generate_signals(m15_dict, symbols=SYMBOLS, only_approved=True, only_warmup_ready=True)
    return [s for s in sigs if start_ms <= s.signal_close_ms + 1 < end_ms]


def get_tracked_dirty_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


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

    # Check equivalence: H3 filter vs Strategy Config (ComboSpec short_max=0.20)
    h3_filter_sigs = filter_c11_sell_demarker(h0_eval_sigs, 0.20, dem_map)
    h3_config_sigs = generate_h3_via_strategy_config(m15_dict, start_ms, end_ms)
    f_keys = [(s.symbol, int(s.combo_id), s.side, int(s.signal_close_ms)) for s in h3_filter_sigs]
    c_keys = [(s.symbol, int(s.combo_id), s.side, int(s.signal_close_ms)) for s in h3_config_sigs]
    assert f_keys == c_keys, f"Strategy-config H3 mismatch: filter={len(f_keys)} vs config={len(c_keys)}"
    print(f"[EQUIVALENCE] H3 filter == H3 strategy ComboSpec: {len(f_keys)} signals across 30 combos!")

    # Run all 4 variants x (OTPC IDEAL/BASE/STRESS + BINGX_PORTFOLIO BASE/STRESS)
    sim_results: dict[tuple[str, str, str], tuple[Any, dict[str, Any]]] = {}
    for vname, thresh in VARIANTS:
        v_sigs = filter_c11_sell_demarker(h0_eval_sigs, thresh, dem_map)
        c11_s = [s for s in v_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 101]
        print(
            f"[{vname:3s} < {thresh:.2f}] ETH/C11 signals={len(c11_s)} "
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

    # Verify other 29 combos untouched between H0 and H3
    other29_check: dict[str, Any] = {}
    for profile in PORT_PROFILES:
        h0_other = [t for t in sim_results[("H0", "BINGX_PORTFOLIO", profile)][0].trades if not (t.symbol == "ETH-USDT" and int(t.combo_id) == 101)]
        h3_other = [t for t in sim_results[("H3", "BINGX_PORTFOLIO", profile)][0].trades if not (t.symbol == "ETH-USDT" and int(t.combo_id) == 101)]
        h0_sig = [(t.signal_id, t.entry_time_ms, t.exit_time_ms, t.exit_reason, round(float(t.pnl_own_entry), 6)) for t in h0_other]
        h3_sig = [(t.signal_id, t.entry_time_ms, t.exit_time_ms, t.exit_reason, round(float(t.pnl_own_entry), 6)) for t in h3_other]
        assert h0_sig == h3_sig, f"Other 29 combos changed in {profile}!"
        other29_check[profile] = {"trades": len(h0_other), "identical_to_h0": True}
    print(f"[VERIFIED] Other 29 combos 100% identical between H0 and H3: {other29_check}")

    # Build tables
    h0_otpc_map = {
        p: {t.signal_id: t for t in sim_results[("H0", "ONE_TRADE_PER_COMBO", p)][0].trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101}
        for p in OTPC_PROFILES
    }
    comp_rows, yearly_rows, monthly_rows, conc_rows, port_rows = [], [], [], [], []
    trades_base_dfs, trades_stress_dfs = [], []

    for vname, thresh in VARIANTS:
        for p in OTPC_PROFILES:
            tr = sim_results[(vname, "ONE_TRADE_PER_COMBO", p)][0].trades
            c11_tr = [t for t in tr if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
            s_all = compute_c11_stats(tr)
            s_buy = compute_c11_stats(tr, "BUY")
            s_sell = compute_c11_stats(tr, "SELL")
            v_map = {t.signal_id: t for t in c11_tr}
            h0_map = h0_otpc_map[p]
            rem = set(h0_map) - set(v_map)
            new = set(v_map) - set(h0_map)
            h0_own = compute_c11_stats(sim_results[("H0", "ONE_TRADE_PER_COMBO", p)][0].trades)["own_net_pnl"]
            comp_rows.append(
                {
                    "variant": vname,
                    "sell_demarker_lt": thresh,
                    "cost_profile": p,
                    "trades": s_all["trades"],
                    "buy_trades": s_buy["trades"],
                    "sell_trades": s_sell["trades"],
                    "tp": s_all["tp"],
                    "sl": s_all["sl"],
                    "win_rate_pct": s_all["win_rate_pct"],
                    "buy_own_pnl": s_buy["own_net_pnl"],
                    "sell_own_pnl": s_sell["own_net_pnl"],
                    "own_net_pnl": s_all["own_net_pnl"],
                    "delta_own_pnl_vs_h0": round(s_all["own_net_pnl"] - h0_own, 4),
                    "pf": s_all["pf"],
                    "closed_max_dd": s_all["closed_max_dd"],
                    "removed_from_h0": len(rem),
                    "removed_h0_tp": sum(1 for sid in rem if h0_map[sid].exit_reason == "TP"),
                    "removed_h0_sl": sum(1 for sid in rem if h0_map[sid].exit_reason == "SL"),
                    "new_concurrency_trades": len(new),
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
                if vname != "H0":
                    for sid in sorted(new):
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
                    "sell_demarker_lt": thresh,
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

    # Stability metrics for H3 vs H0
    df_m = pd.DataFrame(monthly_rows)
    stability: dict[str, Any] = {}
    for p in PORT_PROFILES:
        m0 = df_m[(df_m["variant"] == "H0") & (df_m["cost_profile"] == p)].set_index("month")["own_net_pnl"]
        m3 = df_m[(df_m["variant"] == "H3") & (df_m["cost_profile"] == p)].set_index("month")["own_net_pnl"]
        all_m = sorted(set(m0.index) | set(m3.index))
        m0_a = m0.reindex(all_m, fill_value=0.0)
        m3_a = m3.reindex(all_m, fill_value=0.0)
        excl = ["2024-08", "2025-05"]
        keep_m = [m for m in all_m if m not in excl]
        stability[p] = {
            "months_total": len(all_m),
            "months_h3_gt_h0": int((m3_a > m0_a).sum()),
            "months_h3_lt_h0": int((m3_a < m0_a).sum()),
            "months_h3_eq_h0": int((m3_a == m0_a).sum()),
            "h0_total_pnl": round(float(m0_a.sum()), 4),
            "h3_total_pnl": round(float(m3_a.sum()), 4),
            "h0_pnl_excl_2024_08_2025_05": round(float(m0_a.loc[keep_m].sum()), 4),
            "h3_pnl_excl_2024_08_2025_05": round(float(m3_a.loc[keep_m].sum()), 4),
            "h3_2024_08_pnl": round(float(m3_a.get("2024-08", 0.0)), 4),
            "h3_2025_05_pnl": round(float(m3_a.get("2025-05", 0.0)), 4),
        }
    print(f"[STABILITY] {json.dumps(stability)}")

    pd.DataFrame(comp_rows).to_csv(OUT_DIR / "ETH_C11_R2_COMPARISON.csv", index=False)
    pd.DataFrame(yearly_rows).to_csv(OUT_DIR / "ETH_C11_R2_YEARLY_PNL.csv", index=False)
    df_m.to_csv(OUT_DIR / "ETH_C11_R2_MONTHLY_PNL.csv", index=False)
    pd.DataFrame(port_rows).to_csv(OUT_DIR / "ETH_C11_R2_PORTFOLIO_IMPACT.csv", index=False)
    pd.DataFrame(conc_rows).to_csv(OUT_DIR / "ETH_C11_R2_CONCURRENCY_DIFF.csv", index=False)
    pd.concat(trades_base_dfs, ignore_index=True).to_csv(OUT_DIR / "ETH_C11_R2_TRADES_BASE.csv", index=False)
    pd.concat(trades_stress_dfs, ignore_index=True).to_csv(OUT_DIR / "ETH_C11_R2_TRADES_STRESS.csv", index=False)

    equiv_payload = {
        "h3_filter_signal_count": len(f_keys),
        "h3_strategy_config_signal_count": len(c_keys),
        "signals_100pct_identical": True,
        "other_29_combos_100pct_identical": other29_check,
        "stability_checks": stability,
    }
    (OUT_DIR / "ETH_C11_R2_STRATEGY_EQUIVALENCE.json").write_text(json.dumps(equiv_payload, indent=2), encoding="utf-8")

    code_sha, code_dirty, _ = get_git_state()
    manifest = {
        "research_id": "V3_LAB_4_ETH_C11_ROUND2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "v3_lab_4_config_hash": cfg["config_hash"],
        "dataset_hash": dataset_hash,
        "period": {"start": START_DATE, "end": END_DATE},
        "variants": [{"name": n, "sell_demarker_lt": th} for n, th in VARIANTS],
        "cost_profiles": {p: cost_models[p].profile.profile_hash for p in OTPC_PROFILES},
        "git_state": {
            "sha": code_sha,
            "dirty": code_dirty,
            "tracked_dirty_files": get_tracked_dirty_files(),
        },
        "env_versions": get_env_versions(),
        "runner_script": "scripts/run_v3_lab_4_eth_c11_round2.py",
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nCompleted in {time.time() - t0:.2f}s! Outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()
