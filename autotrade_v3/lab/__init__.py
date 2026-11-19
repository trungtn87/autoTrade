"""Research Lab Core package for AutoTrade V3."""
from autotrade_v3.lab.experiment import ExperimentResult, ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, RunManifest
from autotrade_v3.lab.v3_lab_2_loader import (
    DEFAULT_RESEARCH_STRATEGY_ID,
    load_v3_lab_2_config,
    load_v3_lab_2_strategy,
)
from autotrade_v3.lab.v3_lab_4_loader import (
    V3_LAB_4_ACTIVE_VERSION,
    V3_LAB_4_BASELINE_VERSION,
    V3_LAB_4_STRATEGY_ID,
    load_v3_lab_4_config,
    load_v3_lab_4_strategy,
)

__all__ = [
    "DEFAULT_RESEARCH_STRATEGY_ID",
    "V3_LAB_4_ACTIVE_VERSION",
    "V3_LAB_4_BASELINE_VERSION",
    "V3_LAB_4_STRATEGY_ID",
    "ExperimentRunner",
    "ExperimentResult",
    "RunManager",
    "RunManifest",
    "load_v3_lab_2_config",
    "load_v3_lab_2_strategy",
    "load_v3_lab_4_config",
    "load_v3_lab_4_strategy",
]
