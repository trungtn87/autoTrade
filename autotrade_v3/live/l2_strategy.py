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

from .l3_execution import V3Signal

class HistoryNotReady(RuntimeError):
    """ETH C1 needs ETH_C1_BARS closed M15 bars in DB; other combos keep running."""

ETH_C1_BARS = 12_000
V18_WINDOW_BARS = 3_400  # other combos keep the 3400-bar window (20FINAL semantics unchanged)
from .lab_h1.eth_c1_h1 import (H1_BUY_MAX_SLOPE_4H_PCT, H1_SELL_MIN_DIST_EMA150_4H_PCT, M15_MS,
                              EthC1H1Both1Strategy, compute_eth_c1_l2_features)
from .store import ms_to_dt
from .strategy_registry import REGISTRY_20FINAL, REGISTRY_LAB5_23, REGISTRY_LAB8_23, StrategyRegistry, load_registry
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

        ETH-USDT/C1 H1_BOTH_1 uses the PR #128 generator on the 12000-bar ETH history, unless
        ``eth_c1_h1_enabled`` is False (V3_ETH_C1_H1_L2=off) under 20FINAL. Under a JSON registry whose
        ETH C1 spec is H1_BOTH_1 it is always on: the flag cannot turn ETH C1 back into the baseline.
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
        self.lab5 = None  # V3_LIVE_23_LAB_5_V1: lab V3_LAB_5 strategy called directly (l2_lab5.py)
        self._lab5_frames: dict = {}
        self.eth_c1_spec = None
        reg = self.registry
        if reg is not None and reg.version.startswith((REGISTRY_LAB5_23, REGISTRY_LAB8_23)):
            from .l2_lab5 import Lab5Engine
            if reg.version.startswith(REGISTRY_LAB8_23):
                from autotrade_v3.lab.v3_lab_8_loader import load_v3_lab_8_strategy as _load_lab
            else:
                from autotrade_v3.lab.v3_lab_5_loader import load_v3_lab_5_strategy as _load_lab
            self.lab5 = Lab5Engine(reg, strategy=_load_lab())
            eth_c1_h1_enabled = False  # ETH-C1 H1_BOTH_1 is computed inside the lab strategy
        elif reg is not None and reg.version != REGISTRY_20FINAL:
            spec = reg.get(ETH, 1)
            if spec is not None and spec.signal_variant == "H1_BOTH_1":
                self.eth_c1_spec = spec
                if (float(reg.h1_both_1.get("buy_max_slope_4h_pct")) != H1_BUY_MAX_SLOPE_4H_PCT
                        or float(reg.h1_both_1.get("sell_min_dist_ema150_4h_pct")) != H1_SELL_MIN_DIST_EMA150_4H_PCT
                        or reg.h1_both_1.get("pre_edge_h1") is not True):
                    # Fail closed: never trade an ETH C1 that is not the approved spec.
                    self.registry_error = f"registry {reg.version}: H1_BOTH_1 params differ from the live generator"
                    self.registry = reg = None
            if reg is not None:
                # Registry path: ETH C1 H1_BOTH_1 comes from the PR #128 generator on the 12000-bar ETH
                # history (paged warm-up in L1). V18Engine computes every other combo on the 3400-bar snapshot.
                self.v18 = V18Engine(dataclasses.replace(
                    reg, combos=tuple(c for c in reg.combos if c.signal_variant != "H1_BOTH_1")))
            eth_c1_h1_enabled = self.eth_c1_spec is not None and reg is not None
        self.last_signals: list[dict] = []
        # ETH-USDT/C1 only: H1_BOTH_1 generator (Lab P3 semantics). False = pure V2 output (rollback).
        self.eth_c1_gen = EthC1H1Both1Strategy(enable_eth_c1_h1_l2=True) if eth_c1_h1_enabled else None

    def _report(self, kind: str, message: str, symbol: str = ETH, error_code: str | None = None) -> None:
        if self.control is not None:  # tests and offline tools run without a control log
            self.control.report("L2", kind, message, symbol=symbol, error_code=error_code)

    def registry_view(self) -> dict:
        if self.registry is None:
            return {"version": None, "error": self.registry_error, "new_entries": "blocked_invalid_registry"}
        return {**self.registry.summary(), "provenance": dict(self.registry.provenance)}

    def calculate(self, snapshot) -> list[V3Signal]:
        if self.registry is None:
            raise RuntimeError(self.registry_error or "registry chưa nạp được")
        reg = self.registry
        signals: list[V3Signal]
        if self.lab5 is not None:
            from .l2_lab5 import LAB5_WINDOW_BARS
            sym = str(snapshot.symbol).upper()
            self._lab5_frames[sym] = snapshot.candles.tail(LAB5_WINDOW_BARS)
            return self.lab5.calculate(sym, self._lab5_frames)
        if self.v18 is not None:
            signals = [V3Signal(event_id=s.event_id, symbol=s.symbol, combo=s.combo, combo_name=s.combo_name,
                                side=s.side, timeframe=s.timeframe, close_time=s.close_time, entry=s.entry, tp=s.tp,
                                sl=s.sl, tp_pct=s.tp_pct, sl_pct=s.sl_pct, registry_version=s.registry_version,
                                spec_hash=s.spec_hash, signal_variant=s.signal_variant)
                       for s in self.v18.calculate(snapshot.symbol, snapshot.candles.tail(V18_WINDOW_BARS))]
        else:
            signals = []
            window = dataclasses.replace(snapshot, candles=snapshot.candles.tail(V18_WINDOW_BARS))
            for i in self.engine.calculate(window):
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
            history = snapshot.candles
            if len(history) < ETH_C1_BARS:
                raise HistoryNotReady(f"ETH M15 trong DB {len(history)}/{ETH_C1_BARS} nến")
            c1 = self._eth_c1_latest(snapshot, history)
        except HistoryNotReady as exc:  # not enough trusted history: no unconfirmed ETH C1, other combos run
            log.info("V3_L2_ETH_C1_WAIT %s", exc)
            return others
        except Exception as exc:  # missing indicator: emit no unconfirmed ETH C1, keep the other combos
            self._report("eth_c1_h1",
                                f"Thiếu dữ liệu chỉ báo ETH C1 H1, không phát tín hiệu ETH C1 lượt này: {exc}",
                                symbol=ETH)
            log.error("V3_L2_ETH_C1_H1_ERROR error_class=%s", type(exc).__name__)
            return others
        return others + c1

    def _eth_c1_latest(self, snapshot, history) -> list[V3Signal]:
        """Latest closed M15 bar only, the same way V2 emits C1 at the 1H boundary."""
        gen = self.eth_c1_gen
        gen.last_l2_rejections = []
        feats = compute_eth_c1_l2_features(history)
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
            spec = self.registry.get(ETH, 1) if self.registry is not None else None
            out.append(V3Signal(event_id=f"{ETH}|1h|C1|{sig.side}|{close_time}", symbol=ETH, combo=1,
                                combo_name="C1", side=sig.side, timeframe="1h", close_time=close_time,
                                tp_pct=spec.tp_pct if spec else None, sl_pct=spec.sl_pct if spec else None,
                                registry_version=self.registry.version if self.registry is not None else None,
                                spec_hash=spec.spec_hash if spec else None, signal_variant="H1_BOTH_1",
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
