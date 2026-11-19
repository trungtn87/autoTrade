"""V3_LAB_4 Top 23 Research Handoff Strategy (L2 Signal Generation for V3 Live Integration).

Configuration:
- 23 combos (12 BTC-USDT + 11 ETH-USDT) selected from V3_LAB_4_TOP23_RESEARCH_V1:
  - 15 upgraded combos from V3_LAB_4@4.0.0 (PR #144, 1b82b43)
  - 1 upgraded combo from Batch B R2 (d11e37d): ETH-USDT/C14 A-R2-05_ATR_SL_ADJUSTMENT
  - 7 unchanged H0 combos (BTC-C6, BTC-C9, BTC-C10, BTC-TIER, ETH-C3, ETH-C4, ETH-C6)
- Excludes 7 negative combos: BTC-C3, BTC-C12, BTC-C15, ETH-C2, ETH-C9, ETH-C16, ETH-TIER.
- Preserves H3AsymmetricController for ETH-USDT/C15 and H3_ASYMMETRIC for ETH-USDT/C10.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy
from autotrade_v3.strategy_core.api import StrategySpec
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.indicators import atr
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.strategies.final20.logic import _ichimoku

TOP23_COMBO_KEYS: frozenset[str] = frozenset(
    {
        "BTC-USDT/C1", "BTC-USDT/C2", "BTC-USDT/C4", "BTC-USDT/C6",
        "BTC-USDT/C7", "BTC-USDT/C9", "BTC-USDT/C10", "BTC-USDT/C11",
        "BTC-USDT/C13", "BTC-USDT/C14", "BTC-USDT/C16", "BTC-USDT/TIER",
        "ETH-USDT/C1", "ETH-USDT/C3", "ETH-USDT/C4", "ETH-USDT/C6",
        "ETH-USDT/C7", "ETH-USDT/C10", "ETH-USDT/C11", "ETH-USDT/C12",
        "ETH-USDT/C13", "ETH-USDT/C14", "ETH-USDT/C15",
    }
)


def _combo_key(symbol: str, combo_id: int) -> str:
    cid = int(combo_id)
    if cid == 11:
        return f"{symbol}/TIER"
    if cid >= 100:
        return f"{symbol}/C{cid - 90}"
    return f"{symbol}/C{cid}"


class V3Lab4Top23Strategy(V3Lab4Strategy):
    """23-combo Top23 handoff strategy inheriting V3_LAB_4@4.0.0 + ETH-USDT/C14 A-R2-05."""

    STRATEGY_ID = "V3_LAB_4_TOP23"
    VERSION = "1.0.0"

    def __init__(self, params_dir: Path | None = None) -> None:
        super().__init__(params_dir=params_dir)
        base_spec = super().spec()
        combos23 = tuple(c for c in base_spec.combos if c.combo_key in TOP23_COMBO_KEYS)
        if len(combos23) != 23:
            raise RuntimeError(f"Expected 23 combos in V3_LAB_4_TOP23, got {len(combos23)}")
        self._top23_spec = dataclasses.replace(
            base_spec,
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
            combos=combos23,
        )

    def spec(self) -> StrategySpec:
        return self._top23_spec

    def get_signal_spec_hash(self) -> str:
        return canonical_json_sha256(
            {
                "base_v3_lab_4_signal_hash": super().get_signal_spec_hash(),
                "strategy_id": self.STRATEGY_ID,
                "version": self.VERSION,
                "top23_combos": sorted(TOP23_COMBO_KEYS),
                "eth_c14_override": "A-R2-05_ATR_SL_ADJUSTMENT(kumo_band=[0.005,0.010),tp=0.020,sl=clamp(1.0*ATR14_1H/Close_1H,0.006,0.012))",
            }
        )

    def get_spec_hash(self) -> str:
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    @staticmethod
    def _apply_eth_c14_a_r2_05(
        signals: list[Signal],
        eth_m15: pd.DataFrame | None,
    ) -> list[Signal]:
        """Apply exact A-R2-05_ATR_SL_ADJUSTMENT (d11e37d) for ETH-USDT/C14 using closed 1H bar."""
        if eth_m15 is None or eth_m15.empty:
            return [s for s in signals if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 104)]
        d1 = resample_ohlcv(ensure_dt(eth_m15), "1h")
        if d1.empty:
            return [s for s in signals if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 104)]

        h1 = d1["high"].to_numpy(float)
        l1 = d1["low"].to_numpy(float)
        c1 = d1["close"].to_numpy(float)
        _, _, span_a, span_b = _ichimoku(h1, l1, 12, 30, 60)
        a14 = atr(d1, 14).to_numpy(float)
        d1_close_ms = datetime_index_to_ms(d1.index) + 3_600_000 - 1

        kumo_by_ms: dict[int, float] = {}
        atr_pct_by_ms: dict[int, float] = {}
        for i in range(len(d1)):
            cms = int(d1_close_ms[i])
            if np.isfinite(span_a[i]) and np.isfinite(span_b[i]) and c1[i] > 0:
                kumo_by_ms[cms] = float(abs(span_a[i] - span_b[i]) / c1[i])
            if np.isfinite(a14[i]) and c1[i] > 0:
                atr_pct_by_ms[cms] = float(a14[i] / c1[i])

        out: list[Signal] = []
        for s in signals:
            if s.symbol == "ETH-USDT" and int(s.combo_id) == 104:
                cms = int(s.signal_close_ms)
                kp = kumo_by_ms.get(cms, np.nan)
                if not (np.isfinite(kp) and 0.005 <= kp < 0.010):
                    continue
                ap = atr_pct_by_ms.get(cms, 0.009)
                if not np.isfinite(ap):
                    ap = 0.009
                sl_pct = max(0.006, min(0.012, 1.0 * float(ap)))
                ep = ExitPolicy(
                    tp_pct=0.02,
                    sl_pct=sl_pct,
                    split_legs=False,
                    trailing_act_pct=0.0,
                    trailing_cb_pct=0.0,
                    leg_weights=(1.0,),
                )
                sl_px = float(s.entry_price) * (1.0 - sl_pct) if s.side == "BUY" else float(s.entry_price) * (1.0 + sl_pct)
                out.append(dataclasses.replace(s, sl_pct=sl_pct, sl_price=sl_px, exit_policy=ep, exit_policy_hash=ep.get_policy_hash()))
            else:
                out.append(s)
        return out

    def generate_signals(
        self,
        data: dict[str, Any],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        base_sigs = super().generate_signals(
            data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        sigs23 = [s for s in base_sigs if _combo_key(s.symbol, s.combo_id) in TOP23_COMBO_KEYS]
        return self._apply_eth_c14_a_r2_05(sigs23, data.get("ETH-USDT"))
