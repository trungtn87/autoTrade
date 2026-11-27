"""V3_LIVE_23_LAB_11_V1: registry parity with LAB_8 keys, L2 wiring, and ETH C3/C13 filter wiring.

MOCK / offline tests: no network, no broker, no DB. The base lab generator is replaced by fixed candidate
signals, so only the Lab 10 filter wrappers (ETH-USDT/C3 4H overextension, ETH-USDT/C13 1H vol regime) run.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.live.l2_lab10 import V3Lab10Strategy
from autotrade_v3.live.l2_lab5 import Lab5Engine
from autotrade_v3.live.strategy_registry import (
    DEFAULT_REGISTRY,
    REGISTRY_LAB11_23,
    REGISTRY_PLUS44_44,
    load_registry,
)

M15_MS = 900_000


EXPECTED_KEYS = {
    ("BTC-USDT", 1), ("BTC-USDT", 2), ("BTC-USDT", 4), ("BTC-USDT", 6), ("BTC-USDT", 7), ("BTC-USDT", 9),
    ("BTC-USDT", 10), ("BTC-USDT", 11), ("BTC-USDT", 101), ("BTC-USDT", 103), ("BTC-USDT", 104), ("BTC-USDT", 106),
    ("ETH-USDT", 1), ("ETH-USDT", 3), ("ETH-USDT", 4), ("ETH-USDT", 6), ("ETH-USDT", 7), ("ETH-USDT", 10),
    ("ETH-USDT", 101), ("ETH-USDT", 102), ("ETH-USDT", 103), ("ETH-USDT", 104), ("ETH-USDT", 105),
}


def test_lab10_registry_keeps_fixed_23_keys_for_rollback() -> None:
    reg = load_registry(REGISTRY_LAB11_23)
    assert DEFAULT_REGISTRY == "V3_LIVE_44_PLUS43_V2_1_TP_V1"
    assert len(reg.combos) == 23
    assert reg.allowed() == EXPECTED_KEYS
    assert {c.signal_variant for c in reg.combos} == {"LAB_10"}
    assert all(c.tp_pct > 0 and c.sl_pct > 0 for c in reg.combos)


def test_lab10_engine_wires_v3_lab10_strategy_for_registry() -> None:
    reg = load_registry(REGISTRY_LAB11_23)
    eng = Lab5Engine(reg, strategy=V3Lab10Strategy())
    assert type(eng.strategy).__name__ == "V3Lab10Strategy"
    assert eng.strategy.strategy_id == "V3_LAB_10"


def _frame(n: int, seed: int, drift: float, noise: float, start_ms: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, noise, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, noise, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, noise, n)))
    ts = [start_ms + k * M15_MS for k in range(n)]
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "volume": np.full(n, 500.0)}, index=pd.to_datetime(ts, unit="ms", utc=True))
    df["open_time"] = ts
    return df


def test_lab10_eth_c3_and_c13_filters_drop_and_keep_through_flat_module(monkeypatch) -> None:
    from autotrade_v3.live import l2_lab10 as flat

    start = 1_700_000_000_000 // M15_MS * M15_MS
    # 3000 bars of strong uptrend (overextends 4H EMA20/50 for C3 BUY) then 1200 calm + 800 bars of vol burst.
    up = _frame(3000, 1, 0.0015, 0.001, start)
    calm = _frame(1200, 2, 0.0, 0.0005, start + 3000 * M15_MS)
    burst = _frame(800, 3, 0.0, 0.02, start + 4200 * M15_MS)
    eth = pd.concat([up, calm, burst])
    eth = eth.iloc[:]  # keep index monotonic
    data = {"ETH-USDT": eth, "BTC-USDT": eth.copy()}
    ts = eth["open_time"].to_numpy()
    close = eth["close"].to_numpy()

    def sig(combo: int, side: str, i: int):
        cm = int(ts[i]) + M15_MS - 1
        return flat.Signal(symbol="ETH-USDT", combo_id=combo, combo_name=f"C{combo}", timeframe="15m", side=side,
                           signal_close_ms=cm, signal_close_dt=pd.Timestamp(cm, unit="ms", tz="UTC"),
                           bar_position=i, bar_open_ms=int(ts[i]), entry_price=float(close[i]), tp_pct=0.02,
                           sl_pct=0.008, tp_price=1.0, sl_price=1.0, approved=True, warmup_ready=True, smc_dir=0,
                           ob_blocked=False, provider="test", is_new6=False)

    i_up, i_calm, i_burst = 2900, 3500, 4220  # 4220: 1H atr_ratio_50 ~7.4 (burst onset); 4600 has decayed to ~1.06
    cands = [sig(3, "BUY", i_up), sig(3, "SELL", i_up), sig(103, "SELL", i_calm), sig(103, "SELL", i_burst)]
    monkeypatch.setattr(flat.V3Lab8Strategy, "generate_signals", lambda self, *a, **k: list(cands))

    out = V3Lab10Strategy().generate_signals(data, symbols=None, only_approved=True, only_warmup_ready=True)
    kept = {(s.combo_id, s.side, int(s.bar_position)) for s in out}
    # C3 BUY in a 4H uptrend overextension is dropped; C3 SELL (direction-adjusted) is kept.
    assert (3, "BUY", i_up) not in kept
    assert (3, "SELL", i_up) in kept
    # C13 SELL: calm 1H ATR regime kept; vol-burst 1H ATR ratio above 1.50 dropped.
    assert (103, "SELL", i_calm) in kept
    assert (103, "SELL", i_burst) not in kept
