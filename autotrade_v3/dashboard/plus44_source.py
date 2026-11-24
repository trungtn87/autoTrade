"""Read-only PLUS43 ALL_SIGNALS_PORTFOLIO adapter for the main Lab dashboard.

Reads exclusively from `research/v3_lab_11_plus31/outputs/plus43_all_signals/`
(or `AUTOTRADE_PLUS43_ALL_SIGNALS_DIR` / `AUTOTRADE_PLUS44_ALL_SIGNALS_DIR` when overridden)
and attributes per-combo PnL strictly via `pnl_own_entry`, never VWAP allocation or legacy BINGX_PORTFOLIO.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.dashboard.all_signals import RunReport, VerificationResult


DEFAULT_OUTPUT = Path("research/v3_lab_11_plus31/outputs/plus43_all_signals")
PLUS43_GROUP = "PRUNE23_PLUS_TV20"
PLUS44_GROUP = PLUS43_GROUP
PRUNE23_GROUP = "PRUNE23_ONLY"
PROFILE_ORDER = {"IDEAL": 0, "BASE": 1, "STRESS": 2}
SPLIT_ORDER = {"DEVELOPMENT": 0, "VALIDATION": 1, "HOLDOUT": 2}
YEAR_ORDER = {"Y1": 0, "Y2": 1, "Y3": 2}

REQUIRED_CSV_FILES: tuple[str, ...] = (
    "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv",
    "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv",
    "PORTFOLIO_YEARLY_ALL_SIGNALS.csv",
    "PER_COMBO_PLUS43_ALL_SIGNALS.csv",
    "DAILY_EQUITY_ALL_SIGNALS.csv",
)


def output_dir() -> Path:
    return Path(
        os.environ.get("AUTOTRADE_PLUS43_ALL_SIGNALS_DIR")
        or os.environ.get("AUTOTRADE_PLUS44_ALL_SIGNALS_DIR")
        or str(DEFAULT_OUTPUT)
    )


def _manifest(base_dir: Path | None = None) -> dict[str, Any]:
    path = (base_dir or output_dir()) / "RUN_MANIFEST.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_csv(path)


def list_runs() -> list[RunReport]:
    out = output_dir()
    manifest = _manifest(out)
    summary_path = out / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv"
    if not manifest or not summary_path.is_file():
        return []
    try:
        summary = pd.read_csv(summary_path)
    except (OSError, pd.errors.ParserError, ValueError):
        return []
    if summary.empty or "study_group" not in summary or "profile" not in summary:
        return []

    rows = summary.loc[
        summary["study_group"].astype(str).eq(PLUS44_GROUP)
        & summary["mode"].astype(str).eq("ALL_SIGNALS_PORTFOLIO")
    ].copy()
    if rows.empty:
        return []

    rows["_order"] = rows["profile"].astype(str).str.upper().map(lambda p: PROFILE_ORDER.get(p, 99))
    rows = rows.sort_values("_order")

    execution_id = str(manifest.get("execution_id") or "V3PLUS44_ALLSIG")
    finished_at = str(manifest.get("finished_at") or "")
    git_commit = str(manifest.get("git_head") or "")

    result: list[RunReport] = []
    for rec in rows.to_dict("records"):
        profile = str(rec["profile"]).upper()
        run_id = f"{execution_id}__{PLUS44_GROUP}__{profile}"
        acct_ok = _as_bool(rec.get("accounting_pass")) and int(rec.get("skipped_signals", -1)) == 0
        result.append(
            RunReport(
                run_id=run_id,
                run_dir=out,
                report_dir=None,
                generated_at_utc=finished_at,
                cost_profile=profile,
                cost_profile_hash=str(rec.get("cost_profile_hash") or ""),
                git_commit=git_commit,
                code_dirty=False,
                validation_status="VERIFIED" if acct_ok else "FAILED",
                net_profit=float(rec.get("net_pnl", 0.0)),
                max_drawdown_m2m_pct=float(rec.get("m2m_dd_pct", 0.0)),
                entry_count=int(rec.get("executed_trades", 0)),
            )
        )
    return result


def verify(run: RunReport) -> VerificationResult:
    try:
        out = output_dir()
        manifest = _manifest(out)
        if not manifest:
            return VerificationResult(False, "Thiếu hoặc lỗi RUN_MANIFEST.json của PLUS44 ALL_SIGNALS")
        if manifest.get("mode") != "ALL_SIGNALS_PORTFOLIO":
            return VerificationResult(False, f"Sai mode trong manifest: {manifest.get('mode')}")
        if manifest.get("accounting_all_pass") is not True or manifest.get("skipped_all_zero") is not True:
            return VerificationResult(False, "Manifest báo accounting hoặc skipped_all_zero không đạt")

        output_files = manifest.get("output_files") or {}
        for filename in REQUIRED_CSV_FILES:
            fpath = out / filename
            if not fpath.is_file():
                return VerificationResult(False, f"Thiếu file bắt buộc: {filename}")
            expected_sha = str((output_files.get(filename) or {}).get("sha256") or "")
            if not expected_sha or _sha256_file(fpath) != expected_sha:
                return VerificationResult(False, f"HASH_MISMATCH: {filename}")

        profile = run.cost_profile.upper()
        summary_df = _read_csv(out / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv")
        match = summary_df.loc[
            summary_df["study_group"].astype(str).eq(PLUS44_GROUP)
            & summary_df["profile"].astype(str).str.upper().eq(profile)
        ]
        if len(match) != 1:
            return VerificationResult(False, f"Không tìm thấy dòng {PLUS44_GROUP}/{profile} trong PORTFOLIO_SUMMARY")
        row = match.iloc[0]
        net_pnl = float(row["net_pnl"])
        executed_trades = int(row["executed_trades"])

        per_combo = _read_csv(out / "PER_COMBO_PLUS43_ALL_SIGNALS.csv")
        pc_sub = per_combo.loc[per_combo["profile"].astype(str).str.upper().eq(profile)]
        splits = _read_csv(out / "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv")
        sp_sub = splits.loc[
            splits["study_group"].astype(str).eq(PLUS44_GROUP)
            & splits["profile"].astype(str).str.upper().eq(profile)
        ]
        yearly = _read_csv(out / "PORTFOLIO_YEARLY_ALL_SIGNALS.csv")
        yr_sub = yearly.loc[
            yearly["study_group"].astype(str).eq(PLUS44_GROUP)
            & yearly["profile"].astype(str).str.upper().eq(profile)
        ]
        eq_df = _read_csv(out / "DAILY_EQUITY_ALL_SIGNALS.csv")
        eq_sub = eq_df.loc[
            eq_df["study_group"].astype(str).eq(PLUS44_GROUP)
            & eq_df["profile"].astype(str).str.upper().eq(profile)
        ]

        checks = (
            _as_bool(row["accounting_pass"]),
            int(row["skipped_signals"]) == 0,
            executed_trades == (run.entry_count or -1),
            abs(net_pnl - float(run.net_profit or 0.0)) <= 1e-6,
            len(pc_sub) == 43,
            pc_sub["display_id"].nunique() == 43,
            int(pc_sub["executed_trades"].sum()) == executed_trades,
            abs(float(pc_sub["net_pnl_own_entry"].sum()) - net_pnl) <= 1e-6,
            len(sp_sub) == 3,
            set(sp_sub["split"].astype(str)) == {"DEVELOPMENT", "VALIDATION", "HOLDOUT"},
            int(sp_sub["executed_trades"].sum()) == executed_trades,
            abs(float(sp_sub["net_pnl"].sum()) - net_pnl) <= 1e-6,
            len(yr_sub) == 3,
            set(yr_sub["period"].astype(str)) == {"Y1", "Y2", "Y3"},
            int(yr_sub["executed_trades"].sum()) == executed_trades,
            abs(float(yr_sub["net_pnl"].sum()) - net_pnl) <= 1e-6,
            not eq_sub.empty,
        )
        ok = all(checks)
        detail = (
            "manifest SHA-256 ↔ summary ↔ splits ↔ yearly ↔ per-combo own_entry (43)"
            if ok
            else "PLUS43 ALL_SIGNALS_PORTFOLIO verification mismatch"
        )
        return VerificationResult(ok, detail)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, pd.errors.ParserError) as exc:
        return VerificationResult(False, str(exc))


def load_payload(run: RunReport) -> dict[str, Any] | None:
    if not verify(run).passed:
        return None
    out = output_dir()
    manifest = _manifest(out)
    profile = run.cost_profile.upper()

    summary_df = _read_csv(out / "PORTFOLIO_SUMMARY_ALL_SIGNALS.csv")
    plus44_all = summary_df.loc[summary_df["study_group"].astype(str).eq(PLUS44_GROUP)].copy()
    row = plus44_all.loc[plus44_all["profile"].astype(str).str.upper().eq(profile)].iloc[0]

    prune23_match = summary_df.loc[
        summary_df["study_group"].astype(str).eq(PRUNE23_GROUP)
        & summary_df["profile"].astype(str).str.upper().eq(profile)
    ]
    prune23_row = prune23_match.iloc[0] if not prune23_match.empty else None

    per_combo = _read_csv(out / "PER_COMBO_PLUS43_ALL_SIGNALS.csv")
    pc_sub = per_combo.loc[per_combo["profile"].astype(str).str.upper().eq(profile)].sort_values("display_id")

    splits_df = _read_csv(out / "PORTFOLIO_SPLITS_DEV_VAL_HOLDOUT.csv")
    sp_sub = splits_df.loc[
        splits_df["study_group"].astype(str).eq(PLUS44_GROUP)
        & splits_df["profile"].astype(str).str.upper().eq(profile)
    ].copy()
    sp_sub["_order"] = sp_sub["split"].astype(str).map(lambda s: SPLIT_ORDER.get(s, 99))
    sp_sub = sp_sub.sort_values("_order")

    yearly_df = _read_csv(out / "PORTFOLIO_YEARLY_ALL_SIGNALS.csv")
    yr_sub = yearly_df.loc[
        yearly_df["study_group"].astype(str).eq(PLUS44_GROUP)
        & yearly_df["profile"].astype(str).str.upper().eq(profile)
    ].copy()
    yr_sub["_order"] = yr_sub["period"].astype(str).map(lambda p: YEAR_ORDER.get(p, 99))
    yr_sub = yr_sub.sort_values("_order")

    eq_df = _read_csv(out / "DAILY_EQUITY_ALL_SIGNALS.csv")
    eq_sub = eq_df.loc[
        eq_df["study_group"].astype(str).eq(PLUS44_GROUP)
        & eq_df["profile"].astype(str).str.upper().eq(profile)
    ].sort_values("timestamp_ms")

    rec_df = _read_csv(out / "RECONCILIATION_VS_BINGX_AND_V318.csv")
    bingx_plus44 = (
        rec_df.loc[rec_df["comparison"].astype(str).eq(f"PLUS44_{profile}_VS_BINGX_PORTFOLIO")]
        if not rec_df.empty and "comparison" in rec_df
        else pd.DataFrame()
    )
    bingx_prune23 = (
        rec_df.loc[rec_df["comparison"].astype(str).eq(f"PRUNE23_{profile}_VS_BINGX_PORTFOLIO")]
        if not rec_df.empty and "comparison" in rec_df
        else pd.DataFrame()
    )

    initial = float(manifest.get("initial_equity", 1000.0))
    net_pnl = float(row["net_pnl"])
    combos = [
        {
            "combo_key": str(c["display_id"]),
            "source_group": str(c["source_group"]),
            "symbol": str(c["symbol"]),
            "timeframe": str(c["timeframe"]),
            "own_entry_pnl": {"value": float(c["net_pnl_own_entry"])},
            "fees": {"value": float(c["fees"])},
            "funding": {"value": float(c["funding"])},
            "slippage": {"value": float(c["slippage"])},
            "entries": {"value": int(c["executed_trades"])},
            "leg_win_rate_pct": {"value": float(c["win_rate_pct_own"])},
            "net_pf_leg": {"value": float(c["profit_factor_own"])},
            "dev_net_own": {"value": float(c["dev_net_own"])},
            "val_net_own": {"value": float(c["val_net_own"])},
            "holdout_net_own": {"value": float(c["holdout_net_own"])},
            "y1_net_own": {"value": float(c["y1_net_own"])},
            "y2_net_own": {"value": float(c["y2_net_own"])},
            "y3_net_own": {"value": float(c["y3_net_own"])},
            "execution_basis": "OWN_ENTRY_ATTRIBUTION",
        }
        for c in pc_sub.to_dict("records")
    ]

    splits = [
        {
            "split": str(s["split"]),
            "start": str(s["start"]),
            "end": str(s["end"]),
            "executed_trades": int(s["executed_trades"]),
            "net_pnl": float(s["net_pnl"]),
            "roi_pct": float(s["roi_pct"]),
            "m2m_dd": float(s["m2m_dd"]),
            "max_dd_closed": float(s["max_dd_closed"]),
            "peak_concurrent_legs": int(s["peak_concurrent_legs"]),
            "win_rate_pct": float(s["win_rate_pct"]),
            "profit_factor": float(s["profit_factor"]),
        }
        for s in sp_sub.to_dict("records")
    ]

    yearly = [
        {
            "period": str(y["period"]),
            "start": str(y["start"]),
            "end_exclusive": str(y["end_exclusive"]),
            "executed_trades": int(y["executed_trades"]),
            "net_pnl": float(y["net_pnl"]),
            "roi_pct": float(y["roi_pct"]),
            "m2m_dd": float(y["m2m_dd"]),
            "max_dd_closed": float(y["max_dd_closed"]),
            "peak_concurrent_legs": int(y["peak_concurrent_legs"]),
        }
        for y in yr_sub.to_dict("records")
    ]

    slippage_bps_map = {"IDEAL": 0.0, "BASE": 2.0, "STRESS": 5.0}
    cost_sensitivity = [
        {
            "profile": str(r["profile"]).upper(),
            "slippage_bps": slippage_bps_map[str(r["profile"]).upper()],
            "net_pnl": {"value": float(r["net_pnl"])},
        }
        for r in plus44_all.to_dict("records")
        if str(r["profile"]).upper() in slippage_bps_map
    ]

    summary_metrics = {
        "study_group": PLUS44_GROUP,
        "profile": profile,
        "mode": "ALL_SIGNALS_PORTFOLIO",
        "executed_trades": int(row["executed_trades"]),
        "skipped_signals": int(row["skipped_signals"]),
        "rejected_signals": int(row["rejected_signals"]),
        "net_pnl": net_pnl,
        "roi_pct": float(row["roi_pct"]),
        "gross_pnl": float(row["gross_pnl"]),
        "fees": float(row["fees"]),
        "funding": float(row["funding"]),
        "slippage": float(row["slippage"]),
        "m2m_dd": float(row["m2m_dd"]),
        "m2m_dd_pct": float(row["m2m_dd_pct"]),
        "max_dd_closed": float(row["max_dd_closed"]),
        "peak_concurrent_legs": int(row["peak_concurrent_legs"]),
        "win_rate_pct": float(row["win_rate_pct"]),
        "profit_factor": float(row["profit_factor"]),
        "p1_net": float(row["p1_net"]),
        "p2_net": float(row["p2_net"]),
        "p3_net": float(row["p3_net"]),
        "accounting_pass": _as_bool(row["accounting_pass"]),
    }

    combo_own_sum = float(pc_sub["net_pnl_own_entry"].sum())
    splits_sum = float(sp_sub["net_pnl"].sum())
    yearly_sum = float(yr_sub["net_pnl"].sum())
    reconciliation = {
        "profile": profile,
        "plus44_summary_net": net_pnl,
        "plus44_combo_own_entry_sum": combo_own_sum,
        "plus44_splits_sum": splits_sum,
        "plus44_yearly_sum": yearly_sum,
        "plus44_delta_vs_summary": combo_own_sum - net_pnl,
        "prune23_summary_net": float(prune23_row["net_pnl"]) if prune23_row is not None else None,
        "prune23_trades": int(prune23_row["executed_trades"]) if prune23_row is not None else None,
        "bingx_plus44_net": float(bingx_plus44.iloc[0]["ref_net_pnl"]) if not bingx_plus44.empty else None,
        "bingx_prune23_net": float(bingx_prune23.iloc[0]["ref_net_pnl"]) if not bingx_prune23.empty else None,
    }

    window = manifest.get("window") or {"start": "2023-11-01", "end_exclusive": "2026-11-01"}
    return {
        "generated_at_utc": str(manifest.get("finished_at") or ""),
        "run_info": {
            "run_id": run.run_id,
            "time_window_utc": {"start": str(window["start"]), "end": "2026-10-31"},
            "portfolio": PLUS44_GROUP,
            "mode": "ALL_SIGNALS_PORTFOLIO",
            "cost_profile": profile,
            "attribution": "OWN_ENTRY_ONLY",
        },
        "kpi": {
            "account": {
                "initial_capital": {"value": initial},
                "final_equity_mtm": {"value": initial + net_pnl},
            },
            "risk": {
                "m2m_max_dd_dollars": {"value": float(row["m2m_dd"])},
                "m2m_max_dd_pct": {"value": float(row["m2m_dd_pct"])},
                "closed_max_dd_dollars": {"value": float(row["max_dd_closed"])},
            },
        },
        "series": {
            "equity_mtm": [float(v) for v in eq_sub["equity"].tolist()],
            "timestamps_ms": [int(v) for v in eq_sub["timestamp_ms"].tolist()],
        },
        "breakdowns": {
            "combos": combos,
            "cost_sensitivity": cost_sensitivity,
            "splits": splits,
            "yearly": yearly,
            "reconciliation": reconciliation,
        },
        "metrics": summary_metrics,
    }


def combo_sparklines(run_id: str) -> dict[str, list[float]]:
    profile = run_id.rsplit("__", 1)[-1].upper() if "__" in run_id else "BASE"
    per_combo = _read_csv(output_dir() / "PER_COMBO_PLUS43_ALL_SIGNALS.csv")
    if per_combo.empty:
        return {}
    sub = per_combo.loc[per_combo["profile"].astype(str).str.upper().eq(profile)]
    result: dict[str, list[float]] = {}
    for row in sub.itertuples(index=False):
        y1 = float(row.y1_net_own)
        y2 = float(row.y2_net_own)
        y3 = float(row.y3_net_own)
        result[str(row.display_id)] = [0.0, round(y1, 4), round(y1 + y2, 4), round(y1 + y2 + y3, 4)]
    return result


def payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
