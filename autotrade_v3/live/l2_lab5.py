"""L2 adapter for ``V3_LIVE_23_LAB_11_V1``: calculate each symbol independently.

No strategy logic is re-implemented here. ``V3Lab10Strategy.generate_signals_for_combos`` is the single
source of signals (indicators, Layer 2, ETH-C15 V5 filter, BTC-C14 H3a Kumo band, ETH-C1 H1_BOTH_1, exit
parameters). This module only:

* converts live M15 candles (``open_time`` ms) to the lab frame format,
* restricts the lab output to the combo-symbols of the active registry (exactly 23),
* keeps only signals that close on the latest closed M15 bar of the symbol,
* maps each lab ``Signal`` to the ``V3Signal`` that L3 already consumes.

The active Lab 10 signal rules consume each symbol's own closed M15 candles. BTC
must never wait for ETH or vice versa. Time alignment uses each frame's UTC timestamps.
"""
from __future__ import annotations

import pandas as pd

from .l2_resampler import TimeframeCloseGate
from .l2_tv21 import TV21_CONFIG_MAP, TV21ComboConfig, generate_tv21_signals_from_m15
from .l3_execution import V3Signal
from .strategy_registry import StrategyRegistry, default_window_bars_for

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
    """Runs PRUNE23 (V3_LAB_5/10) and TV21 (l2_tv21) on closed M15 frames for one registry."""

    def __init__(self, registry: StrategyRegistry, strategy=None) -> None:
        tv21_specs = [c for c in registry.combos if c.signal_variant == "TV21"]
        lab_specs = [c for c in registry.combos if c.signal_variant != "TV21"]

        if strategy is None and lab_specs:
            from autotrade_v3.lab.v3_lab_5_loader import load_v3_lab_5_strategy
            strategy = load_v3_lab_5_strategy()
        self.strategy = strategy
        self.registry_version: str = registry.version
        self.registry_spec_hash: str = registry.spec_hash
        self.spec_hash: str = strategy.get_spec_hash() if strategy is not None else registry.spec_hash
        self.allowed: set[tuple[str, int]] = {(c.symbol, int(c.combo_id)) for c in registry.combos}
        self.lab_allowed: set[tuple[str, int]] = {(c.symbol, int(c.combo_id)) for c in lab_specs}
        self.combo_windows: dict[tuple[str, int], int] = {
            (c.symbol, int(c.combo_id)): c.effective_window_bars for c in registry.combos
        }
        if self.lab_allowed:
            if strategy is None:
                raise ValueError(f"{registry.version}: missing lab strategy for PRUNE23 combos")
            lab_keys = {(c.symbol, int(c.combo_id)) for c in strategy.spec().combos}
            if not self.lab_allowed <= lab_keys:
                raise ValueError(f"{registry.version}: combo không có trong V3_LAB_5: {sorted(self.lab_allowed - lab_keys)}")

        self.tv21_configs: dict[tuple[str, int], TV21ComboConfig] = {}
        for spec in tv21_specs:
            cfg = TV21_CONFIG_MAP.get(spec.token)
            if cfg is None or cfg.symbol != spec.symbol or cfg.combo_id != int(spec.combo_id):
                raise ValueError(f"{registry.version}: unknown TV21 combo {spec.symbol}/{spec.combo_id}")
            self.tv21_configs[(cfg.symbol, cfg.combo_id)] = cfg
        self.tv21_gate: TimeframeCloseGate = TimeframeCloseGate()

    def calculate(self, symbol: str, frames: dict[str, pd.DataFrame], *, use_gate: bool = True) -> list[V3Signal]:
        """Evaluate only this symbol's latest closed M15 bar; never gate on the other symbol.

        PRUNE23 combos dispatch through ``self.strategy.generate_signals_for_combos``.
        TV21 combos dispatch through ``generate_tv21_signals_from_m15`` with
        ``only_latest_closed=True`` and ``TimeframeCloseGate``.
        """
        symbol = str(symbol).upper()
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            return []

        lab_allowed = getattr(self, "lab_allowed", None)
        if lab_allowed is None:
            lab_allowed = self.allowed
        tv21_configs = getattr(self, "tv21_configs", None) or {}
        lab_symbol_combos = [(sym, cid) for (sym, cid) in lab_allowed if sym == symbol]
        tv21_symbol_configs = [cfg for (sym, _), cfg in sorted(tv21_configs.items()) if sym == symbol]
        if not lab_symbol_combos and not tv21_symbol_configs:
            return []

        out: list[V3Signal] = []
        if lab_symbol_combos:
            combo_windows = getattr(self, "combo_windows", None) or {}
            default_w = default_window_bars_for(symbol)
            groups: dict[int, set[tuple[str, int]]] = {}
            for key in lab_symbol_combos:
                w = int(combo_windows.get(key, default_w))
                groups.setdefault(w, set()).add(key)

            for window_bars in sorted(groups):
                group_combos = groups[window_bars]
                data = {symbol: to_lab_frame(frame.tail(window_bars))}
                mine = data[symbol]
                if mine.empty:
                    continue
                latest_close_ms = int(mine.index[-1].value // 1_000_000) + M15_MS - 1
                sigs = self.strategy.generate_signals_for_combos(
                    data, allowed_combos=group_combos, symbols=[symbol],
                    only_approved=True, only_warmup_ready=True)
                for s in sigs:
                    if s.symbol != symbol or int(s.signal_close_ms) != latest_close_ms:
                        continue
                    if (s.symbol, int(s.combo_id)) not in group_combos:
                        continue  # defensive: never hand a combo outside the registry to L3
                    out.append(V3Signal(
                        event_id=f"{s.symbol}|{s.timeframe}|{s.combo_name}|{s.side}|{int(s.signal_close_ms)}",
                        symbol=s.symbol, combo=int(s.combo_id), combo_name=str(s.combo_name), side=s.side,
                        timeframe=str(s.timeframe), close_time=int(s.signal_close_ms), entry=float(s.entry_price),
                        tp=float(s.tp_price), sl=float(s.sl_price), tp_pct=float(s.tp_pct), sl_pct=float(s.sl_pct),
                        registry_version=self.registry_version, spec_hash=self.spec_hash,
                        signal_variant=None,
                    ))

        if tv21_symbol_configs:
            gate = getattr(self, "tv21_gate", None) if use_gate else None
            tv21_spec_hash = getattr(self, "registry_spec_hash", None) or self.spec_hash
            for cfg in tv21_symbol_configs:
                out.extend(
                    generate_tv21_signals_from_m15(
                        frame,
                        cfg,
                        only_latest_closed=True,
                        gate=gate,
                        registry_version=self.registry_version,
                        spec_hash=tv21_spec_hash,
                    )
                )
        return out

    def preview(self, symbol: str, frames: dict[str, pd.DataFrame]) -> list[V3Signal]:
        """Read-only evaluation without advancing the TV21 close gate."""
        return self.calculate(symbol, frames, use_gate=False)
