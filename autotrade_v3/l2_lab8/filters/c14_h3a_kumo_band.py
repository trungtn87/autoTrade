"""Causal H3a Ichimoku Kumo Thickness Band filter for BTC-USDT/C14 (V3 Lab research candidate)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.l2_lab8.filters.base import FilterContext, FilterDecision, hash_filter_config
from autotrade_v3.l2_lab8.strategy_core.strategies.final20.logic import _ichimoku

C14_SYMBOL = "BTC-USDT"
C14_COMBO_ID = 104
H3A_MIN_KUMO_PCT_INCLUSIVE = 0.005
H3A_MAX_KUMO_PCT_EXCLUSIVE = 0.010


def build_c14_1h_kumo_lookup(df_1h: pd.DataFrame) -> dict[int, float]:
    """Build strictly causal ``{close_ms: abs(span_a - span_b) / close}`` from closed 1H bars."""
    if df_1h.empty:
        return {}
    d = df_1h.sort_index()
    h = d["high"].to_numpy(float)
    l = d["low"].to_numpy(float)
    c = d["close"].to_numpy(float)
    _, _, span_a, span_b = _ichimoku(h, l, conv=12, base=30, spanb=60)
    kumo_pct = np.where(c > 0.0, np.abs(span_a - span_b) / c, np.nan)

    idx_ns = d.index.tz_convert("UTC").asi8 if d.index.tz is not None else d.index.tz_localize("UTC").asi8
    close_ms = (idx_ns // 1_000_000) + 3_600_000 - 1
    return {int(cms): float(kp) for cms, kp in zip(close_ms, kumo_pct)}


class C14H3aKumoBandFilter:
    """Locked H3a V1 filter: admit BTC-USDT/C14 iff 0.005 <= abs(SpanA - SpanB)/Close < 0.010."""

    name: str = "BTC-USDT_C14_H3a_KUMO_BAND_V1"

    def __init__(self, kumo_by_close_ms: dict[int, float]) -> None:
        self._kumo_by_close_ms = dict(kumo_by_close_ms)
        # Locked parameters per BTC_C14_H3A_STANDARD_CANDIDATE.json
        self.min_inclusive: float = H3A_MIN_KUMO_PCT_INCLUSIVE
        self.max_exclusive: float = H3A_MAX_KUMO_PCT_EXCLUSIVE

    @classmethod
    def from_1h_dataframe(cls, df_1h: pd.DataFrame) -> "C14H3aKumoBandFilter":
        return cls(build_c14_1h_kumo_lookup(df_1h))

    def config_dict(self) -> dict[str, Any]:
        return {
            "filter": self.name,
            "symbol": C14_SYMBOL,
            "combo_id": C14_COMBO_ID,
            "min_inclusive": self.min_inclusive,
            "max_exclusive": self.max_exclusive,
            "locked": True,
        }

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        if str(getattr(signal, "symbol", "")) != C14_SYMBOL or int(getattr(signal, "combo_id", -1)) != C14_COMBO_ID:
            return FilterDecision(action="KEEP")

        cms = int(getattr(signal, "signal_close_ms"))
        kumo_pct = self._kumo_by_close_ms.get(cms)
        if kumo_pct is None:
            return FilterDecision(action="DROP", reason="H3a_MISSING_1H_BAR", label="H3a_DROP")
        if not math.isfinite(kumo_pct):
            return FilterDecision(action="DROP", reason="H3a_NONFINITE_INDICATOR", label="H3a_DROP")

        if self.min_inclusive <= kumo_pct < self.max_exclusive:
            return FilterDecision(action="KEEP", label="H3a_KEEP")
        return FilterDecision(action="DROP", reason="H3a_OUTSIDE_KUMO_BAND", label="H3a_DROP")
