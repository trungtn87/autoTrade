"""L2 for ``V3_LIVE_23_LAB_5_V1``: the lab ``V3_LAB_5@5.0.0`` strategy called directly.

No strategy logic is re-implemented here. ``V3Lab5Strategy.generate_signals_for_combos`` is the single
source of signals (indicators, Layer 2, ETH-C15 V5 filter, BTC-C14 H3a Kumo band, ETH-C1 H1_BOTH_1, exit
parameters). This module only:

* converts live M15 candles (``open_time`` ms) to the lab frame format,
* restricts the lab output to the combo-symbols of the active registry (exactly 23),
* keeps only signals that close on the latest closed M15 bar of the symbol,
* maps each lab ``Signal`` to the ``V3Signal`` that L3 already consumes.

Cross-symbol filters need BTC and ETH history together, so a symbol is evaluated only when both
frames exist and end on the same closed M15 bar (fail closed otherwise).
"""
from __future__ import annotations

import pandas as pd

from .l3_execution import V3Signal
from .strategy_registry import StrategyRegistry

M15_MS = 900_000
BTC = "BTC-USDT"
ETH = "ETH-USDT"
# ETH history needed by the 4H EMA150 / H1 features of the lab strategy (same as V3_LAB_4_TOP23).
LAB5_WINDOW_BARS = 12_000


def to_lab_frame(candles: pd.DataFrame) -> pd.DataFrame:
    """Live candles (``open_time`` ms, open/high/low/close/volume) -> UTC DatetimeIndex (ns) frame."""
    d = candles.copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        idx = pd.to_datetime(d["open_time"].astype("int64"), unit="ms", utc=True)
        d.index = pd.DatetimeIndex(idx).as_unit("ns")
    d = d.sort_index()
    d = d[~d.index.duplicated(keep="last")]
    return d[["open", "high", "low", "close", "volume"]].astype(float)


class Lab5Engine:
    """Runs the lab V3_LAB_5 strategy on closed M15 frames, restricted to one registry."""

    def __init__(self, registry: StrategyRegistry, strategy=None) -> None:
        if strategy is None:
            from autotrade_v3.lab.v3_lab_5_loader import load_v3_lab_5_strategy
            strategy = load_v3_lab_5_strategy()
        self.strategy = strategy
        self.registry_version: str = registry.version
        self.spec_hash: str = strategy.get_spec_hash()
        self.allowed: set[tuple[str, int]] = {(c.symbol, int(c.combo_id)) for c in registry.combos}
        lab_keys = {(c.symbol, int(c.combo_id)) for c in strategy.spec().combos}
        if not self.allowed <= lab_keys:
            raise ValueError(f"{registry.version}: combo không có trong V3_LAB_5: {sorted(self.allowed - lab_keys)}")

    def calculate(self, symbol: str, frames: dict[str, pd.DataFrame]) -> list[V3Signal]:
        symbol = str(symbol).upper()
        if symbol not in frames or BTC not in frames or ETH not in frames:
            return []  # fail closed: cross-symbol filters need both histories
        if frames[BTC].index[-1] != frames[ETH].index[-1]:
            return []  # fail closed: BTC and ETH must be cut at the same closed M15 bar
        data = {BTC: to_lab_frame(frames[BTC].tail(LAB5_WINDOW_BARS)),
                ETH: to_lab_frame(frames[ETH].tail(LAB5_WINDOW_BARS))}
        mine = data[symbol]
        if mine.empty:
            return []
        latest_close_ms = int(mine.index[-1].value // 1_000_000) + M15_MS - 1
        sym_allowed = [sym for sym in (BTC, ETH) if any(s == sym for s, _ in self.allowed)]
        if symbol not in sym_allowed:
            return []
        sigs = self.strategy.generate_signals_for_combos(
            data, allowed_combos=self.allowed, symbols=[symbol], only_approved=True, only_warmup_ready=True)
        out: list[V3Signal] = []
        for s in sigs:
            if s.symbol != symbol or int(s.signal_close_ms) != latest_close_ms:
                continue
            if (s.symbol, int(s.combo_id)) not in self.allowed:
                continue  # defensive: never hand a combo outside the registry to L3
            out.append(V3Signal(
                event_id=f"{s.symbol}|{s.timeframe}|{s.combo_name}|{s.side}|{int(s.signal_close_ms)}",
                symbol=s.symbol, combo=int(s.combo_id), combo_name=str(s.combo_name), side=s.side,
                timeframe=str(s.timeframe), close_time=int(s.signal_close_ms), entry=float(s.entry_price),
                tp=float(s.tp_price), sl=float(s.sl_price), tp_pct=float(s.tp_pct), sl_pct=float(s.sl_pct),
                registry_version=self.registry_version, spec_hash=self.spec_hash,
                signal_variant=None,
            ))
        return out
