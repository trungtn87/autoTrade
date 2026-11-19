"""Backtest research for ETH-USDT/C15 L2 indicator filters on V3_LAB_2@2.0.0.

Variants (fixed a priori thresholds, no grid search):
- H0:  V3_LAB_2@2.0.0 baseline (unchanged)
- H2A: Anti-chasing — require abs(close_1h - kama_40) / atr_14 <= 2.0 on closed 1H candle
- H2B: Trend confirmation — require adx_14 >= 20.0 AND (plus_di > minus_di for BUY, minus_di > plus_di for SELL)
- H2C: Volatility band — require 0.003 <= (atr_14 / close_1h) <= 0.015 on closed 1H candle

Runs H0, H2A, H2B, H2C x (ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO) x (BASE, STRESS) = 16 configs.
Saves all outputs to D:\\AutoTrade\\runs\\lab\\v3_lab_2\\eth_c15_h2\\ and packages V3_LAB_2_ETH_C15_H2.zip.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any
import zipfile

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
from autotrade_v3.sim.types import SignalDecision
from autotrade_v3.strategy_core.indicators.indicators import atr, dmi
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.strategies.final20.logic import _kama_er

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_2" / "eth_c15_h2"
H0_REF_CSV = ROOT / "runs" / "lab" / "v3_lab_2" / "eth_c15_research" / "C15_H0_H1A_H1B_H1C_COMPARISON.csv"
START_DATE = "2023-11-01"
END_DATE = "2026-11-01"
SYMBOLS = ["BTC-USDT", "ETH-USDT"]
MODES = ["ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
PROFILES = ["BASE", "STRESS"]
VARIANTS = ["H0", "H2A", "H2B", "H2C"]
INITIAL_EQUITY = 1000.0
NOTIONAL_PER_TRADE = 100.0
EXPECTED_V3_LAB_2_HASH = "038dd40e4dac72f5811d92f682f6f1e522a0f6f643d8acefa1189dda8065d85e"
EXPECTED_DATASET_HASH = "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628"

# Fixed a priori L2 filter thresholds
H2A_MAX_DIST_KAMA_ATR = 2.0
H2B_MIN_ADX_14 = 20.0
H2C_MIN_ATR_PCT = 0.003
H2C_MAX_ATR_PCT = 0.015


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_eth_1h_l2_features(df_eth_m15: pd.DataFrame) -> dict[int, dict[str, float]]:
    """Compute Strategy Core 1H indicators (KAMA40, ER40, ATR14, ADX14, DI+/DI-) indexed by 1H close_ms."""
    d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
    c = d1["close"].to_numpy(dtype=float)
    kama, er = _kama_er(c, 40, 2, 40)
    atr14 = atr(d1, 14).to_numpy(dtype=float)
    plus_di, minus_di, adx14 = dmi(d1, di_len=14, adx_len=14)
    pdi = plus_di.to_numpy(dtype=float)
    mdi = minus_di.to_numpy(dtype=float)
    adx = adx14.to_numpy(dtype=float)

    open_ms = datetime_index_to_ms(d1.index)
    close_ms = open_ms + 3_600_000 - 1

    feat_map: dict[int, dict[str, float]] = {}
    for i, cms in enumerate(close_ms):
        cl = float(c[i])
        km = float(kama[i])
        at = float(atr14[i])
        dist_atr = abs(cl - km) / at if (np.isfinite(at) and at > 0 and np.isfinite(km)) else np.nan
        atr_pct = at / cl if (np.isfinite(at) and cl > 0) else np.nan
        feat_map[int(cms)] = {
            "close_1h": cl,
            "kama_40": km,
            "er_40": float(er[i]),
            "atr_14": at,
            "dist_kama_atr": dist_atr,
            "adx_14": float(adx[i]),
            "plus_di_14": float(pdi[i]),
            "minus_di_14": float(mdi[i]),
            "atr_pct": atr_pct,
        }
    return feat_map


def evaluate_c15_l2_filter(variant: str, side: str, feat: dict[str, float]) -> tuple[bool, str]:
    """Evaluate fixed a priori L2 filter on closed 1H candle features for ETH-USDT/C15."""
    if variant == "H0":
        return True, "ACCEPTED"

    if variant == "H2A":
        d = feat["dist_kama_atr"]
        if not np.isfinite(d) or d > H2A_MAX_DIST_KAMA_ATR:
            return False, "BLOCKED_L2_H2A_DIST_KAMA_ATR"
        return True, "ACCEPTED"

    if variant == "H2B":
        adx = feat["adx_14"]
        pdi = feat["plus_di_14"]
        mdi = feat["minus_di_14"]
        if not (np.isfinite(adx) and np.isfinite(pdi) and np.isfinite(mdi)):
            return False, "BLOCKED_L2_H2B_ADX_DI"
        if adx < H2B_MIN_ADX_14:
            return False, "BLOCKED_L2_H2B_ADX_DI"
        if side == "BUY" and not (pdi > mdi):
            return False, "BLOCKED_L2_H2B_ADX_DI"
        if side == "SELL" and not (mdi > pdi):
            return False, "BLOCKED_L2_H2B_ADX_DI"
        return True, "ACCEPTED"

    if variant == "H2C":
        ap = feat["atr_pct"]
        if not np.isfinite(ap) or ap < H2C_MIN_ATR_PCT or ap > H2C_MAX_ATR_PCT:
            return False, "BLOCKED_L2_H2C_ATR_PCT"
        return True, "ACCEPTED"

    raise ValueError(f"Unknown variant: {variant}")


def closed_max_drawdown(pnls: list[float]) -> float:
    eq = peak = max_dd = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    return max_dd


def profit_factor(pnls: list[float]) -> float:
    pos = sum(p for p in pnls if p > 0)
    neg = abs(sum(p for p in pnls if p < 0))
    if neg == 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


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
    print(f"V3_LAB_2 eval signals: {len(eval_sigs)} | ETH-USDT/C15 signals: {len(c15_sigs)}")

    feat_map = build_eth_1h_l2_features(m15_dict["ETH-USDT"])
    for s in c15_sigs:
        assert int(s.signal_close_ms) in feat_map, f"Missing 1H feature for {s.signal_close_ms}"

    cost_models = {p: CostModel.from_profile_name(p, is_real_data=True) for p in PROFILES}
    h0_ref_df = pd.read_csv(H0_REF_CSV) if H0_REF_CSV.exists() else None

    comp_rows: list[dict[str, Any]] = []
    bd_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    dec_rows: list[dict[str, Any]] = []
    static_vs_dyn_rows: list[dict[str, Any]] = []

    h0_c15_trades_by_key: dict[tuple[str, str], list[Any]] = {}
    h0_port_by_profile: dict[str, tuple[float, float]] = {}

    run_idx = 0
    for mode in MODES:
        for profile in PROFILES:
            cm = cost_models[profile]
            risk = RiskConfig(
                initial_equity=INITIAL_EQUITY,
                notional_per_trade=NOTIONAL_PER_TRADE,
                cost_profile=profile,
                cost_model=cm,
            )
            sim = UnifiedSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE")

            for variant in VARIANTS:
                run_idx += 1
                t_r0 = time.time()
                kept_sigs: list[Signal] = []
                pre_filtered: list[SignalDecision] = []

                for s in eval_sigs:
                    if s.symbol == "ETH-USDT" and int(s.combo_id) == 105:
                        feat = feat_map[int(s.signal_close_ms)]
                        ok, reason = evaluate_c15_l2_filter(variant, s.side, feat)
                        if not ok:
                            pre_filtered.append(
                                SignalDecision(
                                    signal_id=_resolve_signal_id(s),
                                    symbol=s.symbol,
                                    combo_id=s.combo_id,
                                    combo_name=s.combo_name,
                                    timeframe=s.timeframe,
                                    side=s.side,
                                    signal_close_ms=s.signal_close_ms,
                                    signal_close_dt=str(s.signal_close_dt),
                                    status="SKIPPED",
                                    reason=reason,
                                )
                            )
                            continue
                    kept_sigs.append(s)

                res: SimulationResult = sim.run(
                    mode, kept_sigs, candles_m1_eval, pre_filtered_decisions=pre_filtered
                )
                analysis = ResearchAnalyzer.analyze_simulation(res, initial_equity=INITIAL_EQUITY)
                port_net = float(analysis["pnl"]["net_pnl"])
                port_m2m_dd = float(analysis["risk"].get("m2m_max_drawdown_dollars") or 0.0)
                port_closed_dd = float(analysis["risk"].get("closed_trade_max_drawdown_dollars") or 0.0)

                c15_decs = [d for d in res.signal_decisions if d.symbol == "ETH-USDT" and int(d.combo_id) == 105]
                c15_trades = sorted(
                    [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 105],
                    key=lambda t: (t.exit_time_ms, t.entry_time_ms),
                )

                if variant == "H0":
                    h0_c15_trades_by_key[(mode, profile)] = c15_trades
                    if mode == "BINGX_PORTFOLIO":
                        h0_port_by_profile[profile] = (port_net, port_m2m_dd)
                    # Verify H0 matches prior eth_c15_research H0 100%
                    if h0_ref_df is not None:
                        ref = h0_ref_df[
                            (h0_ref_df["variant"] == "H0")
                            & (h0_ref_df["mode"] == mode)
                            & (h0_ref_df["cost_profile"] == profile)
                        ]
                        if len(ref) == 1:
                            assert int(ref.iloc[0]["c15_trades_accepted"]) == len(c15_trades)
                            assert abs(float(ref.iloc[0]["own_net_pnl"]) - sum(t.pnl_own_entry for t in c15_trades)) < 1e-3

                blocked_l2 = sum(1 for d in c15_decs if d.reason.startswith("BLOCKED_L2_"))
                skipped_conc = sum(1 for d in c15_decs if d.reason == "SKIPPED_BY_CONCURRENCY")
                tp_cnt = sum(1 for t in c15_trades if t.exit_reason == "TP")
                sl_cnt = sum(1 for t in c15_trades if t.exit_reason == "SL")
                n_tr = len(c15_trades)
                wr = (tp_cnt / n_tr * 100.0) if n_tr else 0.0

                own_pnls = [t.pnl_own_entry for t in c15_trades]
                vwap_pnls = [t.pnl_vwap for t in c15_trades]
                own_net = sum(own_pnls)
                vwap_net = sum(vwap_pnls)

                h0_p_net, h0_p_dd = h0_port_by_profile.get(profile, (port_net, port_m2m_dd))
                comp_rows.append(
                    {
                        "variant": variant,
                        "mode": mode,
                        "cost_profile": profile,
                        "c15_signals_total": len(c15_decs),
                        "c15_trades_accepted": n_tr,
                        "c15_blocked_l2": blocked_l2,
                        "c15_skipped_concurrency": skipped_conc,
                        "tp_count": tp_cnt,
                        "sl_count": sl_cnt,
                        "win_rate_pct": round(wr, 2),
                        "gross_pnl_raw": round(sum(t.gross_pnl_raw for t in c15_trades), 4),
                        "gross_pnl": round(sum(t.gross_pnl for t in c15_trades), 4),
                        "fee_total": round(sum(t.fee_total for t in c15_trades), 4),
                        "slippage_cost": round(sum(t.slippage_cost for t in c15_trades), 4),
                        "own_net_pnl": round(own_net, 4),
                        "vwap_allocated_pnl": round(vwap_net, 4),
                        "profit_factor_own": round(profit_factor(own_pnls), 4),
                        "profit_factor_vwap": round(profit_factor(vwap_pnls), 4),
                        "c15_max_dd_own": round(closed_max_drawdown(own_pnls), 4),
                        "portfolio_total_trades": len(res.trades),
                        "portfolio_net_pnl": round(port_net, 4),
                        "portfolio_m2m_max_dd": round(port_m2m_dd, 4),
                        "portfolio_closed_max_dd": round(port_closed_dd, 4),
                        "delta_port_pnl_vs_h0": round(port_net - h0_p_net, 4) if mode == "BINGX_PORTFOLIO" else "",
                        "delta_port_m2m_dd_vs_h0": round(port_m2m_dd - h0_p_dd, 4) if mode == "BINGX_PORTFOLIO" else "",
                    }
                )

                # Static vs Dynamic comparison (apply variant L2 mask statically to H0 trades)
                sig_by_id = {_resolve_signal_id(s): s for s in c15_sigs}
                h0_tr = h0_c15_trades_by_key[(mode, profile)]
                static_kept = [
                    t for t in h0_tr
                    if evaluate_c15_l2_filter(variant, t.side, feat_map[int(sig_by_id[t.signal_id].signal_close_ms)])[0]
                ]
                static_own = sum(t.pnl_own_entry for t in static_kept)
                static_vwap = sum(t.pnl_vwap for t in static_kept)
                static_vs_dyn_rows.append(
                    {
                        "variant": variant,
                        "mode": mode,
                        "cost_profile": profile,
                        "static_trades": len(static_kept),
                        "static_own_net_pnl": round(static_own, 4),
                        "static_vwap_pnl": round(static_vwap, 4),
                        "dynamic_trades": n_tr,
                        "dynamic_own_net_pnl": round(own_net, 4),
                        "dynamic_vwap_pnl": round(vwap_net, 4),
                        "dynamic_minus_static_own": round(own_net - static_own, 4),
                    }
                )

                # Breakdowns by SIDE and YEAR
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
                        bd_rows.append(
                            {
                                "variant": variant,
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

                # Trade details with L2 indicator values at signal time
                for t in c15_trades:
                    s_obj = sig_by_id[t.signal_id]
                    f_val = feat_map[int(s_obj.signal_close_ms)]
                    trade_rows.append(
                        {
                            "variant": variant,
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
                            **{k: (round(v, 6) if np.isfinite(v) else "") for k, v in f_val.items()},
                        }
                    )

                # Signal decisions with L2 indicator values at signal time
                for d in c15_decs:
                    f_val = feat_map[int(d.signal_close_ms)]
                    dec_rows.append(
                        {
                            "variant": variant,
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
                    f"[{run_idx:02d}/16] {variant} | {mode:19s} | {profile:6s} | "
                    f"C15 tr={n_tr:3d} blkL2={blocked_l2:4d} own={own_net:+7.2f} vwap={vwap_net:+7.2f} "
                    f"port={port_net:+7.2f} dd={port_m2m_dd:6.2f} ({time.time()-t_r0:.2f}s)"
                )

    pd.DataFrame(comp_rows).to_csv(OUT_DIR / "C15_H2_COMPARISON.csv", index=False)
    pd.DataFrame(bd_rows).to_csv(OUT_DIR / "C15_H2_YEAR_SIDE_BREAKDOWN.csv", index=False)
    pd.DataFrame(trade_rows).to_csv(OUT_DIR / "C15_H2_TRADE_DETAILS.csv", index=False)
    pd.DataFrame(dec_rows).to_csv(OUT_DIR / "C15_H2_SIGNAL_DECISIONS.csv", index=False)
    pd.DataFrame(static_vs_dyn_rows).to_csv(OUT_DIR / "C15_STATIC_VS_DYNAMIC.csv", index=False)

    code_sha, code_dirty, _ = get_git_state()
    manifest = {
        "research_id": "V3_LAB_2_ETH_C15_H2",
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
        "variants": {
            "H0": "V3_LAB_2@2.0.0 baseline (unchanged)",
            "H2A": f"Anti-chasing L2 filter: abs(close_1h - kama_40) / atr_14 <= {H2A_MAX_DIST_KAMA_ATR}",
            "H2B": f"Trend L2 filter: adx_14 >= {H2B_MIN_ADX_14} AND DI+ > DI- (BUY) / DI- > DI+ (SELL)",
            "H2C": f"Volatility L2 filter: {H2C_MIN_ATR_PCT} <= atr_14 / close_1h <= {H2C_MAX_ATR_PCT}",
        },
        "modes": MODES,
        "cost_profiles": {p: cost_models[p].profile.profile_hash for p in PROFILES},
        "total_runs": 16,
        "git_state": {"sha": code_sha, "dirty": code_dirty},
        "env_versions": get_env_versions(),
        "runner_script": "scripts/run_v3_lab_2_eth_c15_h2.py",
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nAll 16 runs completed in {time.time() - t0:.2f}s! Output dir: {OUT_DIR}")


if __name__ == "__main__":
    main()
