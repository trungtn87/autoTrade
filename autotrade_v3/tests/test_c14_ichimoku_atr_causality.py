"""Zero look-ahead and filter correctness tests for BTC-USDT/C14 Ichimoku/ATR features."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.filters.base import FilterContext
from autotrade_v3.strategy_core.strategies.final20.logic import _IncIchimoku
from scripts.c14_filters import (
    C14DisabledFilter,
    IchimokuAtrFilter,
    build_c14_1h_feature_table,
)


@dataclass
class _DummySignal:
    symbol: str
    combo_id: int
    signal_close_ms: int
    combo_name: str = "C14"
    timeframe: str = "1h"
    side: str = "BUY"
    signal_close_dt: str = "2024-01-01T00:59:59.999000+00:00"


def _make_synthetic_1h(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    rets = rng.normal(0.0, 0.006, size=n)
    close = 50000.0 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0.003, 0.002, size=n))
    high = np.maximum(open_, close) * (1.0 + spread)
    low = np.minimum(open_, close) * (1.0 - spread)
    vol = rng.uniform(100.0, 1000.0, size=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_zero_lookahead_truncation_equivalence() -> None:
    df_full = _make_synthetic_1h(n=180)
    full_feat = build_c14_1h_feature_table(df_full)

    # Check multiple horizons after warmup (>= 95 bars so SpanA/SpanB + ATR14 are finite)
    for cutoff in (95, 110, 140, 175):
        df_trunc = df_full.iloc[: cutoff + 1].copy()
        trunc_feat = build_c14_1h_feature_table(df_trunc)
        row_full = full_feat.iloc[cutoff]
        row_trunc = trunc_feat.iloc[cutoff]
        for col in (
            "tenkan",
            "kijun",
            "span_a",
            "span_b",
            "atr14",
            "dist_close_kijun_atr",
            "kumo_thickness_pct",
            "dist_close_kumo",
        ):
            assert np.isclose(row_full[col], row_trunc[col], rtol=0.0, atol=1e-12), (
                f"Look-ahead mismatch at bar {cutoff}, col {col}: {row_full[col]} vs {row_trunc[col]}"
            )


def test_ichimoku_matches_incremental_causal_engine() -> None:
    df = _make_synthetic_1h(n=150)
    feat = build_c14_1h_feature_table(df)
    inc = _IncIchimoku(conv=12, base=30, spanb=60)
    for i in range(len(df)):
        t_i, k_i, sa_i, sb_i = inc.step(float(df["high"].iloc[i]), float(df["low"].iloc[i]))
        if i >= 90:
            assert np.isclose(feat["tenkan"].iloc[i], t_i, atol=1e-12)
            assert np.isclose(feat["kijun"].iloc[i], k_i, atol=1e-12)
            assert np.isclose(feat["span_a"].iloc[i], sa_i, atol=1e-12)
            assert np.isclose(feat["span_b"].iloc[i], sb_i, atol=1e-12)


def test_c14_disabled_and_h2_filters() -> None:
    df = _make_synthetic_1h(n=120)
    feat = build_c14_1h_feature_table(df)
    ctx = FilterContext()

    # C14DisabledFilter drops only BTC-USDT/104
    loo = C14DisabledFilter()
    cms = int(feat["close_ms"].iloc[-1])
    assert loo.evaluate(_DummySignal("BTC-USDT", 104, cms), ctx).action == "DROP"
    assert loo.evaluate(_DummySignal("ETH-USDT", 104, cms), ctx).action == "KEEP"
    assert loo.evaluate(_DummySignal("BTC-USDT", 1, cms), ctx).action == "KEEP"

    # Inject controlled values at last bar to test H2a / H2b / H2ab thresholds
    feat_mod = feat.copy()
    feat_mod.loc[cms, "dist_close_kijun_atr"] = 1.6  # > 1.5 -> H2a DROP
    feat_mod.loc[cms, "kumo_thickness_pct"] = 0.006  # >= 0.005 -> H2b KEEP

    f_h2a = IchimokuAtrFilter(feat_mod, "H2a")
    f_h2b = IchimokuAtrFilter(feat_mod, "H2b")
    f_h2ab = IchimokuAtrFilter(feat_mod, "H2ab")

    sig_c14 = _DummySignal("BTC-USDT", 104, cms)
    sig_other = _DummySignal("BTC-USDT", 2, cms)

    assert f_h2a.evaluate(sig_c14, ctx).action == "DROP"
    assert f_h2b.evaluate(sig_c14, ctx).action == "KEEP"
    assert f_h2ab.evaluate(sig_c14, ctx).action == "DROP"
    assert f_h2a.evaluate(sig_other, ctx).action == "KEEP"
    assert f_h2ab.evaluate(sig_other, ctx).action == "KEEP"
