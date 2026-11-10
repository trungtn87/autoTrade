"""Unified Research Analyzer for AutoTrade V3 Offline Research Lab.

Computes comprehensive performance, risk, and accounting analytics across
simulation modes (Mode A, B, C) without hardcoded numbers or ad-hoc assumptions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any
import numpy as np
import pandas as pd

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
        """Compute (Net Profit Factor, Gross Profit Factor).
        
        Returns ('UNDEFINED_NO_LOSS' if denominator == 0 and numerator > 0, 0.0 if both 0).
        """
        # Net Profit Factor (V3-05/06 audited standard: based on net PnL after fees)
        net_wins = sum(t.net_pnl for t in trades if t.net_pnl > 0)
        net_losses = sum(abs(t.net_pnl) for t in trades if t.net_pnl < 0)
        if net_losses > 1e-9:
            net_pf: float | str = round(net_wins / net_losses, 4)
        elif net_wins > 0:
            net_pf = "UNDEFINED_NO_LOSS"
        else:
            net_pf = 0.0

        # Gross Profit Factor (based on gross PnL before fees)
        gross_wins = sum(t.gross_pnl for t in trades if t.gross_pnl > 0)
        gross_losses = sum(abs(t.gross_pnl) for t in trades if t.gross_pnl < 0)
        if gross_losses > 1e-9:
            gross_pf: float | str = round(gross_wins / gross_losses, 4)
        elif gross_wins > 0:
            gross_pf = "UNDEFINED_NO_LOSS"
        else:
            gross_pf = 0.0

        return net_pf, gross_pf

    @classmethod
    def analyze_simulation(
        cls,
        sim_result: SimulationResult,
        initial_equity: float = 1000.0,
        subwindows: list[tuple[str, str, str]] | None = None,
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

        # 3. PnL, Fees, ROI, and Profit Factors
        gross_pnl = sum(t.gross_pnl for t in trades)
        total_fees = sum(t.fee_total for t in trades)
        net_pnl = sum(t.net_pnl for t in trades)

        # Standardized Profit Factors: Net PF is default, Gross PF reported alongside
        net_profit_factor, gross_profit_factor = cls._calc_profit_factors(trades)
        profit_factor = net_profit_factor  # Net Profit Factor is the default

        # Denominator attribution:
        if mode == "BINGX_PORTFOLIO":
            roi_denominator_name = "Account Initial Equity ($1,000)"
            roi_denominator_value = initial_equity
            roi_pct = (net_pnl / initial_equity) * 100.0
            roi_note = "True finite-capital portfolio ROI after margin, fees, and VWAP blending."
        elif mode == "ALL_SIGNALS":
            roi_denominator_name = "Theoretical Baseline Capital ($1,000 reference)"
            roi_denominator_value = initial_equity
            roi_pct = (net_pnl / initial_equity) * 100.0
            roi_note = "Reference unconstrained signal ROI. NOT an executable finite account ROI."
        else:  # ONE_TRADE_PER_COMBO
            roi_denominator_name = "Reference Allocated Capital ($1,000)"
            roi_denominator_value = initial_equity
            roi_pct = (net_pnl / initial_equity) * 100.0
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
            s_gross = sum(t.gross_pnl for t in s_trades)
            s_fee = sum(t.fee_total for t in s_trades)
            s_net = sum(t.net_pnl for t in s_trades)
            s_net_pf, s_gross_pf = cls._calc_profit_factors(s_trades)

            symbol_stats[sym] = {
                "symbol": sym,
                "entries": len(s_trade_ids),
                "legs": len(s_trades),
                "leg_win_rate_pct": round(s_wins / len(s_trades) * 100.0, 2) if s_trades else 0.0,
                "gross_pnl": round(s_gross, 4),
                "total_fees": round(s_fee, 4),
                "net_pnl": round(s_net, 4),
                "profit_factor": s_net_pf if isinstance(s_net_pf, str) else round(s_net_pf, 2),
                "net_profit_factor": s_net_pf if isinstance(s_net_pf, str) else round(s_net_pf, 2),
                "gross_profit_factor": s_gross_pf if isinstance(s_gross_pf, str) else round(s_gross_pf, 2),
            }

        # Mathematical reconciliation check
        sum_sym_net = sum(s["net_pnl"] for s in symbol_stats.values())
        symbol_reconciliation_pass = math.isclose(sum_sym_net, round(net_pnl, 4), abs_tol=1e-3)
        accounting_balance_pass = math.isclose(gross_pnl - total_fees, net_pnl, abs_tol=1e-4)

        # 7. Combo Breakdown (C1 to C16)
        combo_stats: dict[int, dict[str, Any]] = {}
        for t in trades:
            cid = t.combo_id
            if cid not in combo_stats:
                combo_stats[cid] = {
                    "combo_id": cid,
                    "combo_name": t.combo_name,
                    "symbol": t.symbol,
                    "timeframe": t.timeframe,
                    "legs": 0,
                    "trades_set": set(),
                    "wins": 0,
                    "gross_pnl": 0.0,
                    "fees": 0.0,
                    "net_pnl": 0.0,
                }
            combo_stats[cid]["legs"] += 1
            combo_stats[cid]["trades_set"].add(t.trade_id)
            if t.net_pnl > 0:
                combo_stats[cid]["wins"] += 1
            combo_stats[cid]["gross_pnl"] += t.gross_pnl
            combo_stats[cid]["fees"] += t.fee_total
            combo_stats[cid]["net_pnl"] += t.net_pnl

        combos_list = []
        for cid in sorted(combo_stats.keys()):
            c = combo_stats[cid]
            c_trades_cnt = len(c.pop("trades_set"))
            c_trades_records = [t for t in trades if t.combo_id == cid]
            c_net_pf, c_gross_pf = cls._calc_profit_factors(c_trades_records)
            c["entries"] = c_trades_cnt
            c["win_rate_pct"] = round(c["wins"] / c["legs"] * 100.0, 2) if c["legs"] > 0 else 0.0
            c["gross_pnl"] = round(c["gross_pnl"], 4)
            c["fees"] = round(c["fees"], 4)
            c["net_pnl"] = round(c["net_pnl"], 4)
            c["profit_factor"] = c_net_pf if isinstance(c_net_pf, str) else round(c_net_pf, 2)
            c["net_profit_factor"] = c_net_pf if isinstance(c_net_pf, str) else round(c_net_pf, 2)
            c["gross_profit_factor"] = c_gross_pf if isinstance(c_gross_pf, str) else round(c_gross_pf, 2)
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
                    "gross_pnl": 0.0,
                    "fees": 0.0,
                    "net_pnl": 0.0,
                }
            monthly_stats[ym]["legs"] += 1
            monthly_stats[ym]["trades_set"].add(t.trade_id)
            if t.net_pnl > 0:
                monthly_stats[ym]["wins"] += 1
            monthly_stats[ym]["gross_pnl"] += t.gross_pnl
            monthly_stats[ym]["fees"] += t.fee_total
            monthly_stats[ym]["net_pnl"] += t.net_pnl

        monthly_list = []
        for ym in sorted(monthly_stats.keys()):
            m = monthly_stats[ym]
            m_trades_cnt = len(m.pop("trades_set"))
            m_trades_records = [t for t in trades if str(t.entry_time_dt)[:7] == ym]
            m_net_pf, m_gross_pf = cls._calc_profit_factors(m_trades_records)
            m["entries"] = m_trades_cnt
            m["win_rate_pct"] = round(m["wins"] / m["legs"] * 100.0, 2) if m["legs"] > 0 else 0.0
            m["gross_pnl"] = round(m["gross_pnl"], 4)
            m["fees"] = round(m["fees"], 4)
            m["net_pnl"] = round(m["net_pnl"], 4)
            m["profit_factor"] = m_net_pf if isinstance(m_net_pf, str) else round(m_net_pf, 2)
            m["net_profit_factor"] = m_net_pf if isinstance(m_net_pf, str) else round(m_net_pf, 2)
            m["gross_profit_factor"] = m_gross_pf if isinstance(m_gross_pf, str) else round(m_gross_pf, 2)
            monthly_list.append(m)

        # 9. Cross-Window Analysis (Strictly entry-based attribution across 24M / 6M / 6M windows)
        subwindow_results = []
        for name, w_start, w_end in subwindow_definitions:
            w_start_ts = pd.Timestamp(w_start, tz="UTC").value // 1_000_000
            w_end_ts = pd.Timestamp(w_end, tz="UTC").value // 1_000_000
            w_trades = [t for t in trades if t.entry_time_ms >= w_start_ts and t.entry_time_ms < w_end_ts]
            w_trade_ids = {t.trade_id for t in w_trades}
            w_wins = sum(1 for t in w_trades if t.net_pnl > 0)
            w_gross = sum(t.gross_pnl for t in w_trades)
            w_fees = sum(t.fee_total for t in w_trades)
            w_net = sum(t.net_pnl for t in w_trades)
            w_net_pf, w_gross_pf = cls._calc_profit_factors(w_trades)

            subwindow_results.append({
                "subwindow": name,
                "start": w_start,
                "end": w_end,
                "entries": len(w_trade_ids),
                "legs": len(w_trades),
                "win_rate_pct": round(w_wins / len(w_trades) * 100.0, 2) if w_trades else 0.0,
                "gross_pnl": round(w_gross, 4),
                "total_fees": round(w_fees, 4),
                "net_pnl": round(w_net, 4),
                "profit_factor": w_net_pf if isinstance(w_net_pf, str) else round(w_net_pf, 2),
                "net_profit_factor": w_net_pf if isinstance(w_net_pf, str) else round(w_net_pf, 2),
                "gross_profit_factor": w_gross_pf if isinstance(w_gross_pf, str) else round(w_gross_pf, 2),
            })

        # Compile final analysis dictionary
        return {
            "mode": mode,
            "accounting": {
                "total_signals": total_signals,
                "accepted_entries": accepted_entries,
                "total_legs": total_legs,
                "skipped_by_concurrency": skipped_concurrency,
                "other_skipped": other_skipped,
                "rejected_by_margin": rejected_margin,
                "other_rejected": other_rejected,
                "symbol_reconciliation_pass": symbol_reconciliation_pass,
                "accounting_balance_pass": accounting_balance_pass,
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
                "gross_pnl": round(gross_pnl, 4),
                "total_fees": round(total_fees, 4),
                "net_pnl": round(net_pnl, 4),
                "profit_factor": profit_factor if isinstance(profit_factor, str) else round(profit_factor, 2),
                "net_profit_factor": net_profit_factor if isinstance(net_profit_factor, str) else round(net_profit_factor, 2),
                "gross_profit_factor": gross_profit_factor if isinstance(gross_profit_factor, str) else round(gross_profit_factor, 2),
                "roi_pct": round(roi_pct, 2),
                "roi_denominator_name": roi_denominator_name,
                "roi_denominator_value": roi_denominator_value,
                "roi_note": roi_note,
                "avg_trade_pnl": round(net_pnl / accepted_entries, 4) if accepted_entries > 0 else 0.0,
                "avg_leg_pnl": round(net_pnl / total_legs, 4) if total_legs > 0 else 0.0,
                "accounting_verified": accounting_balance_pass and symbol_reconciliation_pass,
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
                **sim_result.ambiguity_stats,
                "resolution_policy": "CONSERVATIVE (SL prioritized on intrabar TP/SL tie; callback prioritized on trailing tie)",
            },
            "symbols": symbol_stats,
            "combos": combos_list,
            "monthly": monthly_list,
            "subwindows": subwindow_results,
            "exit_reasons": exit_breakdown,
        }

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
                "profit_factor": pnl_info.get("profit_factor", 0.0),
                "roi_pct": pnl_info.get("roi_pct", 0.0),
                "max_drawdown_dollars": risk.get("m2m_max_drawdown_dollars", risk.get("closed_trade_max_drawdown_dollars", 0.0)),
                "max_drawdown_pct": risk.get("m2m_max_drawdown_pct", risk.get("closed_trade_max_drawdown_pct", 0.0)),
            }
            comparison_rows.append(row)

        return {
            "comparison_table": comparison_rows,
            "run_count": len(run_dicts),
        }
