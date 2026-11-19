"""Official loader for V3_LAB_8@8.0.0."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autotrade_v3.strategy.v3_lab_8 import V3Lab8Strategy

V3_LAB_8_STRATEGY_ID = "V3_LAB_8"
V3_LAB_8_ACTIVE_VERSION = "8.0.0"


def load_v3_lab_8_strategy(params_dir: Path | None = None, version: str = V3_LAB_8_ACTIVE_VERSION) -> V3Lab8Strategy:
    return V3Lab8Strategy(params_dir=params_dir, version=version)


def load_v3_lab_8_config(params_dir: Path | None = None, version: str = V3_LAB_8_ACTIVE_VERSION) -> dict[str, Any]:
    return load_v3_lab_8_strategy(params_dir=params_dir, version=version).config
