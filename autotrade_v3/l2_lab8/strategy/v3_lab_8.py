"""Self-contained official V3_LAB_8@8.0.0 pure candle-only strategy (30 combos, Fixed TP/SL).

Consolidates all Lab upgrades (V3_LAB_2..V3_LAB_8 + Batch C P2) into a single
self-contained strategy class inheriting directly from Baseline20FinalStrategy,
ready to mirror Live deployment:
- Base 20 combos + 10 cloned pairs = 30 combos (Fixed TP/SL, trailing OFF).
- ETH-USDT/C1  (id=1):   H1_BOTH_1
- BTC-USDT/C1  (id=1):   H2_BUY_VOLUME_1_8 (BUY vol > 1.8*SMA20, SELL 1.5*SMA20)
- BTC-USDT/C2  (id=2):   H1_SELL_ONLY
- BTC-USDT/C4  (id=4):   H1_BUY_ONLY
- ETH-USDT/C6  (id=6):   R2_V4_COMBINED (BUY atr_ratio_50>=1.2, SELL CCI<-100 + atr_ratio_50>=0.9)
- BTC-USDT/C7  (id=7):   H1_BUY_ONLY (BODY1.6_VOL2.0, OFF)
- ETH-USDT/C7  (id=7):   R2_V2_ASYM_BUY_VOL20_SELL_TREND (BUY vol_mult>=2.0, SELL ema_trend_1h<=0.0)
- ETH-USDT/C10 (id=10):  H3_ASYMMETRIC (BUY SMC_VETO, SELL SMC_STRICT)
- BTC-USDT/C11 (id=101): H1_BUY_0_78 (DeMarker(60) BUY>0.78 / SELL<0.25)
- ETH-USDT/C11 (id=101): H3_B1_DEMARKER (DeMarker(60) BUY>0.78 / SELL<0.20)
- ETH-USDT/C12 (id=102): V3_ATR065_NO_EXT24H (atr_pct>=0.65, ret_24h<=2.20, SELL close_4h<ema100_4h)
- BTC-USDT/C13 (id=103): H4_SELL_ONLY_4H_TREND
- ETH-USDT/C13 (id=103): H5_SELL_ONLY_4H_TREND
- BTC-USDT/C14 (id=104): H3a_KUMO_BAND_V1 ([0.005, 0.010))
- ETH-USDT/C14 (id=104): A-R2-05_ATR_SL_ADJUSTMENT (Kumo band + clamped 1H ATR SL)
- ETH-USDT/C15 (id=105): V5_SELL_ONLY_ATR140 (SELL-only, atr_ratio_50<=1.40)
- BTC-USDT/C16 (id=106): H3_VORTEX_RATIO_1_22
- Remaining 12 combos: unchanged 20FINAL_FIXED_V1@1 H0.
"""
from __future__ import annotations

from collections.abc import Collection
import copy
import dataclasses
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.l2_lab8.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.l2_lab8.engine.signal_engine import Signal, signal_from_event
from autotrade_v3.l2_lab8.filters.base import FilterContext
from autotrade_v3.l2_lab8.filters.c12_v3_atr065_no_ext24h import (
    C12_COMBO_ID,
    C12_SYMBOL,
    C12V3Atr065NoExt24hFilter,
    build_c12_1h_features_lookup,
)
from autotrade_v3.l2_lab8.filters.c14_h3a_kumo_band import C14H3aKumoBandFilter
from autotrade_v3.l2_lab8.filters.c15_v5_sell_atr140 import C15V5SellAtr140Filter
from autotrade_v3.l2_lab8.filters.c6_r2_v4_combined import (
    C6_COMBO_ID,
    C6_SYMBOL,
    C6R2V4CombinedFilter,
    build_c6_atr_ratio_lookup,
    generate_c6_cci_audit_sell_signals,
)
from autotrade_v3.l2_lab8.filters.c7_r2_v2_asym import (
    C7_COMBO_ID,
    C7_SYMBOL,
    C7R2V2AsymFilter,
    build_c7_features_lookup,
)
from autotrade_v3.l2_lab8.strategy.base import ExitPolicy
from autotrade_v3.l2_lab8.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.l2_lab8.strategy.eth_c1_h1_both_1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
    EthC1H1Both1Strategy,
)
from autotrade_v3.l2_lab8.strategy_core.api import ComboSpec, SignalEvent, Strategy, StrategySpec
from autotrade_v3.l2_lab8.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.l2_lab8.strategy_core.indicators.indicators import atr, ema
from autotrade_v3.l2_lab8.strategy_core.indicators.layer3_long import edge_event, precompute
from autotrade_v3.l2_lab8.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.l2_lab8.strategy_core.indicators.smc_ob import OBConfig, ob_context
from autotrade_v3.l2_lab8.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.l2_lab8.strategy_core.indicators.two_trail_layer12 import approved
from autotrade_v3.l2_lab8.strategy_core.registry import StrategyCoreRegistry
from autotrade_v3.l2_lab8.strategy_core.strategies.final20.logic import _ichimoku, _make_signal_event, _vortex

H1_MS = 3_600_000
H4_MS = 14_400_000
M15_MS = 900_000


def _index_utc_open_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    idx_utc = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return np.asarray(idx_utc.as_unit("ms").asi8, dtype=np.int64)


def _build_v3_lab_8_core(params_dir: Path | None = None, version: str = "8.0.0") -> Strategy:
    """Build self-contained 30-combo Fixed TP/SL core for V3_LAB_8."""
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
                combos.append(
                    dataclasses.replace(
                        copy.deepcopy(src),
                        symbol=tgt,
                        combo_key=f"{tgt}/{src.combo_name}",
                    )
                )

    if len(combos) != 30:
        raise RuntimeError(f"Expected 30 combos in V3_LAB_8 core, got {len(combos)}")

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
        elif c.symbol == "BTC-USDT" and int(c.combo_id) == 101:
            new_entry = dict(c.entry)
            new_entry["indicator"] = "DeMarker"
            new_entry["period"] = 60
            new_entry["long_min"] = 0.78
            new_entry["short_max"] = 0.25
            c = dataclasses.replace(c, entry=new_entry)
        elif c.symbol == "BTC-USDT" and int(c.combo_id) == 106:
            new_entry = dict(c.entry)
            new_entry["indicator"] = "Vortex"
            new_entry["period"] = 14
            new_entry["ratio"] = 1.22
            c = dataclasses.replace(c, entry=new_entry)
        updated.append(c)

    class _V3Lab8Core(Strategy):
        def __init__(self, inner: Strategy, new_combos: list[ComboSpec]) -> None:
            inner_copy: Any = copy.deepcopy(inner)
            self._inner: Any = inner_copy
            self._spec = dataclasses.replace(
                inner_copy.spec(),
                strategy_id="V3_LAB_8",
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

    return _V3Lab8Core(base_core, updated)


class V3Lab8Strategy(Baseline20FinalStrategy):
    """Self-contained official V3_LAB_8@8.0.0 strategy (single-file wiring for all 30 combos)."""

    strategy_id: str = "V3_LAB_8"
    version: str = "8.0.0"
    SUPPORTED_VERSIONS = ("8.0.0",)

    def __init__(self, params_dir: Path | None = None, version: str = "8.0.0") -> None:
        if version not in self.SUPPORTED_VERSIONS:
            raise ValueError(f"Unsupported V3_LAB_8 version {version!r}; expected {self.SUPPORTED_VERSIONS}")
        self._version_value = version
        core30 = _build_v3_lab_8_core(params_dir=params_dir, version=version)
        super().__init__(
            core_strategy=core30,
            strategy_id_override=self.strategy_id,
            version_override=version,
        )
        self._h1_both_1 = EthC1H1Both1Strategy(
            enable_eth_c1_h1_l2=True,
            buy_max_slope_4h_pct=H1_BUY_MAX_SLOPE_4H_PCT,
            sell_min_dist_ema150_4h_pct=H1_SELL_MIN_DIST_EMA150_4H_PCT,
            pre_edge_h1=True,
        )

    def get_exit_policy(self, symbol: str, combo_id: int) -> ExitPolicy:
        if symbol == "ETH-USDT" and int(combo_id) == 1:
            ep = self._h1_both_1.get_exit_policy(symbol, combo_id)
        else:
            ep = super().get_exit_policy(symbol, combo_id)
        if ep.split_legs or float(ep.trailing_act_pct) != 0.0:
            raise ValueError(f"V3_LAB_8 requires Fixed TP/SL without trailing/split for {symbol}/{combo_id}: {ep}")
        return ep

    def get_signal_spec_hash(self) -> str:
        spec_dict: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version": self._version_value,
            "eth_c1": f"H1_BOTH_1({H1_BUY_MAX_SLOPE_4H_PCT},{H1_SELL_MIN_DIST_EMA150_4H_PCT})",
            "btc_c1": "H2_BUY_VOLUME_1_8",
            "eth_c6": "R2_V4_COMBINED(buy>=1.2,sell>=0.9)",
            "eth_c7": "R2_V2_ASYM_BUY_VOL20_SELL_TREND",
            "btc_c11": "H1_BUY_0_78",
            "eth_c11": "H3_B1_DEMARKER",
            "eth_c12": "V3_ATR065_NO_EXT24H",
            "btc_c14": "H3a_KUMO_BAND_V1",
            "eth_c14": "A-R2-05_ATR_SL_ADJUSTMENT",
            "eth_c15": "V5_SELL_ONLY_ATR140",
            "btc_c16": "H3_VORTEX_RATIO_1_22",
        }
        return canonical_json_sha256(spec_dict)

    def get_exit_spec_hash(self) -> str:
        spec = self._core.spec()
        policies = {
            c.combo_key: self.get_exit_policy(c.symbol, c.combo_id).to_dict()
            for c in sorted(spec.combos, key=lambda x: x.combo_key)
        }
        return canonical_json_sha256({"strategy_id": self.strategy_id, "version": self.version, "exit_policies": policies})

    def get_spec_hash(self) -> str:
        return compute_spec_hash(self.get_signal_spec_hash(), self.get_exit_spec_hash(), self.strategy_id, self.version)

    @staticmethod
    def _filter_eth_c10_asymmetric(signals: list[Signal], eth_m15: pd.DataFrame | None) -> list[Signal]:
        if eth_m15 is None or eth_m15.empty:
            return signals
        d15 = ensure_dt(eth_m15)
        smc15 = smc_direction(d15, 50, False)
        open_ms_arr = _index_utc_open_ms(pd.DatetimeIndex(smc15.index))
        smc_by_open_ms = {int(oms): int(val) for oms, val in zip(open_ms_arr, smc15.to_numpy(dtype=np.int64))}
        out: list[Signal] = []
        for s in signals:
            if s.symbol == "ETH-USDT" and int(s.combo_id) == 10 and s.side == "SELL":
                if smc_by_open_ms.get(int(s.signal_close_ms) + 1 - M15_MS, 0) != -1:
                    continue
            out.append(s)
        return out

    @staticmethod
    def _filter_4h_sell_downtrend(signals: list[Signal], sym_m15: pd.DataFrame | None, symbol: str, combo_id: int) -> list[Signal]:
        if sym_m15 is None or sym_m15.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL")]
        d4 = resample_ohlcv(ensure_dt(sym_m15), "4h")
        if d4.empty:
            return [s for s in signals if not (s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL")]
        ema100_4h = ema(d4["close"], 100)
        d4_close_ms = datetime_index_to_ms(d4.index) + H4_MS - 1
        d4_closes = d4["close"].to_numpy(dtype=float)
        d4_emas = ema100_4h.to_numpy(dtype=float)
        out: list[Signal] = []
        for s in signals:
            if s.symbol == symbol and int(s.combo_id) == combo_id and s.side == "SELL":
                idx4 = int(np.searchsorted(d4_close_ms, int(s.signal_close_ms), side="right")) - 1
                if idx4 < 0 or not (np.isfinite(d4_closes[idx4]) and np.isfinite(d4_emas[idx4]) and d4_closes[idx4] < d4_emas[idx4]):
                    continue
            out.append(s)
        return out

    @staticmethod
    def _filter_batch_a_one_sided(signals: list[Signal]) -> list[Signal]:
        drop_rules = {("BTC-USDT", 2, "BUY"), ("BTC-USDT", 4, "SELL"), ("BTC-USDT", 7, "SELL"), ("BTC-USDT", 103, "BUY"), ("ETH-USDT", 103, "BUY")}
        return [s for s in signals if (s.symbol, int(s.combo_id), s.side) not in drop_rules]

    @staticmethod
    def _filter_btc_c16_vortex_ratio(signals: list[Signal], btc_m15: pd.DataFrame | None, min_ratio: float = 1.22) -> list[Signal]:
        if btc_m15 is None or btc_m15.empty:
            return [s for s in signals if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 106)]
        d1 = resample_ohlcv(ensure_dt(btc_m15), "1h")
        if d1.empty:
            return [s for s in signals if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 106)]
        vi_p, vi_m = _vortex(d1["high"].to_numpy(dtype=float), d1["low"].to_numpy(dtype=float), d1["close"].to_numpy(dtype=float), 14)
        d1_close_ms = datetime_index_to_ms(d1.index) + H1_MS - 1
        vortex_by_ms = {int(d1_close_ms[i]): (float(vi_p[i]), float(vi_m[i])) for i in range(len(d1_close_ms))}
        out: list[Signal] = []
        for s in signals:
            if s.symbol == "BTC-USDT" and int(s.combo_id) == 106:
                vp, vm = vortex_by_ms.get(int(s.signal_close_ms), (np.nan, np.nan))
                if not (np.isfinite(vp) and np.isfinite(vm) and vp > 0.0 and vm > 0.0):
                    continue
                if (s.side == "BUY" and not ((vp / vm) > min_ratio)) or (s.side == "SELL" and not ((vm / vp) > min_ratio)):
                    continue
            out.append(s)
        return out

    @staticmethod
    def _apply_eth_c14_atr_sl(signals: list[Signal], eth_m15: pd.DataFrame | None) -> list[Signal]:
        kumo_by_ms: dict[int, float] = {}
        atr_pct_by_ms: dict[int, float] = {}
        if eth_m15 is not None and not eth_m15.empty:
            d1 = resample_ohlcv(ensure_dt(eth_m15), "1h")
            if not d1.empty:
                h1, l1, c1 = d1["high"].to_numpy(dtype=float), d1["low"].to_numpy(dtype=float), d1["close"].to_numpy(dtype=float)
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
                sl_pct = max(0.006, min(0.012, 1.0 * float(atr_pct_by_ms.get(cms, 0.009))))
                ep = ExitPolicy(tp_pct=0.02, sl_pct=sl_pct, split_legs=False, trailing_act_pct=0.0, trailing_cb_pct=0.0, leg_weights=(1.0,))
                out.append(dataclasses.replace(s, sl_pct=sl_pct, exit_policy=ep, exit_policy_hash=ep.get_policy_hash()))
            else:
                out.append(s)
        return out

    def _regenerate_btc_c1_buy_vol_1_8(self, btc_m15: pd.DataFrame, *, only_approved: bool, only_warmup_ready: bool) -> list[Signal]:
        d15 = ensure_dt(btc_m15).tz_convert("UTC")
        pc = precompute(d15, None)
        d1, p1 = pc["d1"], pc["1"]
        c_spec = next(c for c in self.spec().combos if c.symbol == "BTC-USDT" and int(c.combo_id) == 1)
        ep = self.get_exit_policy("BTC-USDT", 1)
        ep_hash = ep.get_policy_hash()
        warmup_bars = int(self.spec().warmup_bars)
        posmap = {ts: i for i, ts in enumerate(d15.index)}

        A21, R, M, ADX, K, D, C = p1["A21"], p1["R"], p1["M"], p1["ADX"], p1["K"], p1["D"], p1["C"]
        macd = ema(d1.close, 8) - ema(d1.close, 21)
        hist = macd - ema(macd, 9)
        volsp = d1.volume > p1["volma"] * 1.8
        adxr = ADX > ADX.shift(1)
        sb08 = (d1.close > d1.open) & ((d1.close - d1.open) > A21 * 0.8)
        tu = d1.close > p1["ema1504"]
        longc = (K > 30) & (K > D) & (C > 80) & (C > C.shift(1))
        lth = float(c_spec.entry.get("mfi_long", 50.0))
        L = edge_event(longc & (R > 55) & (hist > 0) & (M > lth) & adxr & sb08 & tu & volsp)
        smc_s = smc_direction(d1, 50, False)
        ob_df = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))
        offset = pd.Timedelta(minutes=45)

        out: list[Signal] = []
        for ot in L.index[L.fillna(False)]:
            last = ot + offset
            if last not in posmap:
                continue
            sd = int(smc_s.loc[ot])
            blocked = bool(ob_df.loc[ot, "buy_blocked"])
            ok = bool(approved("L", sd, blocked, c_spec.layer2))
            ev = _make_signal_event("BTC-USDT", c_spec, "L", ot, posmap[last], sd, blocked, ok, d15, warmup_bars)
            if (only_approved and not ev.tags["approved"]) or (only_warmup_ready and not ev.tags["warmup_ready"]):
                continue
            out.append(dataclasses.replace(signal_from_event(ev, c_spec), exit_policy=ep, exit_policy_hash=ep_hash))
        return out

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: Collection[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate all 30-combo V3_LAB_8 signals strictly from closed OHLCV bars."""
        sym_list = list(symbols) if symbols is not None else None
        base_sigs = super().generate_signals(
            data=data,
            symbols=sym_list,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )

        btc_m15 = data.get("BTC-USDT")
        eth_m15 = data.get("ETH-USDT")
        ctx = FilterContext()

        # BTC-C14 Kumo Band filter
        if btc_m15 is not None and not btc_m15.empty:
            h3a_flt = C14H3aKumoBandFilter.from_1h_dataframe(resample_ohlcv(ensure_dt(btc_m15), "1h"))
            kept = [s for s in base_sigs if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 1) and h3a_flt.evaluate(s, ctx).action == "KEEP"]
        else:
            kept = [s for s in base_sigs if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 1)]

        kept = self._filter_eth_c10_asymmetric(kept, eth_m15)
        kept = self._filter_4h_sell_downtrend(kept, eth_m15, "ETH-USDT", 102)
        kept = self._filter_batch_a_one_sided(kept)
        kept = self._filter_4h_sell_downtrend(kept, btc_m15, "BTC-USDT", 103)
        kept = self._filter_4h_sell_downtrend(kept, eth_m15, "ETH-USDT", 103)
        kept = self._filter_btc_c16_vortex_ratio(kept, btc_m15, min_ratio=1.22)
        kept = self._apply_eth_c14_atr_sl(kept, eth_m15)

        target_syms = set(symbols) if symbols is not None else set(data.keys())

        # BTC-C1 H2_BUY_VOLUME_1_8
        if "BTC-USDT" in target_syms and btc_m15 is not None and not btc_m15.empty:
            kept = [s for s in kept if not (s.symbol == "BTC-USDT" and int(s.combo_id) == 1 and s.side == "BUY")]
            kept.extend(self._regenerate_btc_c1_buy_vol_1_8(btc_m15, only_approved=only_approved, only_warmup_ready=only_warmup_ready))

        # ETH-C1 H1_BOTH_1
        if "ETH-USDT" in target_syms and eth_m15 is not None and not eth_m15.empty:
            h1_sigs = self._h1_both_1.generate_signals(data=data, symbols=["ETH-USDT"], only_approved=only_approved, only_warmup_ready=only_warmup_ready)
            kept.extend(s for s in h1_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 1)

            eth_dt = ensure_dt(eth_m15)
            eth_15m = resample_ohlcv(eth_dt, "15min")
            eth_1h = resample_ohlcv(eth_dt, "1h")

            # ETH-C15, ETH-C12, ETH-C7, ETH-C6 filters
            c15_flt = C15V5SellAtr140Filter.from_m15_dataframe(eth_m15)
            c12_ms, c12_atr, c12_ret = build_c12_1h_features_lookup(eth_1h)
            c12_flt = C12V3Atr065NoExt24hFilter(c12_ms, c12_atr, c12_ret)
            c15_ms, c7_vol, c7_1h_ms, c7_ema = build_c7_features_lookup(eth_15m, eth_1h)
            c7_flt = C7R2V2AsymFilter(c15_ms, c7_vol, c7_1h_ms, c7_ema)
            c6_ms, c6_atr_ratio = build_c6_atr_ratio_lookup(eth_1h)
            c6_flt = C6R2V4CombinedFilter(c6_ms, c6_atr_ratio)

            eth_filtered: list[Signal] = []
            for s in kept:
                if s.symbol == "ETH-USDT":
                    cid = int(s.combo_id)
                    dec = None
                    if cid == 105:
                        dec = c15_flt.evaluate(s, ctx)
                    elif cid == C12_COMBO_ID:
                        ok, reason = c12_flt.evaluate_signal(s)
                        if not ok:
                            if only_approved:
                                continue
                            s = Signal(**{**s.__dict__, "approved": False, "rejection_reason": reason})
                    elif cid == C7_COMBO_ID:
                        dec = c7_flt.evaluate_signal(s)
                    elif cid == C6_COMBO_ID:
                        dec = c6_flt.evaluate_signal(s)
                    if dec is not None and dec.action != "KEEP":
                        if only_approved:
                            continue
                        s = Signal(**{**s.__dict__, "approved": False, "rejection_reason": dec.reason})
                eth_filtered.append(s)
            kept = eth_filtered

            # ETH-C6 CCI-Audit SELL
            cfg_c6 = self.get_combo_config(C6_SYMBOL, C6_COMBO_ID)
            ep_c6 = self.get_exit_policy(C6_SYMBOL, C6_COMBO_ID)
            for s_sig in generate_c6_cci_audit_sell_signals(
                eth_dt,
                combo_name=str(cfg_c6.get("name", "C6")),
                tp_pct=float(cfg_c6["tp_pct"]),
                sl_pct=float(cfg_c6["sl_pct"]),
                strategy_id=self.strategy_id,
                exit_policy=ep_c6,
                exit_policy_hash=ep_c6.get_policy_hash(),
                warmup_end_ms=None,
            ):
                dec = c6_flt.evaluate_signal(s_sig)
                if dec.action != "KEEP":
                    if only_approved:
                        continue
                    s_sig = Signal(**{**s_sig.__dict__, "approved": False, "rejection_reason": dec.reason})
                kept.append(s_sig)

        kept.sort(key=lambda s: (int(s.signal_close_ms), s.symbol, int(s.combo_id), s.side))
        return kept

    def generate_signals_for_combos(
        self,
        data: dict[str, pd.DataFrame],
        *,
        allowed_combos: Collection[tuple[str, int]] | None = None,
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate V3_LAB_8 signals restricted to an optional ``allowed_combos`` subset."""
        if allowed_combos is None:
            return self.generate_signals(data=data, symbols=symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        allowed = {(str(sym), int(cid)) for sym, cid in allowed_combos}
        if not allowed:
            return []
        allowed_syms = {sym for sym, _ in allowed}
        eff_symbols = [sym for sym in (symbols if symbols is not None else data.keys()) if sym in allowed_syms]
        if not eff_symbols:
            return []
        sigs = self.generate_signals(data=data, symbols=eff_symbols, only_approved=only_approved, only_warmup_ready=only_warmup_ready)
        return [s for s in sigs if (s.symbol, int(s.combo_id)) in allowed]
