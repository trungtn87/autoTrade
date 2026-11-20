"""L2 STRATEGY: run the active registry (``V3_LIVE_23_LAB_10_V1``); persist and hand off every signal.

The signals come from the flat lab module ``autotrade_v3.live.l2_lab10_flat`` (V3_LAB_10@10.0.0) through
``l2_lab5.Lab5Engine``. This module only converts its output, stores the 9 agreed fields in
``strategy_signals`` and dispatches to L3 (execution). L5 emits a signal-only note when execution is disabled;
live execution emits only the BingX-confirmed L3 result. No SMC/AI/extra condition is added here.
"""
from __future__ import annotations

import logging

from .l3_execution import V3Signal
from .store import ms_to_dt
from .strategy_registry import DEFAULT_REGISTRY, StrategyRegistry, load_registry

log = logging.getLogger("autotrade_v3.l2")


def signal_row(sig: V3Signal) -> dict:
    """Exactly the 9 strategy_signals fields (created_at is set by the DB)."""
    return {"event_id": sig.event_id, "symbol": sig.symbol, "combo": sig.combo_name, "side": sig.side,
            "signal_time": ms_to_dt(sig.close_time), "entry": sig.entry, "tp": sig.tp, "sl": sig.sl}


class L2Strategy:
    def __init__(self, store, control, alert=None, executor=None, registry: str | StrategyRegistry | None = None):
        """``registry=None`` uses the default registry (V3_STRATEGY_REGISTRY). Any invalid registry fails closed:
        no new entries are emitted, while L1/L4 keep running so open positions stay protected."""
        self.store = store
        self.control = control
        self.alert = alert
        self.executor = executor
        self.registry_error: str | None = None
        self.registry: StrategyRegistry | None
        if isinstance(registry, StrategyRegistry):
            self.registry = registry
        else:
            try:
                self.registry = load_registry(registry or DEFAULT_REGISTRY)
            except ValueError as exc:
                self.registry, self.registry_error = None, str(exc)
        self.lab5 = None
        self._lab5_frames: dict = {}
        if self.registry is not None:
            from .l2_lab5 import Lab5Engine
            from autotrade_v3.live.l2_lab10_flat import V3Lab10Strategy
            self.lab5 = Lab5Engine(self.registry, strategy=V3Lab10Strategy())
        self.last_signals: list[dict] = []

    def _report(self, kind: str, message: str, symbol: str = "ETH-USDT", error_code: str | None = None) -> None:
        if self.control is not None:  # tests and offline tools run without a control log
            self.control.report("L2", kind, message, symbol=symbol, error_code=error_code)

    def registry_view(self) -> dict:
        if self.registry is None:
            return {"version": None, "error": self.registry_error, "new_entries": "blocked_invalid_registry"}
        return {**self.registry.summary(), "provenance": dict(self.registry.provenance)}

    def calculate(self, snapshot) -> list[V3Signal]:
        if self.registry is None or self.lab5 is None:
            raise RuntimeError(self.registry_error or "registry chưa nạp được")
        from .l2_lab5 import LAB5_WINDOW_BARS
        sym = str(snapshot.symbol).upper()
        self._lab5_frames[sym] = snapshot.candles.tail(LAB5_WINDOW_BARS)
        return self.lab5.calculate(sym, self._lab5_frames)

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
