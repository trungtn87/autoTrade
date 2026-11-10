"""Strategy registry for dynamic strategy discovery and selection."""
from __future__ import annotations

from typing import Type
from autotrade_v3.strategy.base import BaseStrategy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy


class StrategyRegistry:
    """Registry maintaining available research strategies."""

    _strategies: dict[str, BaseStrategy] = {}

    @classmethod
    def register(cls, strategy: BaseStrategy) -> None:
        cls._strategies[strategy.strategy_id] = strategy

    @classmethod
    def get(cls, strategy_id: str) -> BaseStrategy:
        if strategy_id not in cls._strategies:
            # Fallback check for case-insensitive match
            for sid, inst in cls._strategies.items():
                if sid.lower() == strategy_id.lower():
                    return inst
            raise KeyError(
                f"Strategy '{strategy_id}' not found. Registered: {list(cls._strategies.keys())}"
            )
        return cls._strategies[strategy_id]

    @classmethod
    def list_strategies(cls) -> list[dict]:
        return [strat.to_dict() for strat in cls._strategies.values()]

    @classmethod
    def clear(cls) -> None:
        cls._strategies.clear()
        cls._register_defaults()

    @classmethod
    def _register_defaults(cls) -> None:
        cls.register(Baseline20FinalStrategy())


# Initialize defaults
StrategyRegistry._register_defaults()
