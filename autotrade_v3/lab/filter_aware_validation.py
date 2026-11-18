"""Independent Filter-Aware Validation for Lab signal-filtering experiments (LOO-19, H2a, H2b)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

EXPECTED_DATASET_HASH = "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628"

FILTER_RECON_PATHS = {
    "analysis.accounting.three_level_reconciliation_pass",
    "analysis.symbols.BTC-USDT.signals",
    "analysis.combos[3].signals",
    "analysis.reconciliation.signals.sum_by_symbol",
    "analysis.reconciliation.signals.sum_by_combo",
    "analysis.reconciliation.signals.max_absolute_diff",
    "analysis.reconciliation.reconciliation_pass",
}


@dataclass
class FilterAwareGateResult:
    gate_id: str
    name: str
    status: str  # "PASS" | "FAIL" | "INCONCLUSIVE"
    checks_passed: int
    checks_total: int
    violation_count: int
    violations: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""


@dataclass
class FilterAwareValidationReport:
    run_id: str
    variant: str
    overall_status: str  # "PASS" | "FAIL"
    gates: dict[str, dict[str, Any]]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _closed_dd_dollars(trades_df: pd.DataFrame) -> float:
    if trades_df.empty:
        return 0.0
    ordered = trades_df.sort_values(["exit_time_ms", "entry_time_ms", "signal_id"])
    cum = ordered["net_pnl"].astype(float).cumsum().to_numpy()
    peak = np.maximum.accumulate(np.r_[0.0, cum])[1:]
    dd = np.maximum(0.0, peak - cum)
    return float(np.max(dd)) if len(dd) else 0.0


def _check_d2_filter_violation(
    viol: dict[str, Any],
    *,
    n_orig: int,
    n_dropped: int,
    target_symbol: str,
) -> tuple[str, dict[str, Any] | None]:
    """Classify a single standard-D2 violation with strict magnitude & reason checks.

    Returns ``("filter_recon", None)`` or ``("m2m_1e4", None)`` when the violation
    strictly matches expected filter/rounding behavior, or ``("unexplained", detail)``.
    """
    p = str(viol.get("path", ""))
    line = str(viol.get("line", ""))
    exp = viol.get("expected")
    act = viol.get("actual")

    if p == "analysis.risk.m2m_max_drawdown_dollars":
        try:
            if abs(float(exp) - float(act)) <= 2e-4:
                return "m2m_1e4", None
        except (TypeError, ValueError):
            pass
        return "unexplained", {"kind": "M2M_DD_MISMATCH_GT_TOL", "violation": viol}

    # Any filter-reconciliation exemption requires n_dropped > 0
    if n_dropped <= 0:
        return "unexplained", {"kind": "FILTER_VIOLATION_WITH_ZERO_DROPPED", "violation": viol}

    if p in (
        "analysis.accounting.three_level_reconciliation_pass",
        "analysis.reconciliation.reconciliation_pass",
    ) or line == "reconciliation_parity":
        if exp is True and act is False:
            return "filter_recon", None
        return "unexplained", {"kind": "ABNORMAL_RECON_PASS_FLAG", "violation": viol}

    if p == "analysis.reconciliation.signals.max_absolute_diff":
        if exp == 0 and act == n_dropped:
            return "filter_recon", None
        return "unexplained", {"kind": "D2_FILTER_MAX_DIFF_MISMATCH", "expected_dropped": n_dropped, "violation": viol}

    if p in (
        "analysis.reconciliation.signals.sum_by_symbol",
        "analysis.reconciliation.signals.sum_by_combo",
    ):
        if exp == n_orig and act == (n_orig - n_dropped):
            return "filter_recon", None
        return "unexplained", {"kind": "D2_FILTER_SUM_MISMATCH", "expected_orig": n_orig, "expected_kept": n_orig - n_dropped, "violation": viol}

    if p == f"analysis.symbols.{target_symbol}.signals" or (
        p.startswith("analysis.combos[") and p.endswith("].signals")
    ):
        try:
            if (int(exp) - int(act)) == n_dropped and int(act) >= 0:
                return "filter_recon", None
        except (TypeError, ValueError):
            pass
        return "unexplained", {"kind": "D2_FILTER_TARGET_SIGNAL_MISMATCH", "expected_delta": n_dropped, "violation": viol}

    return "unexplained", {"kind": "UNEXPLAINED_D2_VIOLATION", "violation": viol}


def validate_filtered_run(
    run_dir: str | Path,
    variant: str,
    baseline_signals_df: pd.DataFrame,
    *,
    target_symbol: str = "BTC-USDT",
    target_combo_id: int = 104,
    expected_drop_fn: Callable[[pd.Series], tuple[bool, str]] | None = None,
    expected_dataset_hash: str = EXPECTED_DATASET_HASH,
    tol: float = 1e-4,
) -> FilterAwareValidationReport:
    """Validate a persisted Lab run with optional signal filtering."""
    rdir = Path(run_dir)
    manifest = json.loads((rdir / "run_manifest.json").read_text(encoding="utf-8"))
    analysis = json.loads((rdir / "analysis.json").read_text(encoding="utf-8"))
    val_json = json.loads((rdir / "validation.json").read_text(encoding="utf-8"))
    signals_df = pd.read_parquet(rdir / "signals.parquet")
    decisions_df = pd.read_parquet(rdir / "signal_decisions.parquet")
    trades_df = pd.read_parquet(rdir / "trades.parquet")

    run_id = str(manifest.get("run_id", rdir.name))
    gates: dict[str, FilterAwareGateResult] = {}

    # --- FA-1: Signal Conservation & Full 3-Way signal_id Alignment ---
    fa1_viols: list[dict[str, Any]] = []
    base_ids = baseline_signals_df["signal_id"].astype(str).tolist()
    run_ids = signals_df["signal_id"].astype(str).tolist() if not signals_df.empty else []
    dec_ids = decisions_df["signal_id"].astype(str).tolist() if not decisions_df.empty else []

    if len(base_ids) != len(set(base_ids)):
        fa1_viols.append({"kind": "DUPLICATE_BASELINE_SIGNAL"})
    if len(run_ids) != len(set(run_ids)):
        fa1_viols.append({"kind": "DUPLICATE_RUN_SIGNAL", "count": len(run_ids) - len(set(run_ids))})
    if len(dec_ids) != len(set(dec_ids)):
        fa1_viols.append({"kind": "DUPLICATE_DECISION_SIGNAL", "count": len(dec_ids) - len(set(dec_ids))})

    base_set = set(base_ids)
    run_set = set(run_ids)
    dec_set = set(dec_ids)

    missing_in_run = sorted(base_set - run_set)
    extra_in_run = sorted(run_set - base_set)
    if missing_in_run:
        fa1_viols.append({"kind": "MISSING_SIGNAL_IN_RUN_SIGNALS", "count": len(missing_in_run), "sample": missing_in_run[:3]})
    if extra_in_run:
        fa1_viols.append({"kind": "SPURIOUS_SIGNAL_IN_RUN_SIGNALS", "count": len(extra_in_run), "sample": extra_in_run[:3]})

    missing_in_dec = sorted(base_set - dec_set)
    extra_in_dec = sorted(dec_set - base_set)
    if missing_in_dec:
        fa1_viols.append({"kind": "MISSING_SIGNAL_IN_DECISIONS", "count": len(missing_in_dec), "sample": missing_in_dec[:3]})
    if extra_in_dec:
        fa1_viols.append({"kind": "SPURIOUS_SIGNAL_IN_DECISIONS", "count": len(extra_in_dec), "sample": extra_in_dec[:3]})

    n_orig = len(base_set)
    n_dropped = int((decisions_df["status"] == "FILTERED").sum()) if not decisions_df.empty else 0
    n_kept = int((decisions_df["status"] != "FILTERED").sum()) if not decisions_df.empty else 0
    if n_orig != n_kept + n_dropped:
        fa1_viols.append({"kind": "CONSERVATION_MISMATCH", "orig": n_orig, "kept": n_kept, "dropped": n_dropped})

    gates["FA1"] = FilterAwareGateResult(
        gate_id="FA1",
        name="Signal Conservation & 3-Way Alignment (Original = Kept + Dropped)",
        status="PASS" if not fa1_viols else "FAIL",
        checks_passed=8 - min(8, len(fa1_viols)),
        checks_total=8,
        violation_count=len(fa1_viols),
        violations=fa1_viols,
        notes=f"orig={n_orig}, kept={n_kept}, dropped={n_dropped}",
    )

    # --- FA-2: Non-Target Combo Invariance & Filter Decision Fidelity ---
    fa2_viols: list[dict[str, Any]] = []
    if not decisions_df.empty:
        is_target = (decisions_df["symbol"] == target_symbol) & (decisions_df["combo_id"] == target_combo_id)
        non_target_filtered = decisions_df[~is_target & (decisions_df["status"] == "FILTERED")]
        if not non_target_filtered.empty:
            fa2_viols.append(
                {
                    "kind": "NON_TARGET_COMBO_FILTERED",
                    "count": int(len(non_target_filtered)),
                    "sample": non_target_filtered["signal_id"].head(3).tolist(),
                }
            )
        target_filtered = decisions_df[is_target & (decisions_df["status"] == "FILTERED")]
        empty_reasons = target_filtered[target_filtered["reason"].fillna("").str.strip() == ""]
        if not empty_reasons.empty:
            fa2_viols.append({"kind": "FILTERED_WITHOUT_REASON", "count": int(len(empty_reasons))})

        if expected_drop_fn is not None:
            target_decs = decisions_df[is_target]
            for _, row in target_decs.iterrows():
                should_drop, exp_reason = expected_drop_fn(row)
                was_dropped = row["status"] == "FILTERED"
                if should_drop != was_dropped:
                    fa2_viols.append(
                        {
                            "kind": "FILTER_ORACLE_MISMATCH",
                            "signal_id": row["signal_id"],
                            "expected_drop": should_drop,
                            "actual_status": row["status"],
                        }
                    )
                    if len(fa2_viols) >= 20:
                        break
                elif was_dropped and exp_reason and str(row["reason"]) != exp_reason:
                    fa2_viols.append(
                        {
                            "kind": "FILTER_REASON_MISMATCH",
                            "signal_id": row["signal_id"],
                            "expected_reason": exp_reason,
                            "actual_reason": row["reason"],
                        }
                    )
                    if len(fa2_viols) >= 20:
                        break

    gates["FA2"] = FilterAwareGateResult(
        gate_id="FA2",
        name="Non-Target Invariance & Filter Decision Fidelity",
        status="PASS" if not fa2_viols else "FAIL",
        checks_passed=3 - min(3, len(fa2_viols)),
        checks_total=3,
        violation_count=len(fa2_viols),
        violations=fa2_viols,
    )

    # --- FA-3: Order Chain Integrity (Signal -> Decision -> Admission -> Trade -> Exit) ---
    fa3_viols: list[dict[str, Any]] = []
    if not decisions_df.empty:
        accepted_ids = set(decisions_df.loc[decisions_df["status"] == "ACCEPTED", "signal_id"].astype(str))
        non_accepted_ids = set(decisions_df.loc[decisions_df["status"] != "ACCEPTED", "signal_id"].astype(str))
    else:
        accepted_ids = set()
        non_accepted_ids = set()

    trade_sig_ids = trades_df["signal_id"].astype(str).tolist() if not trades_df.empty else []
    trade_sig_set = set(trade_sig_ids)

    if len(trade_sig_ids) != len(trade_sig_set):
        fa3_viols.append({"kind": "DUPLICATE_TRADE_FOR_SIGNAL", "count": len(trade_sig_ids) - len(trade_sig_set)})
    if accepted_ids != trade_sig_set:
        missing_tr = sorted(accepted_ids - trade_sig_set)
        extra_tr = sorted(trade_sig_set - accepted_ids)
        if missing_tr:
            fa3_viols.append({"kind": "ACCEPTED_WITHOUT_TRADE", "count": len(missing_tr), "sample": missing_tr[:3]})
        if extra_tr:
            fa3_viols.append({"kind": "TRADE_WITHOUT_ACCEPTED_DECISION", "count": len(extra_tr), "sample": extra_tr[:3]})
    if trade_sig_set & non_accepted_ids:
        bad = sorted(trade_sig_set & non_accepted_ids)
        fa3_viols.append({"kind": "TRADE_FOR_NON_ACCEPTED_SIGNAL", "count": len(bad), "sample": bad[:3]})

    if not trades_df.empty:
        sig_close_map = dict(
            zip(
                baseline_signals_df["signal_id"].astype(str),
                baseline_signals_df["signal_close_ms"].astype("int64"),
            )
        )
        for _, tr in trades_df.iterrows():
            sid = str(tr["signal_id"])
            sc_ms = sig_close_map.get(sid)
            e_ms = int(tr["entry_time_ms"])
            x_ms = int(tr["exit_time_ms"])
            if sc_ms is not None and e_ms < sc_ms + 1:
                fa3_viols.append({"kind": "LOOKAHEAD_OR_EARLY_ENTRY", "signal_id": sid, "entry_ms": e_ms, "sig_close_ms": sc_ms})
                break
            if x_ms < e_ms:
                fa3_viols.append({"kind": "EXIT_BEFORE_ENTRY", "signal_id": sid, "entry_ms": e_ms, "exit_ms": x_ms})
                break
            if str(tr["exit_reason"]) not in ("TP", "SL", "FORCE_CLOSE", "WINDOW_END"):
                fa3_viols.append({"kind": "INVALID_EXIT_REASON", "signal_id": sid, "exit_reason": tr["exit_reason"]})
                break

    gates["FA3"] = FilterAwareGateResult(
        gate_id="FA3",
        name="Order Chain Integrity (Signal -> Admission -> Trade -> Exit)",
        status="PASS" if not fa3_viols else "FAIL",
        checks_passed=6 - min(6, len(fa3_viols)),
        checks_total=6,
        violation_count=len(fa3_viols),
        violations=fa3_viols,
    )

    # --- FA-4: Per-Trade, Per-Combo, Per-Symbol & Portfolio Accounting Conservation ---
    fa4_viols: list[dict[str, Any]] = []
    if not trades_df.empty:
        # Per-trade internal invariants
        if {"fee_entry", "fee_exit", "fee_total"}.issubset(trades_df.columns):
            fee_diff = (trades_df["fee_entry"].astype(float) + trades_df["fee_exit"].astype(float) - trades_df["fee_total"].astype(float)).abs()
            if (fee_diff > tol).any():
                fa4_viols.append({"kind": "PER_TRADE_FEE_MISMATCH", "max_diff": float(fee_diff.max())})
        if {"gross_pnl", "fee_total", "net_pnl"}.issubset(trades_df.columns):
            net_diff = ((trades_df["gross_pnl"].astype(float) - trades_df["fee_total"].astype(float)) - trades_df["net_pnl"].astype(float)).abs()
            if (net_diff > tol).any():
                fa4_viols.append({"kind": "PER_TRADE_NET_PNL_MISMATCH", "max_diff": float(net_diff.max())})

    tr_net = float(trades_df["net_pnl"].sum()) if not trades_df.empty else 0.0
    tr_fees = float(trades_df["fee_total"].sum()) if not trades_df.empty else 0.0
    tr_own = float(trades_df["pnl_own_entry"].sum()) if (not trades_df.empty and "pnl_own_entry" in trades_df.columns) else tr_net
    tr_vwap = float(trades_df["pnl_vwap"].sum()) if (not trades_df.empty and "pnl_vwap" in trades_df.columns) else tr_net
    tr_dd = _closed_dd_dollars(trades_df)

    an_net = float(analysis["pnl"]["net_pnl"])
    an_fees = float(analysis["pnl"]["total_fees"])
    an_dd = float(analysis["risk"]["closed_trade_max_drawdown_dollars"])

    if abs(tr_net - an_net) > tol:
        fa4_viols.append({"kind": "NET_PNL_MISMATCH", "trades_sum": tr_net, "analysis": an_net})
    if abs(tr_fees - an_fees) > tol:
        fa4_viols.append({"kind": "FEES_MISMATCH", "trades_sum": tr_fees, "analysis": an_fees})
    if abs(tr_own - tr_vwap) > tol:
        fa4_viols.append({"kind": "VWAP_OWN_CONSERVATION_MISMATCH", "own_sum": tr_own, "vwap_sum": tr_vwap})
    if abs(tr_dd - an_dd) > 1e-3:
        fa4_viols.append({"kind": "CLOSED_DD_MISMATCH", "recomputed": tr_dd, "analysis": an_dd})

    # Per-combo independent reconciliation (trades.parquet vs analysis["combos"])
    if not trades_df.empty and "symbol" in trades_df.columns and "combo_id" in trades_df.columns:
        tr_by_combo = {
            (str(sym), int(cid)): g
            for (sym, cid), g in trades_df.groupby(["symbol", "combo_id"], sort=False)
        }
        an_combos = analysis.get("combos", [])
        seen_keys: set[tuple[str, int]] = set()
        for c in an_combos:
            sym_c = str(c.get("symbol", ""))
            cid_c = int(c["combo_id"])
            if not sym_c and len(tr_by_combo) > 0:
                # Fallback for minimal synthetic fixtures without symbol in analysis["combos"]
                matches = [g for (_, k_cid), g in tr_by_combo.items() if k_cid == cid_c]
                g = pd.concat(matches) if matches else pd.DataFrame()
            else:
                g = tr_by_combo.get((sym_c, cid_c), pd.DataFrame())
                seen_keys.add((sym_c, cid_c))

            g_net = float(g["net_pnl"].sum()) if not g.empty else 0.0
            c_net = float(c.get("net_pnl_unrounded", c["net_pnl"]))
            c_tol = tol if "net_pnl_unrounded" in c else max(tol, 1e-4)
            if abs(g_net - c_net) > c_tol:
                fa4_viols.append({"kind": "PER_COMBO_PNL_MISMATCH", "symbol": sym_c, "combo_id": cid_c, "trades": g_net, "analysis": c_net})

            if "fees" in c or "fees_unrounded" in c:
                g_fee = float(g["fee_total"].sum()) if not g.empty else 0.0
                c_fee = float(c.get("fees_unrounded", c.get("fees", 0.0)))
                if abs(g_fee - c_fee) > c_tol:
                    fa4_viols.append({"kind": "PER_COMBO_FEE_MISMATCH", "symbol": sym_c, "combo_id": cid_c, "trades": g_fee, "analysis": c_fee})

            if ("pnl_own_entry" in c or "pnl_own_entry_unrounded" in c) and not g.empty and "pnl_own_entry" in g.columns:
                g_own = float(g["pnl_own_entry"].sum())
                c_own = float(c.get("pnl_own_entry_unrounded", c["pnl_own_entry"]))
                if abs(g_own - c_own) > c_tol:
                    fa4_viols.append({"kind": "PER_COMBO_OWN_PNL_MISMATCH", "symbol": sym_c, "combo_id": cid_c, "trades": g_own, "analysis": c_own})

        # Ensure no trade belongs to a combo missing from analysis["combos"] when symbol is present
        if an_combos and all("symbol" in c for c in an_combos):
            missing_combos = set(tr_by_combo.keys()) - seen_keys
            if missing_combos:
                fa4_viols.append({"kind": "MISSING_COMBO_IN_ANALYSIS", "combos": sorted(list(missing_combos))[:5]})

    # Per-symbol independent reconciliation (trades.parquet vs analysis["symbols"])
    if not trades_df.empty and "symbol" in trades_df.columns:
        tr_by_sym = {str(s): g for s, g in trades_df.groupby("symbol", sort=False)}
        for sym, sv in analysis.get("symbols", {}).items():
            g = tr_by_sym.get(str(sym), pd.DataFrame())
            g_net = float(g["net_pnl"].sum()) if not g.empty else 0.0
            s_net = float(sv.get("net_pnl_unrounded", sv["net_pnl"]))
            if abs(g_net - s_net) > tol:
                fa4_viols.append({"kind": "PER_SYMBOL_PNL_MISMATCH", "symbol": sym, "trades": g_net, "analysis": s_net})
            if "total_fees" in sv or "total_fees_unrounded" in sv:
                g_fee = float(g["fee_total"].sum()) if not g.empty else 0.0
                s_fee = float(sv.get("total_fees_unrounded", sv.get("total_fees", 0.0)))
                if abs(g_fee - s_fee) > tol:
                    fa4_viols.append({"kind": "PER_SYMBOL_FEE_MISMATCH", "symbol": sym, "trades": g_fee, "analysis": s_fee})

    # Portfolio sum checks (explicit 4-decimal rounding bound)
    n_syms = max(1, len(analysis.get("symbols", {})))
    n_combos = max(1, len(analysis.get("combos", [])))
    sym_tol = max(tol, n_syms * 1e-4)
    combo_tol = max(tol, n_combos * 0.5e-4)
    sym_pnl_sum = sum(float(v["net_pnl"]) for v in analysis.get("symbols", {}).values())
    combo_pnl_sum = sum(float(c["net_pnl"]) for c in analysis.get("combos", []))
    if abs(sym_pnl_sum - an_net) > sym_tol:
        fa4_viols.append({"kind": "SYMBOL_PNL_SUM_MISMATCH", "sum": sym_pnl_sum, "analysis": an_net})
    if abs(combo_pnl_sum - an_net) > combo_tol:
        fa4_viols.append({"kind": "COMBO_PNL_SUM_MISMATCH", "sum": combo_pnl_sum, "analysis": an_net})

    gates["FA4"] = FilterAwareGateResult(
        gate_id="FA4",
        name="Per-Trade, Per-Combo, Per-Symbol & VWAP Accounting Conservation",
        status="PASS" if not fa4_viols else "FAIL",
        checks_passed=10 - min(10, len(fa4_viols)),
        checks_total=10,
        violation_count=len(fa4_viols),
        violations=fa4_viols,
    )

    # --- FA-5: Dataset Hash & Strict Filter-Magnitude D2 Audit ---
    fa5_viols: list[dict[str, Any]] = []
    if str(manifest.get("dataset_hash", "")) != expected_dataset_hash:
        fa5_viols.append(
            {
                "kind": "DATASET_HASH_MISMATCH",
                "expected": expected_dataset_hash,
                "actual": manifest.get("dataset_hash"),
            }
        )

    d2_viols = val_json.get("gates", {}).get("D2", {}).get("violations", [])
    filter_recon_count = 0
    m2m_rounding_count = 0
    for v in d2_viols:
        cls, err = _check_d2_filter_violation(v, n_orig=n_orig, n_dropped=n_dropped, target_symbol=target_symbol)
        if cls == "filter_recon":
            filter_recon_count += 1
        elif cls == "m2m_1e4":
            m2m_rounding_count += 1
        elif err is not None:
            fa5_viols.append(err)

    gates["FA5"] = FilterAwareGateResult(
        gate_id="FA5",
        name="Dataset Hash & Strict Filter-Magnitude D2 Audit",
        status="PASS" if not fa5_viols else "FAIL",
        checks_passed=2 - min(2, len(fa5_viols)),
        checks_total=2,
        violation_count=len(fa5_viols),
        violations=fa5_viols,
        notes=f"d2_filter_recon={filter_recon_count}, d2_m2m_1e4={m2m_rounding_count}",
    )

    overall = "PASS" if all(g.status == "PASS" for g in gates.values()) else "FAIL"
    return FilterAwareValidationReport(
        run_id=run_id,
        variant=variant,
        overall_status=overall,
        gates={gid: asdict(g) for gid, g in gates.items()},
        summary={
            "orig_signals": n_orig,
            "kept_signals": n_kept,
            "dropped_signals": n_dropped,
            "accepted_entries": len(accepted_ids),
            "net_pnl": round(an_net, 4),
            "d2_filter_recon_viols": filter_recon_count,
            "d2_m2m_1e4_viols": m2m_rounding_count,
        },
    )
