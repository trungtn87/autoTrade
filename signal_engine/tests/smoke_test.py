import numpy as np
import pandas as pd

from app.strategy import combo15_events, combo60_events, scan_latest, smc_direction


def make_df(n: int, minutes: int, seed: int):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.002, n)
    close = 50000 * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0.001, 0.0005, n))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.lognormal(5, 0.5, n)
    step = minutes * 60_000
    open_time = np.arange(n, dtype=np.int64) * step
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


def main():
    m15 = make_df(1000, 15, 1)
    h1 = make_df(600, 60, 2)
    h4 = make_df(350, 240, 3)
    h6 = make_df(250, 360, 4)

    e15 = combo15_events(m15, h4)
    e60 = combo60_events(h1, h4, h6)
    s15 = smc_direction(m15)
    s60 = smc_direction(h1)
    signals = scan_latest("BTC-USDT", m15, h1, h4, h6)

    assert set(e15) == {5, 7, 8, 9, 10}
    assert set(e60) == {1, 2, 3, 4, 6}
    assert len(s15) == len(m15)
    assert len(s60) == len(h1)
    print("smoke ok; signals:", [s.event_id for s in signals])


if __name__ == "__main__":
    main()
