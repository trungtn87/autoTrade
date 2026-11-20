"""Official loader for V3_LAB_7@7.0.0."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autotrade_v3.strategy.v3_lab_7 import V3Lab7Strategy

V3_LAB_7_STRATEGY_ID = "V3_LAB_7"
V3_LAB_7_ACTIVE_VERSION = "7.0.0"


def load_v3_lab_7_strategy(params_dir: Path | None = None, version: str = V3_LAB_7_ACTIVE_VERSION) -> V3Lab7Strategy:
    return V3Lab7Strategy(params_dir=params_dir, version=version)


def load_v3_lab_7_config(params_dir: Path | None = None, version: str = V3_LAB_7_ACTIVE_VERSION) -> dict[str, Any]:
    return load_v3_lab_7_strategy(params_dir=params_dir, version=version).config
