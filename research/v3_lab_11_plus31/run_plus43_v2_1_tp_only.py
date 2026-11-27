"""Versioned PLUS43 V2.1 TP-Only runner & E2E verifier (PORT_FINAL_TP_ONLY · Zero-Drop · BingX $100)."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
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
import run_plus43_all_signals as p43_all
import run_plus43_forward as p43_fwd
import run_plus44 as plus44
import run_plus44_all_signals as p44_all
import run_tv28_forensics as f28
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.reconcile_v3.keys import (
    build_combo_table,
    event_id_to_signal_id,
    signal_id_to_event_id,
    verify_live_signals_against_combos,
)
from autotrade_v3.sim.costs import canonical_cost_profiles_json_sha256
from autotrade_v3.sim.engine import UnifiedSimulator

REG_V2_PATH = LAB / "registry_v2" / "TV20_PLUS43_V2_REGISTRY.csv"
REG_V2_1_PATH = LAB / "registry_v2" / "TV20_PLUS43_V2_1_TP_REGISTRY.csv"
MANIFEST_PATH = LAB / "registry_v2" / "PLUS43_V2_1_TP_MANIFEST.json"
OUT_V2_1 = LAB / "outputs" / "plus43_v2_1_tp_only"
REPORT_DIR = ROOT / "reports" / "plus43_v2_1_tp_only"
PROFILES: tuple[str, ...] = ("BASE", "STRESS")

UPGRADED_SPECS: dict[str, dict[str, Any]] = {
    "TV03": {"symbol": "BTC-USDT", "tf": "H4", "tf_min": 240, "variant": "Z5A_TP_TIGHT", "orig_tp": 0.03, "new_tp": 0.024, "sl": 0.015},
    "TV07": {"symbol": "ETH-USDT", "tf": "H4", "tf_min": 240, "variant": "Z5B_TP_WIDE", "orig_tp": 0.036, "new_tp": 0.045, "sl": 0.015},
    "TV08": {"symbol": "ETH-USDT", "tf": "H4", "tf_min": 240, "variant": "Z5B_TP_WIDE", "orig_tp": 0.036, "new_tp": 0.045, "sl": 0.015},
    "TV09": {"symbol": "ETH-USDT", "tf": "H4", "tf_min": 240, "variant": "Z5B_TP_WIDE", "orig_tp": 0.03, "new_tp": 0.0375, "sl": 0.015},
    "TV12": {"symbol": "BTC-USDT", "tf": "H8", "tf_min": 480, "variant": "Z5B_TP_WIDE", "orig_tp": 0.03, "new_tp": 0.0375, "sl": 0.0125},
    "TV20": {"symbol": "BTC-USDT", "tf": "H12", "tf_min": 720, "variant": "Z5B_TP_WIDE", "orig_tp": 0.03, "new_tp": 0.0375, "sl": 0.0125},
    "TV24": {"symbol": "ETH-USDT", "tf": "H12", "tf_min": 720, "variant": "Z5B_TP_WIDE", "orig_tp": 0.036, "new_tp": 0.045, "sl": 0.015},
}

STANDALONE_TARGETS: dict[str, dict[str, Any]] = {
    "TV03": {"entries": 67, "base_net": 28.17547160, "base_pf": 1.545742, "stress_net": 26.17787200, "stress_pf": 1.497784, "fwd_entries": 0, "fwd_net": 0.0},
    "TV07": {"entries": 105, "base_net": 52.24003645, "base_pf": 1.473348, "stress_net": 43.07386962, "stress_pf": 1.377632, "fwd_entries": 3, "fwd_net": -4.85894045},
    "TV08": {"entries": 248, "base_net": 125.33231549, "base_pf": 1.483056, "stress_net": 105.89094962, "stress_pf": 1.395621, "fwd_entries": 6, "fwd_net": 8.27446090},
    "TV09": {"entries": 188, "base_net": 56.73492603, "base_pf": 1.293453, "stress_net": 45.88858275, "stress_pf": 1.231219, "fwd_entries": 1, "fwd_net": -1.62106015},
    "TV12": {"entries": 148, "base_net": 45.51120039, "base_pf": 1.340590, "stress_net": 25.97167381, "stress_pf": 1.184405, "fwd_entries": 1, "fwd_net": 3.72361537},
    "TV20": {"entries": 101, "base_net": 32.84338060, "base_pf": 1.362433, "stress_net": 29.74343205, "stress_pf": 1.320875, "fwd_entries": 1, "fwd_net": 3.68711538},
    "TV24": {"entries": 112, "base_net": 52.74628743, "base_pf": 1.449632, "stress_net": 49.37137210, "stress_pf": 1.413112, "fwd_entries": 5, "fwd_net": -2.09570015},
    "TV28": {"entries": 129, "base_net": 40.19922052, "base_pf": 1.385893, "stress_net": 36.31371380, "stress_pf": 1.340861, "fwd_entries": 2, "fwd_net": 1.64394018},
}

PORTFOLIO_TARGETS: dict[str, dict[str, Any]] = {
    "BASE": {
        "entries": 6560,
        "net_own": 1691.79053319,
        "net_vwap": 1696.27118119,
        "m2m_dd": 180.0864,
        "m2m_dd_pct": 9.14,
        "closed_dd": 158.047897,
        "pf_own": 1.382829,
        "pf_vwap": 1.448512,
        "peak_legs": 37,
    },
    "STRESS": {
        "entries": 6560,
        "net_own": 1242.83532158,
        "net_vwap": 1246.91341858,
        "m2m_dd": 196.1109,
        "m2m_dd_pct": 11.21,
        "closed_dd": 186.989494,
        "pf_own": 1.268809,
        "pf_vwap": 1.315861,
        "peak_legs": 36,
    },
}

FORWARD_TARGETS: dict[str, dict[str, Any]] = {
    "BASE": {"entries": 175, "net_own": 108.02925846, "m2m_dd": 28.7270, "m2m_dd_pct": 2.85},
    "STRESS": {"entries": 175, "net_own": 94.64883693, "m2m_dd": 29.9498, "m2m_dd_pct": 2.97},
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_and_validate_v2_1_registry() -> tuple[pd.DataFrame, dict[str, Any]]:
    df_v2 = pd.read_csv(REG_V2_PATH).sort_values("display_id").reset_index(drop=True)
    df_v2_1 = pd.read_csv(REG_V2_1_PATH).sort_values("display_id").reset_index(drop=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert len(df_v2_1) == 20 and df_v2_1["display_id"].nunique() == 20
    assert manifest["version_tag"] == "PLUS43_V2_1_TP_ONLY"
    assert manifest["registry_v2_1_sha256"] == _sha256_file(REG_V2_1_PATH)

    for r2, r21 in zip(df_v2.to_dict("records"), df_v2_1.to_dict("records")):
        did = str(r21["display_id"])
        assert did == str(r2["display_id"])
        assert float(r21["sl_pct"]) <= 0.015 + 1e-12
        if did in UPGRADED_SPECS:
            sp = UPGRADED_SPECS[did]
            assert str(r21["new_combo_id"]) == f"{did}_V2_1_TP"
            assert str(r21["symbol"]) == sp["symbol"]
            assert str(r21["timeframe"]) == sp["tf"]
            assert int(r21["timeframe_minutes"]) == sp["tf_min"]
            assert abs(float(r21["tp_pct"]) - sp["new_tp"]) < 1e-12
            assert abs(float(r21["sl_pct"]) - sp["sl"]) < 1e-12
            for col in ("strategy_id", "variant", "variant_id", "side_scope", "filter", "warmup_bars"):
                assert r21[col] == r2[col], f"{did}.{col} mutated unexpectedly"
        else:
            assert r21 == r2, f"Non-upgraded combo {did} was modified"

    tv21 = df_v2_1.loc[df_v2_1["display_id"] == "TV21"].iloc[0]
    assert abs(float(tv21["tp_pct"]) - 0.036) < 1e-12 and abs(float(tv21["sl_pct"]) - 0.015) < 1e-12

    tv28 = df_v2_1.loc[df_v2_1["display_id"] == "TV28"].iloc[0]
    assert str(tv28["new_combo_id"]) == "TV28_V2_H8"
    assert str(tv28["timeframe"]) == "H8" and int(tv28["timeframe_minutes"]) == 480
    assert abs(float(tv28["tp_pct"]) - 0.03) < 1e-12 and abs(float(tv28["sl_pct"]) - 0.012) < 1e-12
    assert int(tv28["warmup_bars"]) == 120
    return df_v2_1, manifest


def _build_fwd_tv_signals_for_row(
    row: dict[str, Any],
    bars_all: dict[tuple[str, int], pd.DataFrame],
    min_close_ms: int,
) -> list[Any]:
    sym = str(row["symbol"])
    tf_min = int(row["timeframe_minutes"])
    bars = bars_all[(sym, tf_min)]
    evaluated = core.tvlab.PORT_CLASSES[str(row["strategy_id"])]().evaluate(bars, str(row["variant"]))
    warmup = int(row["warmup_bars"])
    if warmup and len(evaluated) > warmup:
        evaluated.loc[evaluated.index[:warmup], "signal"] = 0
    evaluated = evaluated.join(bars[["open", "high", "low", "close", "volume", "provider"]])
    transformed = core.tvlab.apply_variant(evaluated, str(row["variant_id"]))
    sigs = core.tvlab.make_signals(
        transformed, row, float(row["tp_pct"]), float(row["sl_pct"]), str(row["variant_id"])
    )
    did = str(row["display_id"])
    numeric_id = 31000 + int(did[2:])
    out: list[Any] = []
    for s in sigs:
        if int(s.signal_close_ms) < min_close_ms:
            continue
        out.append(
            replace(
                s,
                combo_id=numeric_id,
                combo_name=did,
                signal_id=(
                    f"{did}|{row['run_id']}|{row['strategy_source_sha256']}|"
                    f"{s.signal_close_ms}|{s.side}"
                ),
            )
        )
    return out


def _run_dry_run_upgraded(
    registry_v2_1: pd.DataFrame,
    bars_all: dict[tuple[str, int], pd.DataFrame],
    max_fwd_ms: int,
) -> dict[str, Any]:
    """Dry-run signal & exact TP/SL validation on latest closed BingX bars for the 7 upgraded combos."""
    combo_table = build_combo_table()
    last_closed_by_tf: dict[tuple[str, int], str] = {}
    for (sym, tf_min), bdf in bars_all.items():
        last_open_ms = int(bdf.index[-1].value // 10**6)
        last_close_ms = last_open_ms + tf_min * 60_000 - 1
        assert last_close_ms <= max_fwd_ms + 59_999, f"Unclosed bar leaked for {sym}/{tf_min}m"
        last_closed_by_tf[(sym, tf_min)] = f28._ms_to_iso(last_open_ms)

    sample_events: list[dict[str, Any]] = []
    per_combo_dry: dict[str, Any] = {}
    all_eids: list[str] = []
    all_sids: list[str] = []
    all_cids: set[int] = set()

    for row in registry_v2_1.to_dict("records"):
        did = str(row["display_id"])
        if did not in UPGRADED_SPECS:
            continue
        sp = UPGRADED_SPECS[did]
        sym = str(row["symbol"])
        tf = str(row["timeframe"])
        version_id = str(row["new_combo_id"])
        assert (sym, version_id) in combo_table, f"Missing {(sym, version_id)} in build_combo_table()"
        spec = combo_table[(sym, version_id)]
        assert abs(float(spec.exit_policy.tp_pct) - sp["new_tp"]) < 1e-12
        assert abs(float(spec.exit_policy.sl_pct) - sp["sl"]) < 1e-12
        all_cids.add(int(spec.combo_id))

        sigs = _build_fwd_tv_signals_for_row(row, bars_all, p43_fwd.FORWARD_START_MS - 1)
        ytd_2026_start = int(pd.Timestamp("2026-01-01T00:00:00Z").value // 10**6)
        sigs_ytd = _build_fwd_tv_signals_for_row(row, bars_all, ytd_2026_start)

        verify_batch: list[dict[str, Any]] = []
        for s in sigs_ytd:
            eid = f"{sym}|{tf}|{version_id}|{s.side}|{s.signal_close_ms}"
            sid = event_id_to_signal_id(eid)
            assert signal_id_to_event_id(sid) == eid
            all_eids.append(eid)
            all_sids.append(sid)
            entry = float(s.entry_price)
            tp = float(s.tp_price)
            sl = float(s.sl_price)
            actual_tp_pct = abs(tp - entry) / entry
            actual_sl_pct = abs(sl - entry) / entry
            assert abs(actual_tp_pct - sp["new_tp"]) < 1e-9, f"{did} TP mismatch: {actual_tp_pct} vs {sp['new_tp']}"
            assert abs(actual_sl_pct - sp["sl"]) < 1e-9, f"{did} SL mismatch: {actual_sl_pct} vs {sp['sl']}"
            verify_batch.append(
                {
                    "event_id": eid,
                    "symbol": sym,
                    "combo": version_id,
                    "side": s.side,
                    "entry": str(entry),
                    "tp": str(tp),
                    "sl": str(sl),
                }
            )
        if verify_batch:
            resolved = verify_live_signals_against_combos(verify_batch, combo_table)
            assert len(resolved) == len(verify_batch)
            last_row = verify_batch[-1]
            sample_events.append(
                {
                    "display_id": did,
                    "version_id": version_id,
                    "event_id": last_row["event_id"],
                    "signal_id": event_id_to_signal_id(last_row["event_id"]),
                    "side": last_row["side"],
                    "entry": float(last_row["entry"]),
                    "tp": float(last_row["tp"]),
                    "sl": float(last_row["sl"]),
                    "tp_pct": round(abs(float(last_row["tp"]) - float(last_row["entry"])) / float(last_row["entry"]) * 100, 4),
                    "sl_pct": round(abs(float(last_row["sl"]) - float(last_row["entry"])) / float(last_row["entry"]) * 100, 4),
                }
            )
        per_combo_dry[did] = {
            "version_id": version_id,
            "symbol": sym,
            "timeframe": tf,
            "combo_id": int(spec.combo_id),
            "signals_2026_ytd": len(sigs_ytd),
            "signals_forward_post_nov1": len(sigs),
            "tp_pct": float(spec.exit_policy.tp_pct),
            "sl_pct": float(spec.exit_policy.sl_pct),
            "last_closed_bar_open_utc": last_closed_by_tf[(sym, int(row["timeframe_minutes"]))],
        }

    return {
        "latest_m1_utc": f28._ms_to_iso(max_fwd_ms),
        "distinct_combo_ids_7": sorted(all_cids),
        "unique_event_ids": len(set(all_eids)) == len(all_eids),
        "unique_signal_ids": len(set(all_sids)) == len(all_sids),
        "per_combo": per_combo_dry,
        "latest_verified_signals": sample_events,
        "pass": (
            len(all_cids) == 7
            and len(set(all_eids)) == len(all_eids)
            and len(set(all_sids)) == len(all_sids)
        ),
    }


def _eval_standalone_3y(
    sigs: list[Any],
    m1_sym: pd.DataFrame,
    prof: str,
    tag: str,
) -> dict[str, Any]:
    res = p44_all.run_all_signals_portfolio(sigs, {sigs[0].symbol: m1_sym}, prof, with_funding=True)
    _, raw_frame = core.result_payload(res)
    frame = core.normalized_trades(raw_frame, tag, prof)
    own = frame["pnl_own_entry"].astype(float)
    return {
        "entries": int(len(frame)),
        "wins": int((own > 0.0).sum()),
        "losses": int((own <= 0.0).sum()),
        "net_own": float(own.sum()),
        "fees": float(frame["fee_total"].astype(float).sum()),
        "slippage": float(frame["slippage_cost"].astype(float).sum()),
        "pf": float(p44_all._calc_profit_factor(own)),
    }


def _eval_standalone_fwd(
    sigs: list[Any],
    m1_sim: dict[str, pd.DataFrame],
    funding_all: pd.DataFrame,
    tag: str,
) -> dict[str, Any]:
    if not sigs:
        return {"entries": 0, "net_own": 0.0}
    fwd_model = core.tvlab.SignedFundingCostModel.from_profile_name("BASE", is_real_data=True, funding_table=funding_all)
    fwd_risk = RiskConfig(
        initial_equity=core.INITIAL_EQUITY,
        notional_per_trade=core.NOTIONAL,
        leverage_btc=100,
        leverage_eth=50,
        cost_profile="BASE",
        cost_model=fwd_model,
    )
    res = UnifiedSimulator(
        risk_config=fwd_risk,
        final14_exit_mode="100_HARD",
        cost_model=fwd_model,
        ambiguity_policy="CONSERVATIVE",
    ).run("ALL_SIGNALS_PORTFOLIO", sigs, m1_sim, sample_snapshot_interval_m1=15)
    _, raw_frame = core.result_payload(res)
    frame = core.normalized_trades(raw_frame, tag, "BASE")
    return {
        "entries": int(len(frame)),
        "net_own": float(frame["pnl_own_entry"].astype(float).sum()) if not frame.empty else 0.0,
    }


def _check(checks: dict[str, Any], name: str, actual: float | int, expected: float | int, tol: float) -> None:
    diff = float(actual) - float(expected)
    checks[name] = {
        "expected": expected,
        "actual": actual,
        "diff": diff,
        "tol": tol,
        "pass": abs(diff) <= tol,
    }


def main() -> int:
    started = time.time()
    OUT_V2_1.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    core.configure_tv_lab()
    core.ensure_fixed_v1_registered()
    p44_all._validate_splits_against_report()

    integrity = core.DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(f"Data integrity failed: {integrity}")

    registry_v2_1, manifest_cfg = _load_and_validate_v2_1_registry()
    layer = core.DataLayer()
    m1_3y = {sym: layer.query_m1(sym, core.START, core.END) for sym in ("BTC-USDT", "ETH-USDT")}
    funding_3y = core.tvlab.funding_table()

    print("[1/5] Generating 3Y signals for PRUNE23 (23) + TV20 V2.1 TP (20)...", flush=True)
    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(core._tv_worker, row): row["display_id"] for row in registry_v2_1.to_dict("records")}
        for fut in as_completed(futures):
            tv_signals.extend(fut.result()["signals"])
    assert len(tv_signals) == 2313, f"Expected 2313 TV20 signals, got {len(tv_signals)}"

    start_ms = int(pd.Timestamp(core.START, tz="UTC").value // 10**6)
    end_ms = int(pd.Timestamp(core.END, tz="UTC").value // 10**6)
    m15_3y = {sym: layer.query_timeframe(sym, "15m", core.START, core.END, include_warmup=True) for sym in m1_3y}
    prune = core.V3Lab11Prune23Strategy()
    prune_signals_3y = [
        s
        for s in prune.generate_signals(m15_3y, symbols=list(m1_3y), only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < end_ms
    ]
    assert len(prune_signals_3y) == 4247
    all_signals_3y = prune_signals_3y + tv_signals
    assert len(all_signals_3y) == 6560

    # Prepare Forward M1 & closed bars
    m1_fwd_dict, max_fwd_ms = p43_fwd._load_combined_m1(ROOT / "data" / "bingx")
    fwd_funding = p43_fwd._load_forward_funding(ROOT / "data" / "bingx")
    m1_all = {}
    for sym in ("BTC-USDT", "ETH-USDT"):
        comb = pd.concat([m1_3y[sym], m1_fwd_dict[sym]])
        m1_all[sym] = comb[~comb.index.duplicated(keep="last")].sort_index()
    funding_all = pd.concat([funding_3y, fwd_funding], ignore_index=True).drop_duplicates(
        subset=["symbol", "funding_time_ms"], keep="last"
    )
    sim_start_ts = pd.to_datetime(p43_fwd.SIM_M1_START_MS, unit="ms", utc=True)
    m1_fwd_sim = {sym: df.loc[df.index >= sim_start_ts] for sym, df in m1_all.items()}

    bars_all: dict[tuple[str, int], pd.DataFrame] = {}
    for row in registry_v2_1.to_dict("records"):
        key = (str(row["symbol"]), int(row["timeframe_minutes"]))
        if key not in bars_all:
            bars_all[key] = p43_fwd._resample_closed_safe(m1_all[key[0]], key[1])
    for sym in ("BTC-USDT", "ETH-USDT"):
        if (sym, 15) not in bars_all:
            bars_all[(sym, 15)] = p43_fwd._resample_closed_safe(m1_all[sym], 15)

    # Standalone 3Y & Forward checks for 7 upgraded combos + TV28_V2_H8
    print("[2/5] Running Standalone checks for 7 upgraded combos + TV28_V2_H8...", flush=True)
    standalone_results: dict[str, Any] = {}
    for row in registry_v2_1.to_dict("records"):
        did = str(row["display_id"])
        if did not in STANDALONE_TARGETS:
            continue
        sym = str(row["symbol"])
        sigs_3y = [s for s in tv_signals if s.combo_name == did]
        b3 = _eval_standalone_3y(sigs_3y, m1_3y[sym], "BASE", f"{did}_BASE")
        s3 = _eval_standalone_3y(sigs_3y, m1_3y[sym], "STRESS", f"{did}_STRESS")
        sigs_fwd = _build_fwd_tv_signals_for_row(row, bars_all, p43_fwd.FORWARD_START_MS - 1)
        f3 = _eval_standalone_fwd(sigs_fwd, {sym: m1_fwd_sim[sym]}, funding_all, f"{did}_FWD")
        standalone_results[did] = {
            "new_combo_id": str(row["new_combo_id"]),
            "symbol": sym,
            "timeframe": str(row["timeframe"]),
            "tp_pct": float(row["tp_pct"]),
            "sl_pct": float(row["sl_pct"]),
            "BASE": b3,
            "STRESS": s3,
            "FORWARD_BASE": f3,
        }

    # Full Portfolio 3Y (BASE & STRESS)
    print("[3/5] Running Full Portfolio PORT_FINAL_TP_ONLY 3Y (BASE & STRESS)...", flush=True)
    portfolio_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    equity_frames: list[pd.DataFrame] = []
    trades_by_prof: dict[str, pd.DataFrame] = {}
    port_out: dict[str, Any] = {}
    acct_details: dict[str, Any] = {}

    for prof in PROFILES:
        res = p44_all.run_all_signals_portfolio(all_signals_3y, m1_3y, prof, with_funding=True)
        summary_dict = dict(res.summary_dict or {})
        acct_inv = dict(summary_dict.get("accounting_invariant") or {})
        acct_details[prof] = acct_inv
        assert float(acct_inv.get("equity_diff", 1.0)) < 1e-6, f"Equity diff failed for {prof}: {acct_inv}"
        assert len(res.skipped_signals) == 0 and len(res.rejected_signals) == 0

        metrics, raw_frame = core.result_payload(res)
        frame = core.normalized_trades(raw_frame, "PORT_FINAL_TP_ONLY", prof)
        snaps = res.snapshots.copy()
        peak_legs = plus44.peak_concurrent(frame)
        own = frame["pnl_own_entry"].astype(float)
        m2m_dd = float(metrics["m2m_dd"])
        m2m_dd_pct = float(summary_dict.get("m2m_max_drawdown_pct", snaps["drawdown_pct"].max()))

        trades_by_prof[prof] = frame
        sort_cols = [c for c in ("exit_time_ms", "entry_time_ms", "trade_id") if c in frame.columns]
        ordered_own = frame.sort_values(sort_cols)["pnl_own_entry"].astype(float)
        closed_dd_own = float(p44_all._compute_closed_dd(ordered_own, core.INITIAL_EQUITY))
        port_out[prof] = {
            "entries": int(metrics["executed_trades"]),
            "net_vwap": float(metrics["net_pnl"]),
            "net_own": float(own.sum()),
            "m2m_dd": m2m_dd,
            "m2m_dd_pct": m2m_dd_pct,
            "closed_dd": closed_dd_own,
            "closed_dd_vwap": float(metrics["max_dd_closed"]),
            "pf_vwap": float(metrics["profit_factor"]),
            "pf_own": float(p44_all._calc_profit_factor(own)),
            "peak_concurrent_legs": int(peak_legs),
        }

        portfolio_rows.append(
            {
                "study_group": "PORT_FINAL_TP_ONLY",
                "profile": prof,
                "mode": "ALL_SIGNALS_PORTFOLIO",
                "funding_mode": "SIGNED_HISTORICAL_FUNDING",
                "total_signals": len(all_signals_3y),
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
        eq_df = core.daily_equity(snaps, "PORT_FINAL_TP_ONLY", prof)
        if not eq_df.empty:
            equity_frames.append(eq_df)

        entry_ms = frame["entry_time_ms"].astype(np.int64)
        snap_ms = snaps["timestamp_ms"].astype(np.int64)
        for sp in p44_all.V3_18_SPLITS:
            t_sub = frame.loc[(entry_ms >= int(sp["start_ms"])) & (entry_ms <= int(sp["end_ms"]))]
            s_sub = snaps.loc[(snap_ms >= int(sp["start_ms"])) & (snap_ms <= int(sp["end_ms"]))]
            split_rows.append(
                {
                    "study_group": "PORT_FINAL_TP_ONLY",
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
                    "study_group": "PORT_FINAL_TP_ONLY",
                    "profile": prof,
                    "period": yp["period"],
                    "start": yp["start"],
                    "end_exclusive": yp["end_exclusive"],
                    **p44_all._summarize_window(t_sub, s_sub, use_global_m2m=False),
                }
            )

    # Full Portfolio Forward (2026-11-01 -> latest)
    print("[4/5] Running Full Portfolio Forward & Dry-Run Verification...", flush=True)
    m15_fwd = {sym: bars_all[(sym, 15)] for sym in ("BTC-USDT", "ETH-USDT")}
    prune_fwd = [
        s
        for s in prune.generate_signals(m15_fwd, symbols=list(m15_fwd), only_approved=True, only_warmup_ready=True)
        if int(s.signal_close_ms) + 1 >= p43_fwd.FORWARD_START_MS
    ]
    tv_fwd: list[Any] = []
    for row in registry_v2_1.to_dict("records"):
        tv_fwd.extend(_build_fwd_tv_signals_for_row(row, bars_all, p43_fwd.FORWARD_START_MS - 1))
    all_fwd_signals = prune_fwd + tv_fwd

    fwd_out: dict[str, Any] = {}
    for prof in PROFILES:
        f_model = core.tvlab.SignedFundingCostModel.from_profile_name(prof, is_real_data=True, funding_table=funding_all)
        f_risk = RiskConfig(
            initial_equity=core.INITIAL_EQUITY,
            notional_per_trade=core.NOTIONAL,
            leverage_btc=100,
            leverage_eth=50,
            cost_profile=prof,
            cost_model=f_model,
        )
        f_res = UnifiedSimulator(
            risk_config=f_risk,
            final14_exit_mode="100_HARD",
            cost_model=f_model,
            ambiguity_policy="CONSERVATIVE",
        ).run("ALL_SIGNALS_PORTFOLIO", all_fwd_signals, m1_fwd_sim, sample_snapshot_interval_m1=15)
        f_sum = dict(f_res.summary_dict or {})
        _, f_raw = core.result_payload(f_res)
        f_frame = core.normalized_trades(f_raw, "PORT_FINAL_TP_ONLY_FWD", prof)
        f_snaps = f_res.snapshots.copy()
        f_own = f_frame["pnl_own_entry"].astype(float)
        f_m2m_dd = float(f_sum.get("m2m_max_drawdown_dollars", 0.0))
        f_m2m_pct = float(f_sum.get("m2m_max_drawdown_pct", f_snaps["drawdown_pct"].max()))
        fwd_out[prof] = {
            "entries": int(len(f_frame)),
            "net_own": float(f_own.sum()),
            "net_vwap": float(f_sum.get("net_pnl", 0.0)),
            "m2m_dd": f_m2m_dd,
            "m2m_dd_pct": f_m2m_pct,
        }

    dry_run = _run_dry_run_upgraded(registry_v2_1, bars_all, max_fwd_ms)
    assert dry_run["pass"], f"Dry-run failed: {dry_run}"

    # Reconciliation checks
    checks: dict[str, Any] = {}
    for did, tgt in STANDALONE_TARGETS.items():
        act = standalone_results[did]
        _check(checks, f"{did}_entries", act["BASE"]["entries"], tgt["entries"], 0)
        _check(checks, f"{did}_base_net_own", act["BASE"]["net_own"], tgt["base_net"], 1e-6)
        _check(checks, f"{did}_base_pf", act["BASE"]["pf"], tgt["base_pf"], 1e-5)
        _check(checks, f"{did}_stress_net_own", act["STRESS"]["net_own"], tgt["stress_net"], 1e-6)
        _check(checks, f"{did}_stress_pf", act["STRESS"]["pf"], tgt["stress_pf"], 1e-5)
        _check(checks, f"{did}_fwd_entries", act["FORWARD_BASE"]["entries"], tgt["fwd_entries"], 0)
        _check(checks, f"{did}_fwd_net_own", act["FORWARD_BASE"]["net_own"], tgt["fwd_net"], 1e-6)

    for prof, tgt in PORTFOLIO_TARGETS.items():
        act = port_out[prof]
        _check(checks, f"port_{prof.lower()}_entries", act["entries"], tgt["entries"], 0)
        _check(checks, f"port_{prof.lower()}_net_own", act["net_own"], tgt["net_own"], 1e-4)
        _check(checks, f"port_{prof.lower()}_net_vwap", act["net_vwap"], tgt["net_vwap"], 1e-4)
        _check(checks, f"port_{prof.lower()}_m2m_dd", act["m2m_dd"], tgt["m2m_dd"], 0.005)
        _check(checks, f"port_{prof.lower()}_m2m_dd_pct", act["m2m_dd_pct"], tgt["m2m_dd_pct"], 0.01)
        _check(checks, f"port_{prof.lower()}_closed_dd", act["closed_dd"], tgt["closed_dd"], 1e-4)
        _check(checks, f"port_{prof.lower()}_pf_own", act["pf_own"], tgt["pf_own"], 1e-4)
        _check(checks, f"port_{prof.lower()}_pf_vwap", act["pf_vwap"], tgt["pf_vwap"], 1e-4)
        _check(checks, f"port_{prof.lower()}_peak_legs", act["peak_concurrent_legs"], tgt["peak_legs"], 0)

    for prof, tgt in FORWARD_TARGETS.items():
        act = fwd_out[prof]
        _check(checks, f"fwd_{prof.lower()}_entries", act["entries"], tgt["entries"], 0)
        _check(checks, f"fwd_{prof.lower()}_net_own", act["net_own"], tgt["net_own"], 1e-5)
        _check(checks, f"fwd_{prof.lower()}_m2m_dd", act["m2m_dd"], tgt["m2m_dd"], 0.005)
        _check(checks, f"fwd_{prof.lower()}_m2m_dd_pct", act["m2m_dd_pct"], tgt["m2m_dd_pct"], 0.01)

    all_pass = all(v["pass"] for v in checks.values())
    print(f"    Full Target Reconciliation ({len(checks)} checks): {'PASS' if all_pass else 'FAIL'}", flush=True)
    if not all_pass:
        failing = {k: v for k, v in checks.items() if not v["pass"]}
        raise RuntimeError(f"Reconciliation failed:\n{json.dumps(failing, indent=2)}")

    # Save versioned artifacts
    print("[5/5] Saving outputs to plus43_v2_1_tp_only & verification report...", flush=True)
    prune_meta = sorted({(f"{s.symbol}/{s.combo_name}", str(s.symbol), str(s.timeframe)) for s in prune_signals_3y})
    expected_43 = [{"display_id": d, "source_group": "PRUNE23", "symbol": sym, "timeframe": tf} for d, sym, tf in prune_meta] + [
        {"display_id": str(r["display_id"]), "source_group": "TV20", "symbol": str(r["symbol"]), "timeframe": str(r["timeframe"])}
        for r in registry_v2_1.to_dict("records")
    ]
    per_combo_v2_1 = p43_all._build_per_combo_table_profiles("PORT_FINAL_TP_ONLY", trades_by_prof, expected_43, PROFILES)

    pd.DataFrame(portfolio_rows).to_csv(OUT_V2_1 / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv", index=False)
    pd.DataFrame(split_rows).to_csv(OUT_V2_1 / "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv", index=False)
    pd.DataFrame(yearly_rows).to_csv(OUT_V2_1 / "PORTFOLIO_YEARLY_ALL_SIGNALS.csv", index=False)
    per_combo_v2_1.to_csv(OUT_V2_1 / "PER_COMBO_PLUS43_ALL_SIGNALS.csv", index=False)
    pd.concat(equity_frames, ignore_index=True).to_csv(OUT_V2_1 / "DAILY_EQUITY_ALL_SIGNALS.csv", index=False)

    fwd_end_utc = f28._ms_to_iso(max_fwd_ms)
    fwd_payload = {
        "study_group": "PORT_FINAL_TP_ONLY",
        "start_utc": p43_fwd.FORWARD_START_ISO,
        "end_utc": fwd_end_utc,
        "signals_total": len(all_fwd_signals),
        "signals_prune23": len(prune_fwd),
        "signals_tv20": len(tv_fwd),
        "profiles": fwd_out,
        "standalone": {k: v["FORWARD_BASE"] for k, v in standalone_results.items()},
    }
    (OUT_V2_1 / "FORWARD_POST_3Y.json").write_text(json.dumps(fwd_payload, indent=2), encoding="utf-8")

    manifest_out = {
        "execution_id": datetime.now(timezone.utc).strftime("V3PLUS43_V2_1_TP_%Y%m%dT%H%M%SZ"),
        "study": "V3_LAB_11_PLUS43_V2_1_TP_ONLY",
        "version_tag": "PLUS43_V2_1_TP_ONLY",
        "elapsed_seconds": time.time() - started,
        "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        "registry_v2_1_sha256": manifest_cfg["registry_v2_1_sha256"],
        "config_hash": plus44.config_hash(registry_v2_1),
        "cost_profiles_sha256": canonical_cost_profiles_json_sha256(),
        "git_head": p44_all._git_output(["rev-parse", "HEAD"]),
        "portfolio_summary": portfolio_rows,
    }
    (OUT_V2_1 / "RUN_MANIFEST.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")

    verif_payload = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version_tag": "PLUS43_V2_1_TP_ONLY",
        "manifest_sha256_info": {
            "registry_v2_1_sha256": manifest_cfg["registry_v2_1_sha256"],
            "rollback_v2_tv28_h8_sha256": manifest_cfg["rollback"]["v2_tv28_h8"]["registry_sha256"],
            "rollback_v1_freeze_sha256": manifest_cfg["rollback"]["v1_freeze"]["registry_sha256"],
            "cost_profiles_sha256": canonical_cost_profiles_json_sha256(),
            "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        },
        "standalone_results": standalone_results,
        "portfolio_3y": port_out,
        "portfolio_forward": {"start_utc": p43_fwd.FORWARD_START_ISO, "end_utc": fwd_end_utc, **fwd_out},
        "accounting_details": acct_details,
        "dry_run_upgraded": dry_run,
        "reconciliation_pass": all_pass,
        "reconciliation_checks_count": len(checks),
        "reconciliation_details": checks,
    }
    (REPORT_DIR / "plus43_v2_1_tp_verification.json").write_text(json.dumps(verif_payload, indent=2), encoding="utf-8")
    print(f"DONE in {manifest_out['elapsed_seconds']:.1f}s -> {OUT_V2_1}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
