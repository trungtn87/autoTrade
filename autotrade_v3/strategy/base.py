"""Base interfaces and contracts for Strategy Engine in AutoTrade V3 Research Lab."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy_core.api import SignalEvent, StrategySpec
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash


@dataclass(frozen=True)
class ExitPolicy:
    """Exit policy defining TP, SL, and Trailing parameters for a combo or strategy."""
    tp_pct: float
    sl_pct: float
    split_legs: bool = True  # True for 50% TP + 50% Trailing (FINAL14); False for 100% hard single (NEW6)
    trailing_act_pct: float = 0.005  # 0.5% favorable move to activate trailing
    trailing_cb_pct: float = 0.005   # 0.5% callback to trigger stop
    leg_weights: tuple[float, ...] = (0.5, 0.5)  # Volume weight per leg (e.g. 50/50 or (1.0,))
    max_bars_held: int | None = None
    force_close_on_window_end: bool = False

    def get_policy_hash(self) -> str:
        """Deterministic SHA256 of the ExitPolicy parameters."""
        data = asdict(self)
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["policy_hash"] = self.get_policy_hash()
        return d


class BaseStrategy(ABC):
    """Abstract Base Class for all pluggable trading strategies.

    Principles:
    - Strategy determines signals; Simulator determines execution; Analyzer determines metrics.
    - Strategy ONLY reads past and present candle data up to decision time (strictly no lookahead).
    - Strategy NEVER calls exchange APIs, never accesses live accounts, and never matches orders.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique identifier of the strategy (e.g. '20FINAL_BASELINE' or '20FINAL')."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Version of the strategy (e.g. '1' or '1.0.0')."""
        pass

    @property
    @abstractmethod
    def required_timeframes(self) -> list[str]:
        """List of required timeframes (e.g. ['15m', '1h'])."""
        pass

    def get_signal_spec_hash(self) -> str:
        """Deterministic SHA256 of strategy_core source + signal-affecting parameters."""
        return canonical_json_sha256({"strategy_id": self.strategy_id, "version": self.version, "config": self.config})

    def get_exit_spec_hash(self) -> str:
        """Deterministic SHA256 of exit policies across all combos."""
        return canonical_json_sha256({"strategy_id": self.strategy_id, "version": self.version, "config": self.config})

    def get_spec_hash(self) -> str:
        """Combined SHA256 of signal_spec_hash, exit_spec_hash, strategy_id, and version."""
        return compute_spec_hash(self.get_signal_spec_hash(), self.get_exit_spec_hash(), self.strategy_id, self.version)

    def get_config_hash(self) -> str:
        """Deterministic SHA256 of the full strategy specification (delegates to get_spec_hash)."""
        return self.get_spec_hash()

    @abstractmethod
    def calculate_indicators(self, data: dict[str, pd.DataFrame]) -> dict[str, Any]:
        """Calculate indicators for required timeframes without future data lookahead."""
        pass

    @abstractmethod
    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate entry signals from data. Decisions strictly made at bar close."""
        pass

    @abstractmethod
    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        """Retrieve exit policy for specific symbol and combo."""
        pass

    @abstractmethod
    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        """Retrieve static parameters for specific combo."""
        pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "required_timeframes": self.required_timeframes,
            "signal_spec_hash": self.get_signal_spec_hash(),
            "exit_spec_hash": self.get_exit_spec_hash(),
            "spec_hash": self.get_spec_hash(),
            "config_hash": self.get_config_hash(),
            "config": self.config,
        }
