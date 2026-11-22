"""Official loader for V3_LAB_11_PRUNE23@11.0.0."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy

V3_LAB_11_PRUNE23_STRATEGY_ID = "V3_LAB_11_PRUNE23"
V3_LAB_11_PRUNE23_ACTIVE_VERSION = "11.0.0"


def load_v3_lab_11_prune23_strategy(
    params_dir: Path | None = None,
    version: str = V3_LAB_11_PRUNE23_ACTIVE_VERSION,
) -> V3Lab11Prune23Strategy:
    return V3Lab11Prune23Strategy(params_dir=params_dir, version=version)


def load_v3_lab_11_prune23_config(
    params_dir: Path | None = None,
    version: str = V3_LAB_11_PRUNE23_ACTIVE_VERSION,
) -> dict[str, Any]:
    return load_v3_lab_11_prune23_strategy(params_dir=params_dir, version=version).config
