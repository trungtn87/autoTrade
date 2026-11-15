"""Strategy Engine package for AutoTrade V3."""
from autotrade_v3.strategy.base import BaseStrategy, ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.eth_c1_h1_both_1 import EthC1H1Both1Strategy
from autotrade_v3.strategy.registry import StrategyRegistry

__all__ = [
    "BaseStrategy",
    "ExitPolicy",
    "Baseline20FinalStrategy",
    "EthC1H1Both1Strategy",
    "StrategyRegistry",
]
