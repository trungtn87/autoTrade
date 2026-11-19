"""External signal filters running post-cache and pre-simulator."""
from __future__ import annotations

from autotrade_v3.filters.annotation_filter import AnnotationGuardFilter
from autotrade_v3.filters.c14_h3a_kumo_band import C14H3aKumoBandFilter, build_c14_1h_kumo_lookup
from autotrade_v3.filters.c15_v5_sell_atr140 import C15V5SellAtr140Filter, build_c15_1h_atr_ratio_lookup
from autotrade_v3.filters.base import (
    FilterContext,
    FilterDecision,
    FilterRunResult,
    SignalFilter,
    apply_filters,
    filters_manifest_spec,
)

__all__ = [
    "AnnotationGuardFilter",
    "C14H3aKumoBandFilter",
    "build_c14_1h_kumo_lookup",
    "C15V5SellAtr140Filter",
    "build_c15_1h_atr_ratio_lookup",
    "FilterContext",
    "FilterDecision",
    "FilterRunResult",
    "SignalFilter",
    "apply_filters",
    "filters_manifest_spec",
]
