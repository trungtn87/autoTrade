"""Backtest research for ETH-USDT/C15 H3_ASYMMETRIC on V3_LAB_2@2.0.0.

H3_ASYMMETRIC rules for ETH-USDT/C15 (combo_id=105):
- BUY:  L2 H2A filter (abs(close_1h - kama_40) / atr_14 <= 2.0 on closed 1H candle), no cooldown.
- SELL: Original KAMA_ER + L2 OB, plus 60-minute cooldown after a C15 SELL trade closes by TP (no cooldown after SL).

Runs 4 H3_ASYMMETRIC configs: (ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO) x (BASE, STRESS).
Also runs LOO_C15 (19 combos, excluding ETH-USDT/C15) on BINGX_PORTFOLIO x (BASE, STRESS) for direct comparison.
Reuses H0 & H2A_BUY_ONLY from D:\\AutoTrade\\runs\\lab\\v3_lab_2\\eth_c15_h2_buy_only\\.
Saves outputs to D:\\AutoTrade\\runs\\lab\\v3_lab_2\\eth_c15_h3_asymmetric\\.
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
from autotrade_v3.engine.execution_simulator import SimulationResult
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.lab.v3_lab_2_loader import load_v3_lab_2_config, load_v3_lab_2_strategy
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import UnifiedSimulator, _resolve_signal_id
from scripts.run_v3_lab_2_eth_c15_h2 import (
    END_DATE,
    EXPECTED_DATASET_HASH,
    EXPECTED_V3_LAB_2_HASH,
    H2A_MAX_DIST_KAMA_ATR,
    INITIAL_EQUITY,
    NOTIONAL_PER_TRADE,
    START_DATE,
    SYMBOLS,
    build_eth_1h_l2_features,
    closed_max_drawdown,
    profit_factor,
    sha256_file,
)
from scripts.run_v3_lab_2_eth_c15_research import ResearchUnifiedSimulator

H1_DIR = ROOT / "runs" / "lab" / "v3_lab_2" / "eth_c15_research"
H2_BUY_DIR = ROOT / "runs" / "lab" / "v3_lab_2" / "eth_c15_h2_buy_only"
OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_2" / "eth_c15_h3_asymmetric"
MODES = ["ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
PROFILES = ["BASE", "STRESS"]


class H3AsymmetricController:
    """Chronological admission controller for H3_ASYMMETRIC on ETH-USDT/C15."""

    def __init__(self, feat_map: dict[int, dict[str, float]]) -> None:
        self.feat_map = feat_map
        self.last_sell_tp_exit_ms: int | None = None

    @staticmethod
    def is_target(symbol: str, combo_id: int) -> bool:
        return symbol == "ETH-USDT" and int(combo_id) == 105

    def on_trade_exit(self, symbol: str, combo_id: int, side: str, exit_time_ms: int, exit_reason: str) -> None:
        if self.is_target(symbol, combo_id) and side == "SELL" and exit_reason == "TP":
            if self.last_sell_tp_exit_ms is None or exit_time_ms > self.last_sell_tp_exit_ms:
                self.last_sell_tp_exit_ms = exit_time_ms

    def should_allow(self, sig: Signal, entry_time_ms: int) -> tuple[bool, str]:
        if not self.is_target(sig.symbol, sig.combo_id):
            return True, "ACCEPTED"
        if sig.side == "BUY":
            d = self.feat_map[int(sig.signal_close_ms)]["dist_kama_atr"]
            if not np.isfinite(d) or d > H2A_MAX_DIST_KAMA_ATR:
                return False, "BLOCKED_L2_H2A_BUY_DIST_KAMA_ATR"
            return True, "ACCEPTED"
        if sig.side == "SELL":
            if self.last_sell_tp_exit_ms is not None and entry_time_ms < self.last_sell_tp_exit_ms + 3_600_000:
                return False, "BLOCKED_H1A_SELL_COOLDOWN_1H"
            return True, "ACCEPTED"
        return True, "ACCEPTED"


def main() -> None:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_v3_lab_2_config()
    if cfg.get("config_hash") != EXPECTED_V3_LAB_2_HASH:
        raise RuntimeError(f"V3_LAB_2 config hash mismatch: {cfg.get('config_hash')}")

    strat = load_v3_lab_2_strategy()
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
    eval_sigs = [s for s in all_sigs if start_ms <= s.signal_close_ms + 1 < end_ms]
    c15_sigs = [s for s in eval_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 105]
    sig_by_id = {_resolve_signal_id(s): s for s in c15_sigs}
    feat_map = build_eth_1h_l2_features(m15_dict["ETH-USDT"])

    ref_comp = pd.read_csv(H2_BUY_DIR / "C15_H2A_BUY_ONLY_COMPARISON.csv")
    ref_bd = pd.read_csv(H2_BUY_DIR / "C15_H2A_BUY_ONLY_YEAR_SIDE_BREAKDOWN.csv")
    h2a_buy_trades_df = pd.read_csv(H2_BUY_DIR / "C15_H2A_BUY_ONLY_TRADES.csv")
    h1_trades_df = pd.read_csv(H1_DIR / "C15_TRADE_DETAILS.csv")

    cost_models = {p: CostModel.from_profile_name(p, is_real_data=True) for p in PROFILES}

    new_comp_rows: list[dict[str, Any]] = []
    new_bd_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    dec_rows: list[dict[str, Any]] = []
    parity_rows: list[dict[str, Any]] = []
    loo_rows: list[dict[str, Any]] = []

    # 1. Run H3_ASYMMETRIC (4 configs)
    run_idx = 0
    for mode in MODES:
        for profile in PROFILES:
            run_idx += 1
            t_r0 = time.time()
            cm = cost_models[profile]
            risk = RiskConfig(
                initial_equity=INITIAL_EQUITY,
                notional_per_trade=NOTIONAL_PER_TRADE,
                cost_profile=profile,
                cost_model=cm,
            )
            ctrl = H3AsymmetricController(feat_map)
            sim = ResearchUnifiedSimulator(
                risk_config=risk,
                cost_model=cm,
                ambiguity_policy="CONSERVATIVE",
                c15_controller=ctrl,  # type: ignore[arg-type]
            )
            res: SimulationResult = sim.run(mode, eval_sigs, candles_m1_eval)
            analysis = ResearchAnalyzer.analyze_simulation(res, initial_equity=INITIAL_EQUITY)
            port_net = float(analysis["pnl"]["net_pnl"])
            port_m2m_dd = float(analysis["risk"].get("m2m_max_drawdown_dollars") or 0.0)
            port_closed_dd = float(analysis["risk"].get("closed_trade_max_drawdown_dollars") or 0.0)

            c15_decs = [d for d in res.signal_decisions if d.symbol == "ETH-USDT" and int(d.combo_id) == 105]
            c15_trades = sorted(
                [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 105],
                key=lambda t: (t.exit_time_ms, t.entry_time_ms),
            )

            h0_row = ref_comp[
                (ref_comp["variant"] == "H0") & (ref_comp["mode"] == mode) & (ref_comp["cost_profile"] == profile)
            ].iloc[0]
            h0_p_net = float(h0_row["portfolio_net_pnl"])
            h0_p_dd = float(h0_row["portfolio_m2m_max_dd"])

            h0_ids = set(
                h1_trades_df[
                    (h1_trades_df["variant"] == "H0") & (h1_trades_df["mode"] == mode) & (h1_trades_df["cost_profile"] == profile)
                ]["signal_id"].astype(str)
            )
            h2b_buy_ids = set(
                h2a_buy_trades_df[
                    (h2a_buy_trades_df["mode"] == mode) & (h2a_buy_trades_df["cost_profile"] == profile) & (h2a_buy_trades_df["side"] == "BUY")
                ]["signal_id"].astype(str)
            )
            h1a_sell_ids = set(
                h1_trades_df[
                    (h1_trades_df["variant"] == "H1A") & (h1_trades_df["mode"] == mode) & (h1_trades_df["cost_profile"] == profile) & (h1_trades_df["side"] == "SELL")
                ]["signal_id"].astype(str)
            )

            curr_ids = {t.signal_id for t in c15_trades}
            curr_buy_ids = {t.signal_id for t in c15_trades if t.side == "BUY"}
            curr_sell_ids = {t.signal_id for t in c15_trades if t.side == "SELL"}
            new_vs_h0 = curr_ids - h0_ids

            blocked_h2a = sum(1 for d in c15_decs if d.reason == "BLOCKED_L2_H2A_BUY_DIST_KAMA_ATR")
            blocked_h1a = sum(1 for d in c15_decs if d.reason == "BLOCKED_H1A_SELL_COOLDOWN_1H")
            skipped_conc = sum(1 for d in c15_decs if d.reason == "SKIPPED_BY_CONCURRENCY")
            tp_cnt = sum(1 for t in c15_trades if t.exit_reason == "TP")
            sl_cnt = sum(1 for t in c15_trades if t.exit_reason == "SL")
            n_tr = len(c15_trades)
            own_pnls = [t.pnl_own_entry for t in c15_trades]
            vwap_pnls = [t.pnl_vwap for t in c15_trades]

            new_comp_rows.append(
                {
                    "variant": "H3_ASYMMETRIC",
                    "mode": mode,
                    "cost_profile": profile,
                    "c15_signals_total": len(c15_decs),
                    "c15_trades_accepted": n_tr,
                    "c15_blocked_l2_h2a": blocked_h2a,
                    "c15_blocked_cooldown_h1a": blocked_h1a,
                    "c15_skipped_concurrency": skipped_conc,
                    "c15_new_trades_vs_h0": len(new_vs_h0),
                    "tp_count": tp_cnt,
                    "sl_count": sl_cnt,
                    "win_rate_pct": round((tp_cnt / n_tr * 100.0) if n_tr else 0.0, 2),
                    "gross_pnl_raw": round(sum(t.gross_pnl_raw for t in c15_trades), 4),
                    "gross_pnl": round(sum(t.gross_pnl for t in c15_trades), 4),
                    "fee_total": round(sum(t.fee_total for t in c15_trades), 4),
                    "slippage_cost": round(sum(t.slippage_cost for t in c15_trades), 4),
                    "own_net_pnl": round(sum(own_pnls), 4),
                    "vwap_allocated_pnl": round(sum(vwap_pnls), 4),
                    "profit_factor_own": round(profit_factor(own_pnls), 4),
                    "profit_factor_vwap": round(profit_factor(vwap_pnls), 4),
                    "c15_max_dd_own": round(closed_max_drawdown(own_pnls), 4),
                    "portfolio_total_trades": len(res.trades),
                    "portfolio_net_pnl": round(port_net, 4),
                    "portfolio_m2m_max_dd": round(port_m2m_dd, 4),
                    "portfolio_closed_max_dd": round(port_closed_dd, 4),
                    "delta_port_pnl_vs_h0": round(port_net - h0_p_net, 4),
                    "delta_port_m2m_dd_vs_h0": round(port_m2m_dd - h0_p_dd, 4) if mode == "BINGX_PORTFOLIO" else "",
                }
            )

            parity_rows.append(
                {
                    "mode": mode,
                    "cost_profile": profile,
                    "h3_buy_trades": len(curr_buy_ids),
                    "h2a_buy_only_buy_trades": len(h2b_buy_ids),
                    "buy_identical_to_h2a_buy_only": (curr_buy_ids == h2b_buy_ids),
                    "buy_added": len(curr_buy_ids - h2b_buy_ids),
                    "buy_dropped": len(h2b_buy_ids - curr_buy_ids),
                    "h3_sell_trades": len(curr_sell_ids),
                    "h1a_sell_trades": len(h1a_sell_ids),
                    "sell_identical_to_h1a_sell": (curr_sell_ids == h1a_sell_ids),
                    "sell_added_vs_h1a": len(curr_sell_ids - h1a_sell_ids),
                    "sell_dropped_vs_h1a": len(h1a_sell_ids - curr_sell_ids),
                }
            )

            for gtype, gvals in [
                ("SIDE", ["BUY", "SELL"]),
                ("YEAR", sorted({str(t.entry_time_dt)[:4] for t in c15_trades})),
            ]:
                for gv in gvals:
                    sub = [
                        t for t in c15_trades
                        if (t.side == gv if gtype == "SIDE" else str(t.entry_time_dt).startswith(gv))
                    ]
                    sub_own = [t.pnl_own_entry for t in sub]
                    sub_vwap = [t.pnl_vwap for t in sub]
                    tp_g = sum(1 for t in sub if t.exit_reason == "TP")
                    new_bd_rows.append(
                        {
                            "variant": "H3_ASYMMETRIC",
                            "mode": mode,
                            "cost_profile": profile,
                            "group_type": gtype,
                            "group_value": gv,
                            "trades": len(sub),
                            "tp_count": tp_g,
                            "sl_count": len(sub) - tp_g,
                            "win_rate_pct": round((tp_g / len(sub) * 100.0) if sub else 0.0, 2),
                            "own_net_pnl": round(sum(sub_own), 4),
                            "vwap_allocated_pnl": round(sum(sub_vwap), 4),
                            "profit_factor_own": round(profit_factor(sub_own), 4),
                            "max_dd_own": round(closed_max_drawdown(sub_own), 4),
                        }
                    )

            for t in c15_trades:
                s_obj = sig_by_id[t.signal_id]
                f_val = feat_map[int(s_obj.signal_close_ms)]
                trade_rows.append(
                    {
                        "variant": "H3_ASYMMETRIC",
                        "mode": mode,
                        "cost_profile": profile,
                        "trade_id": t.trade_id,
                        "signal_id": t.signal_id,
                        "side": t.side,
                        "entry_time_dt": str(t.entry_time_dt),
                        "exit_time_dt": str(t.exit_time_dt),
                        "entry_price": round(t.entry_price, 6),
                        "exit_price": round(t.exit_price, 6),
                        "exit_reason": t.exit_reason,
                        "bars_held": t.bars_held,
                        "gross_pnl_raw": round(t.gross_pnl_raw, 6),
                        "fee_total": round(t.fee_total, 6),
                        "slippage_cost": round(t.slippage_cost, 6),
                        "pnl_own_entry": round(t.pnl_own_entry, 6),
                        "pnl_vwap": round(t.pnl_vwap, 6),
                        "is_new_vs_h0": t.signal_id in new_vs_h0,
                        **{k: (round(v, 6) if np.isfinite(v) else "") for k, v in f_val.items()},
                    }
                )

            for d in c15_decs:
                f_val = feat_map[int(d.signal_close_ms)]
                dec_rows.append(
                    {
                        "variant": "H3_ASYMMETRIC",
                        "mode": mode,
                        "cost_profile": profile,
                        "signal_id": d.signal_id,
                        "side": d.side,
                        "signal_close_dt": d.signal_close_dt,
                        "status": d.status,
                        "reason": d.reason,
                        "blocking_trade_id": d.blocking_trade_id,
                        **{k: (round(v, 6) if np.isfinite(v) else "") for k, v in f_val.items()},
                    }
                )

            print(
                f"[{run_idx}/4] H3_ASYMMETRIC | {mode:19s} | {profile:6s} | "
                f"C15 tr={n_tr:3d} blkH2A={blocked_h2a:3d} blkH1A={blocked_h1a:3d} "
                f"own={sum(own_pnls):+7.2f} vwap={sum(vwap_pnls):+7.2f} "
                f"port={port_net:+7.2f} dd={port_m2m_dd:6.2f} ({time.time()-t_r0:.2f}s)"
            )

    # 2. Run LOO_C15 (19 combos without ETH-USDT/C15) on BINGX_PORTFOLIO x (BASE, STRESS)
    loo_sigs = [s for s in eval_sigs if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 105)]
    for profile in PROFILES:
        t_l0 = time.time()
        cm = cost_models[profile]
        risk = RiskConfig(
            initial_equity=INITIAL_EQUITY,
            notional_per_trade=NOTIONAL_PER_TRADE,
            cost_profile=profile,
            cost_model=cm,
        )
        res_loo = UnifiedSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE").run(
            "BINGX_PORTFOLIO", loo_sigs, candles_m1_eval
        )
        an_loo = ResearchAnalyzer.analyze_simulation(res_loo, initial_equity=INITIAL_EQUITY)
        port_net = float(an_loo["pnl"]["net_pnl"])
        port_m2m_dd = float(an_loo["risk"].get("m2m_max_drawdown_dollars") or 0.0)
        port_closed_dd = float(an_loo["risk"].get("closed_trade_max_drawdown_dollars") or 0.0)
        h0_row = ref_comp[
            (ref_comp["variant"] == "H0") & (ref_comp["mode"] == "BINGX_PORTFOLIO") & (ref_comp["cost_profile"] == profile)
        ].iloc[0]
        loo_rows.append(
            {
                "variant": "LOO_C15",
                "mode": "BINGX_PORTFOLIO",
                "cost_profile": profile,
                "c15_signals_total": 0,
                "c15_trades_accepted": 0,
                "c15_blocked_l2_h2a": 0,
                "c15_blocked_cooldown_h1a": 0,
                "c15_skipped_concurrency": 0,
                "c15_new_trades_vs_h0": 0,
                "tp_count": 0,
                "sl_count": 0,
                "win_rate_pct": 0.0,
                "gross_pnl_raw": 0.0,
                "gross_pnl": 0.0,
                "fee_total": 0.0,
                "slippage_cost": 0.0,
                "own_net_pnl": 0.0,
                "vwap_allocated_pnl": 0.0,
                "profit_factor_own": 0.0,
                "profit_factor_vwap": 0.0,
                "c15_max_dd_own": 0.0,
                "portfolio_total_trades": len(res_loo.trades),
                "portfolio_net_pnl": round(port_net, 4),
                "portfolio_m2m_max_dd": round(port_m2m_dd, 4),
                "portfolio_closed_max_dd": round(port_closed_dd, 4),
                "delta_port_pnl_vs_h0": round(port_net - float(h0_row["portfolio_net_pnl"]), 4),
                "delta_port_m2m_dd_vs_h0": round(port_m2m_dd - float(h0_row["portfolio_m2m_max_dd"]), 4),
            }
        )
        print(f"[LOO_C15] BINGX_PORTFOLIO | {profile:6s} | trades={len(res_loo.trades)} port={port_net:+7.2f} dd={port_m2m_dd:6.2f} ({time.time()-t_l0:.2f}s)")

    # Combine H0, H2A_BUY_ONLY, H3_ASYMMETRIC, LOO_C15
    ref_sub = ref_comp[ref_comp["variant"].isin(["H0", "H2A_BUY_ONLY"])].copy()
    ref_sub["c15_blocked_l2_h2a"] = ref_sub["c15_blocked_l2"]
    ref_sub["c15_blocked_cooldown_h1a"] = 0
    comp_all = pd.concat([ref_sub, pd.DataFrame(new_comp_rows), pd.DataFrame(loo_rows)], ignore_index=True)
    comp_all.to_csv(OUT_DIR / "C15_H3_COMPARISON.csv", index=False)

    ref_bd_sub = ref_bd[ref_bd["variant"].isin(["H0", "H2A_BUY_ONLY"])].copy()
    bd_all = pd.concat([ref_bd_sub, pd.DataFrame(new_bd_rows)], ignore_index=True)
    bd_all.to_csv(OUT_DIR / "C15_H3_YEAR_SIDE_BREAKDOWN.csv", index=False)

    pd.DataFrame(trade_rows).to_csv(OUT_DIR / "C15_H3_TRADES.csv", index=False)
    pd.DataFrame(dec_rows).to_csv(OUT_DIR / "C15_H3_SIGNAL_DECISIONS.csv", index=False)
    pd.DataFrame(parity_rows).to_csv(OUT_DIR / "C15_H3_PARITY_CHECK.csv", index=False)

    code_sha, code_dirty, _ = get_git_state()
    manifest = {
        "research_id": "V3_LAB_2_ETH_C15_H3_ASYMMETRIC",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "v3_lab_2_config_id": cfg["config_id"],
        "v3_lab_2_version": cfg["version"],
        "v3_lab_2_config_hash": cfg["config_hash"],
        "v3_lab_2_spec_hash": strat.get_spec_hash(),
        "dataset_hash": dataset_hash,
        "period": {"start": START_DATE, "end": END_DATE},
        "symbols": SYMBOLS,
        "initial_equity": INITIAL_EQUITY,
        "notional_per_trade": NOTIONAL_PER_TRADE,
        "ambiguity_policy": "CONSERVATIVE",
        "rule": "BUY: H2A (dist_kama_atr <= 2.0), no cooldown; SELL: original KAMA_ER + L2 OB + 60m cooldown after SELL TP",
        "runs_executed": 6,
        "git_state": {"sha": code_sha, "dirty": code_dirty},
        "env_versions": get_env_versions(),
        "runner_script": "scripts/run_v3_lab_2_eth_c15_h3_asymmetric.py",
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nCompleted in {time.time() - t0:.2f}s! Output dir: {OUT_DIR}")


if __name__ == "__main__":
    main()
