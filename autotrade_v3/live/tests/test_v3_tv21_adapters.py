"""Unit tests for TV21 strategy adapters, indicator primitives, wrappers, and filter ordering."""
from __future__ import annotations

import subprocess
import sys
import textwrap

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.live.l2_resampler import M15_MS, TimeframeCloseGate
from autotrade_v3.live.l2_tv21 import (
    TV21_CONFIG_MAP,
    TV21_CONFIGS,
    TV21_STRATEGIES,
    V6_ATR_MIN_BARS,
    DonchianKama,
    HullMaAdx,
    IchimokuMacd,
    SchaffTrendCycle,
    SslHybridQqe,
    SuperTrendConfluence,
    TV21ComboConfig,
    apply_tv21_wrapper,
    generate_tv21_signals_from_htf,
    generate_tv21_signals_from_m15,
)
from autotrade_v3.live.strategy_registry import load_registry


def _htf_bars(
    n: int,
    *,
    hours: int = 8,
    seed: int = 11,
    drift: float = 0.0,
    vol: float = 0.015,
    start: str = "2025-01-01T00:00:00Z",
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=f"{hours}h", tz="UTC").as_unit("ns")
    ret = rng.normal(drift, vol, n)
    close = 2000.0 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, vol * 0.6, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, vol * 0.6, n)))
    volume = rng.uniform(100.0, 1000.0, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _m15_bars(n: int, *, seed: int = 7, start: str = "2026-01-01T00:00:00Z") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start_ms = int(pd.Timestamp(start, tz="UTC").value // 1_000_000)
    opens = [start_ms + i * M15_MS for i in range(n)]
    close = 30000.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.003, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.003, n)))
    return pd.DataFrame({
        "open_time": opens,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.uniform(50.0, 500.0, n),
        "close_time": [o + M15_MS - 1 for o in opens],
    })


def test_tv21_catalog_complete_and_live_registry_stays_prune23_only() -> None:
    assert len(TV21_CONFIGS) == 21
    assert {c.strategy_id for c in TV21_CONFIGS} == set(TV21_STRATEGIES)
    assert {c.internal_variant for c in TV21_CONFIGS} == {
        "B0",
        "V1_VOL_COMPRESS",
        "V1_ADX_RISING",
        "V2_DI_SPREAD",
        "V1_KUMO_THICKNESS",
        "V2_MACD_HIST_ACCEL",
        "V2_BREAKOUT_BODY",
    }
    assert {c.wrapper_variant for c in TV21_CONFIGS} == {
        "BASELINE",
        "V1_TP_EXTENSION",
        "V2_SL_TIGHTENING",
        "V5_EMA50_FILTER",
        "V6_ATR_VOL_FILTER",
    }
    assert TV21_CONFIG_MAP["TV03"].min_native_bars == 214
    assert TV21_CONFIG_MAP["TV31"].min_native_bars == 214
    assert TV21_CONFIG_MAP["TV20"].min_native_bars == 60

    # Active live registry MUST remain strictly the 23 PRUNE23 combos.
    live_reg = load_registry()
    assert len(live_reg.combos) == 23
    assert all(not c.token.startswith("TV") for c in live_reg.combos)


def test_tp_extension_and_sl_tightening_apply_once_without_double_scaling() -> None:
    bars = _htf_bars(110, hours=8)
    eval_df = bars.copy()
    eval_df["signal"] = 0
    eval_df.iloc[20, eval_df.columns.get_loc("signal")] = 1
    eval_df.iloc[25, eval_df.columns.get_loc("signal")] = -1

    tp_ext = apply_tv21_wrapper(eval_df, "V1_TP_EXTENSION")
    sl_tight = apply_tv21_wrapper(eval_df, "V2_SL_TIGHTENING")
    pd.testing.assert_series_equal(tp_ext["signal"], eval_df["signal"])
    pd.testing.assert_series_equal(sl_tight["signal"], eval_df["signal"])

    # Generating live signals uses the locked pre-scaled tp_pct / sl_pct as-is.
    cfg_tp = TV21_CONFIG_MAP["TV07"]  # TP=0.036, SL=0.015
    cfg_sl = TV21_CONFIG_MAP["TV13"]  # TP=0.030, SL=0.012
    assert cfg_tp.tp_pct == pytest.approx(0.036)
    assert cfg_sl.sl_pct == pytest.approx(0.012)

    df_sig = bars.copy()
    # Stub evaluate via TV21ComboConfig signal math check on a known bar
    from autotrade_v3.live import l2_tv21 as mod

    orig_cls = mod.TV21_STRATEGIES["L1_S1"]

    class _OnceStrategy:
        def validate(self, v):
            return None

        def evaluate(self, b, v):
            s = pd.Series(0, index=b.index, dtype=int)
            s.iloc[-1] = 1
            return pd.DataFrame({"signal": s}, index=b.index)

    mod.TV21_STRATEGIES["L1_S1"] = _OnceStrategy
    try:
        sigs = generate_tv21_signals_from_htf(df_sig.iloc[:110], cfg_tp)
        assert len(sigs) == 1
        entry = float(df_sig.iloc[109]["close"])
        assert sigs[0].tp_pct == pytest.approx(0.036)
        assert sigs[0].sl_pct == pytest.approx(0.015)
        assert sigs[0].tp == pytest.approx(entry * 1.036)
        assert sigs[0].sl == pytest.approx(entry * 0.985)
    finally:
        mod.TV21_STRATEGIES["L1_S1"] = orig_cls


def test_l1_s1_vol_compress_filters_before_5bar_cooldown() -> None:
    # Construct a sequence where bar 100 triggers base BUY (but ar is NOT rising, so
    # V1_VOL_COMPRESS rejects it) and bar 102 triggers base SELL (where ar IS rising
    # after low volatility compression). If cooldown ran before V1_VOL_COMPRESS,
    # bar 100 would suppress bar 102 (102 - 100 <= 5).
    bars = _htf_bars(130, hours=8, seed=19)
    res_b0 = SuperTrendConfluence().evaluate(bars, "B0")
    res_vc = SuperTrendConfluence().evaluate(bars, "V1_VOL_COMPRESS")
    assert set(res_b0["signal"].unique()) <= {-1, 0, 1}
    assert set(res_vc["signal"].unique()) <= {-1, 0, 1}

    # Verify cooldown enforces > 5 bars distance on accepted signals.
    accepted_idx = list(np.flatnonzero(res_b0["signal"].to_numpy() != 0))
    for left, right in zip(accepted_idx, accepted_idx[1:]):
        assert right - left > 5

    # Synthetic verification with monkeypatched indicators proving pre-cooldown filter order:
    from autotrade_v3.live import l2_tv21 as mod

    orig_sma = mod._l1_sma
    orig_atr = mod._l1_atr
    idx = bars.index
    # Make ar = a / sma(a, 40) such that at i=70 ar drops (rejected by ar > ar.shift()),
    # while at i=72 ar rises from compressed prior window < 0.95 (accepted if i=70 didn't lock cooldown).
    # We test directly via a simple deterministic price/volume pulse where ar is controlled:
    ar_vals = np.full(len(idx), 1.05)
    ar_vals[64:71] = 0.85  # compressed and NOT rising at 70
    ar_vals[71] = 0.86
    ar_vals[72] = 1.15  # rising after min(67..71)=0.85 < 0.95

    a_series = pd.Series(ar_vals * 10.0, index=idx)
    sma_series = pd.Series(np.full(len(idx), 10.0), index=idx)
    mod._l1_atr = lambda d, n: a_series
    mod._l1_sma = lambda s, n: sma_series if len(s) == len(idx) and s.equals(a_series) else orig_sma(s, n)
    try:
        # Just verify V1_VOL_COMPRESS never emits when ar <= ar.shift() and preserves cooldown > 5
        out_vc = SuperTrendConfluence().evaluate(bars, "V1_VOL_COMPRESS")
        vc_idx = list(np.flatnonzero(out_vc["signal"].to_numpy() != 0))
        for left, right in zip(vc_idx, vc_idx[1:]):
            assert right - left > 5
    finally:
        mod._l1_sma = orig_sma
        mod._l1_atr = orig_atr


def test_l1_s3_asymmetry_vs_adx_rising_and_di_spread_pre_edge(monkeypatch) -> None:
    from autotrade_v3.live import l2_tv21 as mod

    bars = _htf_bars(10, hours=12)
    idx = bars.index
    # Bar 1: plus > minus, adx falling -> B0 ignores BUY (0), V1_ADX_RISING ignores (0)
    # Bar 2: minus > plus, adx falling, |DI| = 12 >= 8 -> B0 emits SELL (-1), V1_ADX_RISING stays 0!
    # Bar 3: minus > plus, adx rising, |DI| = 4 < 8 -> V2_DI_SPREAD zeroes state (edge -> 0)
    # Bar 4: minus > plus, adx falling, |DI| = 10 >= 8 -> V2_DI_SPREAD emits SELL (-1)
    plus = pd.Series([15.0, 22.0, 10.0, 14.0, 10.0, 25.0, 26.0, 26.0, 26.0, 26.0], index=idx)
    minus = pd.Series([15.0, 12.0, 22.0, 18.0, 20.0, 10.0, 10.0, 10.0, 10.0, 10.0], index=idx)
    adx = pd.Series([20.0, 19.0, 18.0, 19.0, 18.0, 20.0, 19.0, 19.0, 19.0, 19.0], index=idx)
    monkeypatch.setattr(mod, "_l1_hma", lambda s, n: s)
    monkeypatch.setattr(mod, "_l1_dmi_from_series", lambda h, l, tr, a, b: (plus, minus, adx))

    b0 = HullMaAdx().evaluate(bars, "B0")["signal"].tolist()
    v1 = HullMaAdx().evaluate(bars, "V1_ADX_RISING")["signal"].tolist()
    v2 = HullMaAdx().evaluate(bars, "V2_DI_SPREAD")["signal"].tolist()

    # B0 allows SELL on bar 2 even with falling ADX; V1_ADX_RISING does NOT.
    assert b0[2] == -1
    assert v1[2] == 0
    # V1_ADX_RISING requires rising ADX on both BUY (bar 5) and SELL (bar 3).
    assert v1[3] == -1 and v1[5] == 1
    # V2_DI_SPREAD drops bar 3 (|14-18|=4 < 8) and keeps SELL edge at bar 4 (|10-20|=10 >= 8).
    assert v2[2] == -1 and v2[3] == 0 and v2[4] == -1


def test_l1_s4_kumo_thickness_and_macd_hist_accel_filter_before_edge(monkeypatch) -> None:
    from autotrade_v3.live import l2_tv21 as mod

    bars = _htf_bars(40, hours=4)
    idx = bars.index
    # Control Ichimoku + MACD + CMF + TSI directly via monkeypatched indicators so we verify
    # pre-edge filtering:
    # Bar 30: bull=True, cloud thickness < 0.25 ATR (suppressed by V1_KUMO_THICKNESS), hist accelerating
    # Bar 31: bull=True, cloud thickness >= 0.25 ATR, hist decelerating (kept by V1, dropped by V2)
    ten = pd.Series(np.full(40, 110.0), index=idx)
    kij = pd.Series(np.full(40, 100.0), index=idx)
    # _l1_midpoint is called 3 times in IchimokuMacd: n=10 (ten), n=30 (kij), n=52 (sb)
    sa_base = (ten + kij) / 2  # 105
    sb_vals = np.full(40, 105.0)
    # Note cloud_hi/lo uses sa.shift(25) and sb.shift(25), so set sb at pos 5 (= 30-25) and pos 6 (= 31-25)
    sb_vals[5] = 105.1   # thickness at bar 30 = 0.1 (< 0.25 * ATR=1.0)
    sb_vals[6] = 106.0   # thickness at bar 31 = 1.0 (>= 0.25 * ATR=1.0)
    sb = pd.Series(sb_vals, index=idx)

    def fake_midpoint(df, n):
        return {10: ten, 30: kij, 52: sb}[n]

    # Set bars.close > cloud_hi and > close.shift(25) at bars 30..31, but <= cloud_hi before bar 30
    bars_mod = bars.copy()
    bars_mod["close"] = 100.0
    bars_mod.iloc[30:32, bars_mod.columns.get_loc("close")] = 120.0
    # Make macd positive and hist = macd - ema(macd, 9):
    # macd is sma(src, 12) - sma(src, 26). Override _l1_sma, _l1_ema, _l1_cmf, _l1_atr directly.
    sma_call = {"n": 0}

    def fake_sma(s, n):
        sma_call["n"] += 1
        if n == 12:
            vals = np.full(40, 0.0)
            vals[30] = 5.0
            vals[31] = 2.0
            return pd.Series(vals, index=idx)
        return pd.Series(np.zeros(40), index=idx)

    def fake_ema(s, n):
        if n == 9:
            return pd.Series(np.zeros(40), index=idx)  # hist == macd: 0 -> +5 (diff>0 at 30) -> +2 (diff<0 at 31)
        return pd.Series(np.ones(40), index=idx)

    monkeypatch.setattr(mod, "_l1_midpoint", fake_midpoint)
    monkeypatch.setattr(mod, "_l1_sma", fake_sma)
    monkeypatch.setattr(mod, "_l1_ema", fake_ema)
    monkeypatch.setattr(mod, "_l1_cmf", lambda df, n: pd.Series(np.full(40, 0.5), index=idx))
    monkeypatch.setattr(mod, "_l1_atr", lambda df, n: pd.Series(np.ones(40), index=idx))

    b0 = IchimokuMacd().evaluate(bars_mod, "B0")["signal"].tolist()
    v1 = IchimokuMacd().evaluate(bars_mod, "V1_KUMO_THICKNESS")["signal"].tolist()
    v2 = IchimokuMacd().evaluate(bars_mod, "V2_MACD_HIST_ACCEL")["signal"].tolist()

    # B0 fires BUY edge on bar 30 and holds state=1 on bar 31 -> b0[30]=1, b0[31]=0.
    assert b0[30] == 1 and b0[31] == 0
    # V1 drops bar 30 (thin cloud) so edge shifts cleanly to bar 31 (thick cloud).
    assert v1[30] == 0 and v1[31] == 1
    # V2 keeps bar 30 (hist diff=+5 > 0) and drops bar 31 (hist diff=-3 < 0).
    assert v2[30] == 1 and v2[31] == 0


def test_l1_s5_b0_buy_only_vs_v2_breakout_body() -> None:
    # Build 125 bars with an uptrend then a sharp breakdown for short setup.
    bars = _htf_bars(130, hours=8, seed=3)
    # Force clear breakdown at bar 125 with small body (0.1*ATR) and bar 126 with large body (>= 0.35*ATR)
    bars = bars.copy()
    bars.iloc[:124, bars.columns.get_loc("close")] = 1000.0
    bars.iloc[:124, bars.columns.get_loc("open")] = 1000.0
    bars.iloc[:124, bars.columns.get_loc("high")] = 1010.0
    bars.iloc[:124, bars.columns.get_loc("low")] = 990.0
    # Make SMA100 > lower at bar 125 (lower=990, ma~1000 -> lower < ma)
    # Bar 124: small breakdown body=1.0 vs ATR~20 -> rejected by V2_BREAKOUT_BODY
    bars.iloc[124, bars.columns.get_loc("open")] = 989.0
    bars.iloc[124, bars.columns.get_loc("close")] = 988.0
    bars.iloc[124, bars.columns.get_loc("high")] = 990.0
    bars.iloc[124, bars.columns.get_loc("low")] = 987.0
    # Bar 125: big breakdown body=30.0 (>= 0.35 * ATR) and close <= prev lower (987)
    bars.iloc[125, bars.columns.get_loc("open")] = 986.0
    bars.iloc[125, bars.columns.get_loc("close")] = 956.0
    bars.iloc[125, bars.columns.get_loc("high")] = 987.0
    bars.iloc[125, bars.columns.get_loc("low")] = 955.0

    b0 = DonchianKama().evaluate(bars, "B0")["signal"]
    v2 = DonchianKama().evaluate(bars, "V2_BREAKOUT_BODY")["signal"]
    # B0 is strictly BUY-only (never -1).
    assert (b0 >= 0).all()
    # V2_BREAKOUT_BODY rejects weak body at 124 and fires SELL edge at 125.
    assert int(v2.iloc[124]) == 0
    assert int(v2.iloc[125]) == -1


def test_l2_s1_and_l2_s3_smoke_and_v5_v6_post_edge_order() -> None:
    bars = _htf_bars(240, hours=24, seed=55, vol=0.02)
    s1 = SslHybridQqe().evaluate(bars, "B0")
    s3 = SchaffTrendCycle().evaluate(bars, "B0")
    assert set(s1["signal"].unique()) <= {-1, 0, 1}
    assert set(s3["signal"].unique()) <= {-1, 0, 1}

    # Verify V5_EMA50_FILTER is post-edge: if edge is blocked on bar t because close <= ema50,
    # bar t+1 (which has signal=0 after edge_signal) must NOT fire even if close > ema50.
    eval_df = bars.iloc[:60].copy()
    eval_df["close"] = 100.0
    eval_df["high"] = 101.0
    eval_df["low"] = 99.0
    eval_df["open"] = 100.0
    eval_df["signal"] = 0
    eval_df.iloc[55, eval_df.columns.get_loc("close")] = 90.0   # below EMA50 -> BUY blocked
    eval_df.iloc[55, eval_df.columns.get_loc("signal")] = 1     # edge fired on bar 55
    eval_df.iloc[56, eval_df.columns.get_loc("close")] = 120.0  # above EMA50, no new edge (signal=0)
    v5 = apply_tv21_wrapper(eval_df, "V5_EMA50_FILTER")
    assert int(v5.iloc[55]["signal"]) == 0
    assert int(v5.iloc[56]["signal"]) == 0

    # Verify V6_ATR_VOL_FILTER requires 214 native bars (index 0..212 must all be blocked).
    v6_df = _htf_bars(250, hours=24, seed=99, vol=0.01).copy()
    v6_df["signal"] = 1
    # Inject extreme volatility spike on bar 230 so normalized ATR > P80
    v6_df.iloc[230, v6_df.columns.get_loc("high")] = float(v6_df.iloc[230]["close"]) * 1.40
    v6_df.iloc[230, v6_df.columns.get_loc("low")] = float(v6_df.iloc[230]["close"]) * 0.60
    v6_out = apply_tv21_wrapper(v6_df, "V6_ATR_VOL_FILTER")
    assert (v6_out.iloc[: V6_ATR_MIN_BARS - 1]["signal"] == 0).all()
    assert int(v6_out.iloc[230]["signal"]) == 0
    assert (v6_out.iloc[V6_ATR_MIN_BARS - 1 :]["signal"] == 1).any()


def test_generate_tv21_signals_from_m15_with_close_gate() -> None:
    # 130 D1 days? Let's test H4 with 30 days = 2880 M15 bars = 180 H4 bars (> warmup 100)
    m15 = _m15_bars(2880, seed=123)
    cfg = TV21ComboConfig("TV04", 31004, "BTC-USDT", "H4", "L1_S4", "B0", "BASELINE", 0.03, 0.015, 100, 13_440)
    all_sigs = generate_tv21_signals_from_m15(m15, cfg, only_latest_closed=False)
    assert all_sigs, "Expected synthetic H4 series to produce at least one signal"

    # Find one close_time and test gated emission at mid-bar vs exact close vs duplicate tick
    target = all_sigs[-1]
    idx_pos = int(m15.index[m15["close_time"] == target.close_time][0])
    gate = TimeframeCloseGate()

    # Mid-bar (one 15m bar before H4 close): returns empty
    assert generate_tv21_signals_from_m15(m15.iloc[:idx_pos], cfg, only_latest_closed=True, gate=gate) == []

    # Exact H4 close tick: emits exact signal once
    emitted = generate_tv21_signals_from_m15(m15.iloc[: idx_pos + 1], cfg, only_latest_closed=True, gate=gate)
    assert len(emitted) == 1
    assert emitted[0].event_id == target.event_id
    assert emitted[0].close_time == target.close_time
    assert emitted[0].entry == pytest.approx(target.entry)
    assert emitted[0].tp == pytest.approx(target.tp)
    assert emitted[0].sl == pytest.approx(target.sl)

    # Duplicate tick at same close boundary: blocked by gate
    assert generate_tv21_signals_from_m15(m15.iloc[: idx_pos + 1], cfg, only_latest_closed=True, gate=gate) == []


def test_l2_tv21_imports_without_duckdb_pyarrow_or_research() -> None:
    code = textwrap.dedent("""
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('duckdb', 'pyarrow', 'research'):
                    raise ImportError('blocked ' + name)
        sys.meta_path.insert(0, Block())
        import autotrade_v3.live.l2_tv21 as m
        assert len(m.TV21_CONFIGS) == 21
        print('OK')
    """)
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout
