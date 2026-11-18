"""BTC-USDT/C14 H3a V1 admission filter (Ichimoku Kumo Thickness Band) for the live engine.

Source: V3 Lab candidate ``BTC-USDT_C14_H3a_KUMO_BAND_V1`` (owner-approved 2026-11-18,
``lab/research_candidates.json`` + ``filters/c14_h3a_kumo_band.py`` in the lab tree).

Only signals of ``BTC-USDT / combo 104 (C14)`` are touched. The H3a rule replaces the
old C14 admission: a C14 signal is kept only when, on the closed 1H bar that
emitted it,

    0.005 <= abs(span_a - span_b) / close < 0.010

The Ichimoku entry (tkcross 12/30/60), Layer 2 OB, and Fixed TP 2% / SL 0.666667%
are unchanged. No trailing, no split legs.

Activation: this filter runs only inside ``V18Engine`` for combos present in the
active registry. Registry ``V3_LIVE_20_POSITIVE_FIXED_V1`` does NOT contain combo 104,
so the filter has no effect there until C14 is explicitly added by the owner.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from autotrade_v2.app.strategy.final14_exact_strategy import _ichimoku

C14_SYMBOL = "BTC-USDT"
C14_COMBO_ID = 104
H3A_CANDIDATE_ID = "BTC-USDT_C14_H3a_KUMO_BAND_V1"
H3A_MIN_KUMO_INCLUSIVE = 0.005
H3A_MAX_KUMO_EXCLUSIVE = 0.010
H3A_TENKAN, H3A_KIJUN, H3A_SENKOU_B = 12, 30, 60
H3A_ENTRY_VARIANT = "tkcross_12_30_60"
H3A_LAYER2 = "OB"
H3A_TP_PCT = 0.02
H3A_SL_PCT = 0.006666666666666667


def is_h3a_combo(symbol: str, combo_id: int) -> bool:
    return str(symbol).upper() == C14_SYMBOL and int(combo_id) == C14_COMBO_ID


def h3a_spec_matches(c) -> bool:
    """True when a registry ``ComboSpec`` carries exactly the locked H3a parameters."""
    return (
        c.native_timeframe == "1h"
        and c.entry_variant == H3A_ENTRY_VARIANT
        and c.layer2 == H3A_LAYER2
        and math.isclose(c.tp_pct, H3A_TP_PCT, rel_tol=0, abs_tol=1e-12)
        and math.isclose(c.sl_pct, H3A_SL_PCT, rel_tol=0, abs_tol=1e-12)
        and c.signal_variant == "BASELINE"
        and str(c.entry.get("indicator", "Ichimoku")) == "Ichimoku"
    )


def h3a_kumo_pct(d1: pd.DataFrame) -> pd.Series:
    """``abs(span_a - span_b) / close`` per closed 1H bar (index = 1H bar open, as in ``d1``).

    Uses the same Ichimoku spans as the 20FINAL/C14 entry (shifted by the base period,
    so the value at a bar only reads earlier bars).
    """
    h = d1["high"].to_numpy(float)
    l = d1["low"].to_numpy(float)
    c = d1["close"].to_numpy(float)
    _, _, span_a, span_b = _ichimoku(h, l, H3A_TENKAN, H3A_KIJUN, H3A_SENKOU_B)
    with np.errstate(divide="ignore", invalid="ignore"):
        kumo = np.where(c > 0.0, np.abs(span_a - span_b) / c, np.nan)
    return pd.Series(kumo, index=d1.index, dtype=float)


def h3a_keep_mask(kumo: pd.Series) -> pd.Series:
    """Vectorised band test over a per-1H-bar kumo series; NaN fails closed."""
    return ((kumo >= H3A_MIN_KUMO_INCLUSIVE) & (kumo < H3A_MAX_KUMO_EXCLUSIVE)).fillna(False).astype(bool)


def h3a_keep(kumo_pct: float | None) -> bool:
    """Locked H3a band test. Missing or non-finite kumo fails closed (signal dropped)."""
    if kumo_pct is None or not math.isfinite(kumo_pct):
        return False
    return H3A_MIN_KUMO_INCLUSIVE <= kumo_pct < H3A_MAX_KUMO_EXCLUSIVE
