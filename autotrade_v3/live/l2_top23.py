"""L2 Top 23 handoff: live calls the lab ``V3_LAB_4_TOP23`` strategy directly.

No strategy logic is re-implemented here. ``load_v3_lab_4_top23_strategy()`` is the
single source (Strategy Core + V3_LAB_4 + ETH-C14 A-R2-05 ATR-SL). This module only:

* converts the live M15 candles (``open_time`` ms) to the lab frame format,
* keeps the signals that close on the latest closed M15 bar of the symbol,
* maps each lab ``Signal`` to the ``V3Signal`` that L3 already consumes.

Cross-symbol filters (ETH-C10/C15 controllers, BTC-C16, 4H trend filters) need BTC and ETH
history together, so a symbol is only evaluated when both frames are available.
"""
from __future__ import annotations

import pandas as pd

from .l3_execution import V3Signal

M15_MS = 900_000
TOP23_REGISTRY = "V3_LAB_4_TOP23"
TOP23_VERSION = "V3_LAB_4_TOP23@1.0.0"
BTC = "BTC-USDT"
ETH = "ETH-USDT"
TOP23_WINDOW_BARS = 12_000  # ETH history needed by the 4H EMA150 and H1 features


def to_lab_frame(candles: pd.DataFrame) -> pd.DataFrame:
    """Live candles (``open_time`` ms, open/high/low/close/volume) -> UTC DatetimeIndex (ns) frame."""
    d = candles.copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        idx = pd.to_datetime(d["open_time"].astype("int64"), unit="ms", utc=True)
        d.index = pd.DatetimeIndex(idx).as_unit("ns")
    d = d.sort_index()
    d = d[~d.index.duplicated(keep="last")]
    return d[["open", "high", "low", "close", "volume"]].astype(float)


class Top23Engine:
    """Runs the lab Top 23 strategy on closed M15 frames and returns the latest-bar signals."""

    def __init__(self, strategy=None) -> None:
        if strategy is None:
            from autotrade_v3.lab.v3_lab_4_loader import load_v3_lab_4_top23_strategy
            strategy = load_v3_lab_4_top23_strategy()
        self.strategy = strategy
        self.spec_hash: str = strategy.get_spec_hash()
        self.allowed: set[tuple[str, int]] = {(c.symbol, int(c.combo_id)) for c in strategy.spec().combos}

    def calculate(self, symbol: str, frames: dict[str, pd.DataFrame]) -> list[V3Signal]:
        symbol = str(symbol).upper()
        if symbol not in frames or BTC not in frames or ETH not in frames:
            return []  # fail closed: cross-symbol filters need both histories
        if frames[BTC].index[-1] != frames[ETH].index[-1]:
            return []  # fail closed: BTC and ETH must be cut at the same closed M15 bar
        data = {BTC: to_lab_frame(frames[BTC].tail(TOP23_WINDOW_BARS)),
                ETH: to_lab_frame(frames[ETH].tail(TOP23_WINDOW_BARS))}
        mine = data[symbol]
        if mine.empty:
            return []
        latest_close_ms = int(mine.index[-1].value // 1_000_000) + M15_MS - 1
        sigs = self.strategy.generate_signals(data, symbols=[symbol], only_approved=True, only_warmup_ready=True)
        out: list[V3Signal] = []
        for s in sigs:
            if s.symbol != symbol or int(s.signal_close_ms) != latest_close_ms:
                continue
            if (s.symbol, int(s.combo_id)) not in self.allowed:
                continue  # defensive: never hand a combo outside the 23 to L3
            out.append(V3Signal(
                event_id=f"{s.symbol}|{s.timeframe}|{s.combo_name}|{s.side}|{int(s.signal_close_ms)}",
                symbol=s.symbol, combo=int(s.combo_id), combo_name=str(s.combo_name), side=s.side,
                timeframe=str(s.timeframe), close_time=int(s.signal_close_ms), entry=float(s.entry_price),
                tp=float(s.tp_price), sl=float(s.sl_price), tp_pct=float(s.tp_pct), sl_pct=float(s.sl_pct),
                registry_version=TOP23_VERSION, spec_hash=self.spec_hash,
                signal_variant=None,
            ))
        return out
