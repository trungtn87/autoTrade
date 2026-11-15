"""Canonical metric contracts and calculations for the V3 Research Lab.

This module is intentionally UI-agnostic. Dashboard pages and query functions
must consume these functions instead of reimplementing metric formulas.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    gross_pnl: float
    net_pnl: float
    gross_win: float
    gross_loss: float
    total_fees: float
    total_notional_volume: float
    total_entries: int
    weighted_pf: float | str
    break_even_slippage_pct: float
    fee_drag_pct: float
    net_pnl_per_entry: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComboMetric:
    combo_key: str
    pnl_own_entry: float
    net_pnl: float
    attribution_delta: float
    gross_win: float
    gross_loss: float
    weighted_pf: float | str
    total_fees: float
    total_notional_volume: float
    entries: int
    signals: int
    pos_fill_rate: float


@dataclass(frozen=True)
class SignalEvent:
    signal_id: str
    symbol: str
    combo_key: str
    status: str
    rejection_reason: str


def _required(frame: pd.DataFrame, columns: set[str]) -> None:
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise ValueError(f"Missing ledger columns: {missing}")


def weighted_profit_factor(values: pd.Series) -> float | str:
    """Return sum(wins) / abs(sum(losses)); never average child PF values."""
    pnl = values.fillna(0.0).astype(float)
    wins = float(pnl[pnl > 0].sum())
    losses = float(abs(pnl[pnl < 0].sum()))
    if losses > 1e-9:
        return wins / losses
    if wins > 1e-9:
        return "UNDEFINED_NO_LOSS"
    return 0.0


def _notional_series(frame: pd.DataFrame) -> pd.Series:
    """Return notional with conservative compatibility for older test/run artifacts."""
    if "notional" in frame.columns:
        return frame["notional"].fillna(0.0).astype(float)
    if {"qty", "entry_price"}.issubset(frame.columns):
        return frame["qty"].fillna(0.0).astype(float).abs() * frame["entry_price"].fillna(0.0).astype(float).abs()
    return pd.Series(0.0, index=frame.index, dtype="float64")


def cost_reality_metrics(legs_df: pd.DataFrame, *, run_id: str = "") -> RunSummary:
    _required(legs_df, {"trade_id", "net_pnl", "gross_pnl", "fee_total"})
    pnl = legs_df["net_pnl"].astype(float)
    gross_pnl = float(legs_df["gross_pnl"].astype(float).sum())
    net_pnl = float(pnl.sum())
    fees = float(legs_df["fee_total"].astype(float).sum())
    notional = float(_notional_series(legs_df).sum())
    entries = int(legs_df["trade_id"].nunique())
    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    denominator = 2.0 * notional
    return RunSummary(
        run_id=run_id,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        gross_win=gross_win,
        gross_loss=gross_loss,
        total_fees=fees,
        total_notional_volume=notional,
        total_entries=entries,
        weighted_pf=weighted_profit_factor(pnl),
        break_even_slippage_pct=(net_pnl / denominator * 100.0) if denominator else 0.0,
        fee_drag_pct=(fees / gross_pnl * 100.0) if abs(gross_pnl) > 1e-12 else 0.0,
        net_pnl_per_entry=(net_pnl / entries) if entries else 0.0,
    )


def attribution_breakdown(
    legs_df: pd.DataFrame,
    decisions_df: pd.DataFrame,
    *,
    group_columns: list[str],
) -> pd.DataFrame:
    """Aggregate attribution and cost metrics for any stable ledger dimension."""
    _required(legs_df, set(group_columns) | {"trade_id", "net_pnl", "fee_total"})
    frame = legs_df.copy()
    frame["_metric_notional"] = _notional_series(frame)
    if "pnl_own_entry" not in frame.columns:
        frame["pnl_own_entry"] = frame["net_pnl"].astype(float)

    signal_counts: dict[tuple[Any, ...], int] = {}
    if not decisions_df.empty and set(group_columns).issubset(decisions_df.columns):
        counts = decisions_df.groupby(group_columns, dropna=False).size()
        signal_counts = {k if isinstance(k, tuple) else (k,): int(v) for k, v in counts.items()}

    rows: list[dict[str, Any]] = []
    grouper: str | list[str] = group_columns[0] if len(group_columns) == 1 else group_columns
    for key, group in frame.groupby(grouper, sort=True, dropna=False):
        key_tuple = key if isinstance(key, tuple) else (key,)
        net = group["net_pnl"].astype(float)
        own = float(group["pnl_own_entry"].astype(float).sum())
        net_total = float(net.sum())
        entries = int(group["trade_id"].nunique())
        signals = signal_counts.get(key_tuple, int(group["signal_id"].nunique()) if "signal_id" in group.columns else entries)
        row = {column: value for column, value in zip(group_columns, key_tuple)}
        row.update(
            entries=entries,
            legs=len(group),
            signals=signals,
            pos_fill_rate=(entries / signals) if signals else 0.0,
            pnl_own_entry=own,
            net_pnl=net_total,
            attribution_delta=net_total - own,
            fees=float(group["fee_total"].astype(float).sum()),
            total_notional_volume=float(group["_metric_notional"].sum()),
            gross_win=float(net[net > 0].sum()),
            gross_loss=float(net[net < 0].sum()),
            weighted_pf=weighted_profit_factor(net),
        )
        rows.append(row)
    return pd.DataFrame(rows)


def apply_cost_scenario(
    legs_df: pd.DataFrame,
    *,
    slippage_pct_per_side: float,
    extra_fee_pct: float,
) -> pd.DataFrame:
    """Apply an additive research-only scenario without mutating RunStore data."""
    _required(legs_df, {"net_pnl", "fee_total"})
    result = legs_df.copy()
    notional = _notional_series(result)
    scenario_cost = notional * ((2.0 * slippage_pct_per_side + extra_fee_pct) / 100.0)
    result["scenario_cost"] = scenario_cost
    result["net_pnl"] = result["net_pnl"].astype(float) - scenario_cost
    result["fee_total"] = result["fee_total"].astype(float) + notional * extra_fee_pct / 100.0
    return result


def oos_degradation_ratio(equity_df: pd.DataFrame, split_timestamp: pd.Timestamp) -> float | None:
    if equity_df.empty or "equity" not in equity_df.columns or "timestamp_dt" not in equity_df.columns:
        return None
    frame = equity_df.sort_values("timestamp_dt")
    before = frame[frame["timestamp_dt"] < split_timestamp]["equity"].astype(float)
    after = frame[frame["timestamp_dt"] >= split_timestamp]["equity"].astype(float)
    if len(before) < 2 or len(after) < 2:
        return None
    is_days = max((frame.loc[before.index[-1], "timestamp_dt"] - frame.loc[before.index[0], "timestamp_dt"]).days, 1)
    oos_days = max((frame.loc[after.index[-1], "timestamp_dt"] - frame.loc[after.index[0], "timestamp_dt"]).days, 1)
    is_return = (before.iloc[-1] / before.iloc[0]) ** (365.0 / is_days) - 1.0 if before.iloc[0] else 0.0
    oos_return = (after.iloc[-1] / after.iloc[0]) ** (365.0 / oos_days) - 1.0 if after.iloc[0] else 0.0
    return (oos_return / is_return) if abs(is_return) > 1e-12 else None


def cost_sensitivity_grid(
    legs_df: pd.DataFrame,
    slippage_levels: list[float],
    fee_modes: dict[str, float],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for fee_mode, fee_pct in fee_modes.items():
        for slippage in slippage_levels:
            stressed = apply_cost_scenario(
                legs_df,
                slippage_pct_per_side=slippage,
                extra_fee_pct=fee_pct,
            )
            rows.append({"fee_mode": fee_mode, "slippage_pct": slippage, "net_pnl": float(stressed["net_pnl"].sum())})
    return pd.DataFrame(rows)
