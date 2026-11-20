"""Official loader for V3_LAB_10@10.0.0."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autotrade_v3.strategy.v3_lab_10 import V3Lab10Strategy

V3_LAB_10_STRATEGY_ID = "V3_LAB_10"
V3_LAB_10_ACTIVE_VERSION = "10.0.0"


def load_v3_lab_10_strategy(params_dir: Path | None = None, version: str = V3_LAB_10_ACTIVE_VERSION) -> V3Lab10Strategy:
    return V3Lab10Strategy(params_dir=params_dir, version=version)


def load_v3_lab_10_config(params_dir: Path | None = None, version: str = V3_LAB_10_ACTIVE_VERSION) -> dict[str, Any]:
    return load_v3_lab_10_strategy(params_dir=params_dir, version=version).config
