"""Unified Research Analyzer for AutoTrade V3 Offline Research Lab.

Computes comprehensive performance, risk, and accounting analytics across
simulation modes (Mode A, B, C) without hardcoded numbers or ad-hoc assumptions.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import math
from typing import Any
import numpy as np
import pandas as pd

from autotrade_v3.analysis.metrics import (
    compute_four_profit_factors,
    get_baseline_combo_keys,
)
from autotrade_v3.engine.execution_simulator import SimulationResult, TradeRecord


class ResearchAnalyzer:
    """Computes multidimensional performance, risk, and attribution analytics."""

    DEFAULT_SUBWINDOWS = [
        ("Development (24M)", "2023-11-01", "2025-11-01"),
        ("Validation (6M)", "2025-11-01", "2026-05-01"),
        ("Holdout (6M)", "2026-05-01", "2026-11-01"),
    ]

    @staticmethod
    def _calc_profit_factors(trades: list[TradeRecord]) -> tuple[float | str, float | str]:
        """Compute (Net Profit Factor, Gross Profit Factor) at leg level.
        
        Returns ('UNDEFINED_NO_LOSS' if denominator == 0 and numerator > 0, 0.0 if both 0).
        """
        res = compute_four_profit_factors(trades)
        return res["net_pf_leg"], res["gross_pf_leg"]

    @classmethod
    def analyze_simulation(
        cls,
        sim_result: SimulationResult,
        initial_equity: float = 1000.0,
        subwindows: list[tuple[str, str, str]] | None = None,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform comprehensive analysis of simulation results."""
        trades = sim_result.trades
        mode = sim_result.mode
        subwindow_definitions = subwindows or cls.DEFAULT_SUBWINDOWS

        # 1. Signals and Execution Status Accounting
        total_signals = sim_result.total_signals_count
        accepted_entries = sim_result.accepted_trades_count
        total_legs = len(trades)
        skipped_concurrency = len([s for s in sim_result.skipped_signals if s.get("reason") == "SKIPPED_BY_CONCURRENCY"])
        other_skipped = len(sim_result.skipped_signals) - skipped_concurrency
        rejected_margin = len([r for r in sim_result.rejected_signals if r.get("reason") == "INSUFFICIENT_MARGIN"])
        other_rejected = len(sim_result.rejected_signals) - rejected_margin

        # Extract all signals to attribute per symbol and per combo
        all_signals = list(sim_result.accepted_signals) + list(sim_result.skipped_signals) + list(sim_result.rejected_signals)
        signals_by_symbol: dict[str, int] = defaultdict(int)
        signals_by_combo: dict[str, int] = defaultdict(int)
        for s in all_signals:
            s_sym = getattr(s, "symbol", "") or (s.get("symbol", "") if isinstance(s, dict) else "")
            s_cname = getattr(s, "combo_name", "") or (s.get("combo_name", "") if isinstance(s, dict) else "")
            if s_sym:
                signals_by_symbol[s_sym] += 1
            if s_sym and s_cname:
                signals_by_combo[f"{s_sym}/{s_cname}"] += 1

        # 2. Win Rate Calculations (Distinguish Trade-level vs Leg-level)
        winning_legs = [t for t in trades if t.net_pnl > 0]
        losing_legs = [t for t in trades if t.net_pnl < 0]
        breakeven_legs = [t for t in trades if t.net_pnl == 0]

        leg_win_rate_pct = (len(winning_legs) / total_legs * 100.0) if total_legs > 0 else 0.0

        # Group by trade_id for Trade-level Win Rate
        trades_by_id: dict[str, list[TradeRecord]] = {}
        for t in trades:
            trades_by_id.setdefault(t.trade_id, []).append(t)

        trade_pnl_map: dict[str, float] = {
            tid: sum(leg.net_pnl for leg in leg_list) for tid, leg_list in trades_by_id.items()
        }
        winning_trades = sum(1 for pnl in trade_pnl_map.values() if pnl > 0)
        losing_trades = sum(1 for pnl in trade_pnl_map.values() if pnl < 0)
        breakeven_trades = sum(1 for pnl in trade_pnl_map.values() if pnl == 0)
        trade_win_rate_pct = (winning_trades / len(trade_pnl_map) * 100.0) if trade_pnl_map else 0.0

        # 3. PnL, Fees, ROI, and Profit Factors (4 canonical metrics)
        gross_pnl_u = float(sum(t.gross_pnl for t in trades))
        total_fees_u = float(sum(t.fee_total for t in trades))
        net_pnl_u = float(sum(t.net_pnl for t in trades))

        run_pf = compute_four_profit_factors(trades)

        # Denominator attribution:
        if mode == "BINGX_PORTFOLIO":
            roi_denominator_name = "Account Initial Equity ($1,000)"
            roi_denominator_value = initial_equity
            roi_pct = (net_pnl_u / initial_equity) * 100.0
            roi_note = "True finite-capital portfolio ROI after margin, fees, and VWAP blending."
        elif mode == "ALL_SIGNALS":
            roi_denominator_name = "Theoretical Baseline Capital ($1,000 reference)"
            roi_denominator_value = initial_equity
            roi_pct = (net_pnl_u / initial_equity) * 100.0
            roi_note = "Reference unconstrained signal ROI. NOT an executable finite account ROI."
        else:  # ONE_TRADE_PER_COMBO
            roi_denominator_name = "Reference Allocated Capital ($1,000)"
            roi_denominator_value = initial_equity
            roi_pct = (net_pnl_u / initial_equity) * 100.0
            roi_note = "Reference isolated combo ROI (max 1 open trade per combo)."

        # 4. Drawdown Calculation: strictly distinguish closed-trade vs continuous M2M
        cum_pnl = 0.0
        peak_pnl = 0.0
        max_dd_dollars = 0.0
        max_dd_pct = 0.0
        for t in trades:
            cum_pnl += t.net_pnl
            if cum_pnl > peak_pnl:
                peak_pnl = cum_pnl
            dd = peak_pnl - cum_pnl
            if dd > max_dd_dollars:
                max_dd_dollars = dd
                ref_cap = initial_equity + peak_pnl
                max_dd_pct = (dd / ref_cap * 100.0) if ref_cap > 0 else 0.0

        if mode == "BINGX_PORTFOLIO":
            m2m_dd_dollars: float | None = sim_result.summary_dict.get("m2m_max_drawdown_dollars", max_dd_dollars)
            m2m_dd_pct: float | None = sim_result.summary_dict.get("m2m_max_drawdown_pct", max_dd_pct)
            m2m_status = "AVAILABLE"
            m2m_note = "Continuous intra-trade M2M drawdown tracked across open merged positions."
        else:
            # Mode A & B do not simulate continuous portfolio equity; do NOT misrepresent closed DD as continuous M2M!
            m2m_dd_dollars = None
            m2m_dd_pct = None
            m2m_status = "NOT_APPLICABLE"
            m2m_note = "Continuous intra-trade M2M drawdown is NOT_APPLICABLE in isolated/unconstrained trade modes."

        # 5. Exit Reasons Breakdown
        exit_breakdown: dict[str, dict[str, Any]] = {}
        for t in trades:
            reason = t.exit_reason
            if reason not in exit_breakdown:
                exit_breakdown[reason] = {"count": 0, "gross_pnl": 0.0, "net_pnl": 0.0}
            exit_breakdown[reason]["count"] += 1
            exit_breakdown[reason]["gross_pnl"] += t.gross_pnl
            exit_breakdown[reason]["net_pnl"] += t.net_pnl

        for k, v in exit_breakdown.items():
            v["gross_pnl"] = round(v["gross_pnl"], 4)
            v["net_pnl"] = round(v["net_pnl"], 4)
            v["pct_of_legs"] = round(v["count"] / total_legs * 100.0, 2) if total_legs > 0 else 0.0

        # 6. Symbol Breakdown (BTC vs ETH) with strict parity check
        symbols = sorted({t.symbol for t in trades})
        symbol_stats: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            s_trades = [t for t in trades if t.symbol == sym]
            s_trade_ids = {t.trade_id for t in s_trades}
            s_wins = sum(1 for t in s_trades if t.net_pnl > 0)
            s_gross_u = float(sum(t.gross_pnl for t in s_trades))
            s_fee_u = float(sum(t.fee_total for t in s_trades))
            s_net_u = float(sum(t.net_pnl for t in s_trades))
            s_pf = compute_four_profit_factors(s_trades)

            sym_signals = signals_by_symbol.get(sym, len(s_trade_ids)) if all_signals else len(s_trade_ids)

            symbol_stats[sym] = {
                "symbol": sym,
                "signals": sym_signals,
                "entries": len(s_trade_ids),
                "legs": len(s_trades),
                "leg_win_rate_pct": round(s_wins / len(s_trades) * 100.0, 2) if s_trades else 0.0,
                "gross_pnl": round(s_gross_u, 4),
                "gross_pnl_unrounded": s_gross_u,
                "total_fees": round(s_fee_u, 4),
                "total_fees_unrounded": s_fee_u,
                "net_pnl": round(s_net_u, 4),
                "net_pnl_unrounded": s_net_u,
                "net_pf_trade": s_pf["net_pf_trade"],
                "net_pf_trade_unrounded": s_pf["net_pf_trade_unrounded"],
                "net_pf_leg": s_pf["net_pf_leg"],
                "net_pf_leg_unrounded": s_pf["net_pf_leg_unrounded"],
                "gross_pf_trade": s_pf["gross_pf_trade"],
                "gross_pf_trade_unrounded": s_pf["gross_pf_trade_unrounded"],
                "gross_pf_leg": s_pf["gross_pf_leg"],
                "gross_pf_leg_unrounded": s_pf["gross_pf_leg_unrounded"],
                "profit_factor": s_pf["profit_factor"],
                "profit_factor_basis": s_pf["profit_factor_basis"],
                "net_profit_factor": s_pf["net_pf_leg"],
                "gross_profit_factor": s_pf["gross_pf_leg"],
            }

        # 7. Combo Breakdown (Keyed by f"{symbol}/{combo_name}", e.g. "BTC-USDT/C1")
        combo_stats: dict[str, dict[str, Any]] = {}
        for t in trades:
            ckey = f"{t.symbol}/{t.combo_name}"
            if ckey not in combo_stats:
                combo_stats[ckey] = {
                    "combo_key": ckey,
                    "combo_id": t.combo_id,
                    "combo_name": t.combo_name,
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "legs": 0,
                    "trades_set": set(),
                    "wins": 0,
                    "gross_pnl_unrounded": 0.0,
                    "fees_unrounded": 0.0,
                    "net_pnl_unrounded": 0.0,
                }
            combo_stats[ckey]["legs"] += 1
            combo_stats[ckey]["trades_set"].add(t.trade_id)
            if t.net_pnl > 0:
                combo_stats[ckey]["wins"] += 1
            combo_stats[ckey]["gross_pnl_unrounded"] += t.gross_pnl
            combo_stats[ckey]["fees_unrounded"] += t.fee_total
            combo_stats[ckey]["net_pnl_unrounded"] += t.net_pnl

        combos_list = []
        for ckey in sorted(combo_stats.keys()):
            c = combo_stats[ckey]
            c_trades_cnt = len(c.pop("trades_set"))
            c_trades_records = [t for t in trades if f"{t.symbol}/{t.combo_name}" == ckey]
            c_pf = compute_four_profit_factors(c_trades_records)
            c_signals = signals_by_combo.get(ckey, c_trades_cnt) if all_signals else c_trades_cnt

            c["signals"] = c_signals
            c["entries"] = c_trades_cnt
            c["win_rate_pct"] = round(c["wins"] / c["legs"] * 100.0, 2) if c["legs"] > 0 else 0.0
            c["gross_pnl"] = round(c["gross_pnl_unrounded"], 4)
            c["fees"] = round(c["fees_unrounded"], 4)
            c["net_pnl"] = round(c["net_pnl_unrounded"], 4)
            c["net_pf_trade"] = c_pf["net_pf_trade"]
            c["net_pf_trade_unrounded"] = c_pf["net_pf_trade_unrounded"]
            c["net_pf_leg"] = c_pf["net_pf_leg"]
            c["net_pf_leg_unrounded"] = c_pf["net_pf_leg_unrounded"]
            c["gross_pf_trade"] = c_pf["gross_pf_trade"]
            c["gross_pf_trade_unrounded"] = c_pf["gross_pf_trade_unrounded"]
            c["gross_pf_leg"] = c_pf["gross_pf_leg"]
            c["gross_pf_leg_unrounded"] = c_pf["gross_pf_leg_unrounded"]
            c["profit_factor"] = c_pf["profit_factor"]
            c["profit_factor_basis"] = c_pf["profit_factor_basis"]
            c["net_profit_factor"] = c_pf["net_pf_leg"]
            c["gross_profit_factor"] = c_pf["gross_pf_leg"]
            combos_list.append(c)

        # 8. Monthly Breakdown
        monthly_stats: dict[str, dict[str, Any]] = {}
        for t in trades:
            ym = str(t.entry_time_dt)[:7]
            if ym not in monthly_stats:
                monthly_stats[ym] = {
                    "year_month": ym,
                    "legs": 0,
                    "trades_set": set(),
                    "wins": 0,
                    "gross_pnl_unrounded": 0.0,
                    "fees_unrounded": 0.0,
                    "net_pnl_unrounded": 0.0,
                }
            monthly_stats[ym]["legs"] += 1
            monthly_stats[ym]["trades_set"].add(t.trade_id)
            if t.net_pnl > 0:
                monthly_stats[ym]["wins"] += 1
            monthly_stats[ym]["gross_pnl_unrounded"] += t.gross_pnl
            monthly_stats[ym]["fees_unrounded"] += t.fee_total
            monthly_stats[ym]["net_pnl_unrounded"] += t.net_pnl

        monthly_list = []
        for ym in sorted(monthly_stats.keys()):
            m = monthly_stats[ym]
            m_trades_cnt = len(m.pop("trades_set"))
            m_trades_records = [t for t in trades if str(t.entry_time_dt)[:7] == ym]
            m_pf = compute_four_profit_factors(m_trades_records)
            m["entries"] = m_trades_cnt
            m["win_rate_pct"] = round(m["wins"] / m["legs"] * 100.0, 2) if m["legs"] > 0 else 0.0
            m["gross_pnl"] = round(m["gross_pnl_unrounded"], 4)
            m["fees"] = round(m["fees_unrounded"], 4)
            m["net_pnl"] = round(m["net_pnl_unrounded"], 4)
            m["net_pf_trade"] = m_pf["net_pf_trade"]
            m["net_pf_trade_unrounded"] = m_pf["net_pf_trade_unrounded"]
            m["net_pf_leg"] = m_pf["net_pf_leg"]
            m["net_pf_leg_unrounded"] = m_pf["net_pf_leg_unrounded"]
            m["gross_pf_trade"] = m_pf["gross_pf_trade"]
            m["gross_pf_trade_unrounded"] = m_pf["gross_pf_trade_unrounded"]
            m["gross_pf_leg"] = m_pf["gross_pf_leg"]
            m["gross_pf_leg_unrounded"] = m_pf["gross_pf_leg_unrounded"]
            m["profit_factor"] = m_pf["profit_factor"]
            m["profit_factor_basis"] = m_pf["profit_factor_basis"]
            m["net_profit_factor"] = m_pf["net_pf_leg"]
            m["gross_profit_factor"] = m_pf["gross_pf_leg"]
            monthly_list.append(m)

        # 9. Cross-Window Analysis (Strictly entry-based attribution across 24M / 6M / 6M windows)
        subwindow_results = []
        for name, w_start, w_end in subwindow_definitions:
            w_start_ts = pd.Timestamp(w_start, tz="UTC").value // 1_000_000
            w_end_ts = pd.Timestamp(w_end, tz="UTC").value // 1_000_000
            w_trades = [t for t in trades if t.entry_time_ms >= w_start_ts and t.entry_time_ms < w_end_ts]
            w_trade_ids = {t.trade_id for t in w_trades}
            w_wins = sum(1 for t in w_trades if t.net_pnl > 0)
            w_gross_u = float(sum(t.gross_pnl for t in w_trades))
            w_fees_u = float(sum(t.fee_total for t in w_trades))
            w_net_u = float(sum(t.net_pnl for t in w_trades))
            w_pf = compute_four_profit_factors(w_trades)

            subwindow_results.append({
                "subwindow": name,
                "start": w_start,
                "end": w_end,
                "entries": len(w_trade_ids),
                "legs": len(w_trades),
                "win_rate_pct": round(w_wins / len(w_trades) * 100.0, 2) if w_trades else 0.0,
                "gross_pnl": round(w_gross_u, 4),
                "gross_pnl_unrounded": w_gross_u,
                "total_fees": round(w_fees_u, 4),
                "total_fees_unrounded": w_fees_u,
                "net_pnl": round(w_net_u, 4),
                "net_pnl_unrounded": w_net_u,
                "net_pf_trade": w_pf["net_pf_trade"],
                "net_pf_trade_unrounded": w_pf["net_pf_trade_unrounded"],
                "net_pf_leg": w_pf["net_pf_leg"],
                "net_pf_leg_unrounded": w_pf["net_pf_leg_unrounded"],
                "gross_pf_trade": w_pf["gross_pf_trade"],
                "gross_pf_trade_unrounded": w_pf["gross_pf_trade_unrounded"],
                "gross_pf_leg": w_pf["gross_pf_leg"],
                "gross_pf_leg_unrounded": w_pf["gross_pf_leg_unrounded"],
                "profit_factor": w_pf["profit_factor"],
                "profit_factor_basis": w_pf["profit_factor_basis"],
                "net_profit_factor": w_pf["net_pf_leg"],
                "gross_profit_factor": w_pf["gross_pf_leg"],
            })

        # 10. Three-Level Reconciliation (Run Total vs Sum By Symbol vs Sum By Combo)
        sum_sym_signals = sum(s.get("signals", 0) for s in symbol_stats.values())
        sum_combo_signals = sum(c.get("signals", 0) for c in combos_list)
        signals_diff = max(abs(total_signals - sum_sym_signals), abs(total_signals - sum_combo_signals))

        sum_sym_entries = sum(s.get("entries", 0) for s in symbol_stats.values())
        sum_combo_entries = sum(c.get("entries", 0) for c in combos_list)
        entries_diff = max(abs(accepted_entries - sum_sym_entries), abs(accepted_entries - sum_combo_entries))

        sum_sym_legs = sum(s.get("legs", 0) for s in symbol_stats.values())
        sum_combo_legs = sum(c.get("legs", 0) for c in combos_list)
        legs_diff = max(abs(total_legs - sum_sym_legs), abs(total_legs - sum_combo_legs))

        sum_sym_gross_u = sum(s["gross_pnl_unrounded"] for s in symbol_stats.values())
        sum_combo_gross_u = sum(c["gross_pnl_unrounded"] for c in combos_list)
        gross_diff_u = max(abs(gross_pnl_u - sum_sym_gross_u), abs(gross_pnl_u - sum_combo_gross_u))

        sum_sym_fee_u = sum(s["total_fees_unrounded"] for s in symbol_stats.values())
        sum_combo_fee_u = sum(c["fees_unrounded"] for c in combos_list)
        fee_diff_u = max(abs(total_fees_u - sum_sym_fee_u), abs(total_fees_u - sum_combo_fee_u))

        sum_sym_net_u = sum(s["net_pnl_unrounded"] for s in symbol_stats.values())
        sum_combo_net_u = sum(c["net_pnl_unrounded"] for c in combos_list)
        net_diff_u = max(abs(net_pnl_u - sum_sym_net_u), abs(net_pnl_u - sum_combo_net_u))

        # Baseline symbol-combo alignment check
        baseline_keys = get_baseline_combo_keys()
        alignment_valid = True
        for c in combos_list:
            if baseline_keys and c["combo_key"] not in baseline_keys:
                alignment_valid = False
                break
        for t in trades:
            t_key = f"{t.symbol}/{t.combo_name}"
            if baseline_keys and t_key not in baseline_keys:
                alignment_valid = False
                break

        tolerance = 1e-9
        reconciliation_pass = (
            signals_diff == 0
            and entries_diff == 0
            and legs_diff == 0
            and gross_diff_u <= tolerance
            and fee_diff_u <= tolerance
            and net_diff_u <= tolerance
            and alignment_valid
        )

        reconciliation = {
            "signals": {
                "run_total": total_signals,
                "sum_by_symbol": sum_sym_signals,
                "sum_by_combo": sum_combo_signals,
                "max_absolute_diff": signals_diff,
            },
            "entries": {
                "run_total": accepted_entries,
                "sum_by_symbol": sum_sym_entries,
                "sum_by_combo": sum_combo_entries,
                "max_absolute_diff": entries_diff,
            },
            "legs": {
                "run_total": total_legs,
                "sum_by_symbol": sum_sym_legs,
                "sum_by_combo": sum_combo_legs,
                "max_absolute_diff": legs_diff,
            },
            "gross_pnl": {
                "run_total_unrounded": gross_pnl_u,
                "sum_by_symbol_unrounded": sum_sym_gross_u,
                "sum_by_combo_unrounded": sum_combo_gross_u,
                "max_absolute_diff_unrounded": gross_diff_u,
                "run_total_rounded_4dp": round(gross_pnl_u, 4),
                "sum_by_symbol_rounded_4dp": round(sum(s["gross_pnl"] for s in symbol_stats.values()), 4),
                "sum_by_combo_rounded_4dp": round(sum(c["gross_pnl"] for c in combos_list), 4),
                "rounding_discrepancy_vs_run_rounded": round(abs(sum(c["gross_pnl"] for c in combos_list) - round(gross_pnl_u, 4)), 4),
            },
            "fees": {
                "run_total_unrounded": total_fees_u,
                "sum_by_symbol_unrounded": sum_sym_fee_u,
                "sum_by_combo_unrounded": sum_combo_fee_u,
                "max_absolute_diff_unrounded": fee_diff_u,
                "run_total_rounded_4dp": round(total_fees_u, 4),
                "sum_by_symbol_rounded_4dp": round(sum(s["total_fees"] for s in symbol_stats.values()), 4),
                "sum_by_combo_rounded_4dp": round(sum(c["fees"] for c in combos_list), 4),
                "rounding_discrepancy_vs_run_rounded": round(abs(sum(c["fees"] for c in combos_list) - round(total_fees_u, 4)), 4),
            },
            "net_pnl": {
                "run_total_unrounded": net_pnl_u,
                "sum_by_symbol_unrounded": sum_sym_net_u,
                "sum_by_combo_unrounded": sum_combo_net_u,
                "max_absolute_diff_unrounded": net_diff_u,
                "run_total_rounded_4dp": round(net_pnl_u, 4),
                "sum_by_symbol_rounded_4dp": round(sum(s["net_pnl"] for s in symbol_stats.values()), 4),
                "sum_by_combo_rounded_4dp": round(sum(c["net_pnl"] for c in combos_list), 4),
                "rounding_discrepancy_vs_run_rounded": round(abs(sum(c["net_pnl"] for c in combos_list) - round(net_pnl_u, 4)), 4),
            },
            "symbol_combo_alignment_pass": alignment_valid,
            "tolerance": tolerance,
            "reconciliation_pass": reconciliation_pass,
        }

        accounting_balance_pass = math.isclose(gross_pnl_u - total_fees_u, net_pnl_u, abs_tol=1e-4)

        # Compile final analysis dictionary
        result = {
            "mode": mode,
            "accounting": {
                "total_signals": total_signals,
                "accepted_entries": accepted_entries,
                "total_legs": total_legs,
                "skipped_by_concurrency": skipped_concurrency,
                "other_skipped": other_skipped,
                "rejected_by_margin": rejected_margin,
                "other_rejected": other_rejected,
                "symbol_reconciliation_pass": net_diff_u <= tolerance,
                "accounting_balance_pass": accounting_balance_pass,
                "three_level_reconciliation_pass": reconciliation_pass,
            },
            "win_rate": {
                "leg_win_rate_pct": round(leg_win_rate_pct, 2),
                "trade_win_rate_pct": round(trade_win_rate_pct, 2),
                "winning_legs": len(winning_legs),
                "losing_legs": len(losing_legs),
                "breakeven_legs": len(breakeven_legs),
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "breakeven_trades": breakeven_trades,
            },
            "pnl": {
                "gross_pnl": round(gross_pnl_u, 4),
                "gross_pnl_unrounded": gross_pnl_u,
                "total_fees": round(total_fees_u, 4),
                "total_fees_unrounded": total_fees_u,
                "net_pnl": round(net_pnl_u, 4),
                "net_pnl_unrounded": net_pnl_u,
                "profit_factor": run_pf["profit_factor"],
                "profit_factor_basis": run_pf["profit_factor_basis"],
                "net_profit_factor": run_pf["net_pf_leg"],
                "gross_profit_factor": run_pf["gross_pf_leg"],
                "net_pf_trade": run_pf["net_pf_trade"],
                "net_pf_trade_unrounded": run_pf["net_pf_trade_unrounded"],
                "net_pf_leg": run_pf["net_pf_leg"],
                "net_pf_leg_unrounded": run_pf["net_pf_leg_unrounded"],
                "gross_pf_trade": run_pf["gross_pf_trade"],
                "gross_pf_trade_unrounded": run_pf["gross_pf_trade_unrounded"],
                "gross_pf_leg": run_pf["gross_pf_leg"],
                "gross_pf_leg_unrounded": run_pf["gross_pf_leg_unrounded"],
                "roi_pct": round(roi_pct, 2),
                "roi_denominator_name": roi_denominator_name,
                "roi_denominator_value": roi_denominator_value,
                "roi_note": roi_note,
                "avg_trade_pnl": round(net_pnl_u / accepted_entries, 4) if accepted_entries > 0 else 0.0,
                "avg_leg_pnl": round(net_pnl_u / total_legs, 4) if total_legs > 0 else 0.0,
                "accounting_verified": accounting_balance_pass and (net_diff_u <= tolerance),
            },
            "risk": {
                "closed_trade_max_drawdown_dollars": round(max_dd_dollars, 4),
                "closed_trade_max_drawdown_pct": round(max_dd_pct, 2),
                "m2m_max_drawdown_dollars": round(m2m_dd_dollars, 4) if m2m_dd_dollars is not None else None,
                "m2m_max_drawdown_pct": round(m2m_dd_pct, 2) if m2m_dd_pct is not None else None,
                "m2m_status": m2m_status,
                "m2m_note": m2m_note,
            },
            "ambiguity": {
                "intrabar_tp_sl_ambiguous_count": (
                    sim_result.ambiguity_stats.get("intrabar_tp_sl_ambiguous_count")
                    if sim_result.ambiguity_stats and "intrabar_tp_sl_ambiguous_count" in sim_result.ambiguity_stats
                    else sum(1 for t in trades if getattr(t, "intrabar_ambiguous", False))
                ),
                "intrabar_trailing_ambiguous_count": (
                    sim_result.ambiguity_stats.get("intrabar_trailing_ambiguous_count")
                    if sim_result.ambiguity_stats and "intrabar_trailing_ambiguous_count" in sim_result.ambiguity_stats
                    else sum(1 for t in trades if getattr(t, "intrabar_trailing_ambiguous", False))
                ),
                "resolution_policy": "CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)",
            },
            "symbols": symbol_stats,
            "combos": combos_list,
            "monthly": monthly_list,
            "subwindows": subwindow_results,
            "exit_reasons": exit_breakdown,
            "reconciliation": reconciliation,
        }

        if validation is not None:
            result["validation"] = validation
        elif "validation" in sim_result.summary_dict:
            result["validation"] = sim_result.summary_dict["validation"]

        return result

    @classmethod
    def compare_runs(cls, run_dicts: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate structured comparative matrix across multiple runs."""
        comparison_rows = []
        for r in run_dicts:
            manifest = r.get("manifest", {})
            metrics = r.get("analysis", {})
            pnl_info = metrics.get("pnl", {})
            acct = metrics.get("accounting", {})
            wr = metrics.get("win_rate", {})
            risk = metrics.get("risk", {})

            row = {
                "run_id": manifest.get("run_id", "UNKNOWN"),
                "mode": manifest.get("execution_mode", "UNKNOWN"),
                "strategy_id": manifest.get("strategy_id", "UNKNOWN"),
                "total_signals": acct.get("total_signals", 0),
                "accepted_entries": acct.get("accepted_entries", 0),
                "total_legs": acct.get("total_legs", 0),
                "skipped_signals": acct.get("skipped_by_concurrency", 0),
                "rejected_signals": acct.get("rejected_by_margin", 0),
                "win_rate_trade_pct": wr.get("trade_win_rate_pct", 0.0),
                "win_rate_leg_pct": wr.get("leg_win_rate_pct", 0.0),
                "gross_pnl": pnl_info.get("gross_pnl", 0.0),
                "total_fees": pnl_info.get("total_fees", 0.0),
                "net_pnl": pnl_info.get("net_pnl", 0.0),
                "net_pf_trade": pnl_info.get("net_pf_trade", 0.0),
                "net_pf_leg": pnl_info.get("net_pf_leg", 0.0),
                "gross_pf_trade": pnl_info.get("gross_pf_trade", 0.0),
                "gross_pf_leg": pnl_info.get("gross_pf_leg", 0.0),
                "profit_factor": pnl_info.get("profit_factor", 0.0),
                "profit_factor_basis": pnl_info.get("profit_factor_basis", "leg"),
                "roi_pct": pnl_info.get("roi_pct", 0.0),
                "closed_trade_max_drawdown_dollars": risk.get("closed_trade_max_drawdown_dollars", 0.0),
                "closed_trade_max_drawdown_pct": risk.get("closed_trade_max_drawdown_pct", 0.0),
                "m2m_max_drawdown_dollars": risk.get("m2m_max_drawdown_dollars"),
                "m2m_max_drawdown_pct": risk.get("m2m_max_drawdown_pct"),
            }
            comparison_rows.append(row)

        return {
            "comparison_table": comparison_rows,
            "run_count": len(run_dicts),
        }
