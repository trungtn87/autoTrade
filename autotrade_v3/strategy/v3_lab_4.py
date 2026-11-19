"""Official versioned strategy implementation for V3_LAB_4 (30 combos, Fixed TP/SL).

Configuration:
- Base 30 combos: 20FINAL_FIXED_V1@1 (20 base + 10 cloned cross-symbol pairs, all Fixed TP/SL)
- 6 finalized upgraded combos (pre-Batch A):
  1. ETH-USDT/C1  (combo_id=1):   H1_BOTH_1 (TP=2.0%, SL=0.666667%)
  2. BTC-USDT/C14 (combo_id=104): BTC-USDT_C14_H3a_KUMO_BAND_V1 (TP=2.0%, SL=0.666667%, LOCKED)
  3. ETH-USDT/C15 (combo_id=105): H3_ASYMMETRIC (BUY dist_kama_atr<=2.0; SELL 60m post-TP cooldown via controller)
  4. ETH-USDT/C10 (combo_id=10):  H3_ASYMMETRIC (BUY: ADX>28 + SMC_VETO; SELL: ADX>28 + SMC_STRICT; TP=1.8%, SL=0.9%)
  5. ETH-USDT/C11 (combo_id=101): H3_B1_DEMARKER (DeMarker(60) BUY>0.78 / SELL<0.20, SMC_STRICT_OB, TP=1.8%, SL=0.9%)
  6. ETH-USDT/C12 (combo_id=102): H7_SELL_4H_TREND_ONLY (Aroon(40)>=70, SMC_VETO_OB, SELL close_4h<ema100_4h, TP=1.8%, SL=0.9%)
- 6 Batch A P3 upgraded combos:
  7. BTC-USDT/C2  (combo_id=2):   H1_SELL_ONLY (BUY disabled, SELL ADX>20 + SMC_VETO_OB, 1H, TP=1.2%, SL=0.8%)
  8. BTC-USDT/C4  (combo_id=4):   H1_BUY_ONLY (SELL disabled, BUY ADX>23 + SMC_VETO, 1H, TP=1.8%, SL=0.9%)
  9. BTC-USDT/C7  (combo_id=7):   H1_BUY_ONLY (SELL disabled, BUY body_atr=1.6 + vol_mult=2.0 + Layer2=OFF, 15m, TP=2.0%, SL=0.8%)
 10. BTC-USDT/C13 (combo_id=103): H4_SELL_ONLY_4H_TREND (BUY disabled, SELL CMO(40) cross_down -40 + close_4h<ema100_4h, 1H, TP=2.0%, SL=0.8%)
 11. ETH-USDT/C13 (combo_id=103): H5_SELL_ONLY_4H_TREND (BUY disabled, SELL CMO(40) cross_down -40 + close_4h<ema100_4h, 1H, TP=2.0%, SL=0.8%)
 12. BTC-USDT/C16 (combo_id=106): H3_VORTEX_RATIO_1_22 (Vortex(14) ratio=1.22 both sides, Layer2=OB, 1H, TP=1.1%, SL=0.9%)
- Remaining 18 combos (including BTC-USDT/C10, BTC-USDT/C12): unchanged 20FINAL_FIXED_V1@1 H0.
"""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.filters.base import FilterContext
from autotrade_v3.filters.c14_h3a_kumo_band import C14H3aKumoBandFilter
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy_core.api import ComboSpec, StrategySpec
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.indicators import ema
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry
from autotrade_v3.strategy_core.strategies.final20.logic import _vortex


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
        elif c.symbol == "BTC-USDT" and int(c.combo_id) == 106:
            new_entry = dict(c.entry)
            new_entry["indicator"] = "Vortex"
            new_entry["period"] = 14
            new_entry["ratio"] = 1.22
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
                "eth_c12_override": "H7_SELL_4H_TREND_ONLY(aroon=40,th=70,layer2=SMC_VETO_OB,sell_4h_filter=close_4h<ema100_4h,tp=0.018,sl=0.009)",
                "btc_c2_override": "H1_SELL_ONLY(buy=OFF,sell=H0_ADX>20,layer2=SMC_VETO_OB,tp=0.012,sl=0.008)",
                "btc_c4_override": "H1_BUY_ONLY(buy=H0_ADX>23,sell=OFF,layer2=SMC_VETO,tp=0.018,sl=0.009)",
                "btc_c7_override": "H1_BUY_ONLY(buy=H0_BODY1.6_VOL2.0,sell=OFF,layer2=OFF,tp=0.020,sl=0.008)",
                "btc_c13_override": "H4_SELL_ONLY_4H_TREND(buy=OFF,sell=CMO(40)_cross_down_-40+close_4h<ema100_4h,layer2=OFF,tp=0.020,sl=0.008)",
                "eth_c13_override": "H5_SELL_ONLY_4H_TREND(buy=OFF,sell=CMO(40)_cross_down_-40+close_4h<ema100_4h,layer2=OFF,tp=0.020,sl=0.008)",
                "btc_c16_override": "H3_VORTEX_RATIO_1_22(vortex=14,ratio=1.22,layer2=OB,tp=0.011,sl=0.009)",
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

    @staticmethod
    def _filter_4h_sell_downtrend(
        signals: list[Any],
        sym_m15: pd.DataFrame | None,
        symbol: str,
        combo_id: int,
    ) -> list[Any]:
        """Enforce closed-4H downtrend (close_4h < ema100_4h) on SELL signals for (symbol, combo_id)."""
        if sym_m15 is None or sym_m15.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL")]
        d15 = ensure_dt(sym_m15)
        d4 = resample_ohlcv(d15, "4h")
        if d4.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL")]
        ema100_4h = ema(d4["close"], 100)
        d4_close_ms = datetime_index_to_ms(d4.index) + 14_400_000 - 1
        d4_closes = d4["close"].to_numpy(float)
        d4_emas = ema100_4h.to_numpy(float)

        out = []
        for s in signals:
            if s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL":
                sig_c_ms = int(s.signal_close_ms)
                idx4 = int(np.searchsorted(d4_close_ms, sig_c_ms, side="right")) - 1
                if idx4 < 0:
                    continue
                c4 = float(d4_closes[idx4])
                e4 = float(d4_emas[idx4])
                if not (np.isfinite(c4) and np.isfinite(e4) and (c4 < e4)):
                    continue
            out.append(s)
        return out

    @staticmethod
    def _filter_batch_a_one_sided(signals: list[Any]) -> list[Any]:
        """Enforce Batch A P3 one-sided directional locks (C2 SELL_ONLY, C4/C7 BUY_ONLY, BTC/ETH C13 SELL_ONLY)."""
        drop_rules = {
            ("BTC-USDT", 2, "BUY"),
            ("BTC-USDT", 4, "SELL"),
            ("BTC-USDT", 7, "SELL"),
            ("BTC-USDT", 103, "BUY"),
            ("ETH-USDT", 103, "BUY"),
        }
        return [s for s in signals if (s.symbol, int(s.combo_id), s.side) not in drop_rules]

    @staticmethod
    def _filter_btc_c16_vortex_ratio(
        signals: list[Any],
        btc_m15: pd.DataFrame | None,
        min_ratio: float = 1.22,
    ) -> list[Any]:
        """Enforce Vortex(14) ratio > 1.22 on both BUY and SELL for BTC-USDT/C16 while keeping Layer 2 OB."""
        if btc_m15 is None or btc_m15.empty:
            return [s for s in signals if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 106)]
        d1 = resample_ohlcv(ensure_dt(btc_m15), "1h")
        if d1.empty:
            return [s for s in signals if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 106)]
        vi_p, vi_m = _vortex(
            d1["high"].to_numpy(float),
            d1["low"].to_numpy(float),
            d1["close"].to_numpy(float),
            14,
        )
        d1_close_ms = datetime_index_to_ms(d1.index) + 3_600_000 - 1
        vortex_by_ms = {
            int(d1_close_ms[i]): (float(vi_p[i]), float(vi_m[i]))
            for i in range(len(d1_close_ms))
        }

        out = []
        for s in signals:
            if s.symbol == "BTC-USDT" and int(s.combo_id) == 106:
                vp, vm = vortex_by_ms.get(int(s.signal_close_ms), (np.nan, np.nan))
                if not (np.isfinite(vp) and np.isfinite(vm) and vp > 0.0 and vm > 0.0):
                    continue
                if s.side == "BUY" and not ((vp / vm) > min_ratio):
                    continue
                if s.side == "SELL" and not ((vm / vp) > min_ratio):
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
        eth_m15 = df_m15_by_symbol.get("ETH-USDT")
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

        kept_base = self._filter_eth_c10_asymmetric(kept_base, eth_m15)
        kept_base = self._filter_4h_sell_downtrend(kept_base, eth_m15, "ETH-USDT", 102)
        kept_base = self._filter_batch_a_one_sided(kept_base)
        kept_base = self._filter_4h_sell_downtrend(kept_base, btc_m15, "BTC-USDT", 103)
        kept_base = self._filter_4h_sell_downtrend(kept_base, eth_m15, "ETH-USDT", 103)
        kept_base = self._filter_btc_c16_vortex_ratio(kept_base, btc_m15, min_ratio=1.22)

        eth_kwargs = dict(kwargs)
        eth_kwargs["symbols"] = ["ETH-USDT"]
        h1_sigs = self._h1_both_1.generate_signals(df_m15_by_symbol, **eth_kwargs)
        eth_c1_sigs = [s for s in h1_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 1]

        combined = kept_base + eth_c1_sigs
        combined.sort(key=lambda s: (s.signal_close_ms, s.symbol, int(s.combo_id), s.side))
        return combined
