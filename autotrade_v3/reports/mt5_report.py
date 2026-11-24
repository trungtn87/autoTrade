"""MT5-style backtest report generator for AutoTrade V3 Research Lab (Issue #102).

Reads ledger artifacts directly (trades.parquet, snapshots.parquet,
signal_decisions.parquet, run_manifest.json, validation.json) and produces:
1. ``report_v1.json`` (machine-readable, unrounded, with ``label_vi``)
2. ``report_mt5.html`` (mobile-first, 100% Vietnamese UI labels)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from autotrade_v3.analysis.metrics import compute_mt5_extended_metrics
from autotrade_v3.reports.labels_vi import LABELS_VI


def _metric_field(
    *,
    value: Any,
    unit: str,
    source: str,
    label_vi: str,
    status: str = "AVAILABLE",
    reason: str | None = None,
) -> dict[str, Any]:
    if value is None and status == "AVAILABLE":
        status = "N/A"
    return {
        "value": value,
        "unit": unit,
        "source": source,
        "status": status,
        "reason": reason,
        "label_vi": label_vi,
    }


def _governance_period_for_ym(year_month: str) -> str:
    if year_month.startswith("2026-"):
        if year_month >= "2026-11":
            return LABELS_VI["gov_forward"]
        return LABELS_VI["gov_2026_validation"]
    return LABELS_VI["gov_development"]


def _pf_from_series(pnl: pd.Series) -> float | str:
    wins = float(pnl[pnl > 0.0].sum())
    losses = float(abs(pnl[pnl < 0.0].sum()))
    if losses > 1e-9:
        return wins / losses
    if wins > 1e-9:
        return "UNDEFINED_NO_LOSS"
    return 0.0


def _compute_combo_rows(
    trades_df: pd.DataFrame,
    decisions_df: pd.DataFrame | None,
    execution_mode: str,
) -> list[dict[str, Any]]:
    df = trades_df.copy()
    if execution_mode in ("BINGX_PORTFOLIO", "ALL_SIGNALS_PORTFOLIO") and "pnl_own_entry" not in df.columns:
        raise ValueError(f"Missing required {execution_mode} column: pnl_own_entry")
    own_col = "pnl_own_entry" if execution_mode in ("BINGX_PORTFOLIO", "ALL_SIGNALS_PORTFOLIO") else "net_pnl"

    sig_counts: dict[str, int] = {}
    if decisions_df is not None and not decisions_df.empty and {"symbol", "combo_name"}.issubset(decisions_df.columns):
        for (sym, cname), grp in decisions_df.groupby(["symbol", "combo_name"], dropna=False):
            sig_counts[f"{sym}/{cname}"] = int(len(grp))

    rows: list[dict[str, Any]] = []
    for (sym, cname), grp in df.groupby(["symbol", "combo_name"], sort=True, dropna=False):
        ckey = f"{sym}/{cname}"
        tf = str(grp["timeframe"].iloc[0]) if "timeframe" in grp.columns and not grp.empty else ""
        entries = int(grp["trade_id"].nunique())
        legs = int(len(grp))
        signals = sig_counts.get(ckey, int(grp["signal_id"].nunique()) if "signal_id" in grp.columns else entries)
        fill_pct = (entries / signals * 100.0) if signals > 0 else 0.0
        leg_wins = int((grp["net_pnl"].astype(float) > 0.0).sum())
        leg_wr_pct = (leg_wins / legs * 100.0) if legs > 0 else 0.0
        own_pnl = float(grp[own_col].astype(float).sum())
        port_pnl = float(grp["net_pnl"].astype(float).sum())
        basis_shift = port_pnl - own_pnl
        fees = float(grp["fee_total"].astype(float).sum())
        net_pf_leg = _pf_from_series(grp["net_pnl"].astype(float))
        rows.append(
            {
                "combo_key": ckey,
                "symbol": str(sym),
                "timeframe": tf,
                "signals": _metric_field(value=signals, unit="count", source="signal_decisions.parquet", label_vi=LABELS_VI["total_signals"]),
                "entries": _metric_field(value=entries, unit="count", source="trades.parquet#trade_id", label_vi=LABELS_VI["accepted_entries"]),
                "fill_rate_pct": _metric_field(value=fill_pct, unit="pct", source="derived:entries/signals", label_vi=LABELS_VI["entry_fill_rate_pct"]),
                "legs": _metric_field(value=legs, unit="count", source="trades.parquet#leg_id", label_vi=LABELS_VI["total_legs"]),
                "leg_win_rate_pct": _metric_field(value=leg_wr_pct, unit="pct", source="trades.parquet#net_pnl", label_vi=LABELS_VI["leg_win_rate_pct"]),
                "own_entry_pnl": _metric_field(value=own_pnl, unit="USD", source=f"trades.parquet#{own_col}", label_vi=LABELS_VI["col_own_entry_pnl"]),
                "portfolio_net_pnl": _metric_field(value=port_pnl, unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["col_portfolio_net_pnl"]),
                "basis_shift_delta": _metric_field(value=basis_shift, unit="USD", source="derived:net_pnl-pnl_own_entry", label_vi=LABELS_VI["col_basis_shift"]),
                "fees": _metric_field(value=fees, unit="USD", source="trades.parquet#fee_total", label_vi=LABELS_VI["col_fees"]),
                "net_pf_leg": _metric_field(value=net_pf_leg, unit="ratio", source="trades.parquet#net_pnl", label_vi=LABELS_VI["col_pf"]),
            }
        )
    rows.sort(key=lambda r: float(r["portfolio_net_pnl"]["value"]), reverse=True)
    return rows


def _compute_symbol_rows(trades_df: pd.DataFrame, decisions_df: pd.DataFrame | None) -> list[dict[str, Any]]:
    sig_by_sym: dict[str, int] = {}
    if decisions_df is not None and not decisions_df.empty and "symbol" in decisions_df.columns:
        for sym, grp in decisions_df.groupby("symbol", dropna=False):
            sig_by_sym[str(sym)] = int(len(grp))

    rows: list[dict[str, Any]] = []
    for sym, grp in trades_df.groupby("symbol", sort=True, dropna=False):
        sym_str = str(sym)
        entries = int(grp["trade_id"].nunique())
        legs = int(len(grp))
        signals = sig_by_sym.get(sym_str, int(grp["signal_id"].nunique()) if "signal_id" in grp.columns else entries)
        leg_wins = int((grp["net_pnl"].astype(float) > 0.0).sum())
        leg_wr = (leg_wins / legs * 100.0) if legs > 0 else 0.0
        gross_pnl = float(grp["gross_pnl"].astype(float).sum())
        fees = float(grp["fee_total"].astype(float).sum())
        net_pnl = float(grp["net_pnl"].astype(float).sum())
        net_pf_leg = _pf_from_series(grp["net_pnl"].astype(float))
        net_pf_trade = _pf_from_series(grp.groupby("trade_id")["net_pnl"].sum().astype(float))
        rows.append(
            {
                "symbol": sym_str,
                "signals": _metric_field(value=signals, unit="count", source="signal_decisions.parquet", label_vi=LABELS_VI["total_signals"]),
                "entries": _metric_field(value=entries, unit="count", source="trades.parquet#trade_id", label_vi=LABELS_VI["accepted_entries"]),
                "legs": _metric_field(value=legs, unit="count", source="trades.parquet#leg_id", label_vi=LABELS_VI["total_legs"]),
                "leg_win_rate_pct": _metric_field(value=leg_wr, unit="pct", source="trades.parquet#net_pnl", label_vi=LABELS_VI["leg_win_rate_pct"]),
                "gross_pnl": _metric_field(value=gross_pnl, unit="USD", source="trades.parquet#gross_pnl", label_vi=LABELS_VI["gross_pnl_before_costs"]),
                "total_fees": _metric_field(value=fees, unit="USD", source="trades.parquet#fee_total", label_vi=LABELS_VI["total_fees"]),
                "net_pnl": _metric_field(value=net_pnl, unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["total_net_profit"]),
                "net_pf_trade": _metric_field(value=net_pf_trade, unit="ratio", source="trades.parquet#net_pnl", label_vi=LABELS_VI["net_pf_trade_vwap"]),
                "net_pf_leg": _metric_field(value=net_pf_leg, unit="ratio", source="trades.parquet#net_pnl", label_vi=LABELS_VI["net_pf_leg"]),
            }
        )
    return rows


def _compute_monthly_rows(trades_df: pd.DataFrame) -> list[dict[str, Any]]:
    df = trades_df.copy()
    df["_ym"] = pd.to_datetime(df["exit_time_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    rows: list[dict[str, Any]] = []
    for ym, grp in df.groupby("_ym", sort=True):
        ym_str = str(ym)
        entries = int(grp["trade_id"].nunique())
        legs = int(len(grp))
        leg_wins = int((grp["net_pnl"].astype(float) > 0.0).sum())
        leg_wr = (leg_wins / legs * 100.0) if legs > 0 else 0.0
        gross_pnl = float(grp["gross_pnl"].astype(float).sum())
        fees = float(grp["fee_total"].astype(float).sum())
        net_pnl = float(grp["net_pnl"].astype(float).sum())
        net_pf_leg = _pf_from_series(grp["net_pnl"].astype(float))
        rows.append(
            {
                "year_month": ym_str,
                "governance_period": _governance_period_for_ym(ym_str),
                "entries": _metric_field(value=entries, unit="count", source="trades.parquet#trade_id", label_vi=LABELS_VI["accepted_entries"]),
                "legs": _metric_field(value=legs, unit="count", source="trades.parquet#leg_id", label_vi=LABELS_VI["total_legs"]),
                "leg_win_rate_pct": _metric_field(value=leg_wr, unit="pct", source="trades.parquet#net_pnl", label_vi=LABELS_VI["leg_win_rate_pct"]),
                "gross_pnl": _metric_field(value=gross_pnl, unit="USD", source="trades.parquet#gross_pnl", label_vi=LABELS_VI["gross_pnl_before_costs"]),
                "fees": _metric_field(value=fees, unit="USD", source="trades.parquet#fee_total", label_vi=LABELS_VI["total_fees"]),
                "net_pnl": _metric_field(value=net_pnl, unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["total_net_profit"]),
                "net_pf_leg": _metric_field(value=net_pf_leg, unit="ratio", source="trades.parquet#net_pnl", label_vi=LABELS_VI["net_pf_leg"]),
            }
        )
    return rows


def _downsample_series(values: Sequence[float], max_points: int = 160) -> list[float]:
    n = len(values)
    if n <= max_points:
        return [float(v) for v in values]
    step = max(1, n // max_points)
    sampled = [float(values[i]) for i in range(0, n, step)]
    if sampled[-1] != float(values[-1]):
        sampled.append(float(values[-1]))
    return sampled


def _build_chart_series(
    trades_df: pd.DataFrame,
    snapshots_df: pd.DataFrame | None,
    initial_equity: float,
) -> dict[str, Any]:
    if snapshots_df is not None and not snapshots_df.empty:
        bal = _downsample_series(snapshots_df["balance"].astype(float).tolist())
        eq = _downsample_series(snapshots_df["equity"].astype(float).tolist())
        dd_usd = _downsample_series(snapshots_df["drawdown_dollars"].astype(float).tolist())
        dd_pct = _downsample_series(snapshots_df["drawdown_pct"].astype(float).tolist())
        return {
            "has_equity_mtm": True,
            "balance": bal,
            "equity_mtm": eq,
            "dd_dollars": dd_usd,
            "dd_pct": dd_pct,
        }

    ordered = trades_df.sort_values(["exit_time_ms", "entry_time_ms", "trade_id", "leg_id"], kind="mergesort")
    bal_list: list[float] = [float(initial_equity)]
    dd_usd_list: list[float] = [0.0]
    dd_pct_list: list[float] = [0.0]
    cur = float(initial_equity)
    peak = cur
    for val in ordered["net_pnl"].astype(float):
        cur += float(val)
        if cur > peak:
            peak = cur
        dd = peak - cur
        dd_p = (dd / peak * 100.0) if peak > 0 else 0.0
        bal_list.append(cur)
        dd_usd_list.append(dd)
        dd_pct_list.append(dd_p)

    return {
        "has_equity_mtm": False,
        "balance": _downsample_series(bal_list),
        "equity_mtm": None,
        "dd_dollars": _downsample_series(dd_usd_list),
        "dd_pct": _downsample_series(dd_pct_list),
    }


def _svg_polyline_points(
    vals: Sequence[float],
    *,
    x0: float,
    x1: float,
    y_top: float,
    y_bot: float,
    v_min: float,
    v_max: float,
) -> str:
    n = len(vals)
    if n == 0:
        return ""
    span_x = x1 - x0
    span_v = (v_max - v_min) if (v_max - v_min) > 1e-12 else 1.0
    pts: list[str] = []
    for i, v in enumerate(vals):
        x = x0 if n == 1 else x0 + (i / (n - 1)) * span_x
        y = y_bot - ((v - v_min) / span_v) * (y_bot - y_top)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _fmt_usd(val: float | None) -> str:
    if val is None:
        return LABELS_VI["na_text"]
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


def _fmt_signed_usd(val: float | None) -> str:
    if val is None:
        return LABELS_VI["na_text"]
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    return f"{sign}${abs(val):,.2f}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return LABELS_VI["na_text"]
    return f"{val:.2f}%"


def _fmt_pf(val: float | str | None) -> str:
    if val is None:
        return LABELS_VI["na_text"]
    if isinstance(val, str):
        return "Không lỗ" if val == "UNDEFINED_NO_LOSS" else html.escape(val)
    return f"{val:.2f}"


def _load_mode_comparison(
    current_mode: str,
    current_payload: dict[str, Any],
    sibling_dirs: dict[str, Path] | None,
) -> list[dict[str, Any]]:
    modes = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
    out: list[dict[str, Any]] = []
    for mode in modes:
        if mode == current_mode:
            kpi = current_payload["kpi"]
            val_status = current_payload["run_info"]["validation_status"]
            out.append(
                {
                    "execution_mode": mode,
                    "validation_status": val_status,
                    "entries": kpi["volume"]["accepted_entries"],
                    "legs": kpi["volume"]["total_legs"],
                    "gross_pnl": kpi["cost_reality"]["gross_pnl_before_costs"],
                    "total_fees": kpi["cost_reality"]["total_fees"],
                    "net_pnl": kpi["mt5_results"]["total_net_profit"],
                    "roi_pct": kpi["mt5_results"]["roi_pct"],
                    "leg_win_rate_pct": kpi["mt5_results"]["leg_win_rate_pct"],
                    "net_pf_leg": kpi["risk"]["net_pf_leg"],
                    "closed_dd_pct": kpi["risk"]["closed_max_dd_pct"],
                    "m2m_dd_pct": kpi["risk"]["m2m_max_dd_pct"],
                }
            )
            continue

        sdir = sibling_dirs.get(mode) if sibling_dirs else None
        if sdir is not None and (sdir / "trades.parquet").exists():
            sib = build_report_data(sdir, include_mode_comparison=False)
            skpi = sib["kpi"]
            out.append(
                {
                    "execution_mode": mode,
                    "validation_status": sib["run_info"]["validation_status"],
                    "entries": skpi["volume"]["accepted_entries"],
                    "legs": skpi["volume"]["total_legs"],
                    "gross_pnl": skpi["cost_reality"]["gross_pnl_before_costs"],
                    "total_fees": skpi["cost_reality"]["total_fees"],
                    "net_pnl": skpi["mt5_results"]["total_net_profit"],
                    "roi_pct": skpi["mt5_results"]["roi_pct"],
                    "leg_win_rate_pct": skpi["mt5_results"]["leg_win_rate_pct"],
                    "net_pf_leg": skpi["risk"]["net_pf_leg"],
                    "closed_dd_pct": skpi["risk"]["closed_max_dd_pct"],
                    "m2m_dd_pct": skpi["risk"]["m2m_max_dd_pct"],
                }
            )
        else:
            def _na_sib(u: str) -> dict[str, Any]:
                return _metric_field(value=None, unit=u, source="sibling_run", label_vi="", status="N/A", reason=LABELS_VI["na_not_run"])
            out.append(
                {
                    "execution_mode": mode,
                    "validation_status": LABELS_VI["na_text"],
                    "entries": _na_sib("count"),
                    "legs": _na_sib("count"),
                    "gross_pnl": _na_sib("USD"),
                    "total_fees": _na_sib("USD"),
                    "net_pnl": _na_sib("USD"),
                    "roi_pct": _na_sib("pct"),
                    "leg_win_rate_pct": _na_sib("pct"),
                    "net_pf_leg": _na_sib("ratio"),
                    "closed_dd_pct": _na_sib("pct"),
                    "m2m_dd_pct": _na_sib("pct"),
                }
            )
    return out


def _build_cost_sensitivity_rows(
    current_cost_profile: str,
    current_payload: dict[str, Any],
    sensitivity_runs: dict[float, Path] | None,
) -> list[dict[str, Any]]:
    grid_bps = [0.0, 1.0, 2.0, 3.4, 5.0]
    labels = {
        0.0: "0,0 bps (IDEAL)",
        1.0: "1,0 bps",
        2.0: "2,0 bps (BASE)",
        3.4: "3,4 bps (Hòa vốn tham chiếu)",
        5.0: "5,0 bps (STRESS)",
    }
    rows: list[dict[str, Any]] = []
    for bps in grid_bps:
        matched_payload: dict[str, Any] | None = None
        run_ref: str | None = None
        if bps == 0.0 and current_cost_profile == "IDEAL":
            matched_payload = current_payload
            run_ref = current_payload["run_info"]["run_id"]
        elif bps == 2.0 and current_cost_profile == "BASE":
            matched_payload = current_payload
            run_ref = current_payload["run_info"]["run_id"]
        elif bps == 5.0 and current_cost_profile == "STRESS":
            matched_payload = current_payload
            run_ref = current_payload["run_info"]["run_id"]
        elif sensitivity_runs and bps in sensitivity_runs and (sensitivity_runs[bps] / "trades.parquet").exists():
            matched_payload = build_report_data(sensitivity_runs[bps], include_mode_comparison=False)
            run_ref = str(sensitivity_runs[bps].name)

        if matched_payload is not None:
            mk = matched_payload["kpi"]
            rows.append(
                {
                    "slippage_bps": bps,
                    "label": labels[bps],
                    "run_ref": run_ref,
                    "net_pnl": mk["mt5_results"]["total_net_profit"],
                    "roi_pct": mk["mt5_results"]["roi_pct"],
                    "net_pf_leg": mk["risk"]["net_pf_leg"],
                    "m2m_dd_pct": mk["risk"]["m2m_max_dd_pct"],
                }
            )
        else:
            def _na_sens(u: str) -> dict[str, Any]:
                return _metric_field(value=None, unit=u, source="cost_sensitivity", label_vi="", status="N/A", reason=LABELS_VI["na_not_run"])
            rows.append(
                {
                    "slippage_bps": bps,
                    "label": labels[bps],
                    "run_ref": None,
                    "net_pnl": _na_sens("USD"),
                    "roi_pct": _na_sens("pct"),
                    "net_pf_leg": _na_sens("ratio"),
                    "m2m_dd_pct": _na_sens("pct"),
                }
            )
    return rows


def build_report_data(
    run_dir: Path,
    *,
    sibling_dirs: dict[str, Path] | None = None,
    sensitivity_runs: dict[float, Path] | None = None,
    include_mode_comparison: bool = False,
) -> dict[str, Any]:
    """Read ledger artifacts from ``run_dir`` and construct ``report_v1.json`` payload."""
    manifest_p = run_dir / "run_manifest.json"
    trades_p = run_dir / "trades.parquet"
    if not manifest_p.exists():
        raise FileNotFoundError(f"Missing run_manifest.json in {run_dir}")
    if not trades_p.exists():
        raise FileNotFoundError(f"Missing trades.parquet in {run_dir}")

    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    trades_df = pd.read_parquet(trades_p)

    snapshots_p = run_dir / "snapshots.parquet"
    snapshots_df = pd.read_parquet(snapshots_p) if snapshots_p.exists() else None

    decisions_p = run_dir / "signal_decisions.parquet"
    decisions_df = pd.read_parquet(decisions_p) if decisions_p.exists() else None

    val_p = run_dir / "validation.json"
    validation_status = manifest.get("validation_status", "UNVERIFIED")
    if val_p.exists():
        val_json = json.loads(val_p.read_text(encoding="utf-8"))
        validation_status = str(
            val_json.get("overall_status")
            or val_json.get("overall_validation")
            or validation_status
        )

    execution_mode = str(manifest.get("execution_mode", "BINGX_PORTFOLIO"))
    initial_equity = float(manifest.get("initial_equity", 1000.0))
    total_signals = int(len(decisions_df)) if decisions_df is not None else manifest.get("total_signals")
    skipped_cnt = int(manifest.get("skipped_signals_count", 0))
    rejected_cnt = int(manifest.get("rejected_signals_count", 0))

    ext = compute_mt5_extended_metrics(
        trades_df,
        initial_equity=initial_equity,
        snapshots_df=snapshots_df,
        total_signals=int(total_signals) if total_signals is not None else None,
        skipped_by_concurrency=skipped_cnt,
        rejected_by_margin=rejected_cnt,
        execution_mode=execution_mode,
    )

    has_snaps = ext["has_snapshots"]
    no_snap_reason = LABELS_VI["na_no_snapshots"] if not has_snaps else None
    entry_basis = ext["entry_basis"]
    acc = ext["account"]
    cost = ext["cost_reality"]
    vol = ext["volume"]
    rsk = ext["risk"]
    res = ext["mt5_results"]
    ls = ext["long_short"]
    stk = ext["streaks"]
    conc = ext["concentration"]

    cost_profile_name = str(manifest.get("cost_profile", "IDEAL"))
    cost_profile_hash = str(manifest.get("cost_profile_hash", ""))

    payload: dict[str, Any] = {
        "schema_version": "1.0.0-draft",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "is_sample_layout": False,
        "run_info": {
            "run_id": str(manifest.get("run_id", run_dir.name)),
            "strategy_id": str(manifest.get("strategy_id", "")),
            "strategy_version": str(manifest.get("strategy_version", "")),
            "execution_mode": execution_mode,
            "entry_stats_basis": entry_basis,
            "symbols": list(manifest.get("symbols", [])),
            "time_window_utc": dict(manifest.get("time_window", {"start": "", "end": ""})),
            "cost_profile": {
                "name": cost_profile_name,
                "hash": cost_profile_hash,
                "status": "CONFIRMED",
                "ideal_warning_required": cost_profile_name == "IDEAL",
            },
            "ambiguity_policy": str(manifest.get("ambiguity_policy", "CONSERVATIVE")),
            "git_commit": str(manifest.get("git_sha", manifest.get("code_sha", ""))),
            "dataset_hash": str(manifest.get("dataset_hash", "")),
            "config_hash": str(manifest.get("config_hash", "")),
            "validation_status": validation_status,
            "governance_labels": {
                "window_2026_label": LABELS_VI["gov_2026_validation"],
                "development_window": "2023-11-01..2025-11-01",
                "forward_evidence_start_utc": "2026-11-14",
            },
        },
        "kpi": {
            "account": {
                "initial_capital": _metric_field(value=acc["initial_capital"], unit="USD", source="run_manifest.json#initial_equity", label_vi=LABELS_VI["initial_capital"]),
                "final_balance": _metric_field(value=acc["final_balance"], unit="USD", source="snapshots.parquet#balance[-1]" if has_snaps else "trades.parquet#net_pnl", label_vi=LABELS_VI["final_balance"]),
                "final_equity_mtm": _metric_field(value=acc["final_equity_mtm"], unit="USD", source="snapshots.parquet#equity[-1]", label_vi=LABELS_VI["final_equity_mtm"], status="AVAILABLE" if has_snaps else "N/A", reason=no_snap_reason),
                "max_used_margin": _metric_field(value=acc["max_used_margin"], unit="USD", source="snapshots.parquet#used_margin", label_vi=LABELS_VI["max_used_margin"], status="AVAILABLE" if has_snaps else "N/A", reason=no_snap_reason),
                "min_free_margin": _metric_field(value=acc["min_free_margin"], unit="USD", source="snapshots.parquet#free_margin", label_vi=LABELS_VI["min_free_margin"], status="AVAILABLE" if has_snaps else "N/A", reason=no_snap_reason),
                "min_margin_level_pct": _metric_field(
                    value=acc["min_margin_level_pct"],
                    unit="pct",
                    source="snapshots.parquet#equity/used_margin",
                    label_vi=LABELS_VI["min_margin_level_pct"],
                    status="AVAILABLE" if acc["min_margin_level_pct"] is not None else "N/A",
                    reason=no_snap_reason or (LABELS_VI["na_no_open_margin"] if acc["min_margin_level_pct"] is None else None),
                ),
            },
            "cost_reality": {
                "gross_pnl_before_costs": _metric_field(value=cost["gross_pnl_before_costs"], unit="USD", source="trades.parquet#gross_pnl", label_vi=LABELS_VI["gross_pnl_before_costs"]),
                "total_fees": _metric_field(value=cost["total_fees"], unit="USD", source="trades.parquet#fee_total", label_vi=LABELS_VI["total_fees"]),
                "fee_drag_pct": _metric_field(value=cost["fee_drag_pct"], unit="pct", source="derived:total_fees/gross_pnl", label_vi=LABELS_VI["fee_drag_pct"]),
                "break_even_slippage_per_side_pct": _metric_field(value=cost["break_even_slippage_per_side_pct"], unit="pct", source="derived:net_pnl/(2*notional)", label_vi=LABELS_VI["break_even_slippage_per_side_pct"]),
                "break_even_slippage_per_side_bps": _metric_field(value=cost["break_even_slippage_per_side_bps"], unit="bps", source="derived:net_pnl/(2*notional)*10000", label_vi=LABELS_VI["break_even_slippage_per_side_bps"]),
                "spread_cost": _metric_field(value=cost["spread_cost"], unit="USD", source="trades.parquet#spread_cost", label_vi=LABELS_VI["spread_cost"]),
                "slippage_cost": _metric_field(value=cost["slippage_cost"], unit="USD", source="trades.parquet#slippage_cost", label_vi=LABELS_VI["slippage_cost"]),
                "funding_cost": _metric_field(value=cost["funding_cost"], unit="USD", source="trades.parquet#funding_cost", label_vi=LABELS_VI["funding_cost"]),
            },
            "risk": {
                "net_pf_entry": _metric_field(value=rsk["net_pf_entry"], unit="ratio", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["net_pf_entry"]),
                "net_pf_trade_vwap": _metric_field(value=rsk["net_pf_trade_vwap"], unit="ratio", source="trades.parquet#net_pnl", label_vi=LABELS_VI["net_pf_trade_vwap"]),
                "net_pf_leg": _metric_field(value=rsk["net_pf_leg"], unit="ratio", source="trades.parquet#net_pnl", label_vi=LABELS_VI["net_pf_leg"]),
                "recovery_factor_closed": _metric_field(value=rsk["recovery_factor_closed"], unit="ratio", source="derived:net_pnl/closed_max_dd", label_vi=LABELS_VI["recovery_factor_closed"], status="AVAILABLE" if rsk["recovery_factor_closed"] is not None else "N/A", reason=None if rsk["recovery_factor_closed"] is not None else "DD bằng 0"),
                "recovery_factor_m2m": _metric_field(value=rsk["recovery_factor_m2m"], unit="ratio", source="derived:net_pnl/m2m_max_dd", label_vi=LABELS_VI["recovery_factor_m2m"], status="AVAILABLE" if rsk["recovery_factor_m2m"] is not None else "N/A", reason=no_snap_reason),
                "sharpe_daily_365": _metric_field(value=rsk["sharpe_daily_365"], unit="ratio_annualized_365d", source="snapshots.parquet#balance" if has_snaps else "trades.parquet#net_pnl", label_vi=LABELS_VI["sharpe_daily_365"], status="AVAILABLE" if rsk["sharpe_daily_365"] is not None else "N/A", reason=None if rsk["sharpe_daily_365"] is not None else LABELS_VI["na_insufficient_days"]),
                "closed_max_dd_dollars": _metric_field(value=rsk["closed_max_dd_dollars"], unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["closed_max_dd_dollars"]),
                "closed_max_dd_pct": _metric_field(value=rsk["closed_max_dd_pct"], unit="pct", source="trades.parquet#net_pnl", label_vi=LABELS_VI["closed_max_dd_pct"]),
                "m2m_max_dd_dollars": _metric_field(value=rsk["m2m_max_dd_dollars"], unit="USD", source="snapshots.parquet#drawdown_dollars", label_vi=LABELS_VI["m2m_max_dd_dollars"], status="AVAILABLE" if has_snaps else "N/A", reason=no_snap_reason),
                "m2m_max_dd_pct": _metric_field(value=rsk["m2m_max_dd_pct"], unit="pct", source="snapshots.parquet#drawdown_pct", label_vi=LABELS_VI["m2m_max_dd_pct"], status="AVAILABLE" if has_snaps else "N/A", reason=no_snap_reason),
            },
            "volume": {
                "total_signals": _metric_field(value=vol["total_signals"], unit="count", source="signal_decisions.parquet" if decisions_df is not None else "run_manifest.json#total_signals", label_vi=LABELS_VI["total_signals"]),
                "accepted_entries": _metric_field(value=vol["accepted_entries"], unit="count", source="trades.parquet#trade_id", label_vi=LABELS_VI["accepted_entries"]),
                "entry_fill_rate_pct": _metric_field(value=vol["entry_fill_rate_pct"], unit="pct", source="derived:entries/signals", label_vi=LABELS_VI["entry_fill_rate_pct"]),
                "total_legs": _metric_field(value=vol["total_legs"], unit="count", source="trades.parquet#leg_id", label_vi=LABELS_VI["total_legs"]),
                "skipped_by_concurrency": _metric_field(value=vol["skipped_by_concurrency"], unit="count", source="run_manifest.json#skipped_signals_count", label_vi=LABELS_VI["skipped_by_concurrency"]),
                "rejected_by_margin": _metric_field(value=vol["rejected_by_margin"], unit="count", source="run_manifest.json#rejected_signals_count", label_vi=LABELS_VI["rejected_by_margin"]),
            },
            "mt5_results": {
                "total_net_profit": _metric_field(value=res["total_net_profit"], unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["total_net_profit"]),
                "roi_pct": _metric_field(value=res["roi_pct"], unit="pct", source="derived:net_pnl/initial_equity", label_vi=LABELS_VI["roi_pct"]),
                "gross_profit_entry": _metric_field(value=res["gross_profit_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["gross_profit_entry"]),
                "gross_loss_entry": _metric_field(value=res["gross_loss_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["gross_loss_entry"]),
                "expected_payoff_entry": _metric_field(value=res["expected_payoff_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["expected_payoff_entry"]),
                "expected_payoff_leg": _metric_field(value=res["expected_payoff_leg"], unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["expected_payoff_leg"]),
                "balance_dd_absolute_dollars": _metric_field(value=res["balance_dd_absolute_dollars"], unit="USD", source="snapshots.parquet#balance" if has_snaps else "trades.parquet#net_pnl", label_vi=LABELS_VI["balance_dd_absolute_dollars"]),
                "balance_dd_absolute_pct": _metric_field(value=res["balance_dd_absolute_pct"], unit="pct", source="snapshots.parquet#balance" if has_snaps else "trades.parquet#net_pnl", label_vi=LABELS_VI["balance_dd_absolute_pct"]),
                "balance_dd_maximal_dollars": _metric_field(value=res["balance_dd_maximal_dollars"], unit="USD", source="trades.parquet#net_pnl", label_vi=LABELS_VI["balance_dd_maximal_dollars"]),
                "balance_dd_relative_pct": _metric_field(value=res["balance_dd_relative_pct"], unit="pct", source="trades.parquet#net_pnl", label_vi=LABELS_VI["balance_dd_relative_pct"]),
                "winning_entries": _metric_field(value=res["winning_entries"], unit="count", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["winning_entries"]),
                "losing_entries": _metric_field(value=res["losing_entries"], unit="count", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["losing_entries"]),
                "entry_win_rate_pct": _metric_field(value=res["entry_win_rate_pct"], unit="pct", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["entry_win_rate_pct"]),
                "leg_win_rate_pct": _metric_field(value=res["leg_win_rate_pct"], unit="pct", source="trades.parquet#net_pnl", label_vi=LABELS_VI["leg_win_rate_pct"]),
                "largest_profit_entry": _metric_field(value=res["largest_profit_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["largest_profit_entry"]),
                "largest_loss_entry": _metric_field(value=res["largest_loss_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["largest_loss_entry"]),
                "avg_profit_entry": _metric_field(value=res["avg_profit_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["avg_profit_entry"]),
                "avg_loss_entry": _metric_field(value=res["avg_loss_entry"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["avg_loss_entry"]),
            },
            "long_short": {
                "long_entries": _metric_field(value=ls["long"]["entries"], unit="count", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["long_entries"]),
                "long_won": _metric_field(value=ls["long"]["won"], unit="count", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["long_won"]),
                "long_win_rate_pct": _metric_field(value=ls["long"]["win_rate_pct"], unit="pct", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["long_win_rate_pct"]),
                "long_net_pnl": _metric_field(value=ls["long"]["net_pnl"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["long_net_pnl"]),
                "long_pf": _metric_field(value=ls["long"]["pf"], unit="ratio", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["long_pf"]),
                "short_entries": _metric_field(value=ls["short"]["entries"], unit="count", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["short_entries"]),
                "short_won": _metric_field(value=ls["short"]["won"], unit="count", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["short_won"]),
                "short_win_rate_pct": _metric_field(value=ls["short"]["win_rate_pct"], unit="pct", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["short_win_rate_pct"]),
                "short_net_pnl": _metric_field(value=ls["short"]["net_pnl"], unit="USD", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["short_net_pnl"]),
                "short_pf": _metric_field(value=ls["short"]["pf"], unit="ratio", source=f"trades.parquet#{entry_basis}", label_vi=LABELS_VI["short_pf"]),
            },
            "streaks": {
                k: _metric_field(
                    value=stk[k],
                    unit="USD" if "dollars" in k else "count",
                    source=f"trades.parquet#{entry_basis}",
                    label_vi=LABELS_VI[k],
                )
                for k in (
                    "max_consecutive_wins_count",
                    "max_consecutive_wins_dollars",
                    "max_consecutive_losses_count",
                    "max_consecutive_losses_dollars",
                    "maximal_consecutive_profit_dollars",
                    "maximal_consecutive_profit_count",
                    "maximal_consecutive_loss_dollars",
                    "maximal_consecutive_loss_count",
                    "avg_consecutive_wins",
                    "avg_consecutive_losses",
                )
            },
            "concentration": {
                "max_open_entries_any_sym_side": _metric_field(
                    value=conc["max_open_entries_any_sym_side"],
                    unit="count",
                    source="trades.parquet#entry_time_ms,exit_time_ms",
                    label_vi=LABELS_VI["max_open_entries_any_sym_side"],
                ),
                "max_open_notional_any_sym_side": _metric_field(
                    value=conc["max_open_notional_any_sym_side"],
                    unit="USD",
                    source="trades.parquet#notional",
                    label_vi=LABELS_VI["max_open_notional_any_sym_side"],
                ),
                "max_open_notional_total": _metric_field(
                    value=conc["max_open_notional_total"],
                    unit="USD",
                    source="trades.parquet#notional",
                    label_vi=LABELS_VI["max_open_notional_total"],
                ),
                "btc_c11_own_entry_pnl": _metric_field(
                    value=conc["btc_c11_own_entry_pnl"],
                    unit="USD",
                    source=f"trades.parquet#{entry_basis}",
                    label_vi=LABELS_VI["btc_c11_own_entry_pnl"],
                ),
                "btc_c11_share_of_total_net_pct": _metric_field(
                    value=conc["btc_c11_share_of_total_net_pct"],
                    unit="pct",
                    source=f"derived:btc_c11_{entry_basis}/net_pnl",
                    label_vi=LABELS_VI["btc_c11_share_of_total_net_pct"],
                    status="AVAILABLE" if conc["btc_c11_share_of_total_net_pct"] is not None else "N/A",
                    reason=None if conc["btc_c11_share_of_total_net_pct"] is not None else "Tổng lãi ròng bằng 0",
                ),
            },
        },
        "breakdowns": {
            "cost_sensitivity": [],
            "combos": _compute_combo_rows(trades_df, decisions_df, execution_mode),
            "symbols": _compute_symbol_rows(trades_df, decisions_df),
            "monthly": _compute_monthly_rows(trades_df),
            "concentration_by_sym_side": conc["max_open_entries_by_sym_side"],
            "mode_comparison": [],
        },
        "series": _build_chart_series(trades_df, snapshots_df, initial_equity),
    }

    payload["breakdowns"]["cost_sensitivity"] = _build_cost_sensitivity_rows(
        cost_profile_name, payload, sensitivity_runs
    )
    if include_mode_comparison:
        payload["breakdowns"]["mode_comparison"] = _load_mode_comparison(
            execution_mode, payload, sibling_dirs
        )
    return payload


def render_html_report(payload: dict[str, Any]) -> str:
    """Render 100% Vietnamese mobile-first HTML report from ``report_v1.json`` payload."""
    ri = payload["run_info"]
    kpi = payload["kpi"]
    acc = kpi["account"]
    cost = kpi["cost_reality"]
    rsk = kpi["risk"]
    vol = kpi["volume"]
    res = kpi["mt5_results"]
    ls = kpi["long_short"]
    stk = kpi["streaks"]
    conc = kpi["concentration"]
    brk = payload["breakdowns"]
    series = payload["series"]

    cp_name = ri["cost_profile"]["name"]
    cp_hash = ri["cost_profile"]["hash"][:8]
    banner_cls = "banner-ideal" if cp_name == "IDEAL" else ("banner-base" if cp_name == "BASE" else "banner-stress")
    if cp_name == "BASE":
        fee_banner_label = f"{LABELS_VI['fee_mode_prefix']}: {LABELS_VI['fee_mode_base_detail']}"
    elif cp_name == "STRESS":
        fee_banner_label = f"{LABELS_VI['fee_mode_prefix']}: {LABELS_VI['fee_mode_stress_detail']}"
    elif cp_name == "IDEAL":
        fee_banner_label = f"{LABELS_VI['fee_mode_prefix']}: {LABELS_VI['fee_mode_ideal_ref']}"
    else:
        fee_banner_label = f"{LABELS_VI['fee_mode_prefix']}: {cp_name}"
    ideal_warn_html = (
        f'<div class="banner-warn">{html.escape(LABELS_VI["ideal_warning"])}</div>'
        if ri["cost_profile"]["ideal_warning_required"]
        else ""
    )

    basis_note = (
        "Cơ sở: pnl_own_entry (entry riêng, độc lập vị thế gộp)"
        if ri["entry_stats_basis"] == "pnl_own_entry"
        else "Cơ sở: net_pnl (chế độ không gộp vị thế)"
    )

    # Charts SVG
    bal_vals: list[float] = series["balance"]
    eq_vals: list[float] | None = series["equity_mtm"]
    all_eq = bal_vals + (eq_vals if eq_vals else [])
    v_min = min(all_eq) if all_eq else 0.0
    v_max = max(all_eq) if all_eq else 1.0
    bal_pts = _svg_polyline_points(bal_vals, x0=55, x1=885, y_top=16, y_bot=154, v_min=v_min, v_max=v_max)
    eq_line = ""
    if eq_vals:
        eq_pts = _svg_polyline_points(eq_vals, x0=55, x1=885, y_top=16, y_bot=154, v_min=v_min, v_max=v_max)
        eq_line = f'<polyline fill="none" stroke="#ea580c" stroke-width="1.5" stroke-dasharray="4 3" points="{eq_pts}" />'
        chart_note = (
            f'Số dư (xanh): tối thiểu {_fmt_usd(min(bal_vals))}, tối đa {_fmt_usd(max(bal_vals))}, cuối kỳ {_fmt_usd(bal_vals[-1])} | '
            f'Vốn thực M2M (cam): tối thiểu {_fmt_usd(min(eq_vals))}, tối đa {_fmt_usd(max(eq_vals))}.'
        )
    else:
        chart_note = (
            f'Số dư đã chốt (xanh): tối thiểu {_fmt_usd(min(bal_vals))}, tối đa {_fmt_usd(max(bal_vals))}, cuối kỳ {_fmt_usd(bal_vals[-1])}. '
            f'Vốn thực (M2M): {html.escape(LABELS_VI["na_no_snapshots"])}.'
        )

    dd_usd_vals: list[float] = series["dd_dollars"]
    dd_pct_vals: list[float] = series["dd_pct"]
    dd_u_max = max(dd_usd_vals) if dd_usd_vals else 1.0
    dd_p_max = max(dd_pct_vals) if dd_pct_vals else 1.0
    dd_u_pts = _svg_polyline_points([-v for v in dd_usd_vals], x0=15, x1=405, y_top=12, y_bot=108, v_min=-dd_u_max, v_max=0.0)
    dd_p_pts = _svg_polyline_points([-v for v in dd_pct_vals], x0=15, x1=405, y_top=12, y_bot=108, v_min=-dd_p_max, v_max=0.0)

    # Tables
    sens_rows_html = []
    for sr in brk["cost_sensitivity"]:
        if sr["net_pnl"]["status"] == "AVAILABLE":
            np_val = float(sr["net_pnl"]["value"])
            cls = "pos" if np_val >= 0 else "neg"
            sens_rows_html.append(
                f"<tr><td class='sticky-col fw-600'>{html.escape(sr['label'])}</td>"
                f"<td>{html.escape(str(sr['run_ref'] or ''))}</td>"
                f"<td class='num {cls} fw-600'>{_fmt_signed_usd(np_val)}</td>"
                f"<td class='num'>{_fmt_pct(float(sr['roi_pct']['value']))}</td>"
                f"<td class='num'>{_fmt_pf(sr['net_pf_leg']['value'])}</td>"
                f"<td class='num'>{_fmt_pct(sr['m2m_dd_pct']['value'])}</td>"
                f"<td><span class='badge badge-pass'>ĐÃ CHẠY THẬT</span></td></tr>"
            )
        else:
            na_badge = f"<span class='badge badge-na'>{html.escape(LABELS_VI['na_not_run'])}</span>"
            sens_rows_html.append(
                f"<tr><td class='sticky-col fw-600'>{html.escape(sr['label'])}</td>"
                f"<td>—</td><td class='num'>{na_badge}</td><td class='num'>{na_badge}</td>"
                f"<td class='num'>{na_badge}</td><td class='num'>{na_badge}</td>"
                f"<td>{html.escape(sr['net_pnl']['reason'] or LABELS_VI['na_not_run'])}</td></tr>"
            )

    combo_rows_html = []
    tot_sig = tot_ent = tot_legs = 0
    tot_own = tot_port = tot_fees = 0.0
    for c in brk["combos"]:
        s_v = int(c["signals"]["value"])
        e_v = int(c["entries"]["value"])
        l_v = int(c["legs"]["value"])
        o_v = float(c["own_entry_pnl"]["value"])
        p_v = float(c["portfolio_net_pnl"]["value"])
        d_v = float(c["basis_shift_delta"]["value"])
        f_v = float(c["fees"]["value"])
        tot_sig += s_v
        tot_ent += e_v
        tot_legs += l_v
        tot_own += o_v
        tot_port += p_v
        tot_fees += f_v
        combo_rows_html.append(
            f"<tr><td class='sticky-col fw-600'>{html.escape(c['combo_key'])}</td>"
            f"<td>{html.escape(c['symbol'])}</td><td>{html.escape(c['timeframe'])}</td>"
            f"<td class='num'>{s_v:,}</td><td class='num'>{e_v:,}</td>"
            f"<td class='num'>{float(c['fill_rate_pct']['value']):.1f}%</td>"
            f"<td class='num'>{l_v:,}</td><td class='num'>{float(c['leg_win_rate_pct']['value']):.2f}%</td>"
            f"<td class='num {'pos' if o_v >= 0 else 'neg'}'>{_fmt_signed_usd(o_v)}</td>"
            f"<td class='num {'pos' if p_v >= 0 else 'neg'} fw-600'>{_fmt_signed_usd(p_v)}</td>"
            f"<td class='num {'pos' if d_v >= 0 else 'neg'}'>{_fmt_signed_usd(d_v)}</td>"
            f"<td class='num'>{_fmt_usd(f_v)}</td>"
            f"<td class='num'>{_fmt_pf(c['net_pf_leg']['value'])}</td></tr>"
        )

    sym_cards_html = []
    for sym in brk["symbols"]:
        np_s = float(sym["net_pnl"]["value"])
        sym_cards_html.append(
            f"<div class='card' style='margin-bottom:0;background:#f8fafc;'>"
            f"<h2>{html.escape(sym['symbol'])}</h2>"
            f"<div class='kv'><span class='k'>Tín hiệu / Entry đã khớp / Leg</span><span class='v'>{int(sym['signals']['value']):,} / {int(sym['entries']['value']):,} / {int(sym['legs']['value']):,}</span></div>"
            f"<div class='kv'><span class='k'>Tỷ lệ thắng theo leg</span><span class='v'>{float(sym['leg_win_rate_pct']['value']):.2f}%</span></div>"
            f"<div class='kv'><span class='k'>Lãi gộp trước phí / Tổng phí</span><span class='v'>{_fmt_usd(float(sym['gross_pnl']['value']))} / {_fmt_usd(float(sym['total_fees']['value']))}</span></div>"
            f"<div class='kv'><span class='k'>Tổng lãi ròng</span><span class='v {'pos' if np_s >= 0 else 'neg'}'>{_fmt_signed_usd(np_s)}</span></div>"
            f"<div class='kv'><span class='k'>PF (Entry / Leg)</span><span class='v'>{_fmt_pf(sym['net_pf_trade']['value'])} / {_fmt_pf(sym['net_pf_leg']['value'])}</span></div>"
            f"</div>"
        )

    monthly_rows_html = []
    for m in brk["monthly"]:
        ym = m["year_month"]
        is_2026 = ym.startswith("2026-")
        badge_cls = "badge-val" if is_2026 else "badge-dev"
        np_m = float(m["net_pnl"]["value"])
        monthly_rows_html.append(
            f"<tr><td class='sticky-col fw-600'>{html.escape(ym)}</td>"
            f"<td><span class='badge {badge_cls}'>{html.escape(m['governance_period'])}</span></td>"
            f"<td class='num'>{int(m['entries']['value']):,}</td>"
            f"<td class='num'>{int(m['legs']['value']):,}</td>"
            f"<td class='num'>{float(m['leg_win_rate_pct']['value']):.2f}%</td>"
            f"<td class='num'>{_fmt_signed_usd(float(m['gross_pnl']['value']))}</td>"
            f"<td class='num'>{_fmt_usd(float(m['fees']['value']))}</td>"
            f"<td class='num {'pos' if np_m >= 0 else 'neg'} fw-600'>{_fmt_signed_usd(np_m)}</td>"
            f"<td class='num'>{_fmt_pf(m['net_pf_leg']['value'])}</td></tr>"
        )

    mode_rows_html = []
    for mc in brk["mode_comparison"]:
        if mc["net_pnl"]["status"] == "AVAILABLE":
            m2m_str = (
                _fmt_pct(float(mc["m2m_dd_pct"]["value"]))
                if mc["m2m_dd_pct"]["status"] == "AVAILABLE"
                else f"<span class='badge badge-na'>{html.escape(mc['m2m_dd_pct']['reason'] or LABELS_VI['na_no_snapshots'])}</span>"
            )
            np_mc = float(mc["net_pnl"]["value"])
            mode_rows_html.append(
                f"<tr><td class='sticky-col fw-600'>{html.escape(mc['execution_mode'])}</td>"
                f"<td><span class='badge badge-pass'>{html.escape(mc['validation_status'])}</span></td>"
                f"<td class='num'>{int(mc['entries']['value']):,}</td>"
                f"<td class='num'>{int(mc['legs']['value']):,}</td>"
                f"<td class='num'>{_fmt_signed_usd(float(mc['gross_pnl']['value']))}</td>"
                f"<td class='num'>{_fmt_usd(float(mc['total_fees']['value']))}</td>"
                f"<td class='num {'pos' if np_mc >= 0 else 'neg'} fw-600'>{_fmt_signed_usd(np_mc)}</td>"
                f"<td class='num'>{_fmt_pct(float(mc['roi_pct']['value']))}</td>"
                f"<td class='num'>{_fmt_pct(float(mc['leg_win_rate_pct']['value']))}</td>"
                f"<td class='num'>{_fmt_pf(mc['net_pf_leg']['value'])}</td>"
                f"<td class='num'>{_fmt_pct(float(mc['closed_dd_pct']['value']))}</td>"
                f"<td class='num'>{m2m_str}</td></tr>"
            )
        else:
            na_m = f"<span class='badge badge-na'>{html.escape(LABELS_VI['na_not_run'])}</span>"
            mode_rows_html.append(
                f"<tr><td class='sticky-col fw-600'>{html.escape(mc['execution_mode'])}</td>"
                f"<td>{na_m}</td><td class='num'>{na_m}</td><td class='num'>{na_m}</td>"
                f"<td class='num'>{na_m}</td><td class='num'>{na_m}</td><td class='num'>{na_m}</td>"
                f"<td class='num'>{na_m}</td><td class='num'>{na_m}</td><td class='num'>{na_m}</td>"
                f"<td class='num'>{na_m}</td><td class='num'>{na_m}</td></tr>"
            )

    def _na_or(field: dict[str, Any], fmt_fn: Any) -> str:
        if field["status"] != "AVAILABLE" or field["value"] is None:
            return f"<span class='badge badge-na'>{html.escape(field.get('reason') or LABELS_VI['na_text'])}</span>"
        return str(fmt_fn(field["value"]))

    rec_closed_str = _na_or(rsk["recovery_factor_closed"], lambda v: f"{float(v):.2f}")
    rec_m2m_str = _na_or(rsk["recovery_factor_m2m"], lambda v: f"{float(v):.2f}")
    sharpe_str = _na_or(rsk["sharpe_daily_365"], lambda v: f"{float(v):.2f}")
    m2m_dd_str = (
        f"{_fmt_usd(rsk['m2m_max_dd_dollars']['value'])} ({_fmt_pct(rsk['m2m_max_dd_pct']['value'])})"
        if rsk["m2m_max_dd_dollars"]["status"] == "AVAILABLE"
        else f"<span class='badge badge-na'>{html.escape(LABELS_VI['na_no_snapshots'])}</span>"
    )

    json_for_script = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AutoTrade V3 — Báo cáo chi tiết ({html.escape(ri['run_id'])})</title>
  <style>
    :root {{ --bg:#f1f5f9; --surface:#ffffff; --border:#cbd5e1; --text:#0f172a; --muted:#475569; --pos:#15803d; --pos-bg:#dcfce7; --neg:#b91c1c; --neg-bg:#fee2e2; }}
    * {{ box-sizing:border-box; }}
    html, body {{ max-width:100%; overflow-x:hidden; }}
    body {{ margin:0; font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--text); font-size:14px; line-height:1.45; }}
    .container {{ width:100%; max-width:1320px; margin:0 auto; padding:12px; overflow-x:hidden; }}
    .banner-ideal {{ background:linear-gradient(135deg,#991b1b,#b91c1c); border:2px solid #7f1d1d; color:#fff; border-radius:10px; padding:14px 16px; margin-bottom:14px; display:flex; flex-direction:column; gap:8px; max-width:100%; }}
    .banner-base {{ background:linear-gradient(135deg,#92400e,#d97706); border:2px solid #78350f; color:#fff; border-radius:10px; padding:14px 16px; margin-bottom:14px; display:flex; flex-direction:column; gap:8px; max-width:100%; }}
    .banner-stress {{ background:linear-gradient(135deg,#1e3a8a,#1d4ed8); border:2px solid #1e3a8a; color:#fff; border-radius:10px; padding:14px 16px; margin-bottom:14px; display:flex; flex-direction:column; gap:8px; max-width:100%; }}
    .banner-top {{ display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px; }}
    .fee-pill {{ background:#fff; color:#991b1b; font-weight:800; padding:4px 10px; border-radius:6px; font-size:13px; }}
    .gov-pill {{ background:rgba(255,255,255,0.16); border:1px solid rgba(255,255,255,0.45); color:#fff; padding:4px 10px; border-radius:6px; font-size:12px; font-weight:600; }}
    .banner-warn {{ font-size:13px; font-weight:600; }}
    .banner-meta {{ font-size:12px; color:#f8fafc; display:flex; flex-wrap:wrap; gap:12px; }}
    .btn-export {{ background:#0f172a; color:#fff; border:1px solid rgba(255,255,255,0.35); border-radius:6px; padding:7px 12px; font-size:12px; font-weight:600; cursor:pointer; }}
    .badge {{ display:inline-block; padding:2px 6px; border-radius:4px; font-size:11px; font-weight:700; vertical-align:middle; }}
    .badge-na {{ background:#e2e8f0; color:#334155; border:1px solid #cbd5e1; }}
    .badge-val {{ background:#fef3c7; color:#92400e; border:1px solid #fcd34d; }}
    .badge-dev {{ background:#e0f2fe; color:#075985; border:1px solid #bae6fd; }}
    .badge-pass {{ background:var(--pos-bg); color:var(--pos); }}
    .kpi-grid {{ display:grid; grid-template-columns:1fr; gap:12px; margin-bottom:14px; }}
    .card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px; margin-bottom:14px; min-width:0; max-width:100%; overflow:hidden; }}
    .kpi-grid .card {{ margin-bottom:0; }}
    .card h2 {{ margin:0 0 10px 0; font-size:13.5px; text-transform:uppercase; letter-spacing:0.35px; color:var(--muted); display:flex; align-items:center; justify-content:space-between; gap:6px; flex-wrap:wrap; }}
    .kv {{ display:flex; justify-content:space-between; align-items:baseline; padding:4px 0; border-bottom:1px dashed #e2e8f0; gap:8px; }}
    .kv:last-child {{ border-bottom:none; }}
    .kv .k {{ color:var(--muted); font-size:12.5px; min-width:0; }}
    .kv .v {{ font-weight:700; font-variant-numeric:tabular-nums; text-align:right; overflow-wrap:anywhere; }}
    .pos {{ color:var(--pos); }}
    .neg {{ color:var(--neg); }}
    .fw-600 {{ font-weight:600; }}
    .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
    .two-col, .mt5-grid {{ display:grid; grid-template-columns:1fr; gap:12px; }}
    .table-wrap {{ width:100%; max-width:100%; overflow-x:auto; border:1px solid #e2e8f0; border-radius:8px; }}
    table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
    th, td {{ padding:7px 9px; border-bottom:1px solid #e2e8f0; text-align:left; }}
    th {{ background:#f8fafc; color:var(--muted); font-weight:700; white-space:nowrap; }}
    tfoot td {{ background:#f8fafc; font-weight:700; border-top:2px solid var(--border); }}
    .sticky-col {{ position:sticky; left:0; background:#fff; z-index:1; }}
    th.sticky-col, tfoot td.sticky-col {{ background:#f8fafc; }}
    .chart-box {{ border:1px solid #e2e8f0; border-radius:8px; padding:10px; background:#f8fafc; }}
    .chart-note {{ font-size:12px; color:var(--muted); margin-top:6px; }}
    svg {{ width:100%; height:auto; display:block; }}
    @media (min-width:640px) {{ .kpi-grid, .two-col, .mt5-grid {{ grid-template-columns:repeat(2,1fr); }} }}
    @media (min-width:1024px) {{ .container {{ padding:18px; }} .kpi-grid {{ grid-template-columns:repeat(4,1fr); }} }}
  </style>
</head>
<body>
<div class="container">
  <header class="{banner_cls}">
    <div class="banner-top">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <span class="fee-pill">{html.escape(fee_banner_label)}</span>
        <span class="gov-pill">2026: {html.escape(LABELS_VI['gov_2026_validation'])}</span>
        <span class="badge badge-pass">{html.escape(ri['validation_status'])}</span>
      </div>
      <button class="btn-export" onclick="exportAiJson()">{html.escape(LABELS_VI['export_ai_json'])}</button>
    </div>
    {ideal_warn_html}
    <div class="banner-meta">
      <span><strong>Mã chạy:</strong> {html.escape(ri['run_id'])} ({html.escape(ri['execution_mode'])})</span>
      <span><strong>Khoảng UTC:</strong> {html.escape(str(ri['time_window_utc'].get('start','')))} .. {html.escape(str(ri['time_window_utc'].get('end','')))}</span>
      <span><strong>Cặp giao dịch:</strong> {html.escape(', '.join(ri['symbols']))}</span>
      <span><strong>Xử lý trùng nến:</strong> {html.escape(ri['ambiguity_policy'])}</span>
      <span><strong>Hồ sơ chi phí:</strong> {html.escape(cp_name)} ({html.escape(cp_hash)})</span>
      <span><strong>Git:</strong> {html.escape(ri['git_commit'][:8])}</span>
      <span><strong>Dữ liệu:</strong> {html.escape(ri['dataset_hash'][:8])}</span>
    </div>
  </header>

  <section class="kpi-grid">
    <div class="card">
      <h2>{html.escape(LABELS_VI['sec_account'])}</h2>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['initial_capital'])}</span><span class="v">{_fmt_usd(acc['initial_capital']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['final_balance'])}</span><span class="v {'pos' if float(acc['final_balance']['value']) >= float(acc['initial_capital']['value']) else 'neg'}">{_fmt_usd(acc['final_balance']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['final_equity_mtm'])}</span><span class="v">{_na_or(acc['final_equity_mtm'], _fmt_usd)}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['max_used_margin'])}</span><span class="v">{_na_or(acc['max_used_margin'], _fmt_usd)}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['min_free_margin'])}</span><span class="v">{_na_or(acc['min_free_margin'], _fmt_usd)}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['min_margin_level_pct'])}</span><span class="v">{_na_or(acc['min_margin_level_pct'], _fmt_pct)}</span></div>
    </div>

    <div class="card">
      <h2>{html.escape(LABELS_VI['sec_cost_reality'])}</h2>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['gross_pnl_before_costs'])}</span><span class="v">{_fmt_signed_usd(cost['gross_pnl_before_costs']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['total_fees'])}</span><span class="v neg">-{_fmt_usd(cost['total_fees']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['fee_drag_pct'])}</span><span class="v">{_fmt_pct(cost['fee_drag_pct']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['break_even_slippage_per_side_pct'])}</span><span class="v">{float(cost['break_even_slippage_per_side_pct']['value']):.4f}% ({float(cost['break_even_slippage_per_side_bps']['value']):.2f} bps)</span></div>
      <div class="kv"><span class="k">Chênh lệch / Trượt giá / Funding</span><span class="v">{_fmt_usd(cost['spread_cost']['value'])} / {_fmt_usd(cost['slippage_cost']['value'])} / {_fmt_usd(cost['funding_cost']['value'])}</span></div>
    </div>

    <div class="card">
      <h2>{html.escape(LABELS_VI['sec_risk'])}</h2>
      <div class="kv"><span class="k">PF (Entry / Leg)</span><span class="v">{_fmt_pf(rsk['net_pf_entry']['value'])} / {_fmt_pf(rsk['net_pf_leg']['value'])}</span></div>
      <div class="kv"><span class="k">Hệ số phục hồi (Đã chốt / M2M)</span><span class="v">{rec_closed_str} / {rec_m2m_str}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['sharpe_daily_365'])}</span><span class="v">{sharpe_str}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['closed_max_dd_dollars'])}</span><span class="v neg">{_fmt_usd(rsk['closed_max_dd_dollars']['value'])} ({_fmt_pct(rsk['closed_max_dd_pct']['value'])})</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['m2m_max_dd_dollars'])}</span><span class="v neg">{m2m_dd_str}</span></div>
    </div>

    <div class="card">
      <h2>{html.escape(LABELS_VI['sec_volume'])}</h2>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['total_signals'])}</span><span class="v">{int(vol['total_signals']['value']):,}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['accepted_entries'])}</span><span class="v">{int(vol['accepted_entries']['value']):,}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['entry_fill_rate_pct'])}</span><span class="v">{_fmt_pct(vol['entry_fill_rate_pct']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['total_legs'])}</span><span class="v">{int(vol['total_legs']['value']):,}</span></div>
      <div class="kv"><span class="k">Bỏ qua đồng thời / Thiếu ký quỹ</span><span class="v">{int(vol['skipped_by_concurrency']['value']):,} / {int(vol['rejected_by_margin']['value']):,}</span></div>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_balance_equity_chart'])}</span></h2>
    <div class="chart-box">
      <svg viewBox="0 0 900 170" aria-label="Biểu đồ Số dư và Vốn thực M2M">
        <text x="6" y="22" font-size="11" fill="#475569">{_fmt_usd(v_max)}</text>
        <text x="6" y="156" font-size="11" fill="#475569">{_fmt_usd(v_min)}</text>
        <polyline fill="none" stroke="#2563eb" stroke-width="2" points="{bal_pts}" />
        {eq_line}
      </svg>
      <div class="chart-note">{chart_note}</div>
    </div>
  </section>

  <section class="two-col" style="margin-bottom:14px;">
    <div class="card" style="margin-bottom:0;">
      <h2><span>{html.escape(LABELS_VI['sec_dd_dollars_chart'])}</span></h2>
      <div class="chart-box">
        <svg viewBox="0 0 420 120" aria-label="Biểu đồ Sụt giảm ($)">
          <polyline fill="none" stroke="#b91c1c" stroke-width="2" points="{dd_u_pts}" />
        </svg>
        <div class="chart-note">Sụt giảm tối đa: -{_fmt_usd(dd_u_max)}</div>
      </div>
    </div>
    <div class="card" style="margin-bottom:0;">
      <h2><span>{html.escape(LABELS_VI['sec_dd_pct_chart'])}</span></h2>
      <div class="chart-box">
        <svg viewBox="0 0 420 120" aria-label="Biểu đồ Sụt giảm (%)">
          <polyline fill="none" stroke="#ea580c" stroke-width="2" points="{dd_p_pts}" />
        </svg>
        <div class="chart-note">Sụt giảm tối đa: -{dd_p_max:.2f}%</div>
      </div>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_mt5_results'])}</span><span class="badge badge-dev">{html.escape(basis_note)}</span></h2>
    <div class="mt5-grid">
      <div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['total_net_profit'])} (danh mục)</span><span class="v {'pos' if float(res['total_net_profit']['value']) >= 0 else 'neg'}">{_fmt_signed_usd(res['total_net_profit']['value'])} ({_fmt_pct(res['roi_pct']['value'])})</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['gross_profit_entry'])}</span><span class="v pos">{_fmt_signed_usd(res['gross_profit_entry']['value'])}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['gross_loss_entry'])}</span><span class="v neg">{_fmt_signed_usd(res['gross_loss_entry']['value'])}</span></div>
        <div class="kv"><span class="k">Hệ số lợi nhuận (PF) (Entry / Leg)</span><span class="v">{_fmt_pf(rsk['net_pf_entry']['value'])} / {_fmt_pf(rsk['net_pf_leg']['value'])}</span></div>
        <div class="kv"><span class="k">Kỳ vọng mỗi lệnh (Entry / Leg)</span><span class="v">${float(res['expected_payoff_entry']['value']):.4f} / ${float(res['expected_payoff_leg']['value']):.4f}</span></div>
        <div class="kv"><span class="k">Hệ số phục hồi (Đã chốt / M2M)</span><span class="v">{rec_closed_str} / {rec_m2m_str}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['sharpe_daily_365'])}</span><span class="v">{sharpe_str}</span></div>
      </div>
      <div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['balance_dd_absolute_dollars'])}</span><span class="v">{_fmt_usd(res['balance_dd_absolute_dollars']['value'])} ({_fmt_pct(res['balance_dd_absolute_pct']['value'])})</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['balance_dd_maximal_dollars'])}</span><span class="v">{_fmt_usd(res['balance_dd_maximal_dollars']['value'])}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['balance_dd_relative_pct'])}</span><span class="v">{_fmt_pct(res['balance_dd_relative_pct']['value'])}</span></div>
        <div class="kv"><span class="k">Tín hiệu / Entry đã khớp / Chân lệnh (leg)</span><span class="v">{int(vol['total_signals']['value']):,} / {int(vol['accepted_entries']['value']):,} / {int(vol['total_legs']['value']):,}</span></div>
        <div class="kv"><span class="k">Lệnh thắng / thua (% trên tổng entry)</span><span class="v">{int(res['winning_entries']['value']):,} ({_fmt_pct(res['entry_win_rate_pct']['value'])}) / {int(res['losing_entries']['value']):,} ({100.0 - float(res['entry_win_rate_pct']['value']):.2f}%)</span></div>
        <div class="kv"><span class="k">Lệnh thắng / thua lớn nhất (entry)</span><span class="v">+${float(res['largest_profit_entry']['value']):.4f} / -${abs(float(res['largest_loss_entry']['value'])):.4f}</span></div>
        <div class="kv"><span class="k">Lệnh thắng / thua trung bình (entry)</span><span class="v">+${float(res['avg_profit_entry']['value']):.4f} / -${abs(float(res['avg_loss_entry']['value'])):.4f}</span></div>
      </div>
    </div>
  </section>

  <section class="two-col" style="margin-bottom:14px;">
    <div class="card" style="margin-bottom:0;">
      <h2><span>{html.escape(LABELS_VI['sec_long_short'])}</span><span class="badge badge-dev">{html.escape(basis_note)}</span></h2>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['long_win_rate_pct'])}</span><span class="v">{int(ls['long_entries']['value']):,} (Thắng: {int(ls['long_won']['value']):,} / {_fmt_pct(ls['long_win_rate_pct']['value'])})</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['long_net_pnl'])} / {html.escape(LABELS_VI['long_pf'])}</span><span class="v">{_fmt_signed_usd(ls['long_net_pnl']['value'])} / {_fmt_pf(ls['long_pf']['value'])}</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['short_win_rate_pct'])}</span><span class="v">{int(ls['short_entries']['value']):,} (Thắng: {int(ls['short_won']['value']):,} / {_fmt_pct(ls['short_win_rate_pct']['value'])})</span></div>
      <div class="kv"><span class="k">{html.escape(LABELS_VI['short_net_pnl'])} / {html.escape(LABELS_VI['short_pf'])}</span><span class="v">{_fmt_signed_usd(ls['short_net_pnl']['value'])} / {_fmt_pf(ls['short_pf']['value'])}</span></div>
    </div>
    <div class="card" style="margin-bottom:0;">
      <h2><span>{html.escape(LABELS_VI['sec_streaks'])}</span><span class="badge badge-dev">{html.escape(basis_note)}</span></h2>
      <div class="kv"><span class="k">Chuỗi thắng dài nhất (số lệnh / $)</span><span class="v">{int(stk['max_consecutive_wins_count']['value'])} / {_fmt_signed_usd(stk['max_consecutive_wins_dollars']['value'])}</span></div>
      <div class="kv"><span class="k">Chuỗi thua dài nhất (số lệnh / $)</span><span class="v">{int(stk['max_consecutive_losses_count']['value'])} / {_fmt_signed_usd(stk['max_consecutive_losses_dollars']['value'])}</span></div>
      <div class="kv"><span class="k">Chuỗi lãi lớn nhất ($ / số lệnh)</span><span class="v pos">{_fmt_signed_usd(stk['maximal_consecutive_profit_dollars']['value'])} / {int(stk['maximal_consecutive_profit_count']['value'])}</span></div>
      <div class="kv"><span class="k">Chuỗi lỗ lớn nhất ($ / số lệnh)</span><span class="v neg">{_fmt_signed_usd(stk['maximal_consecutive_loss_dollars']['value'])} / {int(stk['maximal_consecutive_loss_count']['value'])}</span></div>
      <div class="kv"><span class="k">Chuỗi thắng / thua trung bình (số lệnh)</span><span class="v">{float(stk['avg_consecutive_wins']['value']):.2f} / {float(stk['avg_consecutive_losses']['value']):.2f}</span></div>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_concentration'])}</span><span class="badge badge-dev">{html.escape(basis_note)}</span></h2>
    <div class="two-col">
      <div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['max_open_entries_any_sym_side'])}</span><span class="v">{int(conc['max_open_entries_any_sym_side']['value'])}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['max_open_notional_any_sym_side'])}</span><span class="v">{_fmt_usd(conc['max_open_notional_any_sym_side']['value'])}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['max_open_notional_total'])}</span><span class="v">{_fmt_usd(conc['max_open_notional_total']['value'])}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['btc_c11_own_entry_pnl'])}</span><span class="v {'pos' if float(conc['btc_c11_own_entry_pnl']['value']) >= 0 else 'neg'}">{_fmt_signed_usd(conc['btc_c11_own_entry_pnl']['value'])}</span></div>
        <div class="kv"><span class="k">{html.escape(LABELS_VI['btc_c11_share_of_total_net_pct'])}</span><span class="v">{_na_or(conc['btc_c11_share_of_total_net_pct'], _fmt_pct)}</span></div>
      </div>
      <div>
        {''.join(f'<div class="kv"><span class="k">Mở tối đa {html.escape(k)}</span><span class="v">{int(v)} entry</span></div>' for k, v in sorted(brk.get("concentration_by_sym_side", {}).items()))}
      </div>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_cost_sensitivity'])}</span></h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th class="sticky-col">Mức trượt giá</th><th>Mã chạy</th><th class="num">Tổng lãi ròng ($)</th><th class="num">ROI (%)</th><th class="num">PF (Leg)</th><th class="num">DD M2M (%)</th><th>Trạng thái</th></tr></thead>
        <tbody>{''.join(sens_rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_combos'])}</span></h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="sticky-col">Combo</th><th>Cặp</th><th>Khung</th>
            <th class="num">Tín hiệu</th><th class="num">Entry đã khớp</th><th class="num">Tỷ lệ khớp</th>
            <th class="num">Chân lệnh (leg)</th><th class="num">Thắng leg %</th>
            <th class="num">{html.escape(LABELS_VI['col_own_entry_pnl'])}</th>
            <th class="num">{html.escape(LABELS_VI['col_portfolio_net_pnl'])}</th>
            <th class="num">{html.escape(LABELS_VI['col_basis_shift'])}</th>
            <th class="num">{html.escape(LABELS_VI['col_fees'])}</th><th class="num">PF</th>
          </tr>
        </thead>
        <tbody>{''.join(combo_rows_html)}</tbody>
        <tfoot>
          <tr>
            <td class="sticky-col" colspan="3">Tổng danh mục</td>
            <td class="num">{tot_sig:,}</td><td class="num">{tot_ent:,}</td>
            <td class="num">{(tot_ent / tot_sig * 100.0 if tot_sig else 0.0):.1f}%</td>
            <td class="num">{tot_legs:,}</td><td class="num">{float(res['leg_win_rate_pct']['value']):.2f}%</td>
            <td class="num">{_fmt_signed_usd(tot_own)}</td>
            <td class="num">{_fmt_signed_usd(tot_port)}</td>
            <td class="num">{_fmt_signed_usd(tot_port - tot_own)}</td>
            <td class="num">{_fmt_usd(tot_fees)}</td>
            <td class="num">{_fmt_pf(rsk['net_pf_leg']['value'])}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_symbols'])}</span></h2>
    <div class="two-col">{''.join(sym_cards_html)}</div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_monthly'])}</span></h2>
    <div class="table-wrap" style="max-height:380px;">
      <table>
        <thead><tr><th class="sticky-col">Tháng (UTC)</th><th>Kỳ quản trị</th><th class="num">Entry đã khớp</th><th class="num">Chân lệnh (leg)</th><th class="num">Thắng leg %</th><th class="num">Lãi gộp ($)</th><th class="num">Phí ($)</th><th class="num">Lãi ròng ($)</th><th class="num">PF</th></tr></thead>
        <tbody>{''.join(monthly_rows_html)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2><span>{html.escape(LABELS_VI['sec_mode_comparison'])}</span></h2>
    <p style="margin:0;color:var(--muted);font-size:13px;">{html.escape(LABELS_VI['other_modes_out_of_scope'])}</p>
  </section>
</div>
<script>
  const REPORT_V1_DATA = {json_for_script};
  function exportAiJson() {{
    const blob = new Blob([JSON.stringify(REPORT_V1_DATA, null, 2)], {{ type: "application/json" }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = REPORT_V1_DATA.run_info.run_id + "_report_v1.json";
    a.click();
    URL.revokeObjectURL(url);
  }}
</script>
</body>
</html>
"""


def generate_mt5_report(
    run_dir: Path,
    out_dir: Path,
    *,
    sibling_dirs: dict[str, Path] | None = None,
    sensitivity_runs: dict[float, Path] | None = None,
) -> tuple[Path, Path]:
    """Generate ``report_v1.json`` and ``report_mt5.html`` from ``run_dir`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_report_data(
        run_dir,
        sibling_dirs=sibling_dirs,
        sensitivity_runs=sensitivity_runs,
    )
    json_path = out_dir / "report_v1.json"
    html_path = out_dir / "report_mt5.html"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")
    return json_path, html_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate MT5-style V3 report (JSON + Vietnamese HTML).")
    parser.add_argument("--run-dir", type=Path, required=True, help="Run directory containing trades.parquet and run_manifest.json")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for report_v1.json and report_mt5.html")
    parser.add_argument("--slip-0-dir", type=Path, default=None)
    parser.add_argument("--slip-1-dir", type=Path, default=None)
    parser.add_argument("--slip-2-dir", type=Path, default=None)
    parser.add_argument("--slip-34-dir", type=Path, default=None)
    parser.add_argument("--slip-5-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    sens_runs: dict[float, Path] = {}
    if args.slip_0_dir:
        sens_runs[0.0] = args.slip_0_dir
    if args.slip_1_dir:
        sens_runs[1.0] = args.slip_1_dir
    if args.slip_2_dir:
        sens_runs[2.0] = args.slip_2_dir
    if args.slip_34_dir:
        sens_runs[3.4] = args.slip_34_dir
    if args.slip_5_dir:
        sens_runs[5.0] = args.slip_5_dir

    j_path, h_path = generate_mt5_report(
        args.run_dir,
        args.out_dir,
        sensitivity_runs=sens_runs or None,
    )
    print(f"Generated MT5 report:\n  JSON: {j_path}\n  HTML: {h_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
