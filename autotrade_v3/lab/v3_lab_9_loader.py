"""Official loader for V3_LAB_9@9.0.0."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from autotrade_v3.strategy.v3_lab_9 import V3Lab9Strategy

V3_LAB_9_STRATEGY_ID = "V3_LAB_9"
V3_LAB_9_ACTIVE_VERSION = "9.0.0"


def load_v3_lab_9_strategy(params_dir: Path | None = None, version: str = V3_LAB_9_ACTIVE_VERSION) -> V3Lab9Strategy:
    return V3Lab9Strategy(params_dir=params_dir, version=version)


def load_v3_lab_9_config(params_dir: Path | None = None, version: str = V3_LAB_9_ACTIVE_VERSION) -> dict[str, Any]:
    return load_v3_lab_9_strategy(params_dir=params_dir, version=version).config
