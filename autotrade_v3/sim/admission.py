"""Admission policies for ALL_SIGNALS (Mode A), ONE_TRADE_PER_COMBO (Mode B), and PORTFOLIO (Mode C)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from autotrade_v3.sim.types import SignalDecision

if TYPE_CHECKING:
    from autotrade_v3.sim.account import AccountLedger


class AdmissionPolicy(ABC):
    """Base class for chronological signal admission policies."""

    @abstractmethod
    def evaluate(
        self,
        *,
        signal_id: str,
        symbol: str,
        combo_id: int,
        combo_name: str,
        timeframe: str,
        side: str,
        signal_close_ms: int,
        signal_close_dt: str,
        entry_idx: int,
        entry_time_ms: int,
        req_margin: float,
    ) -> SignalDecision:
        """Return a SignalDecision (ACCEPTED, SKIPPED, or REJECTED) for a fillable signal."""


class AllSignals(AdmissionPolicy):
    """Mode A: accepts every fillable signal without concurrency or margin limits."""

    def evaluate(
        self,
        *,
        signal_id: str,
        symbol: str,
        combo_id: int,
        combo_name: str,
        timeframe: str,
        side: str,
        signal_close_ms: int,
        signal_close_dt: str,
        entry_idx: int,
        entry_time_ms: int,
        req_margin: float,
    ) -> SignalDecision:
        return SignalDecision(
            signal_id=signal_id,
            symbol=symbol,
            combo_id=combo_id,
            combo_name=combo_name,
            timeframe=timeframe,
            side=side,
            signal_close_ms=signal_close_ms,
            signal_close_dt=signal_close_dt,
            status="ACCEPTED",
            reason="ACCEPTED",
            entry_bar_idx=entry_idx,
            entry_time_ms=entry_time_ms,
        )


class OneTradePerCombo(AdmissionPolicy):
    """Mode B: at most one open trade per (symbol, combo_id) at a time."""

    def __init__(self) -> None:
        self._combo_open: dict[tuple[str, int], tuple[str, int]] = {}

    def evaluate(
        self,
        *,
        signal_id: str,
        symbol: str,
        combo_id: int,
        combo_name: str,
        timeframe: str,
        side: str,
        signal_close_ms: int,
        signal_close_dt: str,
        entry_idx: int,
        entry_time_ms: int,
        req_margin: float,
    ) -> SignalDecision:
        key = (symbol, combo_id)
        if key in self._combo_open:
            blocking_trade_id, free_bar_idx = self._combo_open[key]
            if free_bar_idx >= entry_idx:
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason="SKIPPED_BY_CONCURRENCY",
                    blocking_trade_id=blocking_trade_id,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        return SignalDecision(
            signal_id=signal_id,
            symbol=symbol,
            combo_id=combo_id,
            combo_name=combo_name,
            timeframe=timeframe,
            side=side,
            signal_close_ms=signal_close_ms,
            signal_close_dt=signal_close_dt,
            status="ACCEPTED",
            reason="ACCEPTED",
            entry_bar_idx=entry_idx,
            entry_time_ms=entry_time_ms,
        )

    def record_open(self, symbol: str, combo_id: int, trade_id: str, free_bar_idx: int) -> None:
        self._combo_open[(symbol, combo_id)] = (trade_id, free_bar_idx)


class Portfolio(AdmissionPolicy):
    """Mode C / ALL_SIGNALS_PORTFOLIO: optional concurrency check plus strict AccountLedger margin gate."""

    def __init__(self, account: AccountLedger, *, allow_multiple_per_combo: bool = False) -> None:
        self.account = account
        self.allow_multiple_per_combo = allow_multiple_per_combo
        self._active_combos: dict[tuple[str, int], list[tuple[str, str]]] = {}

    def evaluate(
        self,
        *,
        signal_id: str,
        symbol: str,
        combo_id: int,
        combo_name: str,
        timeframe: str,
        side: str,
        signal_close_ms: int,
        signal_close_dt: str,
        entry_idx: int,
        entry_time_ms: int,
        req_margin: float,
    ) -> SignalDecision:
        if not self.allow_multiple_per_combo:
            key = (symbol, combo_id)
            active = self._active_combos.get(key)
            if active:
                blocking_trade_id = active[0][1]
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason="SKIPPED_BY_CONCURRENCY",
                    blocking_trade_id=blocking_trade_id,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        free_margin = self.account.free_margin_at_open(entry_idx)
        if free_margin < req_margin:
            return SignalDecision(
                signal_id=signal_id,
                symbol=symbol,
                combo_id=combo_id,
                combo_name=combo_name,
                timeframe=timeframe,
                side=side,
                signal_close_ms=signal_close_ms,
                signal_close_dt=signal_close_dt,
                status="REJECTED",
                reason="INSUFFICIENT_MARGIN",
                entry_bar_idx=entry_idx,
                entry_time_ms=entry_time_ms,
                free_margin=round(free_margin, 4),
                req_margin=round(req_margin, 4),
            )

        return SignalDecision(
            signal_id=signal_id,
            symbol=symbol,
            combo_id=combo_id,
            combo_name=combo_name,
            timeframe=timeframe,
            side=side,
            signal_close_ms=signal_close_ms,
            signal_close_dt=signal_close_dt,
            status="ACCEPTED",
            reason="ACCEPTED",
            entry_bar_idx=entry_idx,
            entry_time_ms=entry_time_ms,
        )

    def record_open_leg(self, symbol: str, combo_id: int, leg_id: str, trade_id: str) -> None:
        self._active_combos.setdefault((symbol, combo_id), []).append((leg_id, trade_id))

    def record_close_leg(self, symbol: str, combo_id: int, leg_id: str) -> None:
        key = (symbol, combo_id)
        entries = self._active_combos.get(key)
        if not entries:
            return
        self._active_combos[key] = [(lid, tid) for lid, tid in entries if lid != leg_id]
        if not self._active_combos[key]:
            del self._active_combos[key]
