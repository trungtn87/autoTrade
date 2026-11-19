"""Official versioned strategy implementation for V3_LAB_2 (20 combos, Fixed TP/SL).

Configuration:
- Base 20 combos: 20FINAL_FIXED_V1@1 (11 BTC-USDT + 9 ETH-USDT, Fixed TP/SL, no trailing/split/breakeven)
- ETH-USDT/C1 (combo_id=1): H1_BOTH_1 (BUY slope_10bar_4h <= 1.5%, SELL CCI9 < -80 & dist_ema150_4h >= -6.0%,
  pre_edge_h1=True, TP=2%, SL=0.666667%)
- BTC-USDT/C14 (combo_id=104): BTC-USDT_C14_H3a_KUMO_BAND_V1 (Ichimoku 1H 12/30/60, tkcross_12_30_60, Layer 2 OB,
  0.005 <= abs(SpanA - SpanB)/Close < 0.010, TP=2%, SL=0.666667%, LOCKED)
- Remaining 18 combos: unchanged 20FINAL_FIXED_V1@1
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext
from autotrade_v3.filters.c14_h3a_kumo_band import C14H3aKumoBandFilter
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


def _load_p3_c1_h1_both_1():
    """Instantiate the exact H1_BOTH_1 implementation from scripts/p3_c1_research_strategy.py."""
    from scripts.p3_c1_research_strategy import P3C1ResearchStrategy

    return P3C1ResearchStrategy(
        variant_id="H1_BOTH_1",
        buy_max_slope_4h_pct=1.5,
        sell_min_dist_ema150_4h_pct=-6.0,
        atr_tpsl_config=None,
    )


class V3Lab2Strategy(Baseline20FinalStrategy):
    """V3_LAB_2: 20-combo Fixed TP/SL strategy with ETH/C1=H1_BOTH_1 and BTC/C14=H3a V1."""

    STRATEGY_ID = "V3_LAB_2"
    VERSION = "2.0.0"

    def __init__(self, params_dir: Path | None = None) -> None:
        core_strat = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1", params_dir=params_dir)
        base_spec = core_strat.spec()
        if len(base_spec.combos) != 20:
            raise RuntimeError(f"Expected 20 combos in 20FINAL_FIXED_V1@1, got {len(base_spec.combos)}")

        spec_v2 = dataclasses.replace(
            base_spec,
            strategy_id=self.STRATEGY_ID,
            version=self.VERSION,
        )
        setattr(core_strat, "_spec", spec_v2)

        super().__init__(
            core_strategy=core_strat,
            strategy_id_override=self.STRATEGY_ID,
            version_override=self.VERSION,
        )
        self._h1_both_1 = _load_p3_c1_h1_both_1()

    def build_c14_h3a_filter(self, df_1h: pd.DataFrame) -> C14H3aKumoBandFilter:
        """Build the locked BTC-USDT/C14 H3a V1 Kumo thickness band filter."""
        return C14H3aKumoBandFilter.from_1h_dataframe(df_1h)

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        if symbol == "ETH-USDT" and int(combo_id) == 1:
            ep = self._h1_both_1.get_exit_policy(symbol, combo_id)
        else:
            ep = super().get_exit_policy(symbol, combo_id)
        # Enforce Fixed TP/SL across all 20 combos: no trailing, no split legs
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f"V3_LAB_2 requires Fixed TP/SL without trailing/split for {symbol}/{combo_id}: {ep}")
        return ep

    def get_signal_spec_hash(self) -> str:
        return canonical_json_sha256(
            {
                "base_strategy": "20FINAL_FIXED_V1@1",
                "strategy_id": self.STRATEGY_ID,
                "version": self.VERSION,
                "eth_c1_override": "H1_BOTH_1(buy_max_slope_4h_pct=1.5,sell_cci9<-80,sell_min_dist_ema150_4h_pct=-6.0,pre_edge_h1=True)",
                "btc_c14_override": "BTC-USDT_C14_H3a_KUMO_BAND_V1(0.005<=abs(span_a-span_b)/close<0.010,locked=True)",
            }
        )

    def get_spec_hash(self) -> str:
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    def generate_signals(
        self,
        data: dict[str, Any],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        # 1. Generate base 20FINAL_FIXED_V1 signals for all combos except ETH-USDT/C1
        base_sigs = super().generate_signals(
            data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        # 2. Apply H3a V1 Kumo Band filter to BTC-USDT/C14 signals
        btc_m15 = data.get("BTC-USDT")
        if btc_m15 is not None and not btc_m15.empty:
            btc_1h = resample_ohlcv(ensure_dt(btc_m15), "1h")
            h3a_flt = self.build_c14_h3a_filter(btc_1h)
            ctx = FilterContext()
            kept_base = [
                s
                for s in base_sigs
                if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 1)
                and h3a_flt.evaluate(s, ctx).action == "KEEP"
            ]
        else:
            kept_base = [s for s in base_sigs if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 1)]

        # 3. Generate ETH-USDT/C1 signals via H1_BOTH_1
        h1_sigs = self._h1_both_1.generate_signals(
            data,
            symbols=["ETH-USDT"],
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        eth_c1_sigs = [s for s in h1_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 1]

        combined = kept_base + eth_c1_sigs
        combined.sort(key=lambda s: (s.signal_close_ms, s.symbol, int(s.combo_id)))
        return combined
