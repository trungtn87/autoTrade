"""L2 STRATEGY: run the active strategy registry; persist and hand off every signal.

Registries ``V3_LIVE_20_POSITIVE_FIXED_V1`` (20 combo-symbols, default) and
``V3_LIVE_18_H1_V2`` (18) are computed by ``strategy_v18.V18Engine``, except ETH-USDT/C1 H1_BOTH_1 which
always uses the PR #128 generator on the 12000-bar ETH history; registry ``20FINAL`` (rollback) is the untouched pure
V2 engine (``autotrade_v2.app.strategy``: FINAL14 + NEW6). This module only converts its output, stores the
9 agreed fields in ``strategy_signals`` and dispatches to L3 (execution). L5 emits a signal-only note when
execution is disabled; live execution emits only the BingX-confirmed L3 result.
No SMC/AI/extra condition is added here.
"""
from __future__ import annotations

import dataclasses
import logging

from autotrade_v2.app.strategy.engine import StrategyEngine
from autotrade_v2.app.strategy.final14_config import case_name, get_case
from autotrade_v2.app.strategy.final14_exact_strategy import hard_tp_sl

from .eth_c1_history import EthC1History, HistoryNotReady
from .l3_execution import V3Signal
from .lab_h1.eth_c1_h1 import (H1_BUY_MAX_SLOPE_4H_PCT, H1_SELL_MIN_DIST_EMA150_4H_PCT, M15_MS,
                              EthC1H1Both1Strategy, compute_eth_c1_l2_features)
from .store import ms_to_dt
from .strategy_registry import REGISTRY_20FINAL, StrategyRegistry, load_registry
from .strategy_v18 import V18Engine

log = logging.getLogger("autotrade_v3.l2")


def signal_row(sig: V3Signal) -> dict:
    """Exactly the 9 strategy_signals fields (created_at is set by the DB)."""
    return {"event_id": sig.event_id, "symbol": sig.symbol, "combo": sig.combo_name, "side": sig.side,
            "signal_time": ms_to_dt(sig.close_time), "entry": sig.entry, "tp": sig.tp, "sl": sig.sl}


ETH = "ETH-USDT"


class L2Strategy:
    def __init__(self, store, control, alert=None, executor=None, engine: StrategyEngine | None = None,
                 registry: str | StrategyRegistry | None = None, eth_c1_h1_enabled: bool = True, historical=None):
        """``registry=None`` keeps the 20FINAL engine (tests / rollback default).

        ETH-USDT/C1 H1_BOTH_1 always comes from the PR #128 generator on the 12000-bar ETH history
        (EMA150 4H needs it to converge). Under 20FINAL it replaces the V2 C1 unless
        ``eth_c1_h1_enabled`` is False (V3_ETH_C1_H1_L2=off). Under a JSON registry whose ETH C1 spec is
        H1_BOTH_1 it is mandatory: the flag cannot turn ETH C1 back into the V2 baseline.
        """
        self.store = store
        self.control = control
        self.alert = alert
        self.executor = executor
        self.engine = engine or StrategyEngine()
        self.registry_error: str | None = None
        self.registry: StrategyRegistry | None
        if isinstance(registry, StrategyRegistry):
            self.registry = registry
        else:
            try:
                self.registry = load_registry(registry or REGISTRY_20FINAL)
            except ValueError as exc:
                # Wrong configuration: emit NO new entries (fail closed) but keep L1/L4 running
                # so open positions stay protected and reconciled.
                self.registry, self.registry_error = None, str(exc)
        self.v18: V18Engine | None = None
        self.eth_c1_spec = None
        reg = self.registry
        if reg is not None and reg.version != REGISTRY_20FINAL:
            spec = reg.get(ETH, 1)
            if spec is not None and spec.signal_variant == "H1_BOTH_1":
                self.eth_c1_spec = spec
                if (float(reg.h1_both_1.get("buy_max_slope_4h_pct")) != H1_BUY_MAX_SLOPE_4H_PCT
                        or float(reg.h1_both_1.get("sell_min_dist_ema150_4h_pct")) != H1_SELL_MIN_DIST_EMA150_4H_PCT
                        or reg.h1_both_1.get("pre_edge_h1") is not True):
                    raise ValueError(f"registry {reg.version}: H1_BOTH_1 params differ from the live generator")
            self.v18 = V18Engine(dataclasses.replace(
                reg, combos=tuple(c for c in reg.combos if c.signal_variant != "H1_BOTH_1")))
            eth_c1_h1_enabled = self.eth_c1_spec is not None
        self.last_signals: list[dict] = []
        # ETH-USDT/C1 only: H1_BOTH_1 generator (Lab P3 semantics). False = pure V2 output (rollback).
        self.eth_c1_gen = EthC1H1Both1Strategy(enable_eth_c1_h1_l2=True) if eth_c1_h1_enabled else None
        self.eth_history = EthC1History(historical) if (eth_c1_h1_enabled and historical is not None) else None

    def registry_view(self) -> dict:
        if self.registry is None:
            return {"version": None, "error": self.registry_error, "new_entries": "blocked_invalid_registry"}
        return {**self.registry.summary(), "provenance": dict(self.registry.provenance)}

    def calculate(self, snapshot) -> list[V3Signal]:
        if self.registry is None:
            raise RuntimeError(self.registry_error or "registry chưa nạp được")
        reg = self.registry
        signals: list[V3Signal]
        if self.v18 is not None:
            signals = [V3Signal(event_id=s.event_id, symbol=s.symbol, combo=s.combo, combo_name=s.combo_name,
                                side=s.side, timeframe=s.timeframe, close_time=s.close_time, entry=s.entry, tp=s.tp,
                                sl=s.sl, tp_pct=s.tp_pct, sl_pct=s.sl_pct, registry_version=s.registry_version,
                                spec_hash=s.spec_hash, signal_variant=s.signal_variant)
                       for s in self.v18.calculate(snapshot.symbol, snapshot.candles)]
        else:
            signals = []
            for i in self.engine.calculate(snapshot):
                spec = reg.get(i.symbol, int(i.combo))
                signals.append(V3Signal(event_id=i.event_id, symbol=i.symbol, combo=int(i.combo),
                                        combo_name=case_name(i.combo), side=i.side, timeframe=i.timeframe,
                                        close_time=int(i.close_time), entry=float(i.entry), tp=float(i.tp),
                                        sl=float(i.sl), tp_pct=spec.tp_pct if spec else None,
                                        sl_pct=spec.sl_pct if spec else None, registry_version=reg.version,
                                        spec_hash=spec.spec_hash if spec else None))
        if self.eth_c1_gen is None or str(snapshot.symbol).upper() != ETH:
            return signals
        # ETH C1 is replaced, not filtered: H1 must sit before edge detection (P3 pre_edge_h1=True).
        others = [s for s in signals if not (s.symbol == ETH and s.combo == 1)]
        try:
            if self.eth_history is None:
                raise HistoryNotReady("nguồn lịch sử ETH M15 chưa được gắn")
            history = self.eth_history.frame_for(snapshot.candles)
            c1 = self._eth_c1_latest(snapshot, history)
        except HistoryNotReady as exc:  # not enough trusted history: no unconfirmed ETH C1, other combos run
            self.control.report("L2", "eth_c1_h1",
                                f"ETH C1 H1 tạm dừng lượt này, chưa đủ lịch sử 12000 nến M15: {exc}", symbol=ETH)
            return others
        except Exception as exc:  # missing indicator: emit no unconfirmed ETH C1, keep the other combos
            self.control.report("L2", "eth_c1_h1",
                                f"Thiếu dữ liệu chỉ báo ETH C1 H1, không phát tín hiệu ETH C1 lượt này: {exc}",
                                symbol=ETH)
            log.error("V3_L2_ETH_C1_H1_ERROR error_class=%s", type(exc).__name__)
            return others
        return others + c1
        return out

    def dispatch(self, signals: list[V3Signal]) -> list:
        """Persist + alert + execute each signal independently. Never raises."""
        threads = []
        for sig in signals:
            # Hand to L3 first: a slow database or Discord never delays the Entry.
            if self.executor is not None:
                try:
                    threads.append(self.executor.submit(sig))
                except Exception as exc:
                    self.control.report("L2", "handoff_l3", f"Không chuyển được tín hiệu sang L3: {exc}",
                                        symbol=sig.symbol, event_id=sig.event_id)
            # Never duplicate L3's confirmed trade with a speculative L2 signal.
            # In signal-only mode there is no real order to confirm, so retain the signal note.
            if self.executor is None and self.alert is not None:
                try:
                    self.alert.signal({**signal_row(sig), "close_time_ms": sig.close_time, "timeframe": sig.timeframe})
                except Exception as exc:
                    log.error("V3_L2_ALERT_SUBMIT_FAILED error_class=%s", type(exc).__name__)
            try:
                self.store.insert_signal(signal_row(sig))
            except Exception as exc:
                self.control.report("L2", "signal_store", f"Không lưu được strategy_signals (vẫn thực thi): {exc}",
                                    symbol=sig.symbol, event_id=sig.event_id)
            self.last_signals.append({"event_id": sig.event_id, "symbol": sig.symbol, "combo": sig.combo_name,
                                      "side": sig.side, "close_time": sig.close_time})
        del self.last_signals[:-100]
        return threads
