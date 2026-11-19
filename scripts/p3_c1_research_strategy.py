"""P3 Research Strategy Adapter for ETH-USDT/C1 BUY & SELL A/B Backtests.

LOCAL RESEARCH ONLY:
- Subclasses C1SellCorrectedFixedTpSlStrategy (20FINAL_FIXED_TPSL_C1_SELL_CORR_VETO).
- Never modifies 20FINAL@1, 20final_v1.json, Golden Baseline, or live engines.
- Only alters ETH-USDT/C1 entry filters (H1) and/or per-signal fixed ATR21 TP/SL (H2),
  leaving all other 19 combos completely unchanged.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256, compute_spec_hash
from autotrade_v3.strategy_core.indicators.indicators import (
    atr,
    cci,
    dmi,
    edge_event,
    ema,
    mfi,
    rsi,
    sma,
    stochastic,
)
from autotrade_v3.strategy_core.indicators.signals import align_confirmed, ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_ob import OBConfig, ob_context
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.strategy_core.indicators.two_trail_layer12 import approved
from scripts.c1_sell_corrected_strategy import M15_MS, C1SellCorrectedFixedTpSlStrategy


def compute_eth_1h_4h_features(df_m15: pd.DataFrame) -> dict[str, Any]:
    """Compute causal 1H & confirmed 4H indicators for ETH-USDT from M15 OHLCV.

    All 4H features use `align_confirmed(..., d1.index, "4h")`, ensuring that at
    1H bar open `ot` (closing at `ot + 1h`), only 4H bars already closed at or
    before `ot` (never the forming 4H bar) are read.
    """
    d15 = ensure_dt(df_m15)
    d1 = resample_ohlcv(d15, "1h")
    d4 = resample_ohlcv(d15, "4h")

    a21 = atr(d1, 21)
    atr21_frac = a21 / d1.close
    atr21_pct = atr21_frac * 100.0

    ema150_4h_raw = ema(d4.close, 150)
    slope_10bar_raw = (ema150_4h_raw - ema150_4h_raw.shift(10)) / ema150_4h_raw.shift(10) * 100.0

    ema150_4h_1h = align_confirmed(ema150_4h_raw, d1.index, "4h")
    slope_10bar_1h = align_confirmed(slope_10bar_raw, d1.index, "4h")
    dist_ema150_4h_pct = (d1.close - ema150_4h_1h) / ema150_4h_1h * 100.0

    r14 = rsi(d1.close, 14)
    m14 = mfi(d1, 14)
    _, _, adx14 = dmi(d1, 14, 14)
    volma20 = sma(d1.volume, 20)
    k11 = stochastic(d1.close, d1.high, d1.low, 11)
    d3 = sma(k11, 3)
    c9 = cci(d1.close, d1.high, d1.low, 9)
    macd = ema(d1.close, 8) - ema(d1.close, 21)
    sig_m = ema(macd, 9)
    hist = macd - sig_m

    smc1 = smc_direction(d1, 50, False)
    ob1 = ob_context(d1, OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5))
    posmap = {ts: i for i, ts in enumerate(d15.index)}

    return {
        "d15": d15,
        "d1": d1,
        "d4": d4,
        "a21": a21,
        "atr21_frac": atr21_frac,
        "atr21_pct": atr21_pct,
        "ema150_4h_1h": ema150_4h_1h,
        "slope_10bar_1h": slope_10bar_1h,
        "dist_ema150_4h_pct": dist_ema150_4h_pct,
        "r14": r14,
        "m14": m14,
        "adx14": adx14,
        "volma20": volma20,
        "k11": k11,
        "d3": d3,
        "c9": c9,
        "hist": hist,
        "smc1": smc1,
        "ob1": ob1,
        "posmap": posmap,
    }


def compute_h2_tpsl_for_bar(
    atr21_frac: float,
    *,
    k_sl: float,
    min_sl: float,
    max_sl: float,
    rr_mult: float,
) -> tuple[float, float]:
    """Compute clipped SL and TP fraction from confirmed 1H ATR21 fraction."""
    if not np.isfinite(atr21_frac) or atr21_frac <= 0.0:
        raise ValueError(f"Invalid atr21_frac={atr21_frac!r} at signal bar")
    sl_pct = min(max(float(k_sl) * float(atr21_frac), float(min_sl)), float(max_sl))
    tp_pct = float(rr_mult) * sl_pct
    return round(tp_pct, 10), round(sl_pct, 10)


class P3C1ResearchStrategy(C1SellCorrectedFixedTpSlStrategy):
    """Research adapter for P3 H1 (4H EMA150 filters) & H2 (ATR21 TP/SL) on ETH-USDT/C1."""

    def __init__(
        self,
        *,
        variant_id: str = "BASELINE",
        buy_max_slope_4h_pct: float | None = None,
        sell_min_dist_ema150_4h_pct: float | None = None,
        atr_tpsl_config: dict[str, Any] | None = None,
        pre_edge_h1: bool = True,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.variant_id = str(variant_id)
        self.buy_max_slope_4h_pct = buy_max_slope_4h_pct
        self.sell_min_dist_ema150_4h_pct = sell_min_dist_ema150_4h_pct
        self.atr_tpsl_config = dict(atr_tpsl_config) if atr_tpsl_config is not None else None
        self.pre_edge_h1 = bool(pre_edge_h1)
        self._base_strategy_id = "20FINAL_FIXED_TPSL_C1_SELL_CORR_VETO"
        self._strategy_id_override = (
            self._base_strategy_id
            if self.variant_id == "BASELINE"
            else f"{self._base_strategy_id}_P3_{self.variant_id}"
        )
        self._version_override = "1.0.0"

    def _is_pure_baseline(self) -> bool:
        return (
            self.variant_id == "BASELINE"
            and self.buy_max_slope_4h_pct is None
            and self.sell_min_dist_ema150_4h_pct is None
            and self.atr_tpsl_config is None
        )

    def get_signal_spec_hash(self) -> str:
        if self._is_pure_baseline():
            return super().get_signal_spec_hash()
        return canonical_json_sha256(
            {
                "base_signal_spec_hash": super().get_signal_spec_hash(),
                "variant_id": self.variant_id,
                "buy_max_slope_4h_pct": self.buy_max_slope_4h_pct,
                "sell_min_dist_ema150_4h_pct": self.sell_min_dist_ema150_4h_pct,
                "pre_edge_h1": self.pre_edge_h1,
                "strategy_id": self._strategy_id_override,
            }
        )

    def get_exit_spec_hash(self) -> str:
        if self._is_pure_baseline():
            return super().get_exit_spec_hash()
        return canonical_json_sha256(
            {
                "base_exit_spec_hash": super().get_exit_spec_hash(),
                "variant_id": self.variant_id,
                "atr_tpsl_config": self.atr_tpsl_config,
            }
        )

    def get_spec_hash(self) -> str:
        if self._is_pure_baseline():
            return super().get_spec_hash()
        return compute_spec_hash(
            self.get_signal_spec_hash(),
            self.get_exit_spec_hash(),
            self.strategy_id,
            self.version,
        )

    def _atr_applies_to_side(self, side: str) -> bool:
        if not self.atr_tpsl_config:
            return False
        scope = str(self.atr_tpsl_config.get("scope", "")).upper()
        if scope == "BOTH":
            return True
        if scope == "BUY_ONLY" and side == "BUY":
            return True
        if scope == "SELL_ONLY" and side == "SELL":
            return True
        return False

    def _attach_atr_exit_policy(self, sig: Signal, ot: pd.Timestamp, features: dict[str, Any]) -> Signal:
        if not self._atr_applies_to_side(sig.side):
            return sig
        cfg = self.atr_tpsl_config
        assert cfg is not None
        atr21_frac = float(features["atr21_frac"].loc[ot])
        tp_pct, sl_pct = compute_h2_tpsl_for_bar(
            atr21_frac,
            k_sl=float(cfg["k_sl"]),
            min_sl=float(cfg.get("min_sl", 0.005)),
            max_sl=float(cfg.get("max_sl", 0.015)),
            rr_mult=float(cfg["rr_mult"]),
        )
        base_ep = sig.exit_policy or self.get_exit_policy(sig.symbol, sig.combo_id)
        sig_ep = ExitPolicy(
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            split_legs=False,
            trailing_act_pct=0.0,
            trailing_cb_pct=0.0,
            leg_weights=(1.0,),
            max_bars_held=base_ep.max_bars_held,
            force_close_on_window_end=bool(base_ep.force_close_on_window_end),
        )
        ep_hash = sig_ep.get_policy_hash()
        if sig.side == "BUY":
            tp_px = sig.entry_price * (1.0 + tp_pct)
            sl_px = sig.entry_price * (1.0 - sl_pct)
        else:
            tp_px = sig.entry_price * (1.0 - tp_pct)
            sl_px = sig.entry_price * (1.0 + sl_pct)
        return dataclasses.replace(
            sig,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            tp_price=tp_px,
            sl_price=sl_px,
            exit_policy=sig_ep,
            exit_policy_hash=ep_hash,
        )

    def _generate_eth_c1_buy_vectorized(
        self,
        features: dict[str, Any],
        *,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Vectorized ETH-USDT/C1 BUY generator with optional H1 slope <= threshold filter."""
        d15 = features["d15"]
        d1 = features["d1"]
        a21 = features["a21"]
        r14 = features["r14"]
        m14 = features["m14"]
        adx14 = features["adx14"]
        volma20 = features["volma20"]
        k11 = features["k11"]
        d3 = features["d3"]
        c9 = features["c9"]
        hist = features["hist"]
        ema150_4h_1h = features["ema150_4h_1h"]
        slope_10bar_1h = features["slope_10bar_1h"]
        smc1 = features["smc1"]
        ob1 = features["ob1"]
        posmap = features["posmap"]

        eth_c1 = self._eth_c1_spec()
        mfi_long = float(eth_c1.entry.get("mfi_long", 50.0))

        volsp = d1.volume > volma20 * 1.5
        adxr = adx14 > adx14.shift(1)
        sb08 = (d1.close > d1.open) & ((d1.close - d1.open) > a21 * 0.8)
        tu = d1.close > ema150_4h_1h
        longc = (k11 > 30) & (k11 > d3) & (c9 > 80) & (c9 > c9.shift(1))
        l_base = longc & (r14 > 55) & (hist > 0) & (m14 > mfi_long) & adxr & sb08 & tu & volsp

        if self.buy_max_slope_4h_pct is not None:
            cond = slope_10bar_1h.notna() & (slope_10bar_1h <= float(self.buy_max_slope_4h_pct))
            if self.pre_edge_h1:
                ev_l = edge_event(l_base & cond)
            else:
                ev_l = edge_event(l_base) & cond
        else:
            ev_l = edge_event(l_base)

        ep = self.get_exit_policy(eth_c1.symbol, eth_c1.combo_id)
        ep_hash = ep.get_policy_hash()
        warmup_bars = self._core.spec().warmup_bars

        out: list[Signal] = []
        for ot in ev_l.index[ev_l.fillna(False)]:
            last = ot + pd.Timedelta(minutes=45)
            if last not in posmap:
                continue
            pos = posmap[last]
            sd = int(smc1.loc[ot])
            blocked = bool(ob1.loc[ot]["buy_blocked"])
            ok = bool(approved("L", sd, blocked, eth_c1.layer2))
            warmup_ok = pos >= (warmup_bars - 1)
            if only_approved and not ok:
                continue
            if only_warmup_ready and not warmup_ok:
                continue

            close_ms = int(d15.index[pos].value // 1_000_000 + M15_MS - 1)
            bar_open_ms = int(d15.index[pos].value // 1_000_000)
            ref_price = float(d15.iloc[pos]["close"])
            provider = str(d15.iloc[pos].get("provider", "UNKNOWN"))
            sig_id = f"SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_BUY_{close_ms}"
            sig = Signal(
                symbol=eth_c1.symbol,
                combo_id=eth_c1.combo_id,
                combo_name=eth_c1.combo_name,
                timeframe=eth_c1.native_timeframe,
                bar_position=pos,
                bar_open_ms=bar_open_ms,
                signal_close_ms=close_ms,
                signal_close_dt=d15.index[pos] + pd.Timedelta(milliseconds=M15_MS - 1),
                side="BUY",
                entry_price=ref_price,
                tp_price=ref_price * (1.0 + ep.tp_pct),
                sl_price=ref_price * (1.0 - ep.sl_pct),
                tp_pct=ep.tp_pct,
                sl_pct=ep.sl_pct,
                approved=ok,
                warmup_ready=warmup_ok,
                smc_dir=sd,
                ob_blocked=blocked,
                is_new6=eth_c1.is_new6,
                provider=provider,
                signal_id=sig_id,
                exit_policy=ep,
                exit_policy_hash=ep_hash,
            )
            out.append(self._attach_atr_exit_policy(sig, ot, features))
        return out

    def _generate_eth_c1_sell_vectorized(
        self,
        features: dict[str, Any],
        *,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Corrected ETH-USDT/C1 SELL (C < -80) with optional H1 dist_ema150_4h >= threshold filter."""
        d15 = features["d15"]
        d1 = features["d1"]
        a21 = features["a21"]
        r14 = features["r14"]
        m14 = features["m14"]
        adx14 = features["adx14"]
        volma20 = features["volma20"]
        k11 = features["k11"]
        d3 = features["d3"]
        c9 = features["c9"]
        hist = features["hist"]
        ema150_4h_1h = features["ema150_4h_1h"]
        dist_ema150_4h_pct = features["dist_ema150_4h_pct"]
        smc1 = features["smc1"]
        ob1 = features["ob1"]
        posmap = features["posmap"]

        eth_c1 = self._eth_c1_spec()
        mfi_short = float(eth_c1.entry.get("mfi_short", 50.0))

        volsp = d1.volume > volma20 * 1.5
        adxr = adx14 > adx14.shift(1)
        ss08 = (d1.close < d1.open) & ((d1.open - d1.close) > a21 * 0.8)
        td = d1.close < ema150_4h_1h
        shortc = (k11 < 70) & (k11 < d3) & (c9 < -80) & (c9 < c9.shift(1))
        s_base = shortc & (r14 < 45) & (hist < 0) & (m14 < mfi_short) & adxr & ss08 & td & volsp

        if self.sell_min_dist_ema150_4h_pct is not None:
            cond = dist_ema150_4h_pct.notna() & (dist_ema150_4h_pct >= float(self.sell_min_dist_ema150_4h_pct))
            if self.pre_edge_h1:
                ev_s = edge_event(s_base & cond)
            else:
                ev_s = edge_event(s_base) & cond
        else:
            ev_s = edge_event(s_base)

        ep = self.get_exit_policy(eth_c1.symbol, eth_c1.combo_id)
        ep_hash = ep.get_policy_hash()
        warmup_bars = self._core.spec().warmup_bars

        out: list[Signal] = []
        for ot in ev_s.index[ev_s.fillna(False)]:
            last = ot + pd.Timedelta(minutes=45)
            if last not in posmap:
                continue
            pos = posmap[last]
            sd = int(smc1.loc[ot])
            blocked = bool(ob1.loc[ot]["sell_blocked"])
            ok = bool(approved("S", sd, blocked, eth_c1.layer2))
            warmup_ok = pos >= (warmup_bars - 1)
            if only_approved and not ok:
                continue
            if only_warmup_ready and not warmup_ok:
                continue

            close_ms = int(d15.index[pos].value // 1_000_000 + M15_MS - 1)
            bar_open_ms = int(d15.index[pos].value // 1_000_000)
            ref_price = float(d15.iloc[pos]["close"])
            provider = str(d15.iloc[pos].get("provider", "UNKNOWN"))
            sig_id = f"SIG_ETHUSDT_{eth_c1.combo_name}_{eth_c1.native_timeframe}_SELL_{close_ms}"
            sig = Signal(
                symbol=eth_c1.symbol,
                combo_id=eth_c1.combo_id,
                combo_name=eth_c1.combo_name,
                timeframe=eth_c1.native_timeframe,
                bar_position=pos,
                bar_open_ms=bar_open_ms,
                signal_close_ms=close_ms,
                signal_close_dt=d15.index[pos] + pd.Timedelta(milliseconds=M15_MS - 1),
                side="SELL",
                entry_price=ref_price,
                tp_price=ref_price * (1.0 - ep.tp_pct),
                sl_price=ref_price * (1.0 + ep.sl_pct),
                tp_pct=ep.tp_pct,
                sl_pct=ep.sl_pct,
                approved=ok,
                warmup_ready=warmup_ok,
                smc_dir=sd,
                ob_blocked=blocked,
                is_new6=eth_c1.is_new6,
                provider=provider,
                signal_id=sig_id,
                exit_policy=ep,
                exit_policy_hash=ep_hash,
            )
            out.append(self._attach_atr_exit_policy(sig, ot, features))
        return out

    def generate_signals_with_cache(
        self,
        *,
        non_c1_signals: list[Signal],
        eth_features: dict[str, Any],
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Fast signal assembly using precomputed non-C1 signals and ETH features."""
        buys = self._generate_eth_c1_buy_vectorized(
            eth_features,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        sells = self._generate_eth_c1_sell_vectorized(
            eth_features,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        combined = list(non_c1_signals) + buys + sells
        combined.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return combined

    def generate_signals(
        self,
        data: dict[str, pd.DataFrame],
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Full standalone BaseStrategy.generate_signals compatible implementation."""
        base_sigs = C1SellCorrectedFixedTpSlStrategy.generate_signals(
            self,
            data=data,
            symbols=symbols,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        if self._is_pure_baseline():
            return base_sigs

        target_symbols = symbols or list(data.keys())
        if "ETH-USDT" not in target_symbols or "ETH-USDT" not in data:
            return base_sigs

        eth_features = compute_eth_1h_4h_features(data["ETH-USDT"])
        kept_non_c1 = [
            s for s in base_sigs
            if not (s.symbol == "ETH-USDT" and s.combo_id == 1)
        ]

        # Start from baseline ETH-USDT/C1 signals so BUY matches Final20 incremental state,
        # and verify against vectorized BUY generator. Wait: applying pre-edge/post-edge H1 BUY filter
        # directly on base_c1_buys vs vectorized is verified equivalent (0 diff). Use vectorized when
        # pre_edge_h1 is enabled, or filter base_c1_buys cleanly.
        buys = self._generate_eth_c1_buy_vectorized(
            eth_features,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        sells = self._generate_eth_c1_sell_vectorized(
            eth_features,
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        combined = kept_non_c1 + buys + sells
        combined.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return combined
