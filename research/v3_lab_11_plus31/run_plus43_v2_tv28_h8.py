"""Versioned PLUS43 v2 runner & E2E verifier for TV28 v2 (BTC-USDT / H8 / E1_FIXED / $100)."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import numpy as np
import pandas as pd

LAB = Path(__file__).resolve().parent
ROOT = LAB.parents[1]
LAB_ROB = ROOT / "research" / "v3_tv28_btc8h_robustness"
for p in (str(LAB), str(LAB_ROB), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import run_backtest as core
import run_btc8h_robustness as rob
import run_plus43_all_signals as p43_all
import run_plus43_forward as p43_fwd
import run_plus44 as plus44
import run_plus44_all_signals as p44_all
import run_tv28_forensics as f28
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.reconcile_v3.keys import build_combo_table, event_id_to_signal_id, signal_id_to_event_id
from autotrade_v3.sim.costs import canonical_cost_profiles_json_sha256
from autotrade_v3.sim.engine import UnifiedSimulator

REG_V2_PATH = LAB / "registry_v2" / "TV20_PLUS43_V2_REGISTRY.csv"
OUT_V2 = LAB / "outputs" / "plus43_v2_tv28_h8"
REPORT_DIR = ROOT / "reports" / "tv28_v2_integration"
PROFILES: tuple[str, ...] = ("BASE", "STRESS")

TARGETS = {
    "standalone_base": {
        "entries": 129,
        "wins": 50,
        "losses": 79,
        "net_own": 40.19922052,
        "fees": 12.88800448,
        "slippage": 5.15520410,
        "funding": -0.46242500,
        "pf": 1.385893,
    },
    "standalone_stress": {
        "entries": 129,
        "net_own": 36.31371380,
        "pf": 1.340861,
    },
    "standalone_forward": {
        "entries": 2,
        "net_own": 1.64394018,
    },
    "portfolio_base": {
        "entries": 6560,
        "net_own": 1561.17895801,
        "m2m_dd": 180.835869,
        "m2m_dd_pct": 9.09,
        "pf_own": 1.410325,
    },
    "portfolio_stress": {
        "entries": 6560,
        "net_own": 1110.11293708,
        "m2m_dd": 208.951001,
        "m2m_dd_pct": 11.05,
        "pf_own": 1.279513,
    },
}


def _load_v2_registry() -> pd.DataFrame:
    df = pd.read_csv(REG_V2_PATH)
    assert len(df) == 20 and df["display_id"].nunique() == 20
    tv28 = df.loc[df["display_id"] == "TV28"].iloc[0]
    assert str(tv28["new_combo_id"]) == "TV28_V2_H8"
    assert str(tv28["symbol"]) == "BTC-USDT"
    assert str(tv28["timeframe"]) == "H8"
    assert int(tv28["timeframe_minutes"]) == 480
    assert float(tv28["tp_pct"]) == 0.03 and float(tv28["sl_pct"]) == 0.012
    assert int(tv28["warmup_bars"]) == 120
    return df.sort_values("display_id").reset_index(drop=True)


def _check_h8_causality(m1_btc_3y: pd.DataFrame) -> dict[str, Any]:
    bars_full = core.tvlab.resample_m1_closed(m1_btc_3y, 480)
    # Verify exact 00:00 / 08:00 / 16:00 UTC schedule
    hours = sorted({int(ts.hour) for ts in bars_full.index})
    assert hours == [0, 8, 16], f"Unexpected H8 hours: {hours}"

    t_ms = m1_btc_3y.index.asi8 // 1_000_000
    cut_idx = int(len(m1_btc_3y) * 0.70)
    cut_ms = int((t_ms[cut_idx] // (480 * 60_000)) * (480 * 60_000))
    cut_m1 = int(np.searchsorted(t_ms, cut_ms, side="left"))

    m1_pref = m1_btc_3y.iloc[:cut_m1].copy()
    m1_pert = m1_btc_3y.copy()
    m1_pert.iloc[cut_m1:, m1_pert.columns.get_loc("close")] *= 1.29
    m1_pert.iloc[cut_m1:, m1_pert.columns.get_loc("high")] = np.maximum(
        m1_pert.iloc[cut_m1:]["high"], m1_pert.iloc[cut_m1:]["close"]
    )
    m1_pert.iloc[cut_m1:, m1_pert.columns.get_loc("low")] = np.minimum(
        m1_pert.iloc[cut_m1:]["low"], m1_pert.iloc[cut_m1:]["close"]
    )

    b_pref = core.tvlab.resample_m1_closed(m1_pref, 480)
    b_pert = core.tvlab.resample_m1_closed(m1_pert, 480)
    n_clean = len(b_pref)

    def _sig_vec(bars: pd.DataFrame) -> np.ndarray:
        ev = core.tvlab.PORT_CLASSES["L2_S1"]().evaluate(bars, "B0")
        ev.loc[ev.index[:120], "signal"] = 0
        return ev["signal"].to_numpy(dtype=int)

    s_full = _sig_vec(bars_full)
    s_pref = _sig_vec(b_pref)
    s_pert = _sig_vec(b_pert)
    s_rep = _sig_vec(bars_full)
    p_ok = bool(np.array_equal(s_full[:n_clean], s_pref))
    f_ok = bool(np.array_equal(s_full[:n_clean], s_pert[:n_clean]))
    r_ok = bool(np.array_equal(s_full, s_rep))
    return {
        "hours_utc": hours,
        "total_h8_bars": len(bars_full),
        "clean_prefix_bars": n_clean,
        "prefix_invariance": p_ok,
        "future_perturbation_invariance": f_ok,
        "repeatability": r_ok,
        "pass": bool(p_ok and f_ok and r_ok and hours == [0, 8, 16]),
    }


def _run_dry_run_h8(registry_v2: pd.DataFrame) -> dict[str, Any]:
    """Dry-run H8 signal generation on latest BingX data for TV10, TV12, and TV28 v2."""
    m1_fwd_dict, max_m1_ms = p43_fwd._load_combined_m1(ROOT / "data" / "bingx")
    m1_btc = m1_fwd_dict["BTC-USDT"]
    bars_h8 = p43_fwd._resample_closed_safe(m1_btc, 480)
    hours = sorted({int(ts.hour) for ts in bars_h8.index})
    last_closed_open_utc = bars_h8.index[-1].strftime("%Y-%m-%dT%H:%M:%SZ")
    last_closed_close_ms = int(bars_h8.index[-1].value // 10**6 + 480 * 60_000 - 1)
    assert last_closed_close_ms <= max_m1_ms + 59_999, "Unclosed H8 bar leaked"

    h8_rows = registry_v2.loc[registry_v2["display_id"].isin(["TV10", "TV12", "TV28"])].to_dict("records")
    sigs_by_combo: dict[str, list[Any]] = {}
    all_signal_ids: list[str] = []
    all_event_ids: list[str] = []
    combo_ids: set[int] = set()
    combo_table = build_combo_table()

    for row in h8_rows:
        did = str(row["display_id"])
        key_id = str(row["new_combo_id"]) if did == "TV28" else did
        assert ("BTC-USDT", key_id) in combo_table, f"Missing {key_id} in combo_table"
        spec = combo_table[("BTC-USDT", key_id)]
        assert spec.native_timeframe == "H8"

        evaluated = core.tvlab.PORT_CLASSES[str(row["strategy_id"])]().evaluate(bars_h8, str(row["variant"]))
        warmup = int(row["warmup_bars"])
        if warmup and len(evaluated) > warmup:
            evaluated.loc[evaluated.index[:warmup], "signal"] = 0
        evaluated = evaluated.join(bars_h8[["open", "high", "low", "close", "volume", "provider"]])
        transformed = core.tvlab.apply_variant(evaluated, str(row["variant_id"]))
        sigs = core.tvlab.make_signals(
            transformed, row, float(row["tp_pct"]), float(row["sl_pct"]), str(row["variant_id"])
        )
        numeric_id = 31000 + int(did[2:])
        combo_ids.add(numeric_id)
        sigs_by_combo[did] = sigs
        for s in sigs:
            eid = f"BTC-USDT|H8|{key_id}|{s.side}|{s.signal_close_ms}"
            sid = event_id_to_signal_id(eid)
            assert signal_id_to_event_id(sid) == eid
            all_event_ids.append(eid)
            all_signal_ids.append(sid)

    return {
        "h8_hours_utc": hours,
        "last_closed_h8_bar_open_utc": last_closed_open_utc,
        "latest_m1_utc": f28._ms_to_iso(max_m1_ms),
        "distinct_combo_ids": sorted(combo_ids),
        "signal_counts_2026_ytd": {k: len(v) for k, v in sigs_by_combo.items()},
        "unique_event_ids": len(set(all_event_ids)) == len(all_event_ids),
        "unique_signal_ids": len(set(all_signal_ids)) == len(all_signal_ids),
        "pass": (
            hours == [0, 8, 16]
            and len(combo_ids) == 3
            and len(set(all_event_ids)) == len(all_event_ids)
            and len(set(all_signal_ids)) == len(all_signal_ids)
        ),
    }


def main() -> int:
    started = time.time()
    OUT_V2.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    core.configure_tv_lab()
    core.ensure_fixed_v1_registered()
    p44_all._validate_splits_against_report()

    integrity = core.DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(f"Data integrity failed: {integrity}")

    registry_v2 = _load_v2_registry()
    layer = core.DataLayer()
    m1 = {sym: layer.query_m1(sym, core.START, core.END) for sym in ("BTC-USDT", "ETH-USDT")}
    funding_tbl_3y = core.tvlab.funding_table()

    print("[1/5] Checking H8 causality / prefix-parity & generating 20 TV20 v2 signals...", flush=True)
    causality = _check_h8_causality(m1["BTC-USDT"])
    assert causality["pass"], f"Causality failed: {causality}"

    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(core._tv_worker, row): row["display_id"] for row in registry_v2.to_dict("records")}
        for fut in as_completed(futures):
            payload = fut.result()
            tv_signals.extend(payload["signals"])

    tv28_signals = [s for s in tv_signals if s.combo_name == "TV28"]
    assert len(tv28_signals) == 129, f"Expected 129 TV28 H8 signals, got {len(tv28_signals)}"
    assert len(tv_signals) == 2313, f"Expected 2313 TV20 v2 signals, got {len(tv_signals)}"

    # Key collision check among TV10, TV12, TV28
    h8_sigs = [s for s in tv_signals if s.combo_name in ("TV10", "TV12", "TV28")]
    assert len({s.signal_id for s in h8_sigs}) == len(h8_sigs)
    assert len({s.combo_id for s in h8_sigs}) == 3

    start_ms = int(pd.Timestamp(core.START, tz="UTC").value // 10**6)
    end_ms = int(pd.Timestamp(core.END, tz="UTC").value // 10**6)
    m15 = {sym: layer.query_timeframe(sym, "15m", core.START, core.END, include_warmup=True) for sym in m1}
    prune = core.V3Lab11Prune23Strategy()
    prune_signals = [
        s
        for s in prune.generate_signals(m15, symbols=list(m1), only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < end_ms
    ]
    assert len(prune_signals) == 4247
    all_signals = prune_signals + tv_signals
    assert len(all_signals) == 6560

    # Standalone TV28 v2 (BASE & STRESS + Forward)
    print("[2/5] Running Standalone TV28 v2 (BASE, STRESS, FORWARD)...", flush=True)
    standalone_out: dict[str, Any] = {}
    for prof in PROFILES:
        res = p44_all.run_all_signals_portfolio(tv28_signals, {"BTC-USDT": m1["BTC-USDT"]}, prof, with_funding=True)
        _, raw_frame = core.result_payload(res)
        frame = core.normalized_trades(raw_frame, "TV28_V2_H8_STANDALONE", prof)
        model = core.tvlab.SignedFundingCostModel.from_profile_name(prof, is_real_data=True, funding_table=funding_tbl_3y)
        fund_vals = [
            f28._signed_funding_cost(model, str(r.symbol), str(r.side), float(r.notional), int(r.entry_time_ms), int(r.exit_time_ms))
            for r in frame.itertuples()
        ]
        own = frame["pnl_own_entry"].astype(float)
        standalone_out[prof] = {
            "entries": int(len(frame)),
            "wins": int((own > 0.0).sum()),
            "losses": int((own <= 0.0).sum()),
            "net_own": float(own.sum()),
            "fees": float(frame["fee_total"].astype(float).sum()),
            "slippage": float(frame["slippage_cost"].astype(float).sum()),
            "funding": float(sum(fund_vals)),
            "pf": float(p44_all._calc_profit_factor(own)),
        }

    # Forward standalone TV28 v2 (2026-11-01 -> 2026-11-26)
    m1_fwd_dict, max_fwd_ms = p43_fwd._load_combined_m1(ROOT / "data" / "bingx")
    fwd_funding = p43_fwd._load_forward_funding(ROOT / "data" / "bingx")
    tv28_row = registry_v2.loc[registry_v2["display_id"] == "TV28"].iloc[0].to_dict()
    bars_fwd = p43_fwd._resample_closed_safe(m1_fwd_dict["BTC-USDT"], 480)
    ev_fwd = core.tvlab.PORT_CLASSES["L2_S1"]().evaluate(bars_fwd, "B0")
    ev_fwd.loc[ev_fwd.index[:120], "signal"] = 0
    ev_fwd = ev_fwd.join(bars_fwd[["open", "high", "low", "close", "volume", "provider"]])
    tr_fwd = core.tvlab.apply_variant(ev_fwd, "V2_SL_TIGHTENING")
    fwd_sigs = [
        replace(s, combo_id=31028, combo_name="TV28", signal_id=f"TV28_V2_H8|{s.signal_close_ms}|{s.side}")
        for s in core.tvlab.make_signals(tr_fwd, tv28_row, 0.03, 0.012, "V2_SL_TIGHTENING")
        if int(s.signal_close_ms) + 1 >= p43_fwd.FORWARD_START_MS
    ]
    sim_start_ts = pd.to_datetime(p43_fwd.SIM_M1_START_MS, unit="ms", utc=True)
    m1_fwd_sim = {"BTC-USDT": m1_fwd_dict["BTC-USDT"].loc[m1_fwd_dict["BTC-USDT"].index >= sim_start_ts]}
    fwd_model = core.tvlab.SignedFundingCostModel.from_profile_name("BASE", is_real_data=True, funding_table=fwd_funding)
    fwd_risk = RiskConfig(
        initial_equity=core.INITIAL_EQUITY, notional_per_trade=core.NOTIONAL,
        leverage_btc=100, leverage_eth=50, cost_profile="BASE", cost_model=fwd_model,
    )
    fwd_res = UnifiedSimulator(
        risk_config=fwd_risk, final14_exit_mode="100_HARD", cost_model=fwd_model, ambiguity_policy="CONSERVATIVE"
    ).run("ALL_SIGNALS_PORTFOLIO", fwd_sigs, m1_fwd_sim, sample_snapshot_interval_m1=15)
    _, fwd_raw = core.result_payload(fwd_res)
    fwd_frame = core.normalized_trades(fwd_raw, "TV28_V2_H8_FWD", "BASE")
    standalone_out["FORWARD_BASE"] = {
        "entries": int(len(fwd_frame)),
        "net_own": float(fwd_frame["pnl_own_entry"].astype(float).sum()) if not fwd_frame.empty else 0.0,
        "end_utc": f28._ms_to_iso(max_fwd_ms),
    }

    # Full Portfolio PRUNE23_PLUS_TV20 (with TV28 v2) on BASE & STRESS
    print("[3/5] Running Full Portfolio PRUNE23_PLUS_TV20 with TV28 v2 (BASE & STRESS)...", flush=True)
    portfolio_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    equity_frames: list[pd.DataFrame] = []
    trades_by_prof: dict[str, pd.DataFrame] = {}
    port_out: dict[str, Any] = {}
    acct_details: dict[str, Any] = {}

    for prof in PROFILES:
        res = p44_all.run_all_signals_portfolio(all_signals, m1, prof, with_funding=True)
        summary_dict = dict(res.summary_dict or {})
        acct_inv = dict(summary_dict.get("accounting_invariant") or {})
        acct_details[prof] = acct_inv
        assert float(acct_inv.get("equity_diff", 1.0)) < 1e-6, f"Equity diff failed for {prof}: {acct_inv}"
        assert len(res.skipped_signals) == 0 and len(res.rejected_signals) == 0

        metrics, raw_frame = core.result_payload(res)
        frame = core.normalized_trades(raw_frame, "PRUNE23_PLUS_TV20", prof)
        snaps = res.snapshots.copy()
        peak_legs = plus44.peak_concurrent(frame)
        own = frame["pnl_own_entry"].astype(float)
        m2m_dd = float(metrics["m2m_dd"])
        m2m_dd_pct = float(summary_dict.get("m2m_max_drawdown_pct", snaps["drawdown_pct"].max()))

        trades_by_prof[prof] = frame
        port_out[prof] = {
            "entries": int(metrics["executed_trades"]),
            "net_vwap": float(metrics["net_pnl"]),
            "net_own": float(own.sum()),
            "m2m_dd": m2m_dd,
            "m2m_dd_pct": m2m_dd_pct,
            "pf_vwap": float(metrics["profit_factor"]),
            "pf_own": float(p44_all._calc_profit_factor(own)),
            "peak_concurrent_legs": int(peak_legs),
        }

        portfolio_rows.append(
            {
                "study_group": "PRUNE23_PLUS_TV20",
                "profile": prof,
                "mode": "ALL_SIGNALS_PORTFOLIO",
                "funding_mode": "SIGNED_HISTORICAL_FUNDING",
                "total_signals": len(all_signals),
                "executed_trades": int(metrics["executed_trades"]),
                "accepted_signals": int(metrics["accepted_signals"]),
                "skipped_signals": 0,
                "rejected_signals": 0,
                "unfillable_signals": 0,
                "buy_trades": int(metrics["buy_trades"]),
                "sell_trades": int(metrics["sell_trades"]),
                "net_pnl": float(metrics["net_pnl"]),
                "net_pnl_own_entry": float(own.sum()),
                "roi_pct": float(metrics["roi_pct"]),
                "gross_pnl": float(metrics["gross_pnl"]),
                "fees": float(metrics["fees"]),
                "funding": float(metrics["funding"]),
                "slippage": float(metrics["slippage"]),
                "trading_costs": float(metrics["trading_costs"]),
                "m2m_dd": m2m_dd,
                "m2m_dd_pct": m2m_dd_pct,
                "max_dd_closed": float(metrics["max_dd_closed"]),
                "peak_concurrent_legs": int(peak_legs),
                "win_rate_pct": float(metrics["win_rate_pct"]),
                "profit_factor": float(metrics["profit_factor"]),
                "profit_factor_own": float(p44_all._calc_profit_factor(own)),
                "p1_net": float(metrics["p1_net"]),
                "p2_net": float(metrics["p2_net"]),
                "p3_net": float(metrics["p3_net"]),
                "accounting_pass": True,
                "cost_profile_hash": str(summary_dict.get("cost_profile_hash", "")),
                "status": str(summary_dict.get("status", "OK")),
            }
        )
        eq_df = core.daily_equity(snaps, "PRUNE23_PLUS_TV20", prof)
        if not eq_df.empty:
            equity_frames.append(eq_df)

        entry_ms = frame["entry_time_ms"].astype(np.int64)
        snap_ms = snaps["timestamp_ms"].astype(np.int64)
        for sp in p44_all.V3_18_SPLITS:
            t_sub = frame.loc[(entry_ms >= int(sp["start_ms"])) & (entry_ms <= int(sp["end_ms"]))]
            s_sub = snaps.loc[(snap_ms >= int(sp["start_ms"])) & (snap_ms <= int(sp["end_ms"]))]
            split_rows.append(
                {
                    "study_group": "PRUNE23_PLUS_TV20",
                    "profile": prof,
                    "split": sp["split"],
                    "start": sp["start"],
                    "end": sp["end"],
                    **p44_all._summarize_window(t_sub, s_sub, use_global_m2m=False),
                }
            )
        for yp in p44_all.YEARLY_PERIODS:
            t_sub = frame.loc[(entry_ms >= int(yp["start_ms"])) & (entry_ms < int(yp["end_ms_exclusive"]))]
            s_sub = snaps.loc[(snap_ms >= int(yp["start_ms"])) & (snap_ms < int(yp["end_ms_exclusive"]))]
            yearly_rows.append(
                {
                    "study_group": "PRUNE23_PLUS_TV20",
                    "profile": prof,
                    "period": yp["period"],
                    "start": yp["start"],
                    "end_exclusive": yp["end_exclusive"],
                    **p44_all._summarize_window(t_sub, s_sub, use_global_m2m=False),
                }
            )

    # Dry-run H8 signal generation
    print("[4/5] Running H8 dry-run signal generation & exact target reconciliation...", flush=True)
    dry_run = _run_dry_run_h8(registry_v2)
    assert dry_run["pass"], f"Dry-run H8 failed: {dry_run}"

    # Reconciliation checks
    checks = {
        "standalone_base_entries": (standalone_out["BASE"]["entries"], TARGETS["standalone_base"]["entries"], 0),
        "standalone_base_wins": (standalone_out["BASE"]["wins"], TARGETS["standalone_base"]["wins"], 0),
        "standalone_base_losses": (standalone_out["BASE"]["losses"], TARGETS["standalone_base"]["losses"], 0),
        "standalone_base_net_own": (standalone_out["BASE"]["net_own"], TARGETS["standalone_base"]["net_own"], 1e-6),
        "standalone_base_fees": (standalone_out["BASE"]["fees"], TARGETS["standalone_base"]["fees"], 1e-6),
        "standalone_base_slippage": (standalone_out["BASE"]["slippage"], TARGETS["standalone_base"]["slippage"], 1e-6),
        "standalone_base_funding": (standalone_out["BASE"]["funding"], TARGETS["standalone_base"]["funding"], 1e-6),
        "standalone_base_pf": (standalone_out["BASE"]["pf"], TARGETS["standalone_base"]["pf"], 1e-5),
        "standalone_stress_entries": (standalone_out["STRESS"]["entries"], TARGETS["standalone_stress"]["entries"], 0),
        "standalone_stress_net_own": (standalone_out["STRESS"]["net_own"], TARGETS["standalone_stress"]["net_own"], 1e-6),
        "standalone_stress_pf": (standalone_out["STRESS"]["pf"], TARGETS["standalone_stress"]["pf"], 1e-5),
        "standalone_forward_entries": (standalone_out["FORWARD_BASE"]["entries"], TARGETS["standalone_forward"]["entries"], 0),
        "standalone_forward_net_own": (standalone_out["FORWARD_BASE"]["net_own"], TARGETS["standalone_forward"]["net_own"], 1e-6),
        "portfolio_base_entries": (port_out["BASE"]["entries"], TARGETS["portfolio_base"]["entries"], 0),
        "portfolio_base_net_own": (port_out["BASE"]["net_own"], TARGETS["portfolio_base"]["net_own"], 1e-4),
        "portfolio_base_m2m_dd": (port_out["BASE"]["m2m_dd"], TARGETS["portfolio_base"]["m2m_dd"], 0.05),
        "portfolio_base_pf": (port_out["BASE"]["pf_vwap"], TARGETS["portfolio_base"]["pf_own"], 0.002),
        "portfolio_stress_entries": (port_out["STRESS"]["entries"], TARGETS["portfolio_stress"]["entries"], 0),
        "portfolio_stress_net_own": (port_out["STRESS"]["net_own"], TARGETS["portfolio_stress"]["net_own"], 1e-4),
        "portfolio_stress_m2m_dd": (port_out["STRESS"]["m2m_dd"], TARGETS["portfolio_stress"]["m2m_dd"], 0.05),
        "portfolio_stress_pf": (port_out["STRESS"]["pf_vwap"], TARGETS["portfolio_stress"]["pf_own"], 0.002),
    }
    recon_table = {}
    for k, (actual, expected, tol) in checks.items():
        diff = actual - expected
        recon_table[k] = {
            "expected": expected,
            "actual": actual,
            "diff": diff,
            "tol": tol,
            "pass": abs(diff) <= tol,
        }
    all_recon_pass = all(v["pass"] for v in recon_table.values())
    print(f"    Full Target Reconciliation: {'PASS' if all_recon_pass else 'FAIL'}", flush=True)
    if not all_recon_pass:
        raise RuntimeError(f"Reconciliation failed: {json.dumps(recon_table, indent=2)}")

    # Save versioned PLUS43 v2 outputs
    print("[5/5] Saving versioned outputs to plus43_v2_tv28_h8 & verification JSON...", flush=True)
    prune_meta = sorted({(f"{s.symbol}/{s.combo_name}", str(s.symbol), str(s.timeframe)) for s in prune_signals})
    expected_43 = [{"display_id": d, "source_group": "PRUNE23", "symbol": sym, "timeframe": tf} for d, sym, tf in prune_meta] + [
        {"display_id": str(r["display_id"]), "source_group": "TV20", "symbol": str(r["symbol"]), "timeframe": str(r["timeframe"])}
        for r in registry_v2.to_dict("records")
    ]
    per_combo_v2 = p43_all._build_per_combo_table_profiles("PRUNE23_PLUS_TV20", trades_by_prof, expected_43, PROFILES)

    pd.DataFrame(portfolio_rows).to_csv(OUT_V2 / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv", index=False)
    pd.DataFrame(split_rows).to_csv(OUT_V2 / "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv", index=False)
    pd.DataFrame(yearly_rows).to_csv(OUT_V2 / "PORTFOLIO_YEARLY_ALL_SIGNALS.csv", index=False)
    per_combo_v2.to_csv(OUT_V2 / "PER_COMBO_PLUS43_ALL_SIGNALS.csv", index=False)
    pd.concat(equity_frames, ignore_index=True).to_csv(OUT_V2 / "DAILY_EQUITY_ALL_SIGNALS.csv", index=False)

    manifest_v2 = {
        "execution_id": datetime.now(timezone.utc).strftime("V3PLUS43_V2_TV28_H8_%Y%m%dT%H%M%SZ"),
        "study": "V3_LAB_11_PLUS43_V2_TV28_H8",
        "tv28_version_tag": "TV28_V2_H8",
        "elapsed_seconds": time.time() - started,
        "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        "config_hash": plus44.config_hash(registry_v2),
        "cost_profiles_sha256": canonical_cost_profiles_json_sha256(),
        "git_head": p44_all._git_output(["rev-parse", "HEAD"]),
        "portfolio_summary": portfolio_rows,
    }
    (OUT_V2 / "RUN_MANIFEST.json").write_text(json.dumps(manifest_v2, indent=2), encoding="utf-8")

    verif_payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "causality_h8": causality,
        "dry_run_h8": dry_run,
        "standalone_tv28_v2": standalone_out,
        "portfolio_plus43_v2": port_out,
        "accounting_details": acct_details,
        "reconciliation_pass": all_recon_pass,
        "reconciliation_details": recon_table,
    }
    (REPORT_DIR / "tv28_v2_verification.json").write_text(json.dumps(verif_payload, indent=2), encoding="utf-8")
    print(f"DONE in {manifest_v2['elapsed_seconds']:.1f}s -> {OUT_V2}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
