"""Account ledger for Mode C (BINGX_PORTFOLIO): merged (symbol, side) VWAP, margin, and M1 M2M equity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
import numpy as np
import pandas as pd


@dataclass
class MergedPosition:
    """Merged hedge-mode position per (symbol, side) with volume-weighted average price."""

    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float = 0.0
    avg_price: float = 0.0
    used_margin: float = 0.0


class AccountLedger:
    """Maintains Mode C cash balance, merged VWAP positions, margin checks, and M1 equity snapshots."""

    def __init__(
        self,
        initial_equity: float,
        symbols: list[str],
        opens_by_symbol: dict[str, np.ndarray],
        closes_by_symbol: dict[str, np.ndarray],
    ) -> None:
        self.initial_equity = initial_equity
        self.balance = initial_equity
        self._opens = opens_by_symbol
        self._closes = closes_by_symbol
        self.positions: dict[tuple[str, str], MergedPosition] = {}
        for sym in symbols:
            self.positions[(sym, "BUY")] = MergedPosition(symbol=sym, side="BUY")
            self.positions[(sym, "SELL")] = MergedPosition(symbol=sym, side="SELL")

        self.peak_equity = initial_equity
        self.max_m2m_dd_dollars = 0.0
        self.max_m2m_dd_pct = 0.0
        self.final_equity = initial_equity
        self.snapshots: list[dict[str, Any]] = []

    def free_margin_at_open(self, bar_idx: int) -> float:
        """Compute real-time free margin at the open of M1 bar ``bar_idx``."""
        unrealized_now = 0.0
        total_used_margin = 0.0
        for pos in self.positions.values():
            if pos.qty > 1e-9:
                total_used_margin += pos.used_margin
                cur_px = float(self._opens[pos.symbol][bar_idx])
                if pos.side == "BUY":
                    unrealized_now += (cur_px - pos.avg_price) * pos.qty
                else:
                    unrealized_now += (pos.avg_price - cur_px) * pos.qty
        current_eq = self.balance + unrealized_now
        return current_eq - total_used_margin

    def open_position(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        notional: float,
        entry_price: float,
        req_margin: float,
        entry_fee: float,
    ) -> float:
        """Add notional to merged (symbol, side) position, update VWAP, and deduct entry fee."""
        self.balance -= entry_fee
        pos = self.positions[(symbol, side)]
        leg_total_qty = notional / entry_price
        new_qty = pos.qty + leg_total_qty
        if new_qty > 1e-9:
            pos.avg_price = (pos.avg_price * pos.qty + notional) / new_qty
        pos.qty = new_qty
        pos.used_margin += req_margin
        return leg_total_qty

    def close_leg(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        leg_qty: float,
        leg_margin: float,
        leg_entry_price: float,
        exec_exit_price: float,
        entry_fee: float,
        exit_fee: float,
    ) -> tuple[float, float, float, float]:
        """Close a leg against the merged position's VWAP and return (effective_entry, gross_pnl, total_fee, net_pnl)."""
        pos = self.positions[(symbol, side)]
        effective_entry = pos.avg_price if pos.avg_price > 0 else leg_entry_price
        if side == "BUY":
            gross_pnl = (exec_exit_price - effective_entry) * leg_qty
        else:
            gross_pnl = (effective_entry - exec_exit_price) * leg_qty

        total_fee = entry_fee + exit_fee
        net_pnl = gross_pnl - total_fee
        self.balance += gross_pnl - exit_fee

        pos.qty = max(0.0, pos.qty - leg_qty)
        pos.used_margin = max(0.0, pos.used_margin - leg_margin)
        if pos.qty <= 1e-9:
            pos.qty = 0.0
            pos.avg_price = 0.0
            pos.used_margin = 0.0

        return effective_entry, gross_pnl, total_fee, net_pnl

    def mark_to_market(
        self,
        *,
        bar_idx: int,
        ts_ms: int,
        ts_dt: Any,
        n_bars: int,
        sample_interval_m1: int = 15,
    ) -> None:
        """Compute M2M equity and drawdown at the close of M1 bar ``bar_idx``."""
        unrealized_total = 0.0
        used_margin_total = 0.0
        for pos in self.positions.values():
            if pos.qty > 1e-9:
                used_margin_total += pos.used_margin
                cl_px = float(self._closes[pos.symbol][bar_idx])
                if pos.side == "BUY":
                    unrealized_total += (cl_px - pos.avg_price) * pos.qty
                else:
                    unrealized_total += (pos.avg_price - cl_px) * pos.qty

        m2m_equity = self.balance + unrealized_total
        self.final_equity = m2m_equity
        if m2m_equity > self.peak_equity:
            self.peak_equity = m2m_equity

        dd_dollars = self.peak_equity - m2m_equity
        dd_pct = (dd_dollars / self.peak_equity) * 100.0 if self.peak_equity > 0 else 0.0
        if dd_dollars > self.max_m2m_dd_dollars:
            self.max_m2m_dd_dollars = dd_dollars
        if dd_pct > self.max_m2m_dd_pct:
            self.max_m2m_dd_pct = dd_pct

        has_open_pos = used_margin_total > 0.0
        if has_open_pos or (bar_idx % sample_interval_m1 == 0) or (bar_idx == n_bars - 1):
            self.snapshots.append({
                "timestamp_ms": ts_ms,
                "timestamp_dt": str(ts_dt),
                "balance": round(self.balance, 4),
                "unrealized_pnl": round(unrealized_total, 4),
                "equity": round(m2m_equity, 4),
                "used_margin": round(used_margin_total, 4),
                "free_margin": round(max(0.0, m2m_equity - used_margin_total), 4),
                "peak_equity": round(self.peak_equity, 4),
                "drawdown_dollars": round(dd_dollars, 4),
                "drawdown_pct": round(dd_pct, 2),
            })

    def snapshots_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.snapshots)
