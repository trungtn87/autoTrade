from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
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
import run_plus44 as plus44
import run_plus44_all_signals as p44_all
from autotrade_v3.sim.costs import canonical_cost_profiles_json_sha256

OUT = LAB / "outputs" / "plus43_all_signals"
OUT.mkdir(parents=True, exist_ok=True)

PROFILES: tuple[str, ...] = ("IDEAL", "BASE", "STRESS")
EXCLUDED_TV: set[str] = {"TV13"}
TV20_KEPT: set[str] = set(plus44.KEPT) - EXCLUDED_TV
PLUS43_GROUP = "PRUNE23_PLUS_TV20"


def _build_per_combo_table_profiles(
    study_group: str,
    runs_by_profile: dict[str, pd.DataFrame],
    expected_combos: list[dict[str, str]],
    profiles: tuple[str, ...] = PROFILES,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        frame = runs_by_profile[profile]
        by_id = {str(k): part.copy() for k, part in frame.groupby("display_id", sort=False)}
        for meta in expected_combos:
            did = meta["display_id"]
            part = by_id.get(did, pd.DataFrame(columns=frame.columns))
            if part.empty:
                rows.append({
                    "study_group": study_group,
                    "display_id": did,
                    "source_group": meta["source_group"],
                    "symbol": meta["symbol"],
                    "timeframe": meta["timeframe"],
                    "profile": profile,
                    "executed_trades": 0,
                    "buy_trades": 0,
                    "sell_trades": 0,
                    "win_rate_pct_own": 0.0,
                    "win_rate_pct_vwap": 0.0,
                    "gross_pnl": 0.0,
                    "fees": 0.0,
                    "funding": 0.0,
                    "slippage": 0.0,
                    "net_pnl_own_entry": 0.0,
                    "net_pnl_vwap": 0.0,
                    "roi_pct_own": 0.0,
                    "roi_pct_vwap": 0.0,
                    "profit_factor_own": 0.0,
                    "max_dd_closed_own": 0.0,
                    "tp_count": 0,
                    "sl_count": 0,
                    "force_close_count": 0,
                    "dev_net_own": 0.0,
                    "val_net_own": 0.0,
                    "holdout_net_own": 0.0,
                    "dev_net_vwap": 0.0,
                    "val_net_vwap": 0.0,
                    "holdout_net_vwap": 0.0,
                    "y1_net_own": 0.0,
                    "y2_net_own": 0.0,
                    "y3_net_own": 0.0,
                })
                continue

            ordered = part.sort_values(["exit_time_ms", "entry_time_ms", "trade_id"])
            own = ordered["pnl_own_entry"].astype(float)
            vwap = ordered["net_pnl"].astype(float)
            entry_ms = ordered["entry_time_ms"].astype(np.int64)

            split_vals: dict[str, float] = {}
            for sp in p44_all.V3_18_SPLITS:
                mask = (entry_ms >= int(sp["start_ms"])) & (entry_ms <= int(sp["end_ms"]))
                key_prefix = {"DEVELOPMENT": "dev", "VALIDATION": "val", "HOLDOUT": "holdout"}[sp["split"]]
                split_vals[f"{key_prefix}_net_own"] = float(own.loc[mask].sum())
                split_vals[f"{key_prefix}_net_vwap"] = float(vwap.loc[mask].sum())

            yr_vals: dict[str, float] = {}
            for yp in p44_all.YEARLY_PERIODS:
                mask = (entry_ms >= int(yp["start_ms"])) & (entry_ms < int(yp["end_ms_exclusive"]))
                yr_vals[f"{yp['period'].lower()}_net_own"] = float(own.loc[mask].sum())

            net_own = float(own.sum())
            net_vwap = float(vwap.sum())
            rows.append({
                "study_group": study_group,
                "display_id": did,
                "source_group": meta["source_group"],
                "symbol": meta["symbol"],
                "timeframe": meta["timeframe"],
                "profile": profile,
                "executed_trades": int(len(ordered)),
                "buy_trades": int(ordered["side"].eq("BUY").sum()),
                "sell_trades": int(ordered["side"].eq("SELL").sum()),
                "win_rate_pct_own": float((own > 0.0).mean() * 100.0),
                "win_rate_pct_vwap": float((vwap > 0.0).mean() * 100.0),
                "gross_pnl": float(ordered["gross_pnl"].astype(float).sum()),
                "fees": float(ordered["fee_total"].astype(float).sum()),
                "funding": float(ordered["funding_cost"].astype(float).sum()),
                "slippage": float(ordered["slippage_cost"].astype(float).sum()),
                "net_pnl_own_entry": net_own,
                "net_pnl_vwap": net_vwap,
                "roi_pct_own": net_own / core.INITIAL_EQUITY * 100.0,
                "roi_pct_vwap": net_vwap / core.INITIAL_EQUITY * 100.0,
                "profit_factor_own": p44_all._calc_profit_factor(own),
                "max_dd_closed_own": p44_all._compute_closed_dd(own, core.INITIAL_EQUITY),
                "tp_count": int(ordered["exit_reason"].eq("TP").sum()),
                "sl_count": int(ordered["exit_reason"].eq("SL").sum()),
                "force_close_count": int(ordered["exit_reason"].eq("FORCE_CLOSE").sum()),
                **split_vals,
                **yr_vals,
            })
    df = pd.DataFrame(rows)
    return df.sort_values(["profile", "source_group", "display_id"]).reset_index(drop=True)


def main() -> int:
    started = time.time()
    execution_id = datetime.now(timezone.utc).strftime("V3PLUS43_ALLSIG_%Y%m%dT%H%M%SZ")
    core.configure_tv_lab()
    p44_all._validate_splits_against_report()

    integrity = core.DataLayer().verify_integrity()
    if not integrity["passed"]:
        raise RuntimeError(f"Data integrity check failed: {integrity}")

    full_registry = core.load_registry()
    assert set(full_registry.display_id) == plus44.KEPT | plus44.REMOVED
    registry = (
        full_registry.loc[full_registry.display_id.isin(TV20_KEPT)]
        .sort_values("display_id")
        .reset_index(drop=True)
    )
    assert len(registry) == 20 and registry.display_id.nunique() == 20

    tv_signals: list[Any] = []
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    print(f"Generating 20 frozen TV configurations (excluding TV13) with {workers} workers...", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(core._tv_worker, row): row["display_id"]
            for row in registry.to_dict("records")
        }
        for future in as_completed(futures):
            payload = future.result()
            tv_signals.extend(payload["signals"])
            print(f"  {payload['display_id']} signals complete ({len(payload['signals'])})", flush=True)

    layer = core.DataLayer()
    m1 = {symbol: layer.query_m1(symbol, core.START, core.END) for symbol in ("BTC-USDT", "ETH-USDT")}
    start_ms = int(pd.Timestamp(core.START, tz="UTC").value // 10**6)
    end_ms = int(pd.Timestamp(core.END, tz="UTC").value // 10**6)
    m15 = {
        symbol: layer.query_timeframe(symbol, "15m", core.START, core.END, include_warmup=True)
        for symbol in m1
    }

    core.ensure_fixed_v1_registered()
    prune = core.V3Lab11Prune23Strategy()
    prune_signals = [
        signal
        for signal in prune.generate_signals(
            m15, symbols=list(m1), only_approved=True, only_warmup_ready=True
        )
        if start_ms <= int(signal.signal_close_ms) + 1 < end_ms
    ]

    print(
        f"Signal counts: PRUNE23={len(prune_signals)}, TV20={len(tv_signals)}, "
        f"PLUS43={len(prune_signals) + len(tv_signals)}",
        flush=True,
    )
    if len(prune_signals) != 4247 or len(tv_signals) != 2225:
        raise RuntimeError(
            f"Unexpected signal counts: PRUNE23={len(prune_signals)} (expected 4247), "
            f"TV20={len(tv_signals)} (expected 2225)"
        )

    prune_combo_meta = sorted(
        {
            (f"{s.symbol}/{s.combo_name}", str(s.symbol), str(s.timeframe))
            for s in prune_signals
        }
    )
    assert len(prune_combo_meta) == 23
    prune_expected = [
        {"display_id": did, "source_group": "PRUNE23", "symbol": sym, "timeframe": tf}
        for did, sym, tf in prune_combo_meta
    ]
    tv_expected = [
        {
            "display_id": str(row["display_id"]),
            "source_group": "TV20",
            "symbol": str(row["symbol"]),
            "timeframe": str(row["timeframe"]),
        }
        for row in registry.to_dict("records")
    ]
    plus43_expected = prune_expected + tv_expected
    assert len(plus43_expected) == 43

    run_specs: list[tuple[str, str, bool, list[Any]]] = [
        ("PRUNE23_ONLY", prof, True, prune_signals) for prof in PROFILES
    ] + [
        (PLUS43_GROUP, prof, True, prune_signals + tv_signals) for prof in PROFILES
    ] + [
        ("PRUNE23_ONLY_NO_FUNDING", "BASE", False, prune_signals),
    ]

    portfolio_rows: list[dict[str, Any]] = []
    split_rows: list[dict[str, Any]] = []
    yearly_rows: list[dict[str, Any]] = []
    equity_frames: list[pd.DataFrame] = []
    trades_map: dict[tuple[str, str], pd.DataFrame] = {}
    summary_map: dict[tuple[str, str], dict[str, Any]] = {}
    accounting_details: dict[str, Any] = {}

    for group_name, profile, with_funding, sigs in run_specs:
        funding_label = "SIGNED_HISTORICAL_FUNDING" if with_funding else "NOT_MODELED"
        print(
            f"Running ALL_SIGNALS_PORTFOLIO {group_name}/{profile} [{funding_label}] ({len(sigs)} signals)...",
            flush=True,
        )
        result = p44_all.run_all_signals_portfolio(sigs, m1, profile, with_funding=with_funding)
        summary_dict = dict(result.summary_dict or {})
        acct_inv = dict(summary_dict.get("accounting_invariant") or {})
        acct_key = f"{group_name}__{profile}"
        accounting_details[acct_key] = acct_inv
        if not acct_inv.get("passed", False):
            raise RuntimeError(f"Accounting invariant FAIL for {acct_key}: {acct_inv}")

        skipped_count = len(result.skipped_signals)
        rejected_count = len(result.rejected_signals)
        unfillable_count = sum(
            1 for d in result.signal_decisions if getattr(d, "status", "") == "UNFILLABLE"
        )
        if skipped_count != 0:
            raise RuntimeError(f"Expected skipped_signals == 0 for {acct_key}, got {skipped_count}")

        metrics, raw_frame = core.result_payload(result)
        frame = core.normalized_trades(raw_frame, group_name, profile)
        snaps = result.snapshots.copy()
        peak_legs = plus44.peak_concurrent(frame)
        m2m_dd_pct = float(
            summary_dict.get("m2m_max_drawdown_pct", snaps["drawdown_pct"].max() if not snaps.empty else 0.0)
        )
        trades_map[(group_name, profile)] = frame

        row_summary = {
            "study_group": group_name,
            "profile": profile,
            "mode": "ALL_SIGNALS_PORTFOLIO",
            "funding_mode": funding_label,
            "total_signals": len(sigs),
            "executed_trades": int(metrics["executed_trades"]),
            "accepted_signals": int(metrics["accepted_signals"]),
            "skipped_signals": int(skipped_count),
            "rejected_signals": int(rejected_count),
            "unfillable_signals": int(unfillable_count),
            "buy_trades": int(metrics["buy_trades"]),
            "sell_trades": int(metrics["sell_trades"]),
            "net_pnl": float(metrics["net_pnl"]),
            "roi_pct": float(metrics["roi_pct"]),
            "gross_pnl": float(metrics["gross_pnl"]),
            "fees": float(metrics["fees"]),
            "funding": float(metrics["funding"]),
            "slippage": float(metrics["slippage"]),
            "trading_costs": float(metrics["trading_costs"]),
            "m2m_dd": float(metrics["m2m_dd"]),
            "m2m_dd_pct": m2m_dd_pct,
            "max_dd_closed": float(metrics["max_dd_closed"]),
            "peak_concurrent_legs": int(peak_legs),
            "win_rate_pct": float(metrics["win_rate_pct"]),
            "profit_factor": float(metrics["profit_factor"]),
            "p1_net": float(metrics["p1_net"]),
            "p2_net": float(metrics["p2_net"]),
            "p3_net": float(metrics["p3_net"]),
            "buy_net_pnl": float(metrics["buy_net_pnl"]),
            "sell_net_pnl": float(metrics["sell_net_pnl"]),
            "hold_avg_hours": float(metrics["hold_avg_hours"]),
            "hold_p90_hours": float(metrics["hold_p90_hours"]),
            "hold_max_hours": float(metrics["hold_max_hours"]),
            "exit_reasons": metrics["exit_reasons"],
            "accounting_pass": bool(acct_inv.get("passed", False)),
            "cost_profile_hash": str(summary_dict.get("cost_profile_hash", "")),
            "status": str(summary_dict.get("status", "OK")),
        }
        summary_map[(group_name, profile)] = row_summary
        portfolio_rows.append(row_summary)

        if with_funding:
            eq_df = core.daily_equity(snaps, group_name, profile)
            if not eq_df.empty:
                equity_frames.append(eq_df)

        entry_ms = frame["entry_time_ms"].astype(np.int64)
        snap_ms = snaps["timestamp_ms"].astype(np.int64) if not snaps.empty else pd.Series([], dtype=np.int64)
        split_trade_sum = 0
        split_net_sum = 0.0
        for sp in p44_all.V3_18_SPLITS:
            t_sub = frame.loc[(entry_ms >= int(sp["start_ms"])) & (entry_ms <= int(sp["end_ms"]))]
            s_sub = snaps.loc[(snap_ms >= int(sp["start_ms"])) & (snap_ms <= int(sp["end_ms"]))] if not snaps.empty else snaps
            sp_stats = p44_all._summarize_window(t_sub, s_sub, use_global_m2m=False)
            split_trade_sum += sp_stats["executed_trades"]
            split_net_sum += sp_stats["net_pnl"]
            split_rows.append({
                "study_group": group_name,
                "profile": profile,
                "funding_mode": funding_label,
                "split": sp["split"],
                "label_vi": sp["label_vi"],
                "start": sp["start"],
                "end": sp["end"],
                **sp_stats,
            })
        assert split_trade_sum == int(metrics["executed_trades"])
        assert abs(split_net_sum - float(metrics["net_pnl"])) < 1e-6

        yr_trade_sum = 0
        yr_net_sum = 0.0
        for yp in p44_all.YEARLY_PERIODS:
            t_sub = frame.loc[(entry_ms >= int(yp["start_ms"])) & (entry_ms < int(yp["end_ms_exclusive"]))]
            s_sub = snaps.loc[(snap_ms >= int(yp["start_ms"])) & (snap_ms < int(yp["end_ms_exclusive"]))] if not snaps.empty else snaps
            yr_stats = p44_all._summarize_window(t_sub, s_sub, use_global_m2m=False)
            yr_trade_sum += yr_stats["executed_trades"]
            yr_net_sum += yr_stats["net_pnl"]
            yearly_rows.append({
                "study_group": group_name,
                "profile": profile,
                "funding_mode": funding_label,
                "period": yp["period"],
                "start": yp["start"],
                "end_exclusive": yp["end_exclusive"],
                **yr_stats,
            })
        assert yr_trade_sum == int(metrics["executed_trades"])
        assert abs(yr_net_sum - float(metrics["net_pnl"])) < 1e-6

        print(
            f"  -> trades={row_summary['executed_trades']} skipped={skipped_count} "
            f"rejected={rejected_count} net={row_summary['net_pnl']:.4f} "
            f"m2m_dd={row_summary['m2m_dd']:.4f} peak_legs={peak_legs} acct_pass={row_summary['accounting_pass']}",
            flush=True,
        )

    per_combo_plus43 = _build_per_combo_table_profiles(
        PLUS43_GROUP,
        {prof: trades_map[(PLUS43_GROUP, prof)] for prof in PROFILES},
        plus43_expected,
        PROFILES,
    )
    per_combo_prune23 = _build_per_combo_table_profiles(
        "PRUNE23_ONLY",
        {prof: trades_map[("PRUNE23_ONLY", prof)] for prof in PROFILES},
        prune_expected,
        PROFILES,
    )

    for grp, df_pc in ((PLUS43_GROUP, per_combo_plus43), ("PRUNE23_ONLY", per_combo_prune23)):
        for prof in PROFILES:
            sub = df_pc.loc[df_pc["profile"] == prof]
            port_net = summary_map[(grp, prof)]["net_pnl"]
            own_sum = float(sub["net_pnl_own_entry"].sum())
            vwap_sum = float(sub["net_pnl_vwap"].sum())
            assert abs(own_sum - port_net) < 1e-6, f"{grp}/{prof} own_sum={own_sum} != {port_net}"
            assert abs(vwap_sum - port_net) < 1e-6, f"{grp}/{prof} vwap_sum={vwap_sum} != {port_net}"

    base_sub = per_combo_plus43.loc[per_combo_plus43["profile"] == "BASE"]
    pos_base_cnt = int((base_sub["net_pnl_own_entry"] > 0.0).sum())
    assert len(base_sub) == 43
    if pos_base_cnt != 43:
        neg_ids = base_sub.loc[base_sub["net_pnl_own_entry"] <= 0.0, "display_id"].tolist()
        raise RuntimeError(f"Expected 43/43 positive BASE own-entry, got {pos_base_cnt}/43 (non-positive={neg_ids})")

    portfolio_path = OUT / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv"
    splits_path = OUT / "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv"
    yearly_path = OUT / "PORTFOLIO_YEARLY_ALL_SIGNALS.csv"
    per_combo_plus43_path = OUT / "PER_COMBO_PLUS43_ALL_SIGNALS.csv"
    per_combo_prune23_path = OUT / "PER_COMBO_PRUNE23_ALL_SIGNALS.csv"
    equity_path = OUT / "DAILY_EQUITY_ALL_SIGNALS.csv"

    pd.DataFrame(portfolio_rows).to_csv(portfolio_path, index=False)
    pd.DataFrame(split_rows).to_csv(splits_path, index=False)
    pd.DataFrame(yearly_rows).to_csv(yearly_path, index=False)
    per_combo_plus43.to_csv(per_combo_plus43_path, index=False)
    per_combo_prune23.to_csv(per_combo_prune23_path, index=False)
    pd.concat(equity_frames, ignore_index=True).to_csv(equity_path, index=False)

    all_acct_pass = all(bool(v.get("passed", False)) for v in accounting_details.values())
    all_skipped_zero = all(row["skipped_signals"] == 0 for row in portfolio_rows)
    any_rejected = any(row["rejected_signals"] > 0 for row in portfolio_rows)

    git_head = p44_all._git_output(["rev-parse", "HEAD"])
    git_branch = p44_all._git_output(["rev-parse", "--abbrev-ref", "HEAD"])
    v3_lab_ref_head = p44_all._git_output(["rev-parse", "v3_lab"])
    cost_profiles_sha256 = canonical_cost_profiles_json_sha256()

    report_lines = [
        "# BÁO CÁO BACKTEST BASELINE PLUS43 (`PRUNE23 + TV20`, LOẠI `TV13`) — `ALL_SIGNALS_PORTFOLIO`",
        "",
        "## 1. Tổng hợp Danh mục 3 năm (`2023-11-01` → `2026-11-01` UTC)",
        "",
        "| Danh mục | Profile | Funding | Net (USD) | ROI (%) | M2M DD (USD) | Max DD đóng (USD) | Entry | Skipped | Rejected | Peak legs | Y1 / Y2 / Y3 Net (USD) |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for grp, lbl in ((PLUS43_GROUP, "PLUS43"), ("PRUNE23_ONLY", "PRUNE23")):
        for prof in PROFILES:
            r = summary_map[(grp, prof)]
            report_lines.append(
                f"| {lbl} | {prof} | {r['funding_mode']} | {r['net_pnl']:.4f} | {r['roi_pct']:.2f}% | "
                f"{r['m2m_dd']:.4f} ({r['m2m_dd_pct']:.2f}%) | {r['max_dd_closed']:.4f} | "
                f"{r['executed_trades']:,} | {r['skipped_signals']} | {r['rejected_signals']} | "
                f"{r['peak_concurrent_legs']} | {r['p1_net']:.2f} / {r['p2_net']:.2f} / {r['p3_net']:.2f} |"
            )

    report_lines += [
        "",
        f"- **Xác nhận BASE own-entry**: `{pos_base_cnt}/43` chiến lược có `net_pnl_own_entry > 0`.",
        f"- **Kế toán & tín hiệu**: `accounting_all_pass={all_acct_pass}`, `skipped_all_zero={all_skipped_zero}`, `any_rejected={any_rejected}`.",
        "",
        "## 2. Kết quả từng chiến lược (`PLUS43` BASE — `net_pnl_own_entry`)",
        "",
        "| Display ID | Nguồn | Symbol | TF | Entry | Net Own-Entry | Gross | Fees | Funding | Slippage | DEV | VAL | HOLDOUT |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in base_sub.sort_values(["source_group", "display_id"]).to_dict("records"):
        report_lines.append(
            f"| {r['display_id']} | {r['source_group']} | {r['symbol']} | {r['timeframe']} | "
            f"{r['executed_trades']:,} | {r['net_pnl_own_entry']:.4f} | {r['gross_pnl']:.4f} | "
            f"{r['fees']:.4f} | {r['funding']:.4f} | {r['slippage']:.4f} | "
            f"{r['dev_net_own']:.2f} | {r['val_net_own']:.2f} | {r['holdout_net_own']:.2f} |"
        )

    report_path = OUT / "FINAL_REPORT_PLUS43_ALL_SIGNALS.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    output_files = {
        path.name: {
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": core.sha256(path),
        }
        for path in (
            portfolio_path,
            splits_path,
            yearly_path,
            per_combo_plus43_path,
            per_combo_prune23_path,
            equity_path,
            report_path,
        )
    }

    manifest = {
        "execution_id": execution_id,
        "study": "V3_LAB_11_PLUS43_ALL_SIGNALS",
        "mode": "ALL_SIGNALS_PORTFOLIO",
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.time() - started,
        "dataset_hash": integrity["manifest"]["data_manifest_hash"],
        "config_hash": plus44.config_hash(registry),
        "cost_profiles_sha256": cost_profiles_sha256,
        "simulator_version": core.SIMULATOR_VERSION,
        "git_head": git_head,
        "git_branch": git_branch,
        "v3_lab_ref_head": v3_lab_ref_head,
        "window": {"start": core.START, "end_exclusive": core.END},
        "v3_18_splits": p44_all.V3_18_SPLITS,
        "yearly_periods": p44_all.YEARLY_PERIODS,
        "initial_equity": core.INITIAL_EQUITY,
        "notional_per_trade": core.NOTIONAL,
        "leverage": {"BTC-USDT": 100, "ETH-USDT": 50},
        "exit_mode": "100_HARD",
        "ambiguity_policy": "CONSERVATIVE",
        "kept_tv": sorted(TV20_KEPT),
        "excluded_tv": sorted(EXCLUDED_TV),
        "removed_tv_legacy": sorted(plus44.REMOVED),
        "profiles": list(PROFILES),
        "signals": {
            "PRUNE23_ONLY": len(prune_signals),
            "TV20_ONLY": len(tv_signals),
            PLUS43_GROUP: len(prune_signals) + len(tv_signals),
        },
        "accounting_all_pass": all_acct_pass,
        "skipped_all_zero": all_skipped_zero,
        "any_rejected": any_rejected,
        "accounting_details": accounting_details,
        "portfolio_summary": portfolio_rows,
        "output_files": output_files,
    }
    manifest_path = OUT / "RUN_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"COMPLETED PLUS43 in {manifest['elapsed_seconds']:.1f}s -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
