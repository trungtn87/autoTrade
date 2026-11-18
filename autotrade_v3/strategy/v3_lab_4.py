"""Official versioned strategy implementation for V3_LAB_4 (30 combos, Fixed TP/SL).

Configuration:
- Base 30 combos: 20FINAL_FIXED_V1@1 (20 base + 10 cloned cross-symbol pairs, all Fixed TP/SL)
- 5 finalized upgraded combos:
  1. ETH-USDT/C1  (combo_id=1):   H1_BOTH_1 (TP=2.0%, SL=0.666667%)
  2. BTC-USDT/C14 (combo_id=104): BTC-USDT_C14_H3a_KUMO_BAND_V1 (TP=2.0%, SL=0.666667%, LOCKED)
  3. ETH-USDT/C15 (combo_id=105): H3_ASYMMETRIC (BUY dist_kama_atr<=2.0; SELL 60m post-TP cooldown via controller)
  4. ETH-USDT/C10 (combo_id=10):  H3_ASYMMETRIC (BUY: ADX>28 + SMC_VETO; SELL: ADX>28 + SMC_STRICT; TP=1.8%, SL=0.9%)
  5. ETH-USDT/C11 (combo_id=101): H3_B1_DEMARKER (DeMarker(60) BUY>0.78 / SELL<0.20, SMC_STRICT_OB, TP=1.8%, SL=0.9%)
- BTC-USDT/C10 (combo_id=10) and remaining 24 combos: unchanged 20FINAL_FIXED_V1@1 H0.
"""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any

import pandas as pd

from autotrade_v3.filters.base import FilterContext
from autotrade_v3.filters.c14_h3a_kumo_band import C14H3aKumoBandFilter
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy_core.api import ComboSpec, StrategySpec
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
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


def _build_v3_lab_4_core(params_dir: Path | None = None) -> Any:
    """Build 30-combo Fixed TP/SL core with ETH-USDT/C10 configured for H3_ASYMMETRIC."""
    base_core = StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1", params_dir=params_dir)
    base_spec = base_core.spec()
    combos = [copy.deepcopy(c) for c in base_spec.combos]
    existing = {(c.combo_id, c.symbol) for c in combos}

    by_id: dict[int, list[ComboSpec]] = {}
    for c in combos:
        by_id.setdefault(c.combo_id, []).append(c)

    for cid in sorted(by_id):
        if len(by_id[cid]) == 1:
            src = by_id[cid][0]
            tgt = "ETH-USDT" if src.symbol == "BTC-USDT" else "BTC-USDT"
            if (cid, tgt) not in existing:
                cloned = dataclasses.replace(
                    copy.deepcopy(src),
                    symbol=tgt,
                    combo_key=f"{tgt}/{src.combo_name}",
                )
                combos.append(cloned)

    if len(combos) != 30:
        raise RuntimeError(f"Expected 30 combos in V3_LAB_4 core, got {len(combos)}")

    # Configure ETH-USDT/C10 base layer2=SMC_VETO (SELL SMC_STRICT enforced in generate_signals)
    # and ETH-USDT/C11 H3_B1_DEMARKER (DeMarker(60) BUY>0.78 / SELL<0.20, SMC_STRICT_OB kept).
    updated: list[ComboSpec] = []
    for c in combos:
        if c.symbol == "ETH-USDT" and int(c.combo_id) == 10:
            c = dataclasses.replace(c, layer2="SMC_VETO")
        elif c.symbol == "ETH-USDT" and int(c.combo_id) == 101:
            new_entry = dict(c.entry)
            new_entry["indicator"] = "DeMarker"
            new_entry["period"] = 60
            new_entry["long_min"] = 0.78
            new_entry["short_max"] = 0.20
            c = dataclasses.replace(c, entry=new_entry)
        updated.append(c)

    class V3Lab4Core:
        def __init__(self, inner: Any, new_combos: list[ComboSpec]) -> None:
            self._inner = copy.deepcopy(inner)
            self._spec = dataclasses.replace(
                self._inner.spec(),
                strategy_id="V3_LAB_4",
                version="4.0.0",
                combos=tuple(new_combos),
            )
            self._inner._spec = self._spec

        def spec(self) -> StrategySpec:
            return self._spec

        def generate(self, bars: Any, symbols: Any = None, only_approved: bool = True, only_warmup_ready: bool = True):
            return self._inner.generate(
                bars=bars,
                symbols=symbols,
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )

        def on_bar_close(self, state: Any, bar: Any, only_approved: bool = True, only_warmup_ready: bool = True):
            return self._inner.on_bar_close(
                state=state,
                bar=bar,
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )

    return V3Lab4Core(base_core, updated)


class V3Lab4Strategy(Baseline20FinalStrategy):
    """V3_LAB_4: 30-combo Fixed TP/SL strategy with 4 finalized upgraded combos."""

    STRATEGY_ID = "V3_LAB_4"
    VERSION = "4.0.0"

    def __init__(self, params_dir: Path | None = None) -> None:
        core30 = _build_v3_lab_4_core(params_dir=params_dir)
        super().__init__(
            core_strategy=core30,
            strategy_id_override=self.STRATEGY_ID,
            version_override=self.VERSION,
        )
        self._h1_both_1 = _load_p3_c1_h1_both_1()

    def build_c14_h3a_filter(self, df_1h: pd.DataFrame) -> C14H3aKumoBandFilter:
        """Build the locked BTC-USDT/C14 H3a V1 Kumo thickness band filter."""
        return C14H3aKumoBandFilter.from_1h_dataframe(df_1h)

    def build_c15_h3_controller(self, df_eth_m15: pd.DataFrame) -> Any:
        """Build the ETH-USDT/C15 H3_ASYMMETRIC chronological admission controller."""
        from scripts.run_v3_lab_2_eth_c15_h2 import build_eth_1h_l2_features
        from scripts.run_v3_lab_2_eth_c15_h3_asymmetric import H3AsymmetricController

        feat_map = build_eth_1h_l2_features(df_eth_m15)
        return H3AsymmetricController(feat_map)

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        if symbol == "ETH-USDT" and int(combo_id) == 1:
            ep = self._h1_both_1.get_exit_policy(symbol, combo_id)
        else:
            ep = super().get_exit_policy(symbol, combo_id)
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f"V3_LAB_4 requires Fixed TP/SL without trailing/split for {symbol}/{combo_id}: {ep}")
        return ep

    def get_signal_spec_hash(self) -> str:
        return canonical_json_sha256(
            {
                "base_strategy": "20FINAL_FIXED_V1@1+CLONED_30",
                "strategy_id": self.STRATEGY_ID,
                "version": self.VERSION,
                "eth_c1_override": "H1_BOTH_1(buy_max_slope_4h_pct=1.5,sell_cci9<-80,sell_min_dist_ema150_4h_pct=-6.0,pre_edge_h1=True)",
                "btc_c14_override": "BTC-USDT_C14_H3a_KUMO_BAND_V1(0.005<=abs(span_a-span_b)/close<0.010,locked=True)",
                "eth_c15_override": "H3_ASYMMETRIC(buy_dist_kama_atr<=2.0,sell_post_tp_cooldown_60m)",
                "eth_c10_override": "H3_ASYMMETRIC(adx>28,buy=SMC_VETO,sell=SMC_STRICT,tp=0.018,sl=0.009)",
                "eth_c11_override": "H3_B1_DEMARKER(period=60,long_min=0.78,short_max=0.20,layer2=SMC_STRICT_OB,tp=0.018,sl=0.009)",
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
    def _filter_eth_c10_asymmetric(signals: list[Any], eth_m15: pd.DataFrame | None) -> list[Any]:
        """Enforce SELL = SMC_STRICT (smc_dir == -1) on ETH-USDT/C10 while keeping BUY = SMC_VETO."""
        if eth_m15 is None or eth_m15.empty:
            return signals
        d15 = ensure_dt(eth_m15)
        smc15 = smc_direction(d15, 50, False)
        smc_by_open_ms = {int(ts.timestamp() * 1000): int(val) for ts, val in smc15.items()}

        out = []
        for s in signals:
            if s.symbol == "ETH-USDT" and int(s.combo_id) == 10 and s.side == "SELL":
                open_ms = int(s.signal_close_ms) + 1 - 900_000
                if smc_by_open_ms.get(open_ms, 0) != -1:
                    continue
            out.append(s)
        return out

    def generate_signals(
        self,
        df_m15_by_symbol: dict[str, pd.DataFrame],
        **kwargs: Any,
    ):
        base_sigs = super().generate_signals(df_m15_by_symbol, **kwargs)

        btc_m15 = df_m15_by_symbol.get("BTC-USDT")
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

        kept_base = self._filter_eth_c10_asymmetric(kept_base, df_m15_by_symbol.get("ETH-USDT"))

        eth_kwargs = dict(kwargs)
        eth_kwargs["symbols"] = ["ETH-USDT"]
        h1_sigs = self._h1_both_1.generate_signals(df_m15_by_symbol, **eth_kwargs)
        eth_c1_sigs = [s for s in h1_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 1]

        combined = kept_base + eth_c1_sigs
        combined.sort(key=lambda s: (s.signal_close_ms, s.symbol, int(s.combo_id), s.side))
        return combined
