"""Base interfaces and contracts for Strategy Engine in AutoTrade V3 Research Lab."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Literal
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal


@dataclass(frozen=True)
class ExitPolicy:
    """Exit policy defining TP, SL, and Trailing parameters for a combo or strategy."""
    tp_pct: float
    sl_pct: float
    split_legs: bool = True  # True for 50% TP + 50% Trailing (FINAL14); False for 100% hard single (NEW6)
    trailing_act_pct: float = 0.005  # 0.5% favorable move to activate trailing
    trailing_cb_pct: float = 0.005   # 0.5% callback to trigger stop
    max_bars_held: int | None = None
    force_close_on_window_end: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        """Unique identifier of the strategy (e.g. '20FINAL_BASELINE')."""
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version of the strategy (e.g. '1.0.0')."""
        pass

    @property
    @abstractmethod
    def required_timeframes(self) -> list[str]:
        """List of required timeframes (e.g. ['15m', '1h'])."""
        pass

    def get_config_hash(self) -> str:
        """Deterministic SHA256 of the strategy configuration."""
        raw = json.dumps(
            {"strategy_id": self.strategy_id, "version": self.version, "config": self.config},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
            "config_hash": self.get_config_hash(),
            "config": self.config,
        }
