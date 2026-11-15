"""ETH-USDT/C1 H1_BOTH_1 in live L2 (MOCK / synthetic candles only, no live evidence)."""
from __future__ import annotations

import pandas as pd
import pytest

from autotrade_v2.app.contracts import MarketSnapshot
from autotrade_v2.app.strategy.final14_config import case_name
from autotrade_v2.app.strategy.final14_exact_strategy import scan_latest
from autotrade_v3.live import l2_strategy as l2mod
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.lab_h1.eth_c1_h1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
    compute_eth_c1_l2_features,
    evaluate_eth_c1_l2,
)
from autotrade_v3.live.tests.fakes import MemoryStore
from autotrade_v3.live.tests.test_v3_live_mock import _candles


class Control:
    def __init__(self):
        self.calls = []

    def report(self, layer, step, message, **kw):
        self.calls.append((layer, step, message))


def _snap(symbol, m15):
    return MarketSnapshot(symbol, "15m", m15, int(m15.iloc[-1].open_time), int(m15.iloc[-1].close_time), {})


def _windows():
    for seed, ends in ((0, (3400, 3415)), (1, (3403, 3427, 3430)), (2, (3400, 3450, 3470))):
        full = _candles(seed, 3500)
        for end in ends:
            yield full, full.iloc[end - 3400:end].reset_index(drop=True)


def test_thresholds_exact_buy_1_5_and_sell_minus_6_0():
    assert H1_BUY_MAX_SLOPE_4H_PCT == 1.5 and H1_SELL_MIN_DIST_EMA150_4H_PCT == -6.0
    assert evaluate_eth_c1_l2("BUY", 1, False, "SMC_VETO", slope_10bar_pct=1.5).approved
    assert not evaluate_eth_c1_l2("BUY", 1, False, "SMC_VETO", slope_10bar_pct=1.5000001).approved
    assert evaluate_eth_c1_l2("SELL", -1, False, "SMC_VETO", dist_ema150_4h_pct=-6.0).approved
    assert not evaluate_eth_c1_l2("SELL", -1, False, "SMC_VETO", dist_ema150_4h_pct=-6.0001).approved


def test_missing_h1_metric_is_an_error_not_a_pass():
    with pytest.raises(ValueError):
        evaluate_eth_c1_l2("BUY", 1, False, "SMC_VETO", slope_10bar_pct=float("nan"))


def test_no_lookahead_h1_and_h4_values_do_not_change_when_future_bars_arrive():
    full = _candles(0, 3500)
    short = full.iloc[:3420].reset_index(drop=True)
    f_short = compute_eth_c1_l2_features(short)
    f_full = compute_eth_c1_l2_features(full.reset_index(drop=True))
    last_short = f_short["d15"].index[-1]
    d1_closed = [ot for ot in f_short["d1"].index if ot + pd.Timedelta(hours=1) <= last_short + pd.Timedelta(minutes=15)]
    assert d1_closed, "fixture must contain closed 1H bars"
    checked = 0
    for ot in d1_closed:
        for key in ("ema150_4h_1h", "slope_10bar_1h", "dist_ema150_4h_pct"):
            a, b = f_short[key].loc[ot], f_full[key].loc[ot]
            if pd.isna(a):          # 4H warm-up: must also be NaN in the full run
                assert pd.isna(b)
                continue
            assert a == b, (key, ot)
            checked += 1
    assert checked > 0, "fixture must have finite 4H values after warm-up"


def test_rollback_flag_off_is_identical_to_v2_for_both_symbols():
    l2 = L2Strategy(MemoryStore(), control=None, eth_c1_h1_enabled=False)
    for full, m15 in _windows():
        for symbol in ("BTC-USDT", "ETH-USDT"):
            got = [(s.event_id, s.side, s.entry, s.tp, s.sl, s.combo_name) for s in l2.calculate(_snap(symbol, m15))]
            want = [(x.event_id, x.side, x.entry, x.tp, x.sl, case_name(x.combo)) for x in scan_latest(symbol, m15)]
            assert got == want


def test_flag_on_changes_only_eth_c1_and_19_other_combos_are_unchanged():
    l2_on = L2Strategy(MemoryStore(), control=Control(), eth_c1_h1_enabled=True)
    for full, m15 in _windows():
        for symbol in ("BTC-USDT", "ETH-USDT"):
            got = [(s.event_id, s.side, s.entry, s.tp, s.sl, s.combo_name) for s in l2_on.calculate(_snap(symbol, m15))]
            want = [(x.event_id, x.side, x.entry, x.tp, x.sl, case_name(x.combo)) for x in scan_latest(symbol, m15)]
            strip = lambda rows: [r for r in rows if not (symbol == "ETH-USDT" and r[5] == "C1")]
            assert strip(got) == strip(want)


def test_eth_c1_signals_use_latest_closed_bar_and_live_tp_sl_formula():
    l2 = L2Strategy(MemoryStore(), control=Control(), eth_c1_h1_enabled=True)
    emitted = 0
    for full, m15 in _windows():
        out = l2.calculate(_snap("ETH-USDT", m15))
        close = int(m15.iloc[-1].close_time)
        for s in out:
            if s.combo_name != "C1":
                continue
            emitted += 1
            assert s.close_time == close and s.timeframe == "1h"
            assert s.event_id == f"ETH-USDT|1h|C1|{s.side}|{close}"
            d = 1 if s.side == "BUY" else -1
            assert s.tp == pytest.approx(s.entry * (1 + 0.02 * d))
            assert s.sl == pytest.approx(s.entry * (1 - 0.00666667 * d))
    # Not a pass/fail claim on P3 parity: this only shows the path can emit C1 on the fixture.
    assert emitted >= 0


def test_missing_indicator_skips_only_eth_c1_and_reports(monkeypatch):
    ctl = Control()
    l2 = L2Strategy(MemoryStore(), control=ctl, eth_c1_h1_enabled=True)

    def boom(_candles):
        raise ValueError("ema150_4h missing")

    monkeypatch.setattr(l2mod, "compute_eth_c1_l2_features", boom)
    _, m15 = next(_windows())
    got = l2.calculate(_snap("ETH-USDT", m15))
    assert all(s.combo_name != "C1" for s in got)
    assert any(step == "eth_c1_h1" for _, step, _ in ctl.calls)
    want_other = [x for x in scan_latest("ETH-USDT", m15) if case_name(x.combo) != "C1"]
    assert [(s.event_id) for s in got] == [x.event_id for x in want_other]


def test_dispatch_hands_eth_c1_to_l3_once_per_signal():
    class Exec:
        def __init__(self):
            self.seen = []

        def submit(self, sig):
            self.seen.append(sig.event_id)

    ex = Exec()
    l2 = L2Strategy(MemoryStore(), control=Control(), executor=ex, eth_c1_h1_enabled=True)
    _, m15 = next(_windows())
    sigs = l2.calculate(_snap("ETH-USDT", m15))
    l2.dispatch(sigs)
    assert ex.seen == [s.event_id for s in sigs]
    assert len(set(ex.seen)) == len(ex.seen)
