"""Performance metrics calculation for AutoTrade V3 backtest results."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
import math
import numpy as np
import pandas as pd

from autotrade_v3.engine.execution_simulator import TradeRecord


@dataclass
class BacktestMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    net_pnl: float
    gross_pnl: float
    total_fees: float
    roi_pct: float
    profit_factor: float
    avg_trade_pnl: float
    avg_win: float
    avg_loss: float
    max_drawdown_dollars: float
    max_drawdown_pct: float
    intrabar_ambiguous_trades: int
    intrabar_ambiguous_pct: float
    exit_reason_breakdown: dict[str, int]
    combo_breakdown: dict[str, dict]
    symbol_breakdown: dict[str, dict]
    monthly_pnl: dict[str, float]
    equity_curve: list[dict]
    # V3-05 Independent Audit metrics
    m2m_max_drawdown_dollars: float = 0.0
    m2m_max_drawdown_pct: float = 0.0
    intrabar_trailing_ambiguous_trades: int = 0
    intrabar_trailing_ambiguous_pct: float = 0.0
    rejected_signals_margin: int = 0
    m2m_equity_curve: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_metrics(
    trades: list[TradeRecord],
    initial_equity: float = 1000.0,
    m2m_snapshots: pd.DataFrame | list[dict] | None = None,
    rejected_signals_count: int = 0,
) -> BacktestMetrics:
    """Calculate comprehensive performance metrics from a list of simulated trade records."""
    if not trades:
        return BacktestMetrics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            win_rate_pct=0.0,
            net_pnl=0.0,
            gross_pnl=0.0,
            total_fees=0.0,
            roi_pct=0.0,
            profit_factor=0.0,
            avg_trade_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            max_drawdown_dollars=0.0,
            max_drawdown_pct=0.0,
            intrabar_ambiguous_trades=0,
            intrabar_ambiguous_pct=0.0,
            exit_reason_breakdown={},
            combo_breakdown={},
            symbol_breakdown={},
            monthly_pnl={},
            equity_curve=[{"trade_idx": 0, "timestamp_ms": 0, "equity": initial_equity, "drawdown_dollars": 0.0}],
        )

    # Sort trades by exit time
    sorted_trades = sorted(trades, key=lambda t: (t.exit_time_ms, t.entry_time_ms, t.trade_id))

    total = len(sorted_trades)
    wins = [t for t in sorted_trades if t.net_pnl > 1e-7]
    losses = [t for t in sorted_trades if t.net_pnl < -1e-7]
    evens = [t for t in sorted_trades if abs(t.net_pnl) <= 1e-7]

    win_count = len(wins)
    loss_count = len(losses)
    even_count = len(evens)
    win_rate = (win_count / total) * 100.0 if total > 0 else 0.0

    net_pnl = sum(t.net_pnl for t in sorted_trades)
    gross_pnl = sum(t.gross_pnl for t in sorted_trades)
    total_fees = sum(t.fee_total for t in sorted_trades)
    roi_pct = (net_pnl / initial_equity) * 100.0

    gross_wins = sum(t.net_pnl for t in wins)
    gross_losses = abs(sum(t.net_pnl for t in losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)

    avg_trade = net_pnl / total if total > 0 else 0.0
    avg_win = gross_wins / win_count if win_count > 0 else 0.0
    avg_loss = gross_losses / loss_count if loss_count > 0 else 0.0

    # Drawdown & Equity Curve
    current_equity = initial_equity
    peak_equity = initial_equity
    max_dd_dollars = 0.0
    max_dd_pct = 0.0
    equity_curve = [{"trade_idx": 0, "timestamp_ms": sorted_trades[0].entry_time_ms, "equity": initial_equity, "drawdown_dollars": 0.0}]

    for idx, t in enumerate(sorted_trades, 1):
        current_equity += t.net_pnl
        if current_equity > peak_equity:
            peak_equity = current_equity
        dd_dollars = peak_equity - current_equity
        dd_pct = (dd_dollars / peak_equity) * 100.0 if peak_equity > 0 else 0.0
        if dd_dollars > max_dd_dollars:
            max_dd_dollars = dd_dollars
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        equity_curve.append(
            {
                "trade_idx": idx,
                "timestamp_ms": t.exit_time_ms,
                "timestamp_dt": str(t.exit_time_dt),
                "equity": round(current_equity, 4),
                "drawdown_dollars": round(dd_dollars, 4),
            }
        )

    # Intrabar ambiguity stats
    ambiguous_count = sum(1 for t in sorted_trades if t.intrabar_ambiguous)
    ambiguous_pct = (ambiguous_count / total) * 100.0 if total > 0 else 0.0
    trailing_ambig_count = sum(1 for t in sorted_trades if getattr(t, "intrabar_trailing_ambiguous", False))
    trailing_ambig_pct = (trailing_ambig_count / total) * 100.0 if total > 0 else 0.0

    # Exit reason breakdown
    exit_reasons = defaultdict(int)
    for t in sorted_trades:
        exit_reasons[t.exit_reason] += 1

    # Combo breakdown
    by_combo = defaultdict(list)
    for t in sorted_trades:
        by_combo[t.combo_name].append(t)

    combo_stats = {}
    for cname, ctrds in sorted(by_combo.items()):
        c_tot = len(ctrds)
        c_wins = sum(1 for t in ctrds if t.net_pnl > 1e-7)
        c_net = sum(t.net_pnl for t in ctrds)
        c_gw = sum(t.net_pnl for t in ctrds if t.net_pnl > 1e-7)
        c_gl = abs(sum(t.net_pnl for t in ctrds if t.net_pnl < -1e-7))
        c_pf = (c_gw / c_gl) if c_gl > 0 else (999.0 if c_gw > 0 else 0.0)
        combo_stats[cname] = {
            "symbol": ctrds[0].symbol,
            "timeframe": ctrds[0].timeframe,
            "total_trades": c_tot,
            "win_rate_pct": round((c_wins / c_tot) * 100.0, 2),
            "net_pnl": round(c_net, 4),
            "profit_factor": round(c_pf, 2),
            "avg_trade_pnl": round(c_net / c_tot, 4),
            "intrabar_ambiguous": sum(1 for t in ctrds if t.intrabar_ambiguous),
        }

    # Symbol breakdown
    by_symbol = defaultdict(list)
    for t in sorted_trades:
        by_symbol[t.symbol].append(t)

    symbol_stats = {}
    for sym, strds in sorted(by_symbol.items()):
        s_tot = len(strds)
        s_wins = sum(1 for t in strds if t.net_pnl > 1e-7)
        s_net = sum(t.net_pnl for t in strds)
        s_gw = sum(t.net_pnl for t in strds if t.net_pnl > 1e-7)
        s_gl = abs(sum(t.net_pnl for t in strds if t.net_pnl < -1e-7))
        s_pf = (s_gw / s_gl) if s_gl > 0 else (999.0 if s_gw > 0 else 0.0)
        symbol_stats[sym] = {
            "total_trades": s_tot,
            "win_rate_pct": round((s_wins / s_tot) * 100.0, 2),
            "net_pnl": round(s_net, 4),
            "profit_factor": round(s_pf, 2),
        }

    # Monthly breakdown
    monthly_pnl = defaultdict(float)
    for t in sorted_trades:
        month_str = pd.to_datetime(t.exit_time_ms, unit="ms", utc=True).strftime("%Y-%m")
        monthly_pnl[month_str] += t.net_pnl

    rounded_monthly = {m: round(val, 4) for m, val in sorted(monthly_pnl.items())}

    # Evaluate M2M continuous drawdown if snapshots provided
    m2m_dd_dollars = max_dd_dollars
    m2m_dd_pct = max_dd_pct
    m2m_eq_curve = equity_curve

    if m2m_snapshots is not None:
        if isinstance(m2m_snapshots, pd.DataFrame):
            if not m2m_snapshots.empty and "drawdown_dollars" in m2m_snapshots:
                m2m_dd_dollars = float(m2m_snapshots["drawdown_dollars"].max())
                m2m_dd_pct = float(m2m_snapshots["drawdown_pct"].max())
                # Downsample curve points for lightweight summary export
                if len(m2m_snapshots) > 500:
                    step = len(m2m_snapshots) // 500
                    sampled_snaps = m2m_snapshots.iloc[::step]
                else:
                    sampled_snaps = m2m_snapshots
                m2m_eq_curve = sampled_snaps.to_dict(orient="records")
        elif isinstance(m2m_snapshots, list) and m2m_snapshots:
            m2m_dd_dollars = max(s.get("drawdown_dollars", 0.0) for s in m2m_snapshots)
            m2m_dd_pct = max(s.get("drawdown_pct", 0.0) for s in m2m_snapshots)
            if len(m2m_snapshots) > 500:
                step = len(m2m_snapshots) // 500
                m2m_eq_curve = m2m_snapshots[::step]
            else:
                m2m_eq_curve = m2m_snapshots

    return BacktestMetrics(
        total_trades=total,
        winning_trades=win_count,
        losing_trades=loss_count,
        breakeven_trades=even_count,
        win_rate_pct=round(win_rate, 2),
        net_pnl=round(net_pnl, 4),
        gross_pnl=round(gross_pnl, 4),
        total_fees=round(total_fees, 4),
        roi_pct=round(roi_pct, 2),
        profit_factor=round(profit_factor, 2),
        avg_trade_pnl=round(avg_trade, 4),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        max_drawdown_dollars=round(max_dd_dollars, 4),
        max_drawdown_pct=round(max_dd_pct, 2),
        intrabar_ambiguous_trades=ambiguous_count,
        intrabar_ambiguous_pct=round(ambiguous_pct, 2),
        exit_reason_breakdown=dict(exit_reasons),
        combo_breakdown=combo_stats,
        symbol_breakdown=symbol_stats,
        monthly_pnl=rounded_monthly,
        equity_curve=equity_curve,
        m2m_max_drawdown_dollars=round(m2m_dd_dollars, 4),
        m2m_max_drawdown_pct=round(m2m_dd_pct, 2),
        intrabar_trailing_ambiguous_trades=trailing_ambig_count,
        intrabar_trailing_ambiguous_pct=round(trailing_ambig_pct, 2),
        rejected_signals_margin=rejected_signals_count,
        m2m_equity_curve=m2m_eq_curve,
    )
