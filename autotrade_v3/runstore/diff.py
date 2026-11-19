"""Per-signal run comparison (`diff.parquet` and `diff_summary.json`)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

CATEGORIES = (
    "IDENTICAL",
    "DATA",
    "SIGNAL",
    "ENTRY",
    "AMBIGUITY",
    "EXIT_LOGIC",
    "COST",
    "UNEXPLAINED",
)


class DiffInvariantError(RuntimeError):
    """Raised when sum(delta_net) != Net(B) - Net(A) beyond 1e-9."""


@dataclass
class DiffResult:
    diff_df: pd.DataFrame
    summary: dict[str, Any]
    diff_parquet_path: Path | None = None
    summary_json_path: Path | None = None


def _load_run_bundle(run_dir: Path) -> dict[str, Any]:
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    signals = pd.read_parquet(run_dir / "signals.parquet") if (run_dir / "signals.parquet").exists() else pd.DataFrame()
    legs_path = run_dir / "legs.parquet" if (run_dir / "legs.parquet").exists() else run_dir / "trades.parquet"
    legs = pd.read_parquet(legs_path) if legs_path.exists() else pd.DataFrame()

    dec_path = run_dir / "signal_decisions.parquet"
    if dec_path.exists():
        decisions = pd.read_parquet(dec_path)
    else:
        rows: list[dict[str, Any]] = []
        leg_sids = set(legs["signal_id"].astype(str)) if len(legs) and "signal_id" in legs.columns else set()
        skip_p = run_dir / "skipped_signals.parquet"
        rej_p = run_dir / "rejected_signals.parquet"
        skip_sids = set(pd.read_parquet(skip_p)["signal_id"].astype(str)) if skip_p.exists() else set()
        rej_sids = set(pd.read_parquet(rej_p)["signal_id"].astype(str)) if rej_p.exists() else set()
        for sid in (signals["signal_id"].astype(str) if len(signals) else []):
            st = "ACCEPTED" if sid in leg_sids else ("SKIPPED" if sid in skip_sids else ("REJECTED" if sid in rej_sids else "UNKNOWN"))
            rows.append({"signal_id": sid, "status": st})
        decisions = pd.DataFrame(rows)

    return {"manifest": manifest, "signals": signals, "decisions": decisions, "legs": legs}


def _float_eq(a: Any, b: Any, tol: float = 1e-11) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= tol


def _str_eq(a: Any, b: Any) -> bool:
    sa = "" if pd.isna(a) else str(a)
    sb = "" if pd.isna(b) else str(b)
    return sa == sb


def _leg_ambig(row: dict[str, Any]) -> bool:
    atype = str(row.get("ambiguity_type", "") or "").strip()
    if atype and atype.upper() != "NONE":
        return True
    return bool(row.get("intrabar_ambiguous", False)) or bool(row.get("intrabar_trailing_ambiguous", False))


def _signal_fields_differ(sa: dict[str, Any], sb: dict[str, Any]) -> bool:
    for col in ("symbol", "combo_id", "combo_name", "timeframe", "side"):
        if col in sa or col in sb:
            if not _str_eq(sa.get(col), sb.get(col)):
                return True
    for col in ("signal_close_ms",):
        if col in sa or col in sb:
            if not _float_eq(sa.get(col), sb.get(col), tol=0.0):
                return True
    for col in ("entry_price", "signal_price"):
        if col in sa and col in sb:
            if not _float_eq(sa.get(col), sb.get(col)):
                return True
    return False


def _entry_differs(la: list[dict[str, Any]], lb: list[dict[str, Any]]) -> bool:
    if not la or not lb:
        return True
    t_a = [r.get("entry_time_ms") for r in la]
    t_b = [r.get("entry_time_ms") for r in lb]
    if len(t_a) != len(t_b):
        # Different leg counts can be entry or exit; check first leg entry first
        if not _float_eq(t_a[0], t_b[0], tol=0.0):
            return True
    else:
        for xa, xb in zip(t_a, t_b):
            if not _float_eq(xa, xb, tol=0.0):
                return True
    px_col = "entry_px_raw" if ("entry_px_raw" in la[0] and "entry_px_raw" in lb[0]) else "entry_price"
    for xa, xb in zip(la, lb):
        if not _float_eq(xa.get(px_col), xb.get(px_col)):
            return True
    return False


def _exit_differs(la: list[dict[str, Any]], lb: list[dict[str, Any]]) -> bool:
    if len(la) != len(lb):
        return True
    for xa, xb in zip(la, lb):
        if not _float_eq(xa.get("exit_time_ms"), xb.get("exit_time_ms"), tol=0.0):
            return True
        if not _str_eq(xa.get("exit_reason"), xb.get("exit_reason")):
            return True
        trig_a = xa.get("exit_px_trigger", xa.get("exit_price"))
        trig_b = xb.get("exit_px_trigger", xb.get("exit_price"))
        if not _float_eq(trig_a, trig_b):
            return True
    return False


def _cost_differs(la: list[dict[str, Any]], lb: list[dict[str, Any]]) -> bool:
    if len(la) != len(lb):
        return False
    cost_cols = (
        "fee_entry",
        "fee_exit",
        "fee_total",
        "spread_cost",
        "slippage_cost",
        "funding_cost",
        "entry_px_fill",
        "exit_px_fill",
    )
    for xa, xb in zip(la, lb):
        for col in cost_cols:
            if col in xa or col in xb:
                if not _float_eq(xa.get(col, 0.0), xb.get(col, 0.0)):
                    return True
    return False


def diff_runs(
    run_a_dir: str | Path,
    run_b_dir: str | Path,
    *,
    out_dir: str | Path | None = None,
    m1_diff_checker: Callable[[str, int, int], bool] | None = None,
) -> DiffResult:
    """Compare two runs per ``signal_id`` using strict priority classification (4.5)."""
    a = _load_run_bundle(Path(run_a_dir))
    b = _load_run_bundle(Path(run_b_dir))

    ma, mb = a["manifest"], b["manifest"]
    diff_dataset = str(ma.get("dataset_hash", "")) != str(mb.get("dataset_hash", ""))
    diff_ambig_policy = str(ma.get("ambiguity_policy", "")) != str(mb.get("ambiguity_policy", ""))

    sig_a = {str(r["signal_id"]): r for r in a["signals"].to_dict("records")} if len(a["signals"]) else {}
    sig_b = {str(r["signal_id"]): r for r in b["signals"].to_dict("records")} if len(b["signals"]) else {}

    dec_a = {str(r["signal_id"]): str(r.get("status", "")) for r in a["decisions"].to_dict("records")} if len(a["decisions"]) else {}
    dec_b = {str(r["signal_id"]): str(r.get("status", "")) for r in b["decisions"].to_dict("records")} if len(b["decisions"]) else {}

    legs_a_by_sid: dict[str, list[dict[str, Any]]] = {}
    if len(a["legs"]):
        for r in a["legs"].sort_values(["signal_id", "leg_id"], kind="mergesort").to_dict("records"):
            legs_a_by_sid.setdefault(str(r["signal_id"]), []).append(r)

    legs_b_by_sid: dict[str, list[dict[str, Any]]] = {}
    if len(b["legs"]):
        for r in b["legs"].sort_values(["signal_id", "leg_id"], kind="mergesort").to_dict("records"):
            legs_b_by_sid.setdefault(str(r["signal_id"]), []).append(r)

    all_sids = sorted(set(sig_a) | set(sig_b) | set(legs_a_by_sid) | set(legs_b_by_sid))
    rows: list[dict[str, Any]] = []

    for sid in all_sids:
        sa = sig_a.get(sid)
        sb = sig_b.get(sid)
        la = legs_a_by_sid.get(sid, [])
        lb = legs_b_by_sid.get(sid, [])

        st_a = dec_a.get(sid, "ACCEPTED" if la else ("ABSENT" if sa is None else "UNKNOWN"))
        st_b = dec_b.get(sid, "ACCEPTED" if lb else ("ABSENT" if sb is None else "UNKNOWN"))

        net_a = float(sum(float(r.get("net_pnl", 0.0)) for r in la))
        net_b = float(sum(float(r.get("net_pnl", 0.0)) for r in lb))
        delta_net = net_b - net_a

        sym = str((sa or sb or (la[0] if la else (lb[0] if lb else {}))).get("symbol", ""))
        combo_name = str((sa or sb or (la[0] if la else (lb[0] if lb else {}))).get("combo_name", ""))
        sc_ms = int((sa or sb or {}).get("signal_close_ms", 0) or 0)
        max_ex_ms = max(
            [int(r.get("exit_time_ms", sc_ms) or sc_ms) for r in (la + lb)] or [sc_ms]
        )

        # Priority 1: DATA
        if diff_dataset and m1_diff_checker is not None and sym and m1_diff_checker(sym, sc_ms, max_ex_ms):
            cat = "DATA"
        # Priority 2: SIGNAL
        elif (sa is None) != (sb is None) or (sa is not None and sb is not None and _signal_fields_differ(sa, sb)):
            cat = "SIGNAL"
        # Priority 3: ENTRY
        elif st_a != st_b or (la and lb and _entry_differs(la, lb)) or (bool(la) != bool(lb)):
            cat = "ENTRY"
        # Priority 4 & 5: AMBIGUITY / EXIT_LOGIC
        elif la and lb and _exit_differs(la, lb):
            has_amb = any(_leg_ambig(r) for r in la) or any(_leg_ambig(r) for r in lb)
            if has_amb or (diff_ambig_policy and has_amb):
                cat = "AMBIGUITY"
            else:
                cat = "EXIT_LOGIC"
        # Priority 6: COST
        elif la and lb and _cost_differs(la, lb):
            cat = "COST"
        # Priority 0: IDENTICAL
        elif abs(delta_net) <= 1e-12:
            cat = "IDENTICAL"
        # Priority 9: UNEXPLAINED
        else:
            cat = "UNEXPLAINED"

        rows.append(
            {
                "signal_id": sid,
                "symbol": sym,
                "combo_name": combo_name,
                "status_a": st_a,
                "status_b": st_b,
                "net_a": net_a,
                "net_b": net_b,
                "delta_net": delta_net,
                "category": cat,
            }
        )

    diff_df = pd.DataFrame(rows)
    if diff_df.empty:
        diff_df = pd.DataFrame(
            columns=["signal_id", "symbol", "combo_name", "status_a", "status_b", "net_a", "net_b", "delta_net", "category"]
        )

    net_total_a = float(a["legs"]["net_pnl"].astype(float).sum()) if len(a["legs"]) else 0.0
    net_total_b = float(b["legs"]["net_pnl"].astype(float).sum()) if len(b["legs"]) else 0.0
    expected_delta = net_total_b - net_total_a
    actual_delta = float(diff_df["delta_net"].sum()) if len(diff_df) else 0.0
    invariant_diff = abs(actual_delta - expected_delta)

    by_category: dict[str, dict[str, Any]] = {}
    for cat in CATEGORIES:
        sub = diff_df[diff_df["category"] == cat] if len(diff_df) else diff_df
        by_category[cat] = {
            "count": int(len(sub)),
            "net_a": float(sub["net_a"].sum()) if len(sub) else 0.0,
            "net_b": float(sub["net_b"].sum()) if len(sub) else 0.0,
            "delta_net": float(sub["delta_net"].sum()) if len(sub) else 0.0,
        }

    sum_cat_delta = sum(v["delta_net"] for v in by_category.values())
    if invariant_diff > 1e-9 or abs(sum_cat_delta - expected_delta) > 1e-9:
        raise DiffInvariantError(
            f"Diff invariant violated: sum(delta_net)={actual_delta:.12f}, "
            f"sum(cat_delta)={sum_cat_delta:.12f}, Net(B)-Net(A)={expected_delta:.12f}, "
            f"diff={invariant_diff:.3e}"
        )

    summary = {
        "run_a": str(ma.get("run_id", Path(run_a_dir).name)),
        "run_b": str(mb.get("run_id", Path(run_b_dir).name)),
        "total_signals": int(len(diff_df)),
        "net_a": net_total_a,
        "net_b": net_total_b,
        "expected_delta_net": expected_delta,
        "actual_delta_net": actual_delta,
        "invariant_diff": invariant_diff,
        "unexplained_count": int(by_category["UNEXPLAINED"]["count"]),
        "by_category": by_category,
    }

    pq_out: Path | None = None
    js_out: Path | None = None
    if out_dir is not None:
        od = Path(out_dir)
        od.mkdir(parents=True, exist_ok=True)
        pq_out = od / "diff.parquet"
        js_out = od / "diff_summary.json"
        diff_df.to_parquet(pq_out, index=False)
        js_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return DiffResult(
        diff_df=diff_df,
        summary=summary,
        diff_parquet_path=pq_out,
        summary_json_path=js_out,
    )
