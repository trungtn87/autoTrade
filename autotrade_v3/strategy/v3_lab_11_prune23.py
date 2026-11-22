"""Official versioned pure candle-only strategy implementation for V3_LAB_11_PRUNE23 (23 combos, Fixed TP/SL).

Configuration (V3_LAB_11_PRUNE23@11.0.0):
- Inherits all upgrades and logic from V3_LAB_11@11.0.0 (including BTC-USDT/C1 H2_ATR_1.00,
  ETH-USDT/C1 H1_BOTH_1, BTC-USDT/C4 B1_VOL_CONFIRM, BTC-USDT/C7 B1_NO_CHASE_1H, etc.).
- Keeps the 23 positive PRUNE_7 combos (12 BTC-USDT + 11 ETH-USDT, including ETH-USDT/C1).
- Excludes the 7 unupgraded negative H0 cloned combos:
  BTC-USDT/C3 (3), BTC-USDT/C12 (102), BTC-USDT/C15 (105),
  ETH-USDT/C2 (2), ETH-USDT/C9 (9), ETH-USDT/TIER (11), ETH-USDT/C16 (106).
"""
from __future__ import annotations

from collections.abc import Collection
import dataclasses
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.v3_lab_11 import V3Lab11Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256

PRUNE23_EXCLUDED_COMBOS: frozenset[tuple[str, int]] = frozenset(
    {
        ("BTC-USDT", 3),    # BTC-USDT/C3
        ("BTC-USDT", 102),  # BTC-USDT/C12
        ("BTC-USDT", 105),  # BTC-USDT/C15
        ("ETH-USDT", 2),    # ETH-USDT/C2
        ("ETH-USDT", 9),    # ETH-USDT/C9
        ("ETH-USDT", 11),   # ETH-USDT/TIER
        ("ETH-USDT", 106),  # ETH-USDT/C16
    }
)


class V3Lab11Prune23Strategy(V3Lab11Strategy):
    """Official V3_LAB_11_PRUNE23@11.0.0 strategy (V3_LAB_11 PRUNE_7 23-combo portfolio)."""

    strategy_id: str = "V3_LAB_11_PRUNE23"
    version: str = "11.0.0"
    SUPPORTED_VERSIONS = ("11.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "11.0.0") -> None:
        super().__init__(params_dir=params_dir, version=version)
        kept_core_combos = tuple(
            c for c in self._core.spec().combos if (c.symbol, int(c.combo_id)) not in PRUNE23_EXCLUDED_COMBOS
        )
        if len(kept_core_combos) != 23:
            raise RuntimeError(f"Expected 23 combos in V3_LAB_11_PRUNE23, got {len(kept_core_combos)}")
        setattr(
            self._core,
            "_spec",
            dataclasses.replace(
                self._core.spec(),
                strategy_id=self.strategy_id,
                version=self._version_value,
                combos=kept_core_combos,
            ),
        )
        inner = getattr(self._core, "_inner", None)
        if inner is not None and hasattr(inner, "_spec"):
            setattr(inner, "_spec", self._core.spec())
        self.config["combos"] = [
            c
            for c in self.config["combos"]
            if (str(c["symbol"]), int(c["combo_id"])) not in PRUNE23_EXCLUDED_COMBOS
        ]
        self._combo_map = {
            (str(c["symbol"]), int(c["combo_id"])): c for c in self.config["combos"]
        }

    def get_signal_spec_hash(self) -> str:
        base_hash = super().get_signal_spec_hash()
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "parent_signal_spec_hash": base_hash,
            "excluded_combos": sorted([f"{sym}:{cid}" for sym, cid in PRUNE23_EXCLUDED_COMBOS]),
        }
        return canonical_json_sha256(spec_dict)

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate all 23-combo V3_LAB_11_PRUNE23 signals strictly from closed OHLCV bars."""
        sigs = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        return [s for s in sigs if (s.symbol, int(s.combo_id)) not in PRUNE23_EXCLUDED_COMBOS]
