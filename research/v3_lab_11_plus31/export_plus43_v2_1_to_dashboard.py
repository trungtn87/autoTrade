"""Export verified PLUS43_V2_1_TP_ONLY (IDEAL/BASE/STRESS + Forward) for NinLab Dashboard."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
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
if str(LAB) not in sys.path:
    sys.path.insert(0, str(LAB))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_backtest as core
import run_plus43_all_signals as p43_all
import run_plus43_forward as p43_fwd
import run_plus44 as plus44
import run_plus44_all_signals as p44_all
from autotrade_v3.sim.costs import canonical_cost_profiles_json_sha256

REG_V2_1_PATH = LAB / "registry_v2" / "TV20_PLUS43_V2_1_TP_REGISTRY.csv"
BASELINE_DIR = LAB / "outputs" / "plus43_all_signals"
OUT_V2_1 = LAB / "outputs" / "plus43_v2_1_tp_only"
PROFILES: tuple[str, ...] = ("IDEAL", "BASE", "STRESS")
PLUS43_GROUP = "PRUNE23_PLUS_TV20"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _summarize_own_window(trades: pd.DataFrame, snapshots: pd.DataFrame) -> dict[str, Any]:
    """Summarize window strictly using pnl_own_entry so splits == yearly == summary == per_combo."""
    if trades.empty:
        return p44_all._summarize_window(trades, snapshots, use_global_m2m=False)
    frame_own = trades.copy()
    frame_own["net_pnl"] = frame_own["pnl_own_entry"].astype(float)
    return p44_all._summarize_window(frame_own, snapshots, use_global_m2m=False)


def main() -> int:
    started = time.time()
    started_at = datetime.now(timezone.utc).isoformat()
    execution_id = datetime.now(timezone.utc).strftime("V3PLUS43_V2_1_TP_%Y%m%dT%H%M%SZ")
    OUT_V2_1.mkdir(parents=True, exist_ok=True)

    core.configure_tv_lab()
    core.ensure_fixed_v1_registered()
    p44_all._validate_splits_against_report()

    integrity = core.DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(f"Data integrity failed: {integrity}")

    registry_v2_1 = pd.read_csv(REG_V2_1_PATH).sort_values("display_id").reset_index(drop=True)
    assert len(registry_v2_1) == 20 and registry_v2_1["display_id"].nunique() == 20

    layer = core.DataLayer()
    m1 = {sym: layer.query_m1(sym, core.START, core.END) for sym in ("BTC-USDT", "ETH-USDT")}
    start_ms = int(pd.Timestamp(core.START, tz="UTC").value // 10**6)
    end_ms = int(pd.Timestamp(core.END, tz="UTC").value // 10**6)
    m15 = {sym: layer.query_timeframe(sym, "15m", core.START, core.END, include_warmup=True) for sym in m1}

    print("[1/4] Generating 3Y signals for PRUNE23 + TV20 V2.1 TP...", flush=True)
    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(core._tv_worker, row): row["display_id"] for row in registry_v2_1.to_dict("records")}
        for fut in as_completed(futures):
            tv_signals.extend(fut.result()["signals"])
    assert len(tv_signals) == 2313

    prune = core.V3Lab11Prune23Strategy()
    prune_signals = [
        s
        for s in prune.generate_signals(m15, symbols=list(m1), only_approved=True, only_warmup_ready=True)
        if start_ms <= int(s.signal_close_ms) + 1 < end_ms
    ]
    assert len(prune_signals) == 4247
    all_signals = prune_signals + tv_signals
    assert len(all_signals) == 6560

    prune_meta = sorted({(f"{s.symbol}/{s.combo_name}", str(s.symbol), str(s.timeframe)) for s in prune_signals})
    expected_43 = [{"display_id": d, "source_group": "PRUNE23", "symbol": sym, "timeframe": tf} for d, sym, tf in prune_meta] + [
        {"display_id": str(r["display_id"]), "source_group": "TV20", "symbol": str(r["symbol"]), "timeframe": str(r["timeframe"])}
        for r in registry_v2_1.to_dict("records")
    ]

    print("[2/4] Running ALL_SIGNALS_PORTFOLIO across IDEAL, BASE, STRESS...", flush=True)
    portfolio_rows: list[dict[str, Any]] = []
    # Preserve baseline PRUNE23_ONLY rows (read-only) for dashboard comparison
    base_summary_path = BASELINE_DIR / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv"
    if base_summary_path.is_file():
        base_summary_df = pd.read_csv(base_summary_path)
        for rec in base_summary_df.loc[base_summary_df["study_group"].isin(["PRUNE23_ONLY", "PRUNE23_ONLY_NO_FUNDING"])].to_dict("records"):
            portfolio_rows.append(rec)

    split_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    equity_frames: list[pd.DataFrame] = []
    trades_by_prof: dict[str, pd.DataFrame] = {}
    summary_by_prof: dict[str, dict[str, Any]] = {}
    acct_details: dict[str, Any] = {}

    for prof in PROFILES:
        res = p44_all.run_all_signals_portfolio(all_signals, m1, prof, with_funding=True)
        summary_dict = dict(res.summary_dict or {})
        acct_inv = dict(summary_dict.get("accounting_invariant") or {})
        acct_details[prof] = acct_inv
        assert float(acct_inv.get("equity_diff", 1.0)) < 1e-6, f"Equity diff failed for {prof}: {acct_inv}"
        assert len(res.skipped_signals) == 0 and len(res.rejected_signals) == 0

        metrics, raw_frame = core.result_payload(res)
        frame = core.normalized_trades(raw_frame, PLUS43_GROUP, prof)
        snaps = res.snapshots.copy()
        trades_by_prof[prof] = frame

        sort_cols = [c for c in ("exit_time_ms", "entry_time_ms", "trade_id") if c in frame.columns]
        ordered = frame.sort_values(sort_cols)
        own = ordered["pnl_own_entry"].astype(float)
        vwap = ordered["net_pnl"].astype(float)
        net_own = float(own.sum())
        net_vwap = float(vwap.sum())
        closed_dd_own = float(p44_all._compute_closed_dd(own, core.INITIAL_EQUITY))
        peak_legs = int(plus44.peak_concurrent(ordered))
        m2m_dd = float(metrics["m2m_dd"])
        m2m_dd_pct = float(summary_dict.get("m2m_max_drawdown_pct", snaps["drawdown_pct"].max()))

        # Attribute portfolio net_pnl strictly via pnl_own_entry so dashboard parity holds to 1e-12
        entry_ms = frame["entry_time_ms"].astype(np.int64)
        period_own = {}
        for idx, yp in enumerate(p44_all.YEARLY_PERIODS, start=1):
            m_yr = (entry_ms >= int(yp["start_ms"])) & (entry_ms < int(yp["end_ms_exclusive"]))
            period_own[f"p{idx}_net"] = float(frame.loc[m_yr, "pnl_own_entry"].astype(float).sum())

        row_sum = {
            "study_group": PLUS43_GROUP,
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
            "net_pnl": net_own,
            "net_pnl_vwap": net_vwap,
            "roi_pct": net_own / core.INITIAL_EQUITY * 100.0,
            "gross_pnl": float(metrics["gross_pnl"]),
            "fees": float(metrics["fees"]),
            "funding": float(metrics["funding"]),
            "slippage": float(metrics["slippage"]),
            "trading_costs": float(metrics["trading_costs"]),
            "m2m_dd": m2m_dd,
            "m2m_dd_pct": m2m_dd_pct,
            "max_dd_closed": closed_dd_own,
            "max_dd_closed_vwap": float(metrics["max_dd_closed"]),
            "peak_concurrent_legs": peak_legs,
            "win_rate_pct": float((own > 0.0).mean() * 100.0),
            "profit_factor": float(p44_all._calc_profit_factor(own)),
            "profit_factor_vwap": float(metrics["profit_factor"]),
            **period_own,
            "buy_net_pnl": float(ordered.loc[ordered["side"].eq("BUY"), "pnl_own_entry"].astype(float).sum()),
            "sell_net_pnl": float(ordered.loc[ordered["side"].eq("SELL"), "pnl_own_entry"].astype(float).sum()),
            "hold_avg_hours": float(metrics["hold_avg_hours"]),
            "hold_p90_hours": float(metrics["hold_p90_hours"]),
            "hold_max_hours": float(metrics["hold_max_hours"]),
            "exit_reasons": metrics["exit_reasons"],
            "accounting_pass": True,
            "cost_profile_hash": str(summary_dict.get("cost_profile_hash", "")),
            "status": str(summary_dict.get("status", "OK")),
        }
        summary_by_prof[prof] = row_sum
        portfolio_rows.append(row_sum)

        eq_df = core.daily_equity(snaps, PLUS43_GROUP, prof)
        if not eq_df.empty:
            equity_frames.append(eq_df)

        snap_ms = snaps["timestamp_ms"].astype(np.int64)
        for sp in p44_all.V3_18_SPLITS:
            t_sub = frame.loc[(entry_ms >= int(sp["start_ms"])) & (entry_ms <= int(sp["end_ms"]))]
            s_sub = snaps.loc[(snap_ms >= int(sp["start_ms"])) & (snap_ms <= int(sp["end_ms"]))]
            sp_stats = _summarize_own_window(t_sub, s_sub)
            split_rows.append(
                {
                    "study_group": PLUS43_GROUP,
                    "profile": prof,
                    "funding_mode": "SIGNED_HISTORICAL_FUNDING",
                    "split": sp["split"],
                    "label_vi": sp["label_vi"],
                    "start": sp["start"],
                    "end": sp["end"],
                    **sp_stats,
                }
            )
        for yp in p44_all.YEARLY_PERIODS:
            t_sub = frame.loc[(entry_ms >= int(yp["start_ms"])) & (entry_ms < int(yp["end_ms_exclusive"]))]
            s_sub = snaps.loc[(snap_ms >= int(yp["start_ms"])) & (snap_ms < int(yp["end_ms_exclusive"]))]
            yr_stats = _summarize_own_window(t_sub, s_sub)
            yearly_rows.append(
                {
                    "study_group": PLUS43_GROUP,
                    "profile": prof,
                    "funding_mode": "SIGNED_HISTORICAL_FUNDING",
                    "period": yp["period"],
                    "start": yp["start"],
                    "end_exclusive": yp["end_exclusive"],
                    **yr_stats,
                }
            )
        print(f"  -> {prof}: trades={row_sum['executed_trades']} net_own={net_own:+.2f} net_vwap={net_vwap:+.2f} m2m_dd={m2m_dd:.2f}", flush=True)

    per_combo_df = p43_all._build_per_combo_table_profiles(PLUS43_GROUP, trades_by_prof, expected_43, PROFILES)

    # Assert exact parity required by dashboard verify()
    summary_df = pd.DataFrame(portfolio_rows)
    splits_df = pd.DataFrame(split_rows)
    yearly_df = pd.DataFrame(yearly_rows)
    equity_df = pd.concat(equity_frames, ignore_index=True)

    for prof in PROFILES:
        tgt_net = float(summary_by_prof[prof]["net_pnl"])
        assert abs(float(per_combo_df.loc[per_combo_df["profile"] == prof, "net_pnl_own_entry"].sum()) - tgt_net) < 1e-6
        assert abs(float(splits_df.loc[splits_df["profile"] == prof, "net_pnl"].sum()) - tgt_net) < 1e-6
        assert abs(float(yearly_df.loc[yearly_df["profile"] == prof, "net_pnl"].sum()) - tgt_net) < 1e-6

    print("[3/4] Writing dashboard-verified CSVs & Forward JSON...", flush=True)
    files_map = {
        "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv": summary_df,
        "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv": splits_df,
        "PORTFOLIO_YEARLY_ALL_SIGNALS.csv": yearly_df,
        "PER_COMBO_PLUS43_ALL_SIGNALS.csv": per_combo_df,
        "DAILY_EQUITY_ALL_SIGNALS.csv": equity_df,
    }
    output_files_meta: dict[str, Any] = {}
    for fname, df_out in files_map.items():
        fpath = OUT_V2_1 / fname
        df_out.to_csv(fpath, index=False)
        output_files_meta[fname] = {"sha256": _sha256_file(fpath), "rows": int(len(df_out))}

    fwd_payload = p43_fwd.run_forward(
        ROOT / "data" / "bingx",
        out_json=OUT_V2_1 / "FORWARD_POST_3Y.json",
        registry_path=REG_V2_1_PATH,
    )
    print(
        f"  -> FORWARD ({fwd_payload['start_utc'][:10]} -> {fwd_payload['end_utc'][:10]}): "
        f"BASE={fwd_payload['profiles']['BASE']['net_pnl']:+.2f}, STRESS={fwd_payload['profiles']['STRESS']['net_pnl']:+.2f}",
        flush=True,
    )

    print("[4/4] Writing RUN_MANIFEST.json for fail-closed dashboard verification...", flush=True)
    finished_at = datetime.now(timezone.utc).isoformat()
    manifest_out = {
        "execution_id": execution_id,
        "study": "V3_LAB_11_PLUS43_V2_1_TP_ONLY",
        "version_tag": "PLUS43_V2_1_TP_ONLY",
        "mode": "ALL_SIGNALS_PORTFOLIO",
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": time.time() - started,
        "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        "config_hash": plus44.config_hash(registry_v2_1),
        "registry_v2_1_sha256": _sha256_file(REG_V2_1_PATH),
        "cost_profiles_sha256": canonical_cost_profiles_json_sha256(),
        "simulator_version": "UnifiedSimulator/local-current",
        "git_head": p44_all._git_output(["rev-parse", "HEAD"]),
        "git_branch": p44_all._git_output(["rev-parse", "--abbrev-ref", "HEAD"]),
        "window": {"start": core.START, "end_exclusive": core.END},
        "initial_equity": core.INITIAL_EQUITY,
        "notional_per_trade": core.NOTIONAL,
        "leverage": {"BTC-USDT": 100, "ETH-USDT": 50},
        "exit_mode": "100_HARD",
        "ambiguity_policy": "CONSERVATIVE",
        "accounting_all_pass": True,
        "skipped_all_zero": True,
        "any_rejected": False,
        "output_files": output_files_meta,
    }
    (OUT_V2_1 / "RUN_MANIFEST.json").write_text(json.dumps(manifest_out, indent=2), encoding="utf-8")
    print(f"DONE in {manifest_out['elapsed_seconds']:.1f}s -> {OUT_V2_1}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
