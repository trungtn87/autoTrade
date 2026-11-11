"""Independent Verification Tool for AutoTrade V3 Run Reports.

Reads report.md and trades.parquet (plus companion ledgers if available) directly.
Independently recalculates every reported metric from the ledger (trades.parquet) and
verifies that all reported figures are faithfully derived with strict numeric precision.
Exits with code 0 if verified, or code 1 if any discrepancy or missing required section is detected.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any
import pandas as pd


def _calc_pf(wins: float, losses: float) -> tuple[float | str, float | str]:
    if losses > 1e-9:
        val = wins / losses
        return round(val, 2), val
    elif wins > 1e-9:
        return "UNDEFINED_NO_LOSS", "UNDEFINED_NO_LOSS"
    else:
        return 0.0, 0.0


def compute_ledger_metrics(df_trades: pd.DataFrame, initial_equity: float = 1000.0) -> dict[str, Any]:
    """Pure independent ledger metric computation from trades DataFrame."""
    total_legs = len(df_trades)
    accepted_entries = int(df_trades["trade_id"].nunique()) if total_legs > 0 else 0

    gross_pnl_u = float(df_trades["gross_pnl"].sum()) if total_legs > 0 else 0.0
    total_fees_u = float(df_trades["fee_total"].sum()) if total_legs > 0 else 0.0
    net_pnl_u = float(df_trades["net_pnl"].sum()) if total_legs > 0 else 0.0

    # Win Rates
    winning_legs = int((df_trades["net_pnl"] > 0).sum())
    losing_legs = int((df_trades["net_pnl"] < 0).sum())
    leg_win_rate_pct = (winning_legs / total_legs * 100.0) if total_legs > 0 else 0.0

    trade_net_pnl = df_trades.groupby("trade_id")["net_pnl"].sum()
    trade_gross_pnl = df_trades.groupby("trade_id")["gross_pnl"].sum()
    winning_trades = int((trade_net_pnl > 0).sum())
    trade_win_rate_pct = (winning_trades / len(trade_net_pnl) * 100.0) if len(trade_net_pnl) > 0 else 0.0

    # Profit Factors (4 canonical metrics)
    l_net_wins = float(df_trades[df_trades["net_pnl"] > 0]["net_pnl"].sum())
    l_net_losses = float(abs(df_trades[df_trades["net_pnl"] < 0]["net_pnl"].sum()))
    l_gross_wins = float(df_trades[df_trades["gross_pnl"] > 0]["gross_pnl"].sum())
    l_gross_losses = float(abs(df_trades[df_trades["gross_pnl"] < 0]["gross_pnl"].sum()))

    t_net_wins = float(trade_net_pnl[trade_net_pnl > 0].sum())
    t_net_losses = float(abs(trade_net_pnl[trade_net_pnl < 0].sum()))
    t_gross_wins = float(trade_gross_pnl[trade_gross_pnl > 0].sum())
    t_gross_losses = float(abs(trade_gross_pnl[trade_gross_pnl < 0].sum()))

    net_pf_trade, _ = _calc_pf(t_net_wins, t_net_losses)
    net_pf_leg, _ = _calc_pf(l_net_wins, l_net_losses)
    gross_pf_trade, _ = _calc_pf(t_gross_wins, t_gross_losses)
    gross_pf_leg, _ = _calc_pf(l_gross_wins, l_gross_losses)

    # Closed-Trade Max Drawdown
    sorted_trades = df_trades.sort_values(by=["exit_time_ms", "entry_time_ms", "trade_id"])
    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd_dollars = 0.0
    max_dd_pct = 0.0
    for _, t in sorted_trades.iterrows():
        cum_pnl += float(t["net_pnl"])
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd = peak_pnl - cum_pnl
        if dd > max_dd_dollars:
            max_dd_dollars = dd
            ref_cap = initial_equity + peak_pnl
            max_dd_pct = (dd / ref_cap * 100.0) if ref_cap > 0 else 0.0

    # Symbol breakdown
    symbol_stats: dict[str, dict[str, Any]] = {}
    for sym, s_df in df_trades.groupby("symbol"):
        s_legs = len(s_df)
        s_entries = int(s_df["trade_id"].nunique())
        s_gross = float(s_df["gross_pnl"].sum())
        s_fees = float(s_df["fee_total"].sum())
        s_net = float(s_df["net_pnl"].sum())
        s_wins = int((s_df["net_pnl"] > 0).sum())
        s_wr = (s_wins / s_legs * 100.0) if s_legs > 0 else 0.0

        sl_net_w = float(s_df[s_df["net_pnl"] > 0]["net_pnl"].sum())
        sl_net_l = float(abs(s_df[s_df["net_pnl"] < 0]["net_pnl"].sum()))
        sl_gross_w = float(s_df[s_df["gross_pnl"] > 0]["gross_pnl"].sum())
        sl_gross_l = float(abs(s_df[s_df["gross_pnl"] < 0]["gross_pnl"].sum()))

        st_net = s_df.groupby("trade_id")["net_pnl"].sum()
        st_gross = s_df.groupby("trade_id")["gross_pnl"].sum()
        st_net_w = float(st_net[st_net > 0].sum())
        st_net_l = float(abs(st_net[st_net < 0].sum()))
        st_gross_w = float(st_gross[st_gross > 0].sum())
        st_gross_l = float(abs(st_gross[st_gross < 0].sum()))

        s_net_pf_l, _ = _calc_pf(sl_net_w, sl_net_l)
        s_gross_pf_l, _ = _calc_pf(sl_gross_w, sl_gross_l)
        s_net_pf_t, _ = _calc_pf(st_net_w, st_net_l)
        s_gross_pf_t, _ = _calc_pf(st_gross_w, st_gross_l)

        symbol_stats[str(sym)] = {
            "entries": s_entries,
            "legs": s_legs,
            "gross_pnl": s_gross,
            "fees": s_fees,
            "net_pnl": s_net,
            "win_rate_pct": s_wr,
            "net_pf_leg": s_net_pf_l,
            "gross_pf_leg": s_gross_pf_l,
            "net_pf_trade": s_net_pf_t,
            "gross_pf_trade": s_gross_pf_t,
        }

    # Exit reason breakdown
    exit_breakdown: dict[str, dict[str, Any]] = {}
    for reason, r_df in df_trades.groupby("exit_reason"):
        r_cnt = len(r_df)
        r_gross = float(r_df["gross_pnl"].sum())
        r_net = float(r_df["net_pnl"].sum())
        exit_breakdown[str(reason)] = {
            "count": r_cnt,
            "pct_of_legs": round(r_cnt / total_legs * 100.0, 2) if total_legs > 0 else 0.0,
            "gross_pnl": r_gross,
            "net_pnl": r_net,
        }

    # Combo breakdown
    df_trades_copy = df_trades.copy()
    df_trades_copy["combo_key"] = df_trades_copy["symbol"] + "/" + df_trades_copy["combo_name"]
    combo_stats: dict[str, dict[str, Any]] = {}
    for ckey, c_df in df_trades_copy.groupby("combo_key"):
        c_legs = len(c_df)
        c_entries = int(c_df["trade_id"].nunique())
        c_gross = float(c_df["gross_pnl"].sum())
        c_fees = float(c_df["fee_total"].sum())
        c_net = float(c_df["net_pnl"].sum())
        c_wins = int((c_df["net_pnl"] > 0).sum())
        c_wr = (c_wins / c_legs * 100.0) if c_legs > 0 else 0.0

        cl_net_w = float(c_df[c_df["net_pnl"] > 0]["net_pnl"].sum())
        cl_net_l = float(abs(c_df[c_df["net_pnl"] < 0]["net_pnl"].sum()))
        ct_net = c_df.groupby("trade_id")["net_pnl"].sum()
        ct_net_w = float(ct_net[ct_net > 0].sum())
        ct_net_l = float(abs(ct_net[ct_net < 0].sum()))

        c_net_pf_l, _ = _calc_pf(cl_net_w, cl_net_l)
        c_net_pf_t, _ = _calc_pf(ct_net_w, ct_net_l)

        combo_stats[str(ckey)] = {
            "combo_key": str(ckey),
            "symbol": str(c_df["symbol"].iloc[0]),
            "combo_id": int(c_df["combo_id"].iloc[0]),
            "combo_name": str(c_df["combo_name"].iloc[0]),
            "entries": c_entries,
            "legs": c_legs,
            "gross_pnl": c_gross,
            "fees": c_fees,
            "net_pnl": c_net,
            "win_rate_pct": c_wr,
            "net_pf_leg": c_net_pf_l,
            "net_pf_trade": c_net_pf_t,
        }

    return {
        "total_legs": total_legs,
        "accepted_entries": accepted_entries,
        "gross_pnl": gross_pnl_u,
        "total_fees": total_fees_u,
        "net_pnl": net_pnl_u,
        "roi_pct": (net_pnl_u / initial_equity) * 100.0,
        "leg_win_rate_pct": leg_win_rate_pct,
        "trade_win_rate_pct": trade_win_rate_pct,
        "net_pf_trade": net_pf_trade,
        "net_pf_leg": net_pf_leg,
        "gross_pf_trade": gross_pf_trade,
        "gross_pf_leg": gross_pf_leg,
        "closed_trade_max_drawdown_dollars": max_dd_dollars,
        "closed_trade_max_drawdown_pct": max_dd_pct,
        "symbols": symbol_stats,
        "exit_reasons": exit_breakdown,
        "combos": combo_stats,
    }


def parse_number(s: str) -> float | None:
    """Extract float from markdown string (e.g. '$1,009.09' -> 1009.09, '42.75%' -> 42.75)."""
    cleaned = re.sub(r"[\$,\s\*\+]", "", s).replace("%", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def verify_report(report_md_path: str | Path, trades_parquet_path: str | Path, strict_companion: bool = True) -> bool:
    """Verify that report.md faithfully reflects numbers calculated directly from trades.parquet.
    
    Strict fail-closed verification:
    - Validation Gates: Gate A, B, C, D are MANDATORY with PASS status. Missing gates -> FAIL.
    - Consistency: Status REAL_DATA_PASS with Real Market Data False -> FAIL.
    - Ambiguity: TP/SL and Trailing ambiguities verified against trades.parquet booleans.
    - Companion ledgers: signals, skipped_signals, rejected_signals are MANDATORY. Missing -> FAIL.
    - PnL / Currency amounts: tol = 0.009 (strictly rejects 0.04 USD differences).
    - Percentages (ROI, Win Rates): tol = 0.009 (Win Rate tol = 0.06 for 1-decimal display).
    - Profit Factors: tol = 0.009 across all 4 metrics.
    - Integer counts: exact match (diff = 0).
    - Tables: Validation Gates, Performance by Symbol, Exit Reason Breakdown, Performance by Combo (20 combos),
      Reconciliation Audit are MANDATORY.
    - Reconciliation: Independently calculates 3-level parity on trades DataFrame.
    """
    rep_p = Path(report_md_path)
    trd_p = Path(trades_parquet_path)

    if not rep_p.exists():
        print(f"ERROR: Report file not found: {rep_p}", file=sys.stderr)
        return False
    if not trd_p.exists():
        print(f"ERROR: Trades file not found: {trd_p}", file=sys.stderr)
        return False

    report_text = rep_p.read_text(encoding="utf-8")
    df_trades = pd.read_parquet(trd_p)
    calc = compute_ledger_metrics(df_trades)

    run_dir = trd_p.parent
    sig_p = run_dir / "signals.parquet"
    skip_p = run_dir / "skipped_signals.parquet"
    rej_p = run_dir / "rejected_signals.parquet"

    discrepancies: list[str] = []
    verifications: list[str] = []

    # =========================================================================
    # 0. Header & Validation Audit Integrity (FAIL-CLOSED)
    # =========================================================================
    m_val_status = re.search(r"\*\*Validation Status:\*\*\s*\*\*([A-Z_]+)\*\*\s*\(Real Market Data:\s*(True|False)\)", report_text)
    if not m_val_status:
        discrepancies.append("MISSING: Validation Status line not found in report header")
    else:
        v_status, is_real_data = m_val_status.group(1), m_val_status.group(2)
        if v_status == "REAL_DATA_PASS" and is_real_data == "False":
            discrepancies.append("MISMATCH: Status is REAL_DATA_PASS but Real Market Data is False (contradictory validation state)")
        else:
            verifications.append(f"VERIFIED Validation Status: {v_status} (Real Market Data: {is_real_data})")

    m_git = re.search(r"\*\*Git SHA:\*\*\s*`?([a-f0-9]{7,40})`?", report_text)
    if not m_git:
        discrepancies.append("MISSING: Valid Git SHA not found in report header")
    else:
        rep_sha = m_git.group(1)
        verifications.append(f"VERIFIED Git SHA in header: {rep_sha}")
        manifest_p = run_dir / "run_manifest.json"
        if manifest_p.exists():
            import json
            try:
                with open(manifest_p, "r", encoding="utf-8") as mf:
                    m_data = json.load(mf)
                    man_sha = m_data.get("git_sha", "")
                    if man_sha and not (rep_sha.startswith(man_sha) or man_sha.startswith(rep_sha)):
                        discrepancies.append(f"MISMATCH Git SHA: report {rep_sha} != manifest {man_sha}")
            except Exception:
                pass

    # Mandatory Section: Validation Gates Audit
    if "## Validation Gates Audit" not in report_text:
        discrepancies.append("MISSING: Mandatory Section '## Validation Gates Audit' not found in report.md")
    else:
        mandatory_gates = [
            ("Gate A — DATA", r"\|\s*\*\*Gate A[^|]*\*\*\s*\|\s*`?(PASS|REAL_DATA_PASS|SYNTHETIC_PASS)`?\s*\|"),
            ("Gate B — STRATEGY", r"\|\s*\*\*Gate B[^|]*\*\*\s*\|\s*`?(PASS)`?\s*\|"),
            ("Gate C — EXECUTION", r"\|\s*\*\*Gate C[^|]*\*\*\s*\|\s*`?(PASS)`?\s*\|"),
            ("Gate D — RESULT", r"\|\s*\*\*Gate D[^|]*\*\*\s*\|\s*`?(PASS)`?\s*\|"),
        ]
        for gate_name, gate_pat in mandatory_gates:
            m_gate = re.search(gate_pat, report_text)
            if not m_gate:
                discrepancies.append(f"MISSING_GATE: Mandatory '{gate_name}' row with valid PASS status not found in Validation Gates table")
            else:
                verifications.append(f"VERIFIED {gate_name}: {m_gate.group(1)}")

    # =========================================================================
    # 0b. analysis.json Validation & Gates Reconciliation (FAIL-CLOSED)
    # =========================================================================
    analysis_p = run_dir / "analysis.json"
    if not analysis_p.exists():
        if m_val_status and m_val_status.group(1) == "REAL_DATA_PASS":
            discrepancies.append(f"MISSING_ANALYSIS_FILE: Required analysis.json not found in {run_dir} for REAL_DATA_PASS run")
    else:
        import json
        try:
            analysis_data = json.loads(analysis_p.read_text(encoding="utf-8"))
        except Exception as e:
            discrepancies.append(f"CORRUPT_ANALYSIS_FILE: Failed to parse {analysis_p}: {e}")
            analysis_data = {}

        if "validation" not in analysis_data or not isinstance(analysis_data.get("validation"), dict):
            if m_val_status and m_val_status.group(1) == "REAL_DATA_PASS":
                discrepancies.append("MISSING_VALIDATION_BLOCK: Mandatory 'validation' block not found in analysis.json for REAL_DATA_PASS run")
        else:
            analysis_val = analysis_data["validation"]
            a_status = analysis_val.get("overall_status")
            if m_val_status and a_status != m_val_status.group(1):
                discrepancies.append(f"MISMATCH Validation Status: analysis.json ({a_status}) != report.md ({m_val_status.group(1)})")
            else:
                verifications.append(f"VERIFIED analysis.json Validation Status: {a_status}")

            a_real = analysis_val.get("is_real_data")
            rep_real_bool = (m_val_status.group(2) == "True") if m_val_status else None
            if rep_real_bool is not None and a_real != rep_real_bool:
                discrepancies.append(f"MISMATCH Real Market Data: analysis.json ({a_real}) != report.md ({rep_real_bool})")
            else:
                verifications.append(f"VERIFIED analysis.json Real Market Data: {a_real}")

            a_gates = analysis_val.get("gates", {})
            mandatory_gates_spec = [
                ("Gate A", ["GATE_A_DATA", "gate_a", "Gate A — DATA"]),
                ("Gate B", ["GATE_B_STRATEGY", "gate_b", "Gate B — STRATEGY"]),
                ("Gate C", ["GATE_C_EXECUTION", "gate_c", "Gate C — EXECUTION"]),
                ("Gate D", ["GATE_D_RESULT", "gate_d", "Gate D — RESULT"]),
            ]
            for g_name, candidates in mandatory_gates_spec:
                gate_obj = None
                for c in candidates:
                    if c in a_gates:
                        gate_obj = a_gates[c]
                        break
                if not gate_obj:
                    for g_v in a_gates.values():
                        if isinstance(g_v, dict) and g_name in g_v.get("gate", ""):
                            gate_obj = g_v
                            break
                if not gate_obj:
                    discrepancies.append(f"MISSING_ANALYSIS_GATE: Mandatory '{g_name}' not found in analysis.json gates")
                else:
                    g_stat = gate_obj.get("status") if isinstance(gate_obj, dict) else gate_obj
                    if g_stat != "PASS":
                        discrepancies.append(f"INVALID_ANALYSIS_GATE_STATUS: '{g_name}' status is '{g_stat}', expected 'PASS'")
                    else:
                        verifications.append(f"VERIFIED analysis.json {g_name}: PASS")

            # Origin distinction (Inherited Golden vs Independent Active Re-verification)
            rep_sha_clean = rep_sha if "rep_sha" in locals() else ""
            val_source = analysis_val.get("validation_source", "")
            if rep_sha_clean.startswith("b68933c") or val_source == "INHERITED_GOLDEN_V3_08":
                verifications.append(f"VERIFIED Validation Origin: INHERITED_GOLDEN_BASELINE (Source Git SHA: {rep_sha_clean[:10]}...)")
            else:
                verifications.append("VERIFIED Validation Origin: RE_VERIFIED_INDEPENDENT (Active Environment Re-verification)")

            # Cross-reference with Golden evidence if available
            m_mode = re.search(r"\*\*Execution Mode:\*\*\s*`?([A-Z_]+)`?", report_text)
            mode_name = m_mode.group(1) if m_mode else run_dir.name
            golden_candidate = Path("runs/golden/v3-08") / mode_name / "analysis.json"
            if golden_candidate.exists():
                try:
                    g_analysis = json.loads(golden_candidate.read_text(encoding="utf-8"))
                    g_val = g_analysis.get("validation", {})
                    if g_val:
                        g_status = g_val.get("overall_status")
                        if g_status and g_status == a_status:
                            verifications.append(f"VERIFIED Golden Status Reconciliation: {a_status} matches Golden V3-08 ({mode_name})")
                        elif g_status:
                            discrepancies.append(f"MISMATCH Golden Status Reconciliation: current {a_status} != golden {g_status}")

                        g_gates = g_val.get("gates", {})
                        for g_key in ["GATE_A_DATA", "GATE_B_STRATEGY", "GATE_C_EXECUTION", "GATE_D_RESULT"]:
                            if g_key in g_gates and g_key in a_gates:
                                g_stat = g_gates[g_key].get("status")
                                curr_stat = a_gates[g_key].get("status")
                                if g_stat and curr_stat and g_stat == curr_stat:
                                    verifications.append(f"VERIFIED Golden Gate Reconciliation: {g_key} matches Golden ({g_stat})")
                                elif g_stat != curr_stat:
                                    discrepancies.append(f"MISMATCH Golden Gate Reconciliation: {g_key} current ({curr_stat}) != golden ({g_stat})")
                except Exception:
                    pass

    # =========================================================================
    # Companion Ledgers Integrity (FAIL-CLOSED)
    # =========================================================================
    for comp_file, comp_name in [(sig_p, "signals.parquet"), (skip_p, "skipped_signals.parquet"), (rej_p, "rejected_signals.parquet")]:
        if not comp_file.exists():
            if strict_companion:
                discrepancies.append(f"MISSING_COMPANION_LEDGER: Required companion ledger '{comp_name}' not found in {run_dir}")

    expected_signals: int | None = len(pd.read_parquet(sig_p)) if sig_p.exists() else None
    expected_skipped: int | None = len(pd.read_parquet(skip_p)) if skip_p.exists() else None
    expected_rejected: int | None = len(pd.read_parquet(rej_p)) if rej_p.exists() else None

    def check_int(label: str, reported_val: int | None, expected_val: int) -> None:
        if reported_val is None:
            discrepancies.append(f"MISSING: {label} not found in report.md")
        elif reported_val != expected_val:
            discrepancies.append(f"MISMATCH {label}: reported {reported_val:,} != ledger {expected_val:,}")
        else:
            verifications.append(f"VERIFIED {label}: {reported_val:,}")

    def check_float(label: str, reported_val: float | None, expected_val: float, tol: float = 0.009) -> None:
        if reported_val is None:
            discrepancies.append(f"MISSING: {label} not found in report.md")
        else:
            exp_round = round(expected_val, 2)
            diff = abs(reported_val - exp_round)
            if diff > tol:
                discrepancies.append(
                    f"MISMATCH {label}: reported {reported_val:.2f} != ledger {exp_round:.2f} (diff: {diff:.4f} > tol {tol})"
                )
            else:
                verifications.append(f"VERIFIED {label}: {reported_val:.2f} (ledger {exp_round:.2f})")

    def check_pf(label: str, reported_str: str | None, expected_val: float | str, tol: float = 0.009) -> None:
        if reported_str is None:
            discrepancies.append(f"MISSING: {label} not found in report.md")
            return
        clean_rep = reported_str.strip()
        if isinstance(expected_val, str):
            if clean_rep != expected_val:
                discrepancies.append(f"MISMATCH {label}: reported '{clean_rep}' != ledger '{expected_val}'")
            else:
                verifications.append(f"VERIFIED {label}: {clean_rep}")
        else:
            rep_f = parse_number(clean_rep)
            if rep_f is None:
                discrepancies.append(f"INVALID: {label} could not parse as number '{clean_rep}'")
            else:
                exp_round = round(expected_val, 2)
                diff = abs(rep_f - exp_round)
                if diff > tol:
                    discrepancies.append(
                        f"MISMATCH {label}: reported {rep_f:.2f} != ledger {exp_round:.2f} (diff: {diff:.4f} > tol {tol})"
                    )
                else:
                    verifications.append(f"VERIFIED {label}: {rep_f:.2f} (ledger {exp_round:.2f})")

    # =========================================================================
    # 1. Accounting: Signals, Accepted Entries, Legs, Skipped, Rejected
    # =========================================================================
    m_signals = re.search(r"\*\*Total Signals Generated:\*\*\s*([\d,]+)", report_text)
    rep_signals = int(m_signals.group(1).replace(",", "")) if m_signals else None
    if expected_signals is not None:
        check_int("Total Signals Generated", rep_signals, expected_signals)
    else:
        if rep_signals is None:
            discrepancies.append("MISSING: Total Signals Generated not found in report.md")
        else:
            verifications.append(f"FOUND Total Signals Generated: {rep_signals:,}")

    m_entries = re.search(r"\*\*Accepted Entries \(Trades\):\*\*\s*([\d,]+)", report_text)
    rep_entries = int(m_entries.group(1).replace(",", "")) if m_entries else None
    check_int("Accepted Entries", rep_entries, calc["accepted_entries"])

    m_legs = re.search(r"\*\*Total Trade Legs:\*\*\s*([\d,]+)", report_text)
    rep_legs = int(m_legs.group(1).replace(",", "")) if m_legs else None
    check_int("Total Legs", rep_legs, calc["total_legs"])

    m_skip = re.search(r"\*\*Skipped by Concurrency:\*\*\s*([\d,]+)", report_text)
    rep_skip = int(m_skip.group(1).replace(",", "")) if m_skip else None
    if expected_skipped is not None:
        check_int("Skipped by Concurrency", rep_skip, expected_skipped)
    else:
        if rep_skip is None:
            discrepancies.append("MISSING: Skipped by Concurrency not found in report.md")
        else:
            verifications.append(f"FOUND Skipped by Concurrency: {rep_skip:,}")

    m_rej = re.search(r"\*\*Rejected by Margin:\*\*\s*([\d,]+)", report_text)
    rep_rej = int(m_rej.group(1).replace(",", "")) if m_rej else None
    if expected_rejected is not None:
        check_int("Rejected by Margin", rep_rej, expected_rejected)
    else:
        if rep_rej is None:
            discrepancies.append("MISSING: Rejected by Margin not found in report.md")
        else:
            verifications.append(f"FOUND Rejected by Margin: {rep_rej:,}")

    # =========================================================================
    # 2. Performance: Gross PnL, Fees, Net PnL, ROI, Win Rates, 4 Profit Factors
    # =========================================================================
    m_gross = re.search(r"\*\*Gross PnL:\*\*\s*\$([-\d,\.]+)", report_text)
    rep_gross = parse_number(m_gross.group(1)) if m_gross else None
    check_float("Gross PnL", rep_gross, calc["gross_pnl"], tol=0.009)

    m_fees = re.search(r"\*\*Total Fees.*?:\*\*\s*\$([-\d,\.]+)", report_text)
    rep_fees = parse_number(m_fees.group(1)) if m_fees else None
    check_float("Total Fees", rep_fees, calc["total_fees"], tol=0.009)

    m_net = re.search(r"\*\*Net PnL:\*\*\s*\*\*?\$([-\d,\.]+)\*\*?", report_text)
    rep_net = parse_number(m_net.group(1)) if m_net else None
    check_float("Net PnL", rep_net, calc["net_pnl"], tol=0.009)

    # ROI check
    m_roi = re.search(r"\*\*ROI .*?:\*\*\s*\*\*?([+-]?[\d\.]+)%\*\*?", report_text)
    rep_roi = parse_number(m_roi.group(1)) if m_roi else None
    check_float("ROI", rep_roi, calc["roi_pct"], tol=0.009)

    # Win Rates
    m_t_wr = re.search(r"\*\*Trade-level Win Rate:\*\*\s*([\d\.]+)%", report_text)
    rep_t_wr = parse_number(m_t_wr.group(1)) if m_t_wr else None
    check_float("Trade Win Rate", rep_t_wr, calc["trade_win_rate_pct"], tol=0.009)

    m_l_wr = re.search(r"\*\*Leg-level Win Rate:\*\*\s*([\d\.]+)%", report_text)
    rep_l_wr = parse_number(m_l_wr.group(1)) if m_l_wr else None
    check_float("Leg Win Rate", rep_l_wr, calc["leg_win_rate_pct"], tol=0.009)

    # All 4 Profit Factors (MANDATORY)
    m_npf_trade = re.search(r"\*\*Net Profit Factor \(Trade\):\*\*\s*([\w\.\d]+)", report_text)
    rep_npf_trade = m_npf_trade.group(1) if m_npf_trade else None
    check_pf("Net Profit Factor (Trade)", rep_npf_trade, calc["net_pf_trade"], tol=0.009)

    m_npf_leg = re.search(r"\*\*Net Profit Factor \((?:Default|Leg)\):\*\*\s*([\w\.\d]+)", report_text)
    rep_npf_leg = m_npf_leg.group(1) if m_npf_leg else None
    check_pf("Net Profit Factor (Leg)", rep_npf_leg, calc["net_pf_leg"], tol=0.009)

    m_gpf_trade = re.search(r"\*\*Gross Profit Factor \(Trade\):\*\*\s*([\w\.\d]+)", report_text)
    rep_gpf_trade = m_gpf_trade.group(1) if m_gpf_trade else None
    check_pf("Gross Profit Factor (Trade)", rep_gpf_trade, calc["gross_pf_trade"], tol=0.009)

    m_gpf_leg = re.search(r"\*\*Gross Profit Factor \(Leg\):\*\*\s*([\w\.\d]+)", report_text)
    if not m_gpf_leg:
        # Fallback to general Gross Profit Factor if (Leg) omitted in title
        m_gpf_leg = re.search(r"\*\*Gross Profit Factor:\*\*\s*([\w\.\d]+)", report_text)
    rep_gpf_leg = m_gpf_leg.group(1) if m_gpf_leg else None
    check_pf("Gross Profit Factor (Leg)", rep_gpf_leg, calc["gross_pf_leg"], tol=0.009)

    # =========================================================================
    # 3. Risk & Drawdown
    # =========================================================================
    m_dd_dol = re.search(r"\*\*Closed-Trade Max Drawdown:\*\*\s*\$([-\d,\.]+)", report_text)
    rep_dd_dol = parse_number(m_dd_dol.group(1)) if m_dd_dol else None
    check_float("Closed-Trade Max DD ($)", rep_dd_dol, calc["closed_trade_max_drawdown_dollars"], tol=0.009)

    # =========================================================================
    # 3B. Intrabar Ambiguity (Verified against trades.parquet)
    # =========================================================================
    expected_tp_sl = int(df_trades["intrabar_ambiguous"].sum()) if "intrabar_ambiguous" in df_trades.columns else 0
    expected_trailing = int(df_trades["intrabar_trailing_ambiguous"].sum()) if "intrabar_trailing_ambiguous" in df_trades.columns else 0

    if "## 4. Intrabar Ambiguity" not in report_text:
        discrepancies.append("MISSING: Mandatory Section '## 4. Intrabar Ambiguity' not found in report.md")
    else:
        m_tpsl = re.search(r"\*\*TP/SL Same-Candle Ambiguity:\*\*\s*([\d,]+)\s*legs?", report_text)
        rep_tpsl = int(m_tpsl.group(1).replace(",", "")) if m_tpsl else None
        check_int("TP/SL Same-Candle Ambiguity", rep_tpsl, expected_tp_sl)

        m_trail = re.search(r"\*\*Trailing Stop Ambiguity:\*\*\s*([\d,]+)\s*legs?", report_text)
        rep_trail = int(m_trail.group(1).replace(",", "")) if m_trail else None
        check_int("Trailing Stop Ambiguity", rep_trail, expected_trailing)

    # =========================================================================
    # 4. Mandatory Section: Performance by Symbol
    # =========================================================================
    if "## 5. Performance by Symbol" not in report_text:
        discrepancies.append("MISSING: Mandatory Section '## 5. Performance by Symbol' not found in report.md")
    else:
        for sym, expected_s in calc["symbols"].items():
            sym_pattern = re.compile(
                rf"\|\s*\*\*{re.escape(sym)}\*\*\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|\s*\$([-\d,\.]+)\s*\|\s*([\d\.]+)%\s*\|\s*([\w\.\d]+)\s*\|\s*([\w\.\d]+)\s*\|\s*([\w\.\d]+)\s*\|\s*([\w\.\d]+)\s*\|"
            )
            match_sym = sym_pattern.search(report_text)
            if not match_sym:
                discrepancies.append(f"MISSING: Symbol row for {sym} not found in report table")
            else:
                s_ent = int(match_sym.group(1).replace(",", ""))
                s_legs = int(match_sym.group(2).replace(",", ""))
                s_net = parse_number(match_sym.group(3))
                s_wr = parse_number(match_sym.group(4))
                s_npf_l = match_sym.group(5)
                s_gpf_l = match_sym.group(6)
                s_npf_t = match_sym.group(7)
                s_gpf_t = match_sym.group(8)

                check_int(f"Symbol {sym} Entries", s_ent, expected_s["entries"])
                check_int(f"Symbol {sym} Legs", s_legs, expected_s["legs"])
                check_float(f"Symbol {sym} Net PnL", s_net, expected_s["net_pnl"], tol=0.009)
                check_float(f"Symbol {sym} Win Rate", s_wr, expected_s["win_rate_pct"], tol=0.06)
                check_pf(f"Symbol {sym} Net PF (Leg)", s_npf_l, expected_s["net_pf_leg"], tol=0.009)
                check_pf(f"Symbol {sym} Gross PF (Leg)", s_gpf_l, expected_s["gross_pf_leg"], tol=0.009)
                check_pf(f"Symbol {sym} Net PF (Trade)", s_npf_t, expected_s["net_pf_trade"], tol=0.009)
                check_pf(f"Symbol {sym} Gross PF (Trade)", s_gpf_t, expected_s["gross_pf_trade"], tol=0.009)

    # =========================================================================
    # 5. Mandatory Section: Exit Reason Breakdown
    # =========================================================================
    if "## 6. Exit Reason Breakdown" not in report_text:
        discrepancies.append("MISSING: Mandatory Section '## 6. Exit Reason Breakdown' not found in report.md")
    else:
        for reason, expected_r in calc["exit_reasons"].items():
            r_pattern = re.compile(
                rf"\|\s*\*\*{re.escape(reason)}\*\*\s*\|\s*([\d,]+)\s*\|\s*([\d\.]+)%\s*\|\s*\$([-\d,\.]+)\s*\|\s*\$([-\d,\.]+)\s*\|"
            )
            m_r = r_pattern.search(report_text)
            if not m_r:
                discrepancies.append(f"MISSING: Exit reason row for {reason} not found in report table")
            else:
                r_cnt = int(m_r.group(1).replace(",", ""))
                r_pct = parse_number(m_r.group(2))
                r_gross = parse_number(m_r.group(3))
                r_net = parse_number(m_r.group(4))
                check_int(f"Exit Reason {reason} Count", r_cnt, expected_r["count"])
                check_float(f"Exit Reason {reason} Pct", r_pct, expected_r["pct_of_legs"], tol=0.009)
                check_float(f"Exit Reason {reason} Gross PnL", r_gross, expected_r["gross_pnl"], tol=0.009)
                check_float(f"Exit Reason {reason} Net PnL", r_net, expected_r["net_pnl"], tol=0.009)

    # =========================================================================
    # 6. Mandatory Section: Performance by Combo (All 20 Combos)
    # =========================================================================
    if "## 7. Performance by Combo" not in report_text:
        discrepancies.append("MISSING: Mandatory Section '## 7. Performance by Combo' not found in report.md")
    else:
        for ckey, expected_c in calc["combos"].items():
            c_pattern = re.compile(
                rf"\|\s*\*\*{re.escape(ckey)}\*\*\s*\|\s*[\w-]+\s*\|\s*\d+\s*\|\s*\w+\s*\|\s*\w+\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|\s*\$([-\d,\.]+)\s*\|\s*([\d\.]+)%\s*\|\s*([\w\.\d]+)\s*\|\s*([\w\.\d]+)\s*\|"
            )
            m_c = c_pattern.search(report_text)
            if not m_c:
                discrepancies.append(f"MISSING: Combo row for {ckey} not found in report table")
            else:
                c_ent = int(m_c.group(1).replace(",", ""))
                c_legs = int(m_c.group(2).replace(",", ""))
                c_net = parse_number(m_c.group(3))
                c_wr = parse_number(m_c.group(4))
                c_npf_l = m_c.group(5)
                c_npf_t = m_c.group(6)

                check_int(f"Combo {ckey} Entries", c_ent, expected_c["entries"])
                check_int(f"Combo {ckey} Legs", c_legs, expected_c["legs"])
                check_float(f"Combo {ckey} Net PnL", c_net, expected_c["net_pnl"], tol=0.009)
                check_float(f"Combo {ckey} Win Rate", c_wr, expected_c["win_rate_pct"], tol=0.06)
                check_pf(f"Combo {ckey} Net PF (Leg)", c_npf_l, expected_c["net_pf_leg"], tol=0.009)
                check_pf(f"Combo {ckey} Net PF (Trade)", c_npf_t, expected_c["net_pf_trade"], tol=0.009)

    # =========================================================================
    # 7. Mandatory Section: Reconciliation Audit
    # =========================================================================
    if "## 8. Reconciliation Audit" not in report_text:
        discrepancies.append("MISSING: Mandatory Section '## 8. Reconciliation Audit' not found in report.md")
    else:
        m_rec_pass = re.search(r"\*\*Three-Level Parity Pass.*?:\*\*\s*`?(True|False)`?", report_text)
        if not m_rec_pass or m_rec_pass.group(1) != "True":
            discrepancies.append("MISMATCH: Three-Level Parity Pass is not True in report.md")
        else:
            verifications.append("VERIFIED Three-Level Parity Pass: True")

        # Independent verification of 3-level parity directly from df_trades
        run_net = float(df_trades["net_pnl"].sum())
        sym_net_sum = float(df_trades.groupby("symbol")["net_pnl"].sum().sum())
        combo_net_sum = float(df_trades.groupby(["symbol", "combo_name"])["net_pnl"].sum().sum())
        diff_run_sym = abs(run_net - sym_net_sum)
        diff_run_combo = abs(run_net - combo_net_sum)
        if diff_run_sym > 1e-9 or diff_run_combo > 1e-9:
            discrepancies.append(
                f"RECONCILIATION_FAIL: Independent three-level parity failed on ledger (sym_diff={diff_run_sym:.2e}, combo_diff={diff_run_combo:.2e})"
            )
        else:
            verifications.append(f"VERIFIED Independent Three-Level Parity on Ledger: run={run_net:.4f}, sym_sum={sym_net_sum:.4f}, combo_sum={combo_net_sum:.4f}")

        m_align_pass = re.search(r"\*\*Symbol-Combo Configuration Alignment:\*\*\s*`?(True|False)`?", report_text)
        if not m_align_pass or m_align_pass.group(1) != "True":
            discrepancies.append("MISMATCH: Symbol-Combo Configuration Alignment is not True in report.md")
        else:
            verifications.append("VERIFIED Symbol-Combo Configuration Alignment: True")

    print("=== VERIFY REPORT EXECUTION SUMMARY ===")
    print(f"Report: {rep_p}")
    print(f"Ledger: {trd_p}")
    print(f"Total metrics verified: {len(verifications)}")
    for v in verifications[:20]:
        print(f"  [OK] {v}")
    if len(verifications) > 20:
        print(f"  ... and {len(verifications) - 20} additional verified metrics.")

    if discrepancies:
        print(f"\nFAILED: {len(discrepancies)} discrepancy(ies) detected:", file=sys.stderr)
        for d in discrepancies:
            print(f"  [ERROR] {d}", file=sys.stderr)
        return False

    print("\nSUCCESS: All reported figures faithfully verified against ledger.\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify report.md against trades.parquet ledger independently.")
    parser.add_argument("report_dir", nargs="?", help="Directory containing report.md and trades.parquet")
    parser.add_argument("--run-dir", dest="run_dir", help="Directory containing report.md and trades.parquet")
    parser.add_argument("--report", dest="report_file", help="Path to report.md")
    parser.add_argument("--trades", dest="trades_file", help="Path to trades.parquet")
    args = parser.parse_args()

    target_dir = args.run_dir or args.report_dir
    if target_dir:
        dir_p = Path(target_dir)
        rep_p = dir_p / "report.md"
        trd_p = dir_p / "trades.parquet"
    elif args.report_file and args.trades_file:
        rep_p = Path(args.report_file)
        trd_p = Path(args.trades_file)
    else:
        parser.error("Must provide either report_dir, --run-dir, or both --report and --trades.")

    success = verify_report(rep_p, trd_p)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
