"""Core data types for the unified M1 execution simulator."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal
import numpy as np
import pandas as pd

DecisionStatus = Literal["ACCEPTED", "SKIPPED", "REJECTED", "FILTERED", "UNFILLABLE"]


@dataclass(frozen=True)
class ExitRules:
    """Exit parameters frozen at leg entry time."""

    tp_pct: float
    sl_pct: float
    trailing_act_pct: float = 0.0
    trailing_cb_pct: float = 0.0
    max_bars_held: int | None = None
    exit_policy_hash: str = ""
    be_trigger_pct: float | None = None
    be_offset_pct: float | None = None


@dataclass(frozen=True)
class LegSpec:
    """Specification of a single trade leg."""

    leg_id: str
    trade_id: str
    leg_type: str  # "SINGLE", "TP_HALF", "TRAIL_HALF"
    notional: float
    is_trailing: bool = False


@dataclass(frozen=True)
class LegOutcome:
    """Pure M1 simulation outcome for a single leg."""

    leg_id: str
    trade_id: str
    leg_type: str
    entry_idx: int
    exit_bar_idx: int
    entry_price: float
    exit_price: float
    exit_reason: str
    bars_held: int
    intrabar_ambiguous: bool
    intrabar_trailing_ambiguous: bool
    ambiguity_type: str = "NONE"
    ambiguity_policy: str = "LEGACY"
    tp_price: float = 0.0
    sl_price: float = 0.0


@dataclass(frozen=True)
class SignalDecision:
    """Per-signal admission decision record (exactly one per signal_id)."""

    signal_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: str
    signal_close_ms: int
    signal_close_dt: str
    status: DecisionStatus
    reason: str
    blocking_trade_id: str = ""
    entry_bar_idx: int | None = None
    entry_time_ms: int | None = None
    free_margin: float | None = None
    req_margin: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Event:
    """Execution event emitted on leg open or close."""

    seq: int
    event_id: str
    timestamp_ms: int
    timestamp_dt: str
    trade_id: str
    leg_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    event_type: Literal["ENTRY", "EXIT"]
    side: Literal["BUY", "SELL"]
    order_action: Literal["OPEN", "CLOSE"]
    price: float
    qty: float
    notional: float
    fee: float
    realized_pnl: float
    exit_reason: str
    signal_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class M1BarArrays:
    """Pre-extracted numpy arrays for fast M1 simulation."""

    t_ms: np.ndarray
    opens: np.ndarray | None
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    index: pd.DatetimeIndex
    providers: np.ndarray
    n_bars: int
