"""ETH-USDT/C1 H1_BOTH_1 in live L2 (MOCK / synthetic candles only, no live evidence)."""
from __future__ import annotations

import pandas as pd
import pytest

from autotrade_v2.app.contracts import MarketSnapshot
from autotrade_v2.app.strategy.final14_config import case_name
from autotrade_v2.app.strategy.final14_exact_strategy import scan_latest
from autotrade_v3.live import l2_strategy as l2mod
from autotrade_v3.live.eth_c1_history import EthC1History
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.lab_h1.eth_c1_h1 import (
    H1_BUY_MAX_SLOPE_4H_PCT,
    H1_SELL_MIN_DIST_EMA150_4H_PCT,
    compute_eth_c1_l2_features,
    evaluate_eth_c1_l2,
)
from autotrade_v3.live.tests.fakes import MemoryStore
from autotrade_v3.live.tests.test_v3_live_mock import _candles

BARS = 12_000


class Control:
    def __init__(self):
        self.calls = []

    def report(self, layer, step, message, **kw):
        self.calls.append((layer, step, message))


class FakeHistory:
    """MOCK BingX history: returns the last `bars` closed M15 bars up to `self.end` and counts REST calls."""

    def __init__(self, big):
        self.big = big
        self.end = None
        self.calls = 0
        self.fail = False

    def fetch_history(self, symbol, bars, now_ms=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("mock REST down")
        return self.big.iloc[self.end - bars:self.end].reset_index(drop=True)


def _snap(symbol, m15):
    return MarketSnapshot(symbol, "15m", m15, int(m15.iloc[-1].open_time), int(m15.iloc[-1].close_time), {})


def _setup(seed=0, end=12_000, n=12_600):
    big = _candles(seed, n)
    hist = FakeHistory(big)
    hist.end = end
    ctl = Control()
    l2 = L2Strategy(MemoryStore(), control=ctl, eth_c1_h1_enabled=True, historical=hist)
    l2.eth_history = EthC1History(hist, bars=BARS, retry_sec=0.0)
    return l2, hist, ctl, big


def _m15(big, end):
    return big.iloc[end - 3400:end].reset_index(drop=True)


def _small_windows():
    for seed, ends in ((0, (3400, 3415)), (1, (3403, 3427, 3430))):
        full = _candles(seed, 3500)
        for end in ends:
            yield full.iloc[end - 3400:end].reset_index(drop=True)


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
    checked = 0
    for ot in d1_closed:
        for key in ("ema150_4h_1h", "slope_10bar_1h", "dist_ema150_4h_pct"):
            a, b = f_short[key].loc[ot], f_full[key].loc[ot]
            if pd.isna(a):
                assert pd.isna(b)
                continue
            assert a == b, (key, ot)
            checked += 1
    assert checked > 0


def test_rollback_flag_off_is_identical_to_v2_for_both_symbols():
    l2 = L2Strategy(MemoryStore(), control=None, eth_c1_h1_enabled=False)
    for m15 in _small_windows():
        for symbol in ("BTC-USDT", "ETH-USDT"):
            snap = _snap(symbol, m15)
            got = [(s.event_id, s.side, s.entry, s.tp, s.sl, s.combo_name) for s in l2.calculate(snap)]
            want = [(x.event_id, x.side, x.entry, x.tp, x.sl, case_name(x.combo)) for x in scan_latest(symbol, m15)]
            assert got == want


def test_flag_on_changes_only_eth_c1_and_19_other_combos_are_unchanged():
    l2, hist, ctl, big = _setup()
    for end in (12_000, 12_001, 12_300):
        hist.end = end
        m15 = _m15(big, end)
        for symbol in ("BTC-USDT", "ETH-USDT"):
            got = [(s.event_id, s.side, s.entry, s.tp, s.sl, s.combo_name) for s in l2.calculate(_snap(symbol, m15))]
            want = [(x.event_id, x.side, x.entry, x.tp, x.sl, case_name(x.combo)) for x in scan_latest(symbol, m15)]
            if symbol == "ETH-USDT":
                got = [r for r in got if r[5] != "C1"]
                want = [r for r in want if r[5] != "C1"]
            assert got == want
    assert not [c for c in ctl.calls if c[1] == "eth_c1_h1"], "ready history must not report errors"


def test_eth_c1_signals_use_latest_closed_bar_and_live_tp_sl_formula():
    l2, hist, ctl, big = _setup()
    for end in range(12_000, 12_060):
        hist.end = end
        m15 = _m15(big, end)
        for s in l2.calculate(_snap("ETH-USDT", m15)):
            if s.combo_name != "C1":
                continue
            close = int(m15.iloc[-1].close_time)
            assert s.close_time == close and s.timeframe == "1h"
            assert s.event_id == f"ETH-USDT|1h|C1|{s.side}|{close}"
            d = 1 if s.side == "BUY" else -1
            assert s.tp == pytest.approx(s.entry * (1 + 0.02 * d))
            assert s.sl == pytest.approx(s.entry * (1 - 0.00666667 * d))


def test_history_not_ready_emits_no_eth_c1_but_other_combos_run():
    l2, hist, ctl, big = _setup()
    hist.fail = True
    m15 = _m15(big, 12_000)
    got = l2.calculate(_snap("ETH-USDT", m15))
    assert all(s.combo_name != "C1" for s in got)
    want = [x.event_id for x in scan_latest("ETH-USDT", m15) if case_name(x.combo) != "C1"]
    assert [s.event_id for s in got] == want
    assert any(step == "eth_c1_h1" for _, step, _ in ctl.calls)


def test_rest_warmup_is_rate_limited_and_not_called_per_signal():
    l2, hist, ctl, big = _setup()
    hist.fail = True
    l2.eth_history.retry_ms = 60_000   # production default is 60 s
    for _ in range(3):
        l2.calculate(_snap("ETH-USDT", _m15(big, 12_000)))
    assert hist.calls == 1
    hist.fail = False
    hist.calls = 0
    l2.eth_history._last_attempt_ms = None
    for end in range(12_000, 12_020):
        hist.end = end
        l2.calculate(_snap("ETH-USDT", _m15(big, end)))
    assert hist.calls <= 1, "ready cache must not call REST on each closed bar"


def test_gap_in_live_bars_resets_history_and_waits():
    """A lost closed bar must not be bridged: the cache resets and stays not ready while REST is down."""
    from autotrade_v3.live.eth_c1_history import HistoryNotReady
    l2, hist, ctl, big = _setup()
    hist.end = 12_000
    l2.eth_history.frame_for(_m15(big, 12_000))
    assert l2.eth_history._frame is not None
    gapped = big.iloc[12_000:12_100].drop(index=[12_050]).reset_index(drop=True)
    hist.fail = True
    with pytest.raises(HistoryNotReady):
        l2.eth_history.frame_for(gapped)
    assert l2.eth_history._frame is None


def test_eth_c1_formula_matches_live_v2_config():
    from autotrade_v2.app.strategy.final14_config import get_case
    from autotrade_v3.live.lab_h1.eth_c1_h1 import ETH_C1_SL_PCT, ETH_C1_TP_PCT
    cfg = get_case("ETH-USDT", 1)
    assert cfg["tp_pct"] == pytest.approx(ETH_C1_TP_PCT) and cfg["sl_pct"] == pytest.approx(ETH_C1_SL_PCT)
    assert cfg["layer2"] == "SMC_VETO" and cfg["entry_variant"] == "MFI_L50_S50"


def test_missing_indicator_skips_only_eth_c1_and_reports(monkeypatch):
    l2, hist, ctl, big = _setup()

    def boom(_candles):
        raise ValueError("ema150_4h missing")

    monkeypatch.setattr(l2mod, "compute_eth_c1_l2_features", boom)
    m15 = _m15(big, 12_000)
    got = l2.calculate(_snap("ETH-USDT", m15))
    assert all(s.combo_name != "C1" for s in got)
    assert any(step == "eth_c1_h1" for _, step, _ in ctl.calls)
    want = [x.event_id for x in scan_latest("ETH-USDT", m15) if case_name(x.combo) != "C1"]
    assert [s.event_id for s in got] == want


def test_dispatch_hands_signals_to_l3_once_per_signal():
    class Exec:
        def __init__(self):
            self.seen = []

        def submit(self, sig):
            self.seen.append(sig.event_id)

    ex = Exec()
    l2, hist, ctl, big = _setup()
    l2.executor = ex
    sigs = l2.calculate(_snap("ETH-USDT", _m15(big, 12_000)))
    l2.dispatch(sigs)
    assert ex.seen == [s.event_id for s in sigs]
    assert len(set(ex.seen)) == len(ex.seen)
