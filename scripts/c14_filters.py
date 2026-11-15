"""Causal SignalFilter implementations for BTC-USDT/C14 LOO-19 and H2 A/B research."""
from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
import pandas as pd

from autotrade_v3.filters.base import FilterContext, FilterDecision, hash_filter_config
from autotrade_v3.strategy_core.indicators.indicators import atr
from autotrade_v3.strategy_core.strategies.final20.logic import _ichimoku

C14_SYMBOL = "BTC-USDT"
C14_COMBO_ID = 104
H2A_OVEREXT_MAX = 1.5
H2B_KUMO_MIN_PCT = 0.005


def is_c14_signal(signal: Any) -> bool:
    return str(getattr(signal, "symbol", "")) == C14_SYMBOL and int(getattr(signal, "combo_id", -1)) == C14_COMBO_ID


def build_c14_1h_feature_table(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Compute strictly causal 1H Ichimoku (12/30/60) and ATR(14) features keyed by 1H close_ms.

    Every value at row ``i`` (with ``close_ms = open_ms + 3_600_000 - 1``) depends ONLY on
    1H bars ``0..i`` (already closed at ``close_ms``):
    - ``tenkan``: 12-bar mid of high/low over ``[i-11 .. i]``
    - ``kijun``: 30-bar mid of high/low over ``[i-29 .. i]``
    - ``span_a``: ``((tenkan + kijun) / 2)`` from bar ``i - 30`` (``shift(+30)``)
    - ``span_b``: 60-bar mid of high/low from bar ``i - 30`` (``shift(+30)``)
    - ``atr14``: Wilder RMA ATR(14) over ``[0 .. i]``
    """
    if df_1h.empty:
        return pd.DataFrame()
    d = df_1h.sort_index().copy()
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    c = d["close"].to_numpy(float)

    tenkan, kijun, span_a, span_b = _ichimoku(h, l, conv=12, base=30, spanb=60)
    atr14 = atr(d, 14).to_numpy(float)

    idx_ns = d.index.tz_convert("UTC").asi8 if d.index.tz is not None else d.index.tz_localize("UTC").asi8
    open_ms = idx_ns // 1_000_000
    close_ms = open_ms + 3_600_000 - 1

    kumo_top = np.maximum(span_a, span_b)
    kumo_bottom = np.minimum(span_a, span_b)
    kumo_thickness = np.abs(span_a - span_b)
    kumo_thickness_pct = np.where(c > 0, kumo_thickness / c, np.nan)

    dist_close_kijun = c - kijun
    abs_dist_close_kijun = np.abs(dist_close_kijun)
    dist_close_kijun_atr = np.where(atr14 > 0, abs_dist_close_kijun / atr14, np.nan)

    # Signed distance to active Kumo: positive above cloud, negative below, 0 inside
    dist_close_kumo = np.where(
        c > kumo_top,
        c - kumo_top,
        np.where(c < kumo_bottom, c - kumo_bottom, 0.0),
    )

    out = pd.DataFrame(
        {
            "open_ms": open_ms,
            "close_ms": close_ms,
            "open": d["open"].to_numpy(float),
            "high": h,
            "low": l,
            "close": c,
            "volume": d["volume"].to_numpy(float) if "volume" in d.columns else np.nan,
            "tenkan": tenkan,
            "kijun": kijun,
            "span_a": span_a,
            "span_b": span_b,
            "kumo_top": kumo_top,
            "kumo_bottom": kumo_bottom,
            "kumo_thickness": kumo_thickness,
            "kumo_thickness_pct": kumo_thickness_pct,
            "atr14": atr14,
            "atr14_pct": np.where(c > 0, atr14 / c, np.nan),
            "dist_close_kijun": dist_close_kijun,
            "abs_dist_close_kijun": abs_dist_close_kijun,
            "dist_close_kijun_atr": dist_close_kijun_atr,
            "dist_close_kumo": dist_close_kumo,
            "abs_dist_close_kumo_pct": np.where(c > 0, np.abs(dist_close_kumo) / c, np.nan),
        },
        index=pd.Index(close_ms, name="close_ms"),
    )
    return out


class C14DisabledFilter:
    """Leave-One-Out filter that removes BTC-USDT/C14 (combo_id=104) signals only."""

    name: str = "C14_DISABLED"

    def config_dict(self) -> dict[str, Any]:
        return {"filter": self.name, "symbol": C14_SYMBOL, "combo_id": C14_COMBO_ID}

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        if is_c14_signal(signal):
            return FilterDecision(action="DROP", reason="LOO_C14_DISABLED", label="C14_DISABLED")
        return FilterDecision(action="KEEP")


class IchimokuAtrFilter:
    """Causal Ichimoku/ATR entry filter for BTC-USDT/C14 (H2a, H2b, H2ab)."""

    def __init__(
        self,
        feature_table: pd.DataFrame,
        variant: Literal["H2a", "H2b", "H2ab"],
        overext_max: float = H2A_OVEREXT_MAX,
        kumo_min_pct: float = H2B_KUMO_MIN_PCT,
    ) -> None:
        if variant not in ("H2a", "H2b", "H2ab"):
            raise ValueError(f"Unsupported variant: {variant}")
        self.variant = variant
        self.name = f"C14_{variant}"
        self.overext_max = float(overext_max)
        self.kumo_min_pct = float(kumo_min_pct)
        # Pre-index as dict[int, tuple[float, float]] for O(1) deterministic lookup
        self._by_close_ms: dict[int, tuple[float, float]] = {
            int(cms): (float(row.dist_close_kijun_atr), float(row.kumo_thickness_pct))
            for cms, row in feature_table[["dist_close_kijun_atr", "kumo_thickness_pct"]].iterrows()
        }

    def config_dict(self) -> dict[str, Any]:
        return {
            "filter": self.name,
            "variant": self.variant,
            "symbol": C14_SYMBOL,
            "combo_id": C14_COMBO_ID,
            "overext_max": self.overext_max,
            "kumo_min_pct": self.kumo_min_pct,
        }

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        if not is_c14_signal(signal):
            return FilterDecision(action="KEEP")

        cms = int(getattr(signal, "signal_close_ms"))
        vals = self._by_close_ms.get(cms)
        if vals is None:
            return FilterDecision(action="DROP", reason=f"{self.variant}_MISSING_1H_BAR", label=self.variant)

        overext, kumo_pct = vals
        if self.variant in ("H2a", "H2ab"):
            if not math.isfinite(overext) or overext > self.overext_max:
                return FilterDecision(action="DROP", reason="H2a_OVEREXTENDED", label="H2a_DROP")

        if self.variant in ("H2b", "H2ab"):
            if not math.isfinite(kumo_pct) or kumo_pct < self.kumo_min_pct:
                return FilterDecision(action="DROP", reason="H2b_THIN_KUMO", label="H2b_DROP")

        return FilterDecision(action="KEEP", label=f"{self.variant}_KEEP")
