"""Strategy Engine package for AutoTrade V3."""
from autotrade_v3.strategy.base import BaseStrategy, ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.eth_c1_h1_both_1 import EthC1H1Both1Strategy
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_2 import V3Lab2Strategy
from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy
from autotrade_v3.strategy.v3_lab_6 import V3Lab6Strategy
from autotrade_v3.strategy.v3_lab_7 import V3Lab7Strategy
from autotrade_v3.strategy.v3_lab_8 import V3Lab8Strategy

__all__ = [
    "BaseStrategy",
    "ExitPolicy",
    "Baseline20FinalStrategy",
    "EthC1H1Both1Strategy",
    "StrategyRegistry",
    "V3Lab2Strategy",
    "V3Lab5Strategy",
    "V3Lab6Strategy",
    "V3Lab7Strategy",
    "V3Lab8Strategy",
]
