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
    """Validate a persisted Lab run with optional signal filtering.

    Never compares filtered signal counts to unfiltered baseline counts as an error;
    instead verifies exact conservation: Original Signals == Kept + Dropped,
    non-target combo invariance, full order-chain integrity, Own/VWAP accounting
    conservation, and classifies any standard D2 violations.
    """
    rdir = Path(run_dir)
    manifest = json.loads((rdir / "run_manifest.json").read_text(encoding="utf-8"))
    analysis = json.loads((rdir / "analysis.json").read_text(encoding="utf-8"))
    val_json = json.loads((rdir / "validation.json").read_text(encoding="utf-8"))
    signals_df = pd.read_parquet(rdir / "signals.parquet")
    decisions_df = pd.read_parquet(rdir / "signal_decisions.parquet")
    trades_df = pd.read_parquet(rdir / "trades.parquet")

    run_id = str(manifest.get("run_id", rdir.name))
    gates: dict[str, FilterAwareGateResult] = {}

    # --- FA-1: Signal Conservation & No Spurious/Duplicate Signals ---
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
    dec_set = set(dec_ids)
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
        name="Signal Conservation (Original = Kept + Dropped)",
        status="PASS" if not fa1_viols else "FAIL",
        checks_passed=5 - min(5, len(fa1_viols)),
        checks_total=5,
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

    # --- FA-4: Accounting, Fees, Slippage, Own vs VWAP Conservation, & Drawdown ---
    fa4_viols: list[dict[str, Any]] = []
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

    # Symbol & combo PnL sums in analysis.json (each symbol/combo net_pnl is rounded to 4 decimals)
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
        name="Accounting, Fees, Own/VWAP Conservation & Closed DD",
        status="PASS" if not fa4_viols else "FAIL",
        checks_passed=6 - min(6, len(fa4_viols)),
        checks_total=6,
        violation_count=len(fa4_viols),
        violations=fa4_viols,
    )

    # --- FA-5: Dataset Hash & Non-Filter D2 Audit ---
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
        p = v.get("path", "")
        line = v.get("line", "")
        if p in FILTER_RECON_PATHS or line == "reconciliation_parity":
            filter_recon_count += 1
        elif p == "analysis.risk.m2m_max_drawdown_dollars" and abs(float(v.get("expected", 0)) - float(v.get("actual", 0))) <= 2e-4:
            m2m_rounding_count += 1
        else:
            fa5_viols.append({"kind": "UNEXPLAINED_D2_VIOLATION", "violation": v})

    gates["FA5"] = FilterAwareGateResult(
        gate_id="FA5",
        name="Dataset Hash & Non-Filter D2 Violation Audit",
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
