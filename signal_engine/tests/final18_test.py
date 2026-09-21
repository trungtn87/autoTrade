from __future__ import annotations

import json
import numpy as np
import pandas as pd

from app.final_config import FINAL_CASES, DISABLED_CASES, enabled_combos, get_case
from app.final_strategy import combo_readiness, scan_latest
from app.position_manager import register_execution, manage_symbol_positions, is_case_active
from app.strategy import Signal


def make_df(n: int, minutes: int, seed: int, start_ms: int = 1704067200000):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.002, n)
    close = 50000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0.001, 0.0005, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.lognormal(5, 0.5, n)
    step = minutes * 60_000
    open_time = start_ms + np.arange(n, dtype=np.int64) * step
    close_time = open_time + step - 1
    return pd.DataFrame({
        "open_time": open_time,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "close_time": close_time,
    })


class MemoryState:
    def __init__(self):
        self.d = {}
    def get_runtime_value(self, key, default=""):
        return self.d.get(key, default)
    def set_runtime_value(self, key, value):
        self.d[key] = str(value)


class FakeExecutor:
    def __init__(self):
        self.replaced = []
        self.cancelled = []
        self.closed = []
        self.status = {"leg1": "NEW", "leg2": "NEW"}

    def final18_order_detail(self, symbol, order_id):
        return {"status": self.status.get(order_id, "NEW")}

    def final18_replace_stop(self, symbol, old_order_id, side, position_side, qty, stop_price):
        new_id = old_order_id + "r"
        self.status[new_id] = "NEW"
        self.replaced.append((symbol, old_order_id, new_id, side, position_side, qty, stop_price))
        return {"order_id": new_id, "order": {"status": "NEW"}}

    def final18_cancel_order(self, symbol, order_id):
        self.cancelled.append((symbol, order_id))
        return {"code": 0}

    def final18_close_slice(self, symbol, position_side, qty):
        self.closed.append((symbol, position_side, qty))
        return {"code": 0}


def test_config():
    assert sum(len(v) for v in FINAL_CASES.values()) == 18
    assert sum(len(v) for v in DISABLED_CASES.values()) == 4
    assert enabled_combos("BTC-USDT") == (1,2,3,4,6,7,9,10,11)
    assert enabled_combos("ETH-USDT") == (1,2,4,5,6,7,8,10,11)
    assert get_case("BTC-USDT", 5) is None
    assert get_case("ETH-USDT", 3) is None
    assert get_case("BTC-USDT", 11)["stage"] == "L3"


def test_strategy_smoke():
    m15 = make_df(4000, 15, 1)
    h1 = make_df(1200, 60, 2)
    h4 = make_df(500, 240, 3)
    h6 = make_df(350, 360, 4)

    rb = combo_readiness(m15, h1, h4, h6, symbol="BTC-USDT")
    re = combo_readiness(m15, h1, h4, h6, symbol="ETH-USDT")
    assert set(rb) == set(enabled_combos("BTC-USDT"))
    assert set(re) == set(enabled_combos("ETH-USDT"))
    assert all(x["ready"] for x in rb.values())
    assert all(x["ready"] for x in re.values())

    sb = scan_latest("BTC-USDT", m15, h1, h4, h6)
    se = scan_latest("ETH-USDT", m15, h1, h4, h6)
    assert all(s.combo in enabled_combos("BTC-USDT") for s in sb)
    assert all(s.combo in enabled_combos("ETH-USDT") for s in se)


def test_manager_protect_next_bar():
    state = MemoryState()
    ex = FakeExecutor()
    cfg = get_case("BTC-USDT", 10)
    assert cfg["protect_pct"] == 0.002

    sig = Signal(
        symbol="BTC-USDT",
        combo=10,
        side="BUY",
        timeframe="15m",
        close_time=1000,
        entry=100.0,
        tp=105.0,
        sl=99.1,
        smc_dir=0,
    )
    result = {
        "ok": True,
        "entry_filled": True,
        "avg_price": 100.0,
        "price_precision": 2,
        "sl": 99.1,
        "protect_price": 100.2,
        "leg1_qty": 0.5,
        "leg2_qty": 0.5,
        "trail1_activation": 100.5,
        "trail2_activation": 101.0,
        "trail1_callback": 0.005,
        "trail2_callback": 0.005,
        "leg1_order_id": "leg1",
        "leg2_order_id": "leg2",
        "leg1_stop_price": 99.1,
        "leg2_stop_price": 99.1,
    }
    register_execution(state, sig, result)
    assert is_case_active(state, "BTC-USDT", 10)

    # The first closed candle after entry crosses T1 activation.
    row = pd.Series({"close_time": 2000, "high": 100.8, "low": 99.8})
    actions = manage_symbol_positions(state, ex, "BTC-USDT", row)
    assert any(a.get("action") == "stop_promoted" for a in actions)

    data = json.loads(state.d["final18_active_positions_v1"])
    rec = data[0]
    assert rec["protect_active"] is True
    # T2 has not activated, but shared +0.2 protection must still move its stop.
    assert rec["legs"]["2"]["current_stop"] >= 100.2


if __name__ == "__main__":
    test_config()
    test_strategy_smoke()
    test_manager_protect_next_bar()
    print("final18 tests ok")
