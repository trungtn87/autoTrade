"""Official loader for V3_LAB_11@11.0.0."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autotrade_v3.strategy.v3_lab_11 import V3Lab11Strategy

V3_LAB_11_STRATEGY_ID = "V3_LAB_11"
V3_LAB_11_ACTIVE_VERSION = "11.0.0"


def load_v3_lab_11_strategy(params_dir: Path | None = None, version: str = V3_LAB_11_ACTIVE_VERSION) -> V3Lab11Strategy:
    return V3Lab11Strategy(params_dir=params_dir, version=version)


def load_v3_lab_11_config(params_dir: Path | None = None, version: str = V3_LAB_11_ACTIVE_VERSION) -> dict[str, Any]:
    return load_v3_lab_11_strategy(params_dir=params_dir, version=version).config
