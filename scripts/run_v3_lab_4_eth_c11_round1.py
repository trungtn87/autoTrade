"""ETH-USDT/C11 Round 1 Research & A/B Backtest on V3_LAB_4@4.0.0.

Variants (only ETH-USDT/C11 modified; other 29 combos of V3_LAB_4@4.0.0 unchanged):
- H0: BASELINE (DeMarker(60) BUY>0.75 / SELL<0.25, SMC_STRICT_OB, TP 1.8% / SL 0.9%)
- H1: BUY_ONLY (disable ETH/C11 SELL; BUY unchanged)
- H2: SELL_4H_TREND_FILTER (SELL requires closed 4H close < EMA100(4H) and EMA100(4H) falling)
- H3: SELL_DEMARKER_STRONG (SELL requires DeMarker(60) < 0.20 on closed 1H candle)
- H4: SELL_ADX_CONFIRMATION (SELL requires ADX(14) > 20 and -DI > +DI on closed 1H candle)

Zero look-ahead: 1H indicators evaluated on signal's closed 1H bar; 4H indicators evaluated
on the latest 4H bar with close_ms <= signal_close_ms.

Outputs saved to D:\\AutoTrade\\runs\\lab\\v3_lab_4\\eth_c11_round1\\
and packaged into exports/V3_LAB_4_ETH_C11_ROUND1.zip.
"""
from __future__ import annotations

from datetime import datetime, timezone
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

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab import load_v3_lab_4_config, load_v3_lab_4_strategy
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.strategy_core.indicators.indicators import atr, dmi, ema
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_ob import OBConfig, ob_context
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
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

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "eth_c11_round1"
EXP_DIR = ROOT / "exports"
VARIANTS = ["H0", "H1", "H2", "H3", "H4"]
OTPC_PROFILES = ["IDEAL", "BASE", "STRESS"]
PORT_PROFILES = ["BASE", "STRESS"]


def verify_v3_lab_4_preconditions() -> tuple[dict[str, Any], Any]:
    """Verify V3_LAB_4@4.0.0 exists and has all 4 Owner-approved upgrades + BTC/C10 H0."""
    cfg = load_v3_lab_4_config()
    strat = load_v3_lab_4_strategy()
    expected_upgrades = {
        "ETH-USDT/C1": "H1_BOTH_1",
        "BTC-USDT/C14": "BTC-USDT_C14_H3a_KUMO_BAND_V1",
        "ETH-USDT/C15": "H3_ASYMMETRIC",
        "ETH-USDT/C10": "H3_ASYMMETRIC",
    }
    if cfg.get("upgraded_combos") != expected_upgrades:
        raise RuntimeError(f"V3_LAB_4 upgraded_combos mismatch: {cfg.get('upgraded_combos')}")
    by_key = {c["combo_key"]: c for c in cfg["combos"]}
    if by_key["BTC-USDT/C10"]["execution_version"] != "20FINAL_FIXED_V1@1":
        raise RuntimeError("BTC-USDT/C10 is not H0 in V3_LAB_4")
    c11 = by_key["ETH-USDT/C11"]
    if int(c11["combo_id"]) != 101 or c11["timeframe"] != "1h" or c11["layer2"] != "SMC_STRICT_OB":
        raise RuntimeError(f"ETH-USDT/C11 spec mismatch: {c11}")
    if abs(float(c11["tp_pct"]) - 0.018) > 1e-9 or abs(float(c11["sl_pct"]) - 0.009) > 1e-9:
        raise RuntimeError(f"ETH-USDT/C11 TP/SL mismatch: {c11}")
    return cfg, strat


def build_eth_c11_feature_table(df_eth_m15: pd.DataFrame) -> dict[int, dict[str, Any]]:
    """Precompute closed-candle 1H & 4H features keyed by 1H signal_close_ms (zero look-ahead)."""
    d15 = ensure_dt(df_eth_m15)
    d1 = resample_ohlcv(d15, "1h")
    d4 = resample_ohlcv(d15, "4h")

    dem_1h = pd.Series(_demarker(d1["high"].to_numpy(float), d1["low"].to_numpy(float), 60), index=d1.index)
    pdi_1h, mdi_1h, adx_1h = dmi(d1, 14, 14)
    atr_1h = atr(d1, 14)
    smc_1h = smc_direction(d1, 50, False)
    ob_1h = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))

    ema100_4h = ema(d4["close"], 100)
    slope_ema100_4h = ema100_4h - ema100_4h.shift(1)
    d4_open_ms = datetime_index_to_ms(d4.index)
    d4_close_ms = d4_open_ms + 14_400_000 - 1

    d1_open_ms = datetime_index_to_ms(d1.index)
    d1_close_ms = d1_open_ms + 3_600_000 - 1

    feat_by_close_ms: dict[int, dict[str, Any]] = {}
    for i in range(len(d1)):
        c_ms = int(d1_close_ms[i])
        # Latest strictly closed 4H bar with d4_close_ms <= c_ms
        idx4 = int(np.searchsorted(d4_close_ms, c_ms, side="right")) - 1
        if idx4 >= 0:
            c4 = float(d4["close"].iloc[idx4])
            e4 = float(ema100_4h.iloc[idx4]) if pd.notna(ema100_4h.iloc[idx4]) else np.nan
            s4 = float(slope_ema100_4h.iloc[idx4]) if pd.notna(slope_ema100_4h.iloc[idx4]) else np.nan
            c4_dt = str(d4.index[idx4])
        else:
            c4, e4, s4, c4_dt = np.nan, np.nan, np.nan, ""

        dem_v = float(dem_1h.iloc[i]) if pd.notna(dem_1h.iloc[i]) else np.nan
        adx_v = float(adx_1h.iloc[i]) if pd.notna(adx_1h.iloc[i]) else np.nan
        pdi_v = float(pdi_1h.iloc[i]) if pd.notna(pdi_1h.iloc[i]) else np.nan
        mdi_v = float(mdi_1h.iloc[i]) if pd.notna(mdi_1h.iloc[i]) else np.nan
        atr_v = float(atr_1h.iloc[i]) if pd.notna(atr_1h.iloc[i]) else np.nan

        h2_pass = bool(np.isfinite(c4) and np.isfinite(e4) and np.isfinite(s4) and (c4 < e4) and (s4 < 0.0))
        h3_pass = bool(np.isfinite(dem_v) and (dem_v < 0.20))
        h4_pass = bool(np.isfinite(adx_v) and np.isfinite(pdi_v) and np.isfinite(mdi_v) and (adx_v > 20.0) and (mdi_v > pdi_v))

        feat_by_close_ms[c_ms] = {
            "demarker_60": dem_v,
            "adx_14": adx_v,
            "plus_di_14": pdi_v,
            "minus_di_14": mdi_v,
            "atr_14_1h": atr_v,
            "smc_dir_1h": int(smc_1h.iloc[i]),
            "ob_buy_blocked": bool(ob_1h["buy_blocked"].iloc[i]),
            "ob_sell_blocked": bool(ob_1h["sell_blocked"].iloc[i]),
            "close_4h": c4,
            "ema100_4h": e4,
            "slope_ema100_4h": s4,
            "closed_4h_open_dt": c4_dt,
            "h2_sell_pass": h2_pass,
            "h3_sell_pass": h3_pass,
            "h4_sell_pass": h4_pass,
        }
    return feat_by_close_ms


def filter_signals_for_variant(
    h0_sigs: list[Any],
    variant: str,
    feat_by_close_ms: dict[int, dict[str, Any]],
) -> list[Any]:
    """Filter ETH-USDT/C11 SELL signals according to H0..H4 while keeping BUY and all other 29 combos identical."""
    if variant == "H0":
        return list(h0_sigs)
    out = []
    for s in h0_sigs:
        if s.symbol == "ETH-USDT" and int(s.combo_id) == 101 and s.side == "SELL":
            if variant == "H1":
                continue
            f = feat_by_close_ms.get(int(s.signal_close_ms))
            if f is None:
                continue
            if variant == "H2" and not f["h2_sell_pass"]:
                continue
            if variant == "H3" and not f["h3_sell_pass"]:
                continue
            if variant == "H4" and not f["h4_sell_pass"]:
                continue
        out.append(s)
    return out


def compute_c11_stats(trades: list[Any], side: str | None = None) -> dict[str, Any]:
    sub = sorted(
        [t for t in trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101 and (side is None or t.side == side)],
        key=lambda t: (t.exit_time_ms, t.entry_time_ms),
    )
    pnls = [float(t.pnl_own_entry) for t in sub]
    tp = sum(1 for t in sub if t.exit_reason == "TP")
    sl = sum(1 for t in sub if t.exit_reason == "SL")
    wr = (tp / len(sub) * 100.0) if sub else 0.0
    return {
        "trades": len(sub),
        "tp": tp,
        "sl": sl,
        "win_rate_pct": round(wr, 2),
        "own_net_pnl": round(sum(pnls), 4),
        "gross_pnl": round(sum(float(t.gross_pnl) for t in sub), 4),
        "fee_total": round(sum(float(t.fee_total) for t in sub), 4),
        "pf": round(profit_factor(pnls), 4),
        "closed_max_dd": round(closed_max_drawdown(pnls), 4),
    }


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
    feat_by_close_ms = build_eth_c11_feature_table(m15_dict["ETH-USDT"])
    cost_models = {p: CostModel.from_profile_name(p, is_real_data=True) for p in OTPC_PROFILES}

    sim_results: dict[tuple[str, str, str], tuple[Any, dict[str, Any]]] = {}
    for vname in VARIANTS:
        v_sigs = filter_signals_for_variant(h0_eval_sigs, vname, feat_by_close_ms)
        eth11_sigs = [s for s in v_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 101]
        print(
            f"[{vname}] ETH/C11 eval signals: total={len(eth11_sigs)}, "
            f"BUY={sum(1 for s in eth11_sigs if s.side == 'BUY')}, "
            f"SELL={sum(1 for s in eth11_sigs if s.side == 'SELL')}"
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
                c11_s = compute_c11_stats(res.trades)
                print(
                    f"  {mode:19s} | {profile:6s} | port_trades={len(res.trades):5d} "
                    f"port_net={an['pnl']['net_pnl']:+8.2f} dd={an['risk'].get('m2m_max_drawdown_dollars') or 0.0:6.2f} "
                    f"| c11={c11_s['trades']:3d} TP={c11_s['tp']:3d} SL={c11_s['sl']:3d} "
                    f"own={c11_s['own_net_pnl']:+7.4f} ({time.time()-t_r0:.2f}s)"
                )

    # Verify H0 baseline matches V3_LAB_2_FULL_30_UPGRADED_3.zip
    h0_b = compute_c11_stats(sim_results[("H0", "ONE_TRADE_PER_COMBO", "BASE")][0].trades)
    h0_s = compute_c11_stats(sim_results[("H0", "ONE_TRADE_PER_COMBO", "STRESS")][0].trades)
    assert h0_b["trades"] == 461 and h0_b["tp"] == 178 and h0_b["sl"] == 283
    assert abs(h0_b["own_net_pnl"] - 10.3875) < 1e-3
    assert abs(h0_s["own_net_pnl"] - (-17.8424)) < 1e-3
    assert abs(h0_b["closed_max_dd"] - 23.1114) < 1e-3
    print("\n[VERIFIED] H0 ETH/C11 matches V3_LAB_2_FULL_30_UPGRADED_3 baseline 100%!")

    # 1. Feature Diagnostics on 461 H0 BASE trades
    h0_base_c11 = sorted(
        [t for t in sim_results[("H0", "ONE_TRADE_PER_COMBO", "BASE")][0].trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101],
        key=lambda t: t.entry_time_ms,
    )
    diag_rows = []
    for t in h0_base_c11:
        sig_close_ms = int(t.signal_id.rsplit("_", 1)[-1])
        f = feat_by_close_ms.get(sig_close_ms, {})
        dt_str = str(t.entry_time_dt)
        diag_rows.append(
            {
                "signal_id": t.signal_id,
                "side": t.side,
                "year": dt_str[:4],
                "month": dt_str[:7],
                "entry_time_dt": dt_str,
                "exit_time_dt": str(t.exit_time_dt),
                "entry_price": round(float(t.entry_price), 4),
                "exit_price": round(float(t.exit_price), 4),
                "tp_pct": float(t.tp_pct),
                "sl_pct": float(t.sl_pct),
                "bars_held": int(t.bars_held),
                "demarker_60": round(float(f.get("demarker_60", np.nan)), 5),
                "adx_14": round(float(f.get("adx_14", np.nan)), 4),
                "plus_di_14": round(float(f.get("plus_di_14", np.nan)), 4),
                "minus_di_14": round(float(f.get("minus_di_14", np.nan)), 4),
                "close_4h": round(float(f.get("close_4h", np.nan)), 4),
                "ema100_4h": round(float(f.get("ema100_4h", np.nan)), 4),
                "slope_ema100_4h": round(float(f.get("slope_ema100_4h", np.nan)), 6),
                "atr_14_1h": round(float(f.get("atr_14_1h", np.nan)), 4),
                "smc_dir_1h": f.get("smc_dir_1h"),
                "ob_blocked": f.get("ob_buy_blocked") if t.side == "BUY" else f.get("ob_sell_blocked"),
                "layer2": "SMC_STRICT_OB",
                "exit_reason": t.exit_reason,
                "own_net_pnl_base": round(float(t.pnl_own_entry), 4),
                "h2_sell_pass": f.get("h2_sell_pass") if t.side == "SELL" else True,
                "h3_sell_pass": f.get("h3_sell_pass") if t.side == "SELL" else True,
                "h4_sell_pass": f.get("h4_sell_pass") if t.side == "SELL" else True,
            }
        )
    pd.DataFrame(diag_rows).to_csv(OUT_DIR / "ETH_C11_FEATURE_DIAGNOSTICS.csv", index=False)

    # 2. Comparison table H0..H4 + Signal Diff + Yearly/Monthly + Portfolio Impact + Trades CSV
    comp_rows = []
    yearly_rows = []
    diff_rows = []
    port_rows = []
    trades_base_dfs = []
    trades_stress_dfs = []

    h0_otpc_map = {
        p: {t.signal_id: t for t in sim_results[("H0", "ONE_TRADE_PER_COMBO", p)][0].trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101}
        for p in OTPC_PROFILES
    }

    for vname in VARIANTS:
        for p in OTPC_PROFILES:
            tr = sim_results[(vname, "ONE_TRADE_PER_COMBO", p)][0].trades
            c11_tr = [t for t in tr if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
            s_all = compute_c11_stats(tr)
            s_buy = compute_c11_stats(tr, "BUY")
            s_sell = compute_c11_stats(tr, "SELL")

            v_map = {t.signal_id: t for t in c11_tr}
            h0_map = h0_otpc_map[p]
            removed_sids = set(h0_map) - set(v_map)
            new_sids = set(v_map) - set(h0_map)
            removed_tp = sum(1 for sid in removed_sids if h0_map[sid].exit_reason == "TP")
            removed_sl = sum(1 for sid in removed_sids if h0_map[sid].exit_reason == "SL")

            comp_rows.append(
                {
                    "variant": vname,
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
                    "delta_own_pnl_vs_h0": round(s_all["own_net_pnl"] - compute_c11_stats(sim_results[("H0", "ONE_TRADE_PER_COMBO", p)][0].trades)["own_net_pnl"], 4),
                    "pf": s_all["pf"],
                    "closed_max_dd": s_all["closed_max_dd"],
                    "removed_from_h0": len(removed_sids),
                    "removed_h0_tp": removed_tp,
                    "removed_h0_sl": removed_sl,
                    "new_concurrency_trades": len(new_sids),
                }
            )

            # Signal diff for H1..H4 under BASE and STRESS
            if vname != "H0" and p in ("BASE", "STRESS"):
                for sid in sorted(set(h0_map) | set(v_map)):
                    t0_r = h0_map.get(sid)
                    tv_r = v_map.get(sid)
                    if t0_r is not None and tv_r is not None:
                        status = "KEPT"
                    elif t0_r is not None and tv_r is None:
                        status = "FILTERED_OUT"
                    else:
                        status = "NEW_CONCURRENCY"
                    ref = tv_r or t0_r
                    diff_rows.append(
                        {
                            "variant": vname,
                            "cost_profile": p,
                            "signal_id": sid,
                            "status": status,
                            "side": ref.side,
                            "entry_time_dt": str(ref.entry_time_dt),
                            "h0_exit_reason": t0_r.exit_reason if t0_r else None,
                            "h0_own_pnl": round(float(t0_r.pnl_own_entry), 4) if t0_r else None,
                            "var_exit_reason": tv_r.exit_reason if tv_r else None,
                            "var_own_pnl": round(float(tv_r.pnl_own_entry), 4) if tv_r else None,
                        }
                    )

            # Yearly breakdown
            if p in ("BASE", "STRESS"):
                for yr in ["2023", "2024", "2025", "2026"]:
                    for sd in ["ALL", "BUY", "SELL"]:
                        sub_y = [
                            t for t in c11_tr
                            if str(t.entry_time_dt).startswith(yr) and (sd == "ALL" or t.side == sd)
                        ]
                        tp_y = sum(1 for t in sub_y if t.exit_reason == "TP")
                        yearly_rows.append(
                            {
                                "variant": vname,
                                "cost_profile": p,
                                "year": yr,
                                "side": sd,
                                "trades": len(sub_y),
                                "tp": tp_y,
                                "sl": len(sub_y) - tp_y,
                                "own_net_pnl": round(sum(float(t.pnl_own_entry) for t in sub_y), 4),
                            }
                        )

        # Portfolio impact
        for p in PORT_PROFILES:
            res_p, an_p = sim_results[(vname, "BINGX_PORTFOLIO", p)]
            c11_p = [t for t in res_p.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 101]
            port_rows.append(
                {
                    "variant": vname,
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

    pd.DataFrame(comp_rows).to_csv(OUT_DIR / "ETH_C11_H0_H4_COMPARISON.csv", index=False)
    pd.DataFrame(yearly_rows).to_csv(OUT_DIR / "ETH_C11_BUY_SELL_YEARLY.csv", index=False)
    pd.DataFrame(diff_rows).to_csv(OUT_DIR / "ETH_C11_SIGNAL_DIFF.csv", index=False)
    pd.DataFrame(port_rows).to_csv(OUT_DIR / "ETH_C11_PORTFOLIO_IMPACT.csv", index=False)
    pd.concat(trades_base_dfs, ignore_index=True).to_csv(OUT_DIR / "ETH_C11_TRADES_BASE.csv", index=False)
    pd.concat(trades_stress_dfs, ignore_index=True).to_csv(OUT_DIR / "ETH_C11_TRADES_STRESS.csv", index=False)

    code_sha, code_dirty, _ = get_git_state()
    manifest = {
        "research_id": "V3_LAB_4_ETH_C11_ROUND1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "v3_lab_4_config_hash": cfg["config_hash"],
        "dataset_hash": dataset_hash,
        "period": {"start": START_DATE, "end": END_DATE},
        "symbols": SYMBOLS,
        "initial_equity": INITIAL_EQUITY,
        "notional_per_trade": NOTIONAL_PER_TRADE,
        "ambiguity_policy": "CONSERVATIVE",
        "variants": VARIANTS,
        "cost_profiles": {p: cost_models[p].profile.profile_hash for p in OTPC_PROFILES},
        "git_state": {"sha": code_sha, "dirty": code_dirty},
        "env_versions": get_env_versions(),
        "runner_script": "scripts/run_v3_lab_4_eth_c11_round1.py",
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nCompleted in {time.time() - t0:.2f}s! Outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()
