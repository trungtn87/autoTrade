"""L2 STRATEGY: run 20FINAL exactly as frozen; persist and hand off every signal.

The calculation is the existing pure engine (``autotrade_v2.app.strategy``:
FINAL14 + NEW6 = 20FINAL). This module only converts its output, stores the
9 agreed fields in ``strategy_signals`` and dispatches to L3 (execution). L5 emits a signal-only note when
execution is disabled; live execution emits only the BingX-confirmed L3 result.
No SMC/AI/extra condition is added here.
"""
from __future__ import annotations

import logging

from autotrade_v2.app.strategy.engine import StrategyEngine
from autotrade_v2.app.strategy.final14_config import case_name

from .l3_execution import V3Signal
from .store import ms_to_dt

log = logging.getLogger("autotrade_v3.l2")


def signal_row(sig: V3Signal) -> dict:
    """Exactly the 9 strategy_signals fields (created_at is set by the DB)."""
    return {"event_id": sig.event_id, "symbol": sig.symbol, "combo": sig.combo_name, "side": sig.side,
            "signal_time": ms_to_dt(sig.close_time), "entry": sig.entry, "tp": sig.tp, "sl": sig.sl}


class L2Strategy:
    def __init__(self, store, control, alert=None, executor=None, engine: StrategyEngine | None = None):
        self.store = store
        self.control = control
        self.alert = alert
        self.executor = executor
        self.engine = engine or StrategyEngine()
        self.last_signals: list[dict] = []

    def calculate(self, snapshot) -> list[V3Signal]:
        return [V3Signal(event_id=i.event_id, symbol=i.symbol, combo=int(i.combo), combo_name=case_name(i.combo),
                         side=i.side, timeframe=i.timeframe, close_time=int(i.close_time), entry=float(i.entry),
                         tp=float(i.tp), sl=float(i.sl))
                for i in self.engine.calculate(snapshot)]

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
