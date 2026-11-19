"""Official versioned pure candle-only strategy implementation for V3_LAB_5 (30 combos, Fixed TP/SL).

Configuration (V3_LAB_5@5.0.0):
- Inherits all 30 combo-symbol pairs from V3_LAB_4@4.1.1 (`20FINAL_FIXED_V1@1` + 10 cloned pairs).
- Leaves 29/30 combos strictly identical in logic and Fixed TP/SL parameters to V3_LAB_4@4.1.1.
- Replaces ONLY `ETH-USDT/C15` (`combo_id=105`) `H3_ASYMMETRIC` with `V5_SELL_ONLY_ATR140`:
  * Timeframe: 1H (strictly closed OHLCV bars resampled from M15)
  * BUY: DISABLED
  * SELL: KAMA_ER(40, 2, 40, er_min=0.4) + Layer 2 OB + ATR(14)_1H / SMA(ATR(14)_1H, 50) <= 1.40
  * Exit policy: Fixed TP 2.0%, SL 0.8%, no trailing, no post-TP cooldown.
- Strictly pure L2 package code: ZERO imports from `scripts.*`, ZERO stateful controller
  (`H3AsymmetricController`), ZERO trade/fill/position/DB/simulator/L3/L4 dependencies.
"""
from __future__ import annotations

from collections.abc import Collection
import copy
import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.base import FilterContext
from autotrade_v3.filters.c14_h3a_kumo_band import C14H3aKumoBandFilter
from autotrade_v3.filters.c15_v5_sell_atr140 import (
    C15_V5_ATR_PERIOD,
    C15_V5_ATR_RATIO_MAX,
    C15_V5_ATR_SMA_PERIOD,
    C15_V5_VARIANT_NAME,
    C15V5SellAtr140Filter,
)
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.eth_c1_h1_both_1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
    EthC1H1Both1Strategy,
)
from autotrade_v3.strategy_core.api import ComboSpec, SignalEvent, Strategy, StrategySpec
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.indicators import atr, ema
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry
from autotrade_v3.strategy_core.strategies.final20.logic import _ichimoku, _vortex

H1_MS = 3_600_000
H4_MS = 14_400_000
M15_MS = 900_000


def _build_v3_lab_5_core(params_dir: Path | None = None, version: str = "5.0.0") -> Strategy:
    """Build 30-combo Fixed TP/SL core for V3_LAB_5 preserving all 20FINAL + R1/Batch-A params."""
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
        raise RuntimeError(f"Expected 30 combos in V3_LAB_5 core, got {len(combos)}")

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

    class _V3Lab5Core(Strategy):
        def __init__(self, inner: Strategy, new_combos: list[ComboSpec]) -> None:
            inner_copy: Any = copy.deepcopy(inner)
            self._inner: Any = inner_copy
            self._spec = dataclasses.replace(
                inner_copy.spec(),
                strategy_id="V3_LAB_5",
                version=version,
                combos=tuple(new_combos),
            )
            setattr(inner_copy, "_spec", self._spec)

        def spec(self) -> StrategySpec:
            return self._spec

        def generate(
            self,
            bars: dict[str, pd.DataFrame] | pd.DataFrame,
            symbols: list[str] | None = None,
            only_approved: bool = True,
            only_warmup_ready: bool = True,
        ) -> list[SignalEvent]:
            return list(
                self._inner.generate(
                    bars=bars,
                    symbols=symbols,
                    only_approved=only_approved,
                    only_warmup_ready=only_warmup_ready,
                )
            )

        def on_bar_close(
            self,
            state: Any,
            bar: Any,
            only_approved: bool = True,
            only_warmup_ready: bool = True,
        ) -> list[SignalEvent]:
            return list(
                self._inner.on_bar_close(
                    state=state,
                    bar=bar,
                    only_approved=only_approved,
                    only_warmup_ready=only_warmup_ready,
                )
            )

    return _V3Lab5Core(base_core, updated)


def _index_utc_open_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    """Convert DatetimeIndex cleanly to UTC epoch ms without precision loss."""
    idx_utc = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return np.asarray(idx_utc.as_unit("ms").asi8, dtype=np.int64)


class V3Lab5Strategy(Baseline20FinalStrategy):
    """V3_LAB_5@5.0.0: pure candle-only 30-combo Fixed TP/SL strategy with ETH-USDT/C15 V5_SELL_ONLY_ATR140."""

    STRATEGY_ID = "V3_LAB_5"
    VERSION = "5.0.0"
    SUPPORTED_VERSIONS = ("5.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "5.0.0") -> None:
        if version not in self.SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported V3_LAB_5 version {version!r}; expected one of {self.SUPPORTED_VERSIONS}")
        self._version_value = version
        core30 = _build_v3_lab_5_core(params_dir=params_dir, version=version)
        super().__init__(
            core_strategy=core30,
            strategy_id_override=self.STRATEGY_ID,
            version_override=version,
        )
        self._h1_both_1 = EthC1H1Both1Strategy(
            enable_eth_c1_h1_l2=True,
            buy_max_slope_4h_pct=H1_BUY_MAX_SLOPE_4H_PCT,
            sell_min_dist_ema150_4h_pct=H1_SELL_MIN_DIST_EMA150_4H_PCT,
            pre_edge_h1=True,
        )

    def build_c14_h3a_filter(self, df_1h: pd.DataFrame) -> C14H3aKumoBandFilter:
        """Build the locked BTC-USDT/C14 H3a V1 Kumo thickness band filter."""
        return C14H3aKumoBandFilter.from_1h_dataframe(df_1h)

    def build_c15_v5_filter(self, df_eth_m15: pd.DataFrame | None) -> C15V5SellAtr140Filter:
        """Build the pure candle-only ETH-USDT/C15 V5_SELL_ONLY_ATR140 filter from closed M15 bars."""
        return C15V5SellAtr140Filter.from_m15_dataframe(df_eth_m15)

    def evaluate_c15_v5_decisions(
        self,
        signals: list[Signal],
        eth_m15: pd.DataFrame | None,
    ) -> tuple[list[Signal], list[tuple[Signal, str]]]:
        """Evaluate C15 V5 filter over signals, returning ``(kept, [(dropped_sig, reason), ...])``."""
        c15_flt = self.build_c15_v5_filter(eth_m15)
        ctx = FilterContext()
        kept: list[Signal] = []
        dropped: list[tuple[Signal, str]] = []
        for s in signals:
            dec = c15_flt.evaluate(s, ctx)
            if dec.action == "KEEP":
                kept.append(s)
            else:
                dropped.append((s, dec.reason or "BLOCKED_V5"))
        return kept, dropped

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        if symbol == "ETH-USDT" and int(combo_id) == 1:
            ep = self._h1_both_1.get_exit_policy(symbol, combo_id)
        else:
            ep = super().get_exit_policy(symbol, combo_id)
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f"V3_LAB_5 requires Fixed TP/SL without trailing/split for {symbol}/{combo_id}: {ep}")
        return ep

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "base_strategy": "20FINAL_FIXED_V1@1+CLONED_30",
            "parent_baseline": "V3_LAB_4@4.1.1",
            "strategy_id": self.STRATEGY_ID,
            "version": self._version_value,
            "eth_c1_override": (
                f"H1_BOTH_1(buy_max_slope_4h_pct={H1_BUY_MAX_SLOPE_4H_PCT},"
                f"sell_cci9<-80,sell_min_dist_ema150_4h_pct={H1_SELL_MIN_DIST_EMA150_4H_PCT},pre_edge_h1=True)"
            ),
            "btc_c14_override": "BTC-USDT_C14_H3a_KUMO_BAND_V1(0.005<=abs(span_a-span_b)/close<0.010,locked=True)",
            "eth_c15_override": (
                f"{C15_V5_VARIANT_NAME}(buy=OFF,"
                f"sell=KAMA_ER(40,2,40,er_min=0.4)+layer2=OB+"
                f"atr({C15_V5_ATR_PERIOD})_1h/sma(atr({C15_V5_ATR_PERIOD})_1h,{C15_V5_ATR_SMA_PERIOD})<={C15_V5_ATR_RATIO_MAX:.2f},"
                "tp=0.020,sl=0.008,cooldown=0,candle_only=True)"
            ),
            "eth_c10_override": "H3_ASYMMETRIC(adx>28,buy=SMC_VETO,sell=SMC_STRICT,tp=0.018,sl=0.009)",
            "eth_c11_override": "H3_B1_DEMARKER(period=60,long_min=0.78,short_max=0.20,layer2=SMC_STRICT_OB,tp=0.018,sl=0.009)",
            "eth_c12_override": "H7_SELL_4H_TREND_ONLY(aroon=40,th=70,layer2=SMC_VETO_OB,sell_4h_filter=close_4h<ema100_4h,tp=0.018,sl=0.009)",
            "btc_c2_override": "H1_SELL_ONLY(buy=OFF,sell=H0_ADX>20,layer2=SMC_VETO_OB,tp=0.012,sl=0.008)",
            "btc_c4_override": "H1_BUY_ONLY(buy=H0_ADX>23,sell=OFF,layer2=SMC_VETO,tp=0.018,sl=0.009)",
            "btc_c7_override": "H1_BUY_ONLY(buy=H0_BODY1.6_VOL2.0,sell=OFF,layer2=OFF,tp=0.020,sl=0.008)",
            "btc_c13_override": "H4_SELL_ONLY_4H_TREND(buy=OFF,sell=CMO(40)_cross_down_-40+close_4h<ema100_4h,layer2=OFF,tp=0.020,sl=0.008)",
            "eth_c13_override": "H5_SELL_ONLY_4H_TREND(buy=OFF,sell=CMO(40)_cross_down_-40+close_4h<ema100_4h,layer2=OFF,tp=0.020,sl=0.008)",
            "btc_c16_override": "H3_VORTEX_RATIO_1_22(vortex=14,ratio=1.22,layer2=OB,tp=0.011,sl=0.009)",
            "eth_c14_override": "A-R2-05_ATR_SL_ADJUSTMENT(kumo_band=[0.005,0.010),tp=0.020,sl=clamp(1.0*ATR14_1H/Close_1H,0.006,0.012))",
            "c15_v5_filter_config_hash": C15V5SellAtr140Filter({}).config_hash(),
        }
        return canonical_json_sha256(spec_dict)

    def get_exit_spec_hash(self) -> str:
        spec = self._core.spec()
        policies = {
            c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict()
            for c in sorted(spec.combos, key=lambda x: x.combo_key)
        }
        return canonical_json_sha256(
            {
                "strategy_id": self.strategy_id,
                "version": self.version,
                "exit_policies": policies,
                "dynamic_sl_overrides": {
                    "ETH-USDT/C14": "clamp(1.0*ATR14_1H/Close_1H,0.006,0.012)",
                },
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
    def _filter_eth_c10_asymmetric(signals: list[Signal], eth_m15: pd.DataFrame | None) -> list[Signal]:
        """Enforce SELL = SMC_STRICT (smc_dir == -1) on ETH-USDT/C10 while keeping BUY = SMC_VETO."""
        if eth_m15 is None or eth_m15.empty:
            return signals
        d15 = ensure_dt(eth_m15)
        smc15 = smc_direction(d15, 50, False)
        open_ms_arr = _index_utc_open_ms(pd.DatetimeIndex(smc15.index))
        smc_by_open_ms = {int(oms): int(val) for oms, val in zip(open_ms_arr, smc15.to_numpy(dtype=np.int64))}

        out: list[Signal] = []
        for s in signals:
            if s.symbol == "ETH-USDT" and int(s.combo_id) == 10 and s.side == "SELL":
                open_ms = int(s.signal_close_ms) + 1 - M15_MS
                if smc_by_open_ms.get(open_ms, 0) != -1:
                    continue
            out.append(s)
        return out

    @staticmethod
    def _filter_4h_sell_downtrend(
        signals: list[Signal],
        sym_m15: pd.DataFrame | None,
        symbol: str,
        combo_id: int,
    ) -> list[Signal]:
        """Enforce closed-4H downtrend (close_4h < ema100_4h) on SELL signals for (symbol, combo_id)."""
        if sym_m15 is None or sym_m15.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL")]
        d15 = ensure_dt(sym_m15)
        d4 = resample_ohlcv(d15, "4h")
        if d4.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL")]
        ema100_4h = ema(d4["close"], 100)
        d4_close_ms = datetime_index_to_ms(d4.index) + H4_MS - 1
        d4_closes = d4["close"].to_numpy(dtype=float)
        d4_emas = ema100_4h.to_numpy(dtype=float)

        out: list[Signal] = []
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
    def _filter_batch_a_one_sided(signals: list[Signal]) -> list[Signal]:
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
        signals: list[Signal],
        btc_m15: pd.DataFrame | None,
        min_ratio: float = 1.22,
    ) -> list[Signal]:
        """Enforce Vortex(14) ratio > 1.22 on both BUY and SELL for BTC-USDT/C16 while keeping Layer 2 OB."""
        if btc_m15 is None or btc_m15.empty:
            return [s for s in signals if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 106)]
        d1 = resample_ohlcv(ensure_dt(btc_m15), "1h")
        if d1.empty:
            return [s for s in signals if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 106)]
        vi_p, vi_m = _vortex(
            d1["high"].to_numpy(dtype=float),
            d1["low"].to_numpy(dtype=float),
            d1["close"].to_numpy(dtype=float),
            14,
        )
        d1_close_ms = datetime_index_to_ms(d1.index) + H1_MS - 1
        vortex_by_ms = {
            int(d1_close_ms[i]): (float(vi_p[i]), float(vi_m[i]))
            for i in range(len(d1_close_ms))
        }

        out: list[Signal] = []
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

    @staticmethod
    def _apply_eth_c14_atr_sl(signals: list[Signal], eth_m15: pd.DataFrame | None) -> list[Signal]:
        """Apply A-R2-05 (ETH-USDT/C14 Kumo band + closed-1H ATR14/Close SL clamping)."""
        kumo_by_ms: dict[int, float] = {}
        atr_pct_by_ms: dict[int, float] = {}
        if eth_m15 is not None and not eth_m15.empty:
            d1 = resample_ohlcv(ensure_dt(eth_m15), "1h")
            if not d1.empty:
                h1 = d1["high"].to_numpy(dtype=float)
                l1 = d1["low"].to_numpy(dtype=float)
                c1 = d1["close"].to_numpy(dtype=float)
                _, _, span_a, span_b = _ichimoku(h1, l1, 12, 30, 60)
                a14 = atr(d1, 14).to_numpy(dtype=float)
                d1_close_ms = datetime_index_to_ms(d1.index) + H1_MS - 1
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
                sl_pct = max(0.006, min(0.012, 1.0 * float(ap)))
                ep = ExitPolicy(
                    tp_pct=0.02,
                    sl_pct=sl_pct,
                    split_legs=False,
                    trailing_act_pct=0.0,
                    trailing_cb_pct=0.0,
                    leg_weights=(1.0,),
                )
                out.append(dataclasses.replace(s, sl_pct=sl_pct, exit_policy=ep, exit_policy_hash=ep.get_policy_hash()))
            else:
                out.append(s)
        return out

    def _apply_c15_v5_filter(
        self,
        signals: list[Signal],
        eth_m15: pd.DataFrame | None,
        *,
        only_approved: bool,
    ) -> list[Signal]:
        """Apply pure candle-only ETH-USDT/C15 V5_SELL_ONLY_ATR140 gate."""
        has_c15 = any(s.symbol == "ETH-USDT" and int(s.combo_id) == 105 for s in signals)
        if not has_c15:
            return signals
        c15_flt = self.build_c15_v5_filter(eth_m15)
        ctx = FilterContext()
        out: list[Signal] = []
        for s in signals:
            if s.symbol == "ETH-USDT" and int(s.combo_id) == 105:
                dec = c15_flt.evaluate(s, ctx)
                if dec.action != "KEEP":
                    if only_approved:
                        continue
                    out.append(dataclasses.replace(s, approved=False))
                    continue
            out.append(s)
        return out

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate 30-combo pure candle-only V3_LAB_5 signals strictly from closed OHLCV bars."""
        base_sigs = super().generate_signals(
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        btc_m15 = data.get("BTC-USDT")
        eth_m15 = data.get("ETH-USDT")
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
        kept_base = self._apply_eth_c14_atr_sl(kept_base, eth_m15)
        kept_base = self._apply_c15_v5_filter(kept_base, eth_m15, only_approved=only_approved)

        target_symbols = list(symbols) if symbols is not None else list(data.keys())
        eth_c1_sigs: list[Signal] = []
        if "ETH-USDT" in target_symbols and "ETH-USDT" in data:
            h1_sigs = self._h1_both_1.generate_signals(
                data=data,
                symbols=["ETH-USDT"],
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )
            eth_c1_sigs = [s for s in h1_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 1]

        combined = kept_base + eth_c1_sigs
        combined.sort(key=lambda s: (s.signal_close_ms, s.symbol, int(s.combo_id), s.side))
        return combined

    def generate_signals_for_combos(
        self,
        data: dict[str, pd.DataFrame],
        *,
        allowed_combos: Collection[tuple[str, int]] | None = None,
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate V3_LAB_5 signals restricted to an optional ``allowed_combos`` subset."""
        if allowed_combos is None:
            return self.generate_signals(
                data=data,
                symbols=symbols,
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )

        allowed = {(str(sym), int(cid)) for sym, cid in allowed_combos}
        if not allowed:
            return []

        allowed_syms = {sym for sym, _ in allowed}
        if symbols is not None:
            eff_symbols = [sym for sym in symbols if sym in allowed_syms]
        else:
            eff_symbols = [sym for sym in data.keys() if sym in allowed_syms]
        if not eff_symbols:
            return []

        sigs = self.generate_signals(
            data=data,
            symbols=eff_symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        return [s for s in sigs if (s.symbol, int(s.combo_id)) in allowed]
