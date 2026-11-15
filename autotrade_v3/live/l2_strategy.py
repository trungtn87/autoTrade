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
from autotrade_v2.app.strategy.final14_config import case_name, get_case
from autotrade_v2.app.strategy.final14_exact_strategy import hard_tp_sl

from .l3_execution import V3Signal
from .lab_h1.eth_c1_h1 import M15_MS, EthC1H1Both1Strategy, compute_eth_c1_l2_features
from .store import ms_to_dt

log = logging.getLogger("autotrade_v3.l2")


def signal_row(sig: V3Signal) -> dict:
    """Exactly the 9 strategy_signals fields (created_at is set by the DB)."""
    return {"event_id": sig.event_id, "symbol": sig.symbol, "combo": sig.combo_name, "side": sig.side,
            "signal_time": ms_to_dt(sig.close_time), "entry": sig.entry, "tp": sig.tp, "sl": sig.sl}


ETH = "ETH-USDT"


class L2Strategy:
    def __init__(self, store, control, alert=None, executor=None, engine: StrategyEngine | None = None,
                 eth_c1_h1_enabled: bool = True):
        self.store = store
        self.control = control
        self.alert = alert
        self.executor = executor
        self.engine = engine or StrategyEngine()
        self.last_signals: list[dict] = []
        # ETH-USDT/C1 only: H1_BOTH_1 generator (Lab P3 semantics). False = pure V2 output (rollback).
        self.eth_c1_gen = EthC1H1Both1Strategy(enable_eth_c1_h1_l2=True) if eth_c1_h1_enabled else None

    def calculate(self, snapshot) -> list[V3Signal]:
        signals = [V3Signal(event_id=i.event_id, symbol=i.symbol, combo=int(i.combo), combo_name=case_name(i.combo),
                            side=i.side, timeframe=i.timeframe, close_time=int(i.close_time), entry=float(i.entry),
                            tp=float(i.tp), sl=float(i.sl))
                   for i in self.engine.calculate(snapshot)]
        if self.eth_c1_gen is None or str(snapshot.symbol).upper() != ETH:
            return signals
        # ETH C1 is replaced, not filtered: H1 must sit before edge detection (P3 pre_edge_h1=True).
        others = [s for s in signals if not (s.symbol == ETH and s.combo == 1)]
        try:
            c1 = self._eth_c1_latest(snapshot)
        except Exception as exc:  # missing indicator: emit no unconfirmed ETH C1, keep the other combos
            self.control.report("L2", "eth_c1_h1",
                                f"Thiếu dữ liệu chỉ báo ETH C1 H1, không phát tín hiệu ETH C1 lượt này: {exc}",
                                symbol=ETH)
            log.error("V3_L2_ETH_C1_H1_ERROR error_class=%s", type(exc).__name__)
            return others
        return others + c1

    def _eth_c1_latest(self, snapshot) -> list[V3Signal]:
        """Latest closed M15 bar only, the same way V2 emits C1 at the 1H boundary."""
        gen = self.eth_c1_gen
        gen.last_l2_rejections = []
        feats = compute_eth_c1_l2_features(snapshot.candles)
        buys = gen._generate_eth_c1_side(feats, side="BUY", only_approved=True, only_warmup_ready=True)
        sells = gen._generate_eth_c1_side(feats, side="SELL", only_approved=True, only_warmup_ready=True)
        latest_open_ms = int(feats["d15"].index[-1].value // 1_000_000)
        latest_close_ms = latest_open_ms + M15_MS - 1
        for rej in gen.last_l2_rejections:
            if rej["signal_close_ms"] == latest_close_ms:
                log.info("V3_L2_ETH_C1_H1_REJECT side=%s reason=%s slope_4h=%s dist_4h=%s close_ms=%s",
                         rej["side"], rej["reason"], rej["ema150_4h_slope_10bar_pct"], rej["dist_ema150_4h_pct"],
                         latest_close_ms)
        close_time = int(snapshot.candles.iloc[-1]["close_time"]) if "close_time" in snapshot.candles.columns \
            else latest_close_ms
        entry = float(feats["d15"]["close"].iloc[-1])
        cfg = get_case(ETH, 1)
        out: list[V3Signal] = []
        for sig in [s for s in buys + sells if s.signal_close_ms == latest_close_ms]:
            direction = 1 if sig.side == "BUY" else -1
            tp, sl = hard_tp_sl(entry, direction, cfg)
            out.append(V3Signal(event_id=f"{ETH}|1h|C1|{sig.side}|{close_time}", symbol=ETH, combo=1,
                                combo_name="C1", side=sig.side, timeframe="1h", close_time=close_time,
                                entry=entry, tp=float(tp), sl=float(sl)))
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
