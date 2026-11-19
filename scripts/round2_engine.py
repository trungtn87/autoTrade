"""Round 2 Research Engine: Causal Indicators, Custom Exit Legs, Chronological Controllers,
and Simulator Wrapper for the 15 Active Round 2 Variants on V3_LAB_4@4.0.0.
"""
from __future__ import annotations

import copy
import dataclasses
import heapq
from pathlib import Path
import sys
from typing import Any, Literal

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.engine.execution_simulator import ExecutionEvent, SimulationResult, TradeRecord
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.account import AccountLedger, verify_mode_c_accounting_invariant
from autotrade_v3.sim.admission import AllSignals, OneTradePerCombo, Portfolio
from autotrade_v3.sim.ambiguity import resolve_tp_and_sl
from autotrade_v3.sim.engine import (
    UnifiedSimulator,
    _PendingPortfolioLeg,
    _build_exit_rules,
    _build_leg_specs,
    _canonicalize_mode,
    _resolve_exit_policy,
    _resolve_signal_id,
    _to_execution_event,
)
from autotrade_v3.sim.leg import simulate_leg
from autotrade_v3.sim.types import Event, ExitRules, LegOutcome, LegSpec, M1BarArrays, SignalDecision
from autotrade_v3.strategy_core.indicators.indicators import atr, dmi
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.indicators.smc_structure import smc_direction
from autotrade_v3.strategy_core.strategies.final20.logic import _clean_bool, _ichimoku, _kama_er


def build_round2_extra_features(m15_dict: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Build strictly causal 1H exit/entry features for Round 2 variants."""
    out: dict[str, Any] = {}
    for sym in ("BTC-USDT", "ETH-USDT"):
        d15 = ensure_dt(m15_dict[sym])
        d1 = resample_ohlcv(d15, "1h")
        c1 = d1["close"].to_numpy(float)
        h1 = d1["high"].to_numpy(float)
        l1 = d1["low"].to_numpy(float)
        a14 = atr(d1, 14).to_numpy(float)
        _, _, adx1 = dmi(d1, 14, 14)
        adx_arr = adx1.to_numpy(float)
        smc1 = smc_direction(d1, 50, False).to_numpy(int)
        _, _, span_a, span_b = _ichimoku(h1, l1, 12, 30, 60)

        d1_open_ms = datetime_index_to_ms(d1.index)
        d1_close_ms = d1_open_ms + 3_600_000 - 1

        # ATR% lookup by 1H close_ms
        atr_pct_by_1h: dict[int, float] = {}
        for i in range(len(d1)):
            if np.isfinite(a14[i]) and c1[i] > 0:
                atr_pct_by_1h[int(d1_close_ms[i])] = float(a14[i] / c1[i])

        # Closed-1H regime exit trigger arrays (sorted close_ms where invalidation is True)
        smc_flip_sell_exit_ms = d1_close_ms[smc1 == 1]    # SELL exits if smc_dir_1h == +1
        smc_flip_buy_exit_ms = d1_close_ms[smc1 == -1]    # BUY exits if smc_dir_1h == -1

        kumo_top = np.maximum(span_a, span_b)
        kumo_bot = np.minimum(span_a, span_b)
        kumo_buy_inv = np.isfinite(kumo_bot) & (c1 < kumo_bot)
        kumo_sell_inv = np.isfinite(kumo_top) & (c1 > kumo_top)
        kumo_buy_exit_ms = d1_close_ms[kumo_buy_inv]
        kumo_sell_exit_ms = d1_close_ms[kumo_sell_inv]

        adx_prev = np.r_[np.nan, adx_arr[:-1]]
        adx_rollover = np.isfinite(adx_arr) & np.isfinite(adx_prev) & (adx_arr < adx_prev) & (adx_arr < 28.0)
        adx_rollover_ms = d1_close_ms[adx_rollover]

        # BTC/C15 KAMA_ER(40,2,40,0.4) state series for B-R2-03
        kama, er = _kama_er(c1, 40, 2, 40)
        slope = np.r_[np.nan, np.diff(kama)]
        kama_buy = _clean_bool((c1 > kama) & (slope > 0) & (er > 0.4), d1.index).to_numpy(bool)
        kama_sell = _clean_bool((c1 < kama) & (slope < 0) & (er > 0.4), d1.index).to_numpy(bool)

        out[sym] = {
            "atr_pct_by_1h": atr_pct_by_1h,
            "smc_flip_buy_exit_ms": smc_flip_buy_exit_ms,
            "smc_flip_sell_exit_ms": smc_flip_sell_exit_ms,
            "kumo_buy_exit_ms": kumo_buy_exit_ms,
            "kumo_sell_exit_ms": kumo_sell_exit_ms,
            "adx_rollover_ms": adx_rollover_ms,
            "kama_1h_close_ms": d1_close_ms,
            "kama_1h_buy": kama_buy,
            "kama_1h_sell": kama_sell,
        }
    return out


class BtcC15ResetController:
    """B-R2-03: After any BTC/C15 trade exit, require 1H KAMA_ER state to reset False->True before re-entry."""

    def __init__(self, kama_close_ms: np.ndarray, kama_buy: np.ndarray, kama_sell: np.ndarray) -> None:
        self.kama_close_ms = kama_close_ms
        self.kama_cond = {"BUY": kama_buy, "SELL": kama_sell}
        self.state = {"BUY": "OPEN", "SELL": "OPEN"}
        self.cursor = {"BUY": 0, "SELL": 0}

    @staticmethod
    def is_target(symbol: str, combo_id: int) -> bool:
        return symbol == "BTC-USDT" and int(combo_id) == 105

    def on_trade_exit(self, symbol: str, combo_id: int, side: str, exit_time_ms: int) -> None:
        if not self.is_target(symbol, combo_id):
            return
        start_idx = int(np.searchsorted(self.kama_close_ms, int(exit_time_ms), side="right"))
        self.state[side] = "WAIT_FALSE"
        self.cursor[side] = start_idx

    def should_allow(self, sig: Signal) -> tuple[bool, str]:
        if not self.is_target(sig.symbol, sig.combo_id):
            return True, "ACCEPTED"
        side = sig.side
        sig_cms = int(sig.signal_close_ms)
        idx = self.cursor[side]
        st = self.state[side]
        cond_arr = self.kama_cond[side]
        n = len(self.kama_close_ms)
        while idx < n and int(self.kama_close_ms[idx]) <= sig_cms:
            if st == "WAIT_FALSE":
                if not bool(cond_arr[idx]):
                    st = "WAIT_TRUE"
            elif st == "WAIT_TRUE":
                if bool(cond_arr[idx]):
                    st = "OPEN"
            idx += 1
        self.cursor[side] = idx
        self.state[side] = st
        if st != "OPEN":
            return False, "BLOCKED_BY_SIGNAL_RESET_REENTRY"
        return True, "ACCEPTED"


def simulate_round2_leg(
    bars_m1: M1BarArrays,
    entry_idx: int,
    entry_price: float,
    side: str,
    leg_spec: LegSpec,
    exit_rules: ExitRules,
    *,
    be_trigger_pct: float | None = None,
    be_offset_pct: float | None = None,
    regime_exit_close_ms: np.ndarray | None = None,
) -> LegOutcome:
    """Simulate a single leg with optional Round 2 exit rules (BE lock, Time Stop, Closed-1H Regime Exit).

    Strictly conservative intrabar ambiguity resolution; zero look-ahead.
    """
    if be_trigger_pct is None and regime_exit_close_ms is None:
        return simulate_leg(
            bars_m1=bars_m1,
            entry_idx=entry_idx,
            entry_price=entry_price,
            side=side,
            leg_spec=leg_spec,
            exit_rules=exit_rules,
            ambiguity_policy="CONSERVATIVE",
        )

    is_buy = side.upper() in ("BUY", "L")
    tp_price = entry_price * (1.0 + exit_rules.tp_pct) if is_buy else entry_price * (1.0 - exit_rules.tp_pct)
    sl_price = entry_price * (1.0 - exit_rules.sl_pct) if is_buy else entry_price * (1.0 + exit_rules.sl_pct)

    be_armed = False
    trigger_px = 0.0
    be_stop_px = sl_price
    if be_trigger_pct is not None and be_offset_pct is not None:
        trigger_px = entry_price * (1.0 + be_trigger_pct) if is_buy else entry_price * (1.0 - be_trigger_pct)
        be_stop_px = entry_price * (1.0 + be_offset_pct) if is_buy else entry_price * (1.0 + be_offset_pct)
        # Note: for SELL, be_offset_pct is negative (e.g., -0.0014 -> entry_price * 0.9986 < entry_price)

    next_regime_ptr = 0
    if regime_exit_close_ms is not None and len(regime_exit_close_ms) > 0:
        entry_t_ms = int(bars_m1.t_ms[entry_idx])
        next_regime_ptr = int(np.searchsorted(regime_exit_close_ms, entry_t_ms, side="left"))

    opens = bars_m1.opens
    highs = bars_m1.highs
    lows = bars_m1.lows
    closes = bars_m1.closes
    t_ms = bars_m1.t_ms
    n_bars = bars_m1.n_bars

    ambiguous = False
    amb_type = "NONE"
    exit_price = 0.0
    exit_reason = ""
    exit_bar_idx = n_bars - 1

    for i in range(entry_idx, n_bars):
        bar_t_ms = int(t_ms[i])

        # 1. Closed-1H regime exit: actionable at the open of M1 bar `i` iff closed 1H bar closed strictly before `bar_t_ms`
        if (
            i > entry_idx
            and regime_exit_close_ms is not None
            and next_regime_ptr < len(regime_exit_close_ms)
            and int(regime_exit_close_ms[next_regime_ptr]) < bar_t_ms
        ):
            exit_price = float(opens[i]) if opens is not None else float(closes[i])
            exit_reason = "REGIME_EXIT"
            exit_bar_idx = i
            break

        # 2. Max bars held (Time Stop)
        if exit_rules.max_bars_held is not None and (i - entry_idx) >= exit_rules.max_bars_held:
            exit_price = float(opens[i]) if opens is not None else float(closes[i])
            exit_reason = "MAX_BARS_HELD"
            exit_bar_idx = i
            break

        cur_sl = be_stop_px if be_armed else sl_price
        cur_sl_reason = "BREAKEVEN_STOP" if be_armed else "SL"

        # 3. Gap open check
        if i > entry_idx and opens is not None:
            op = float(opens[i])
            if is_buy:
                if op <= cur_sl:
                    exit_price, exit_reason, exit_bar_idx = op, cur_sl_reason, i
                    break
                elif op >= tp_price:
                    exit_price, exit_reason, exit_bar_idx = op, "TP", i
                    break
            else:
                if op >= cur_sl:
                    exit_price, exit_reason, exit_bar_idx = op, cur_sl_reason, i
                    break
                elif op <= tp_price:
                    exit_price, exit_reason, exit_bar_idx = op, "TP", i
                    break

        h = float(highs[i])
        l = float(lows[i])

        if is_buy:
            hit_tp = h >= tp_price
            hit_sl = l <= cur_sl
        else:
            hit_tp = l <= tp_price
            hit_sl = h >= cur_sl

        # 4. TP & SL collision (CONSERVATIVE)
        if hit_tp and hit_sl:
            bar_op = float(opens[i]) if opens is not None else float(closes[i])
            bar_cl = float(closes[i])
            res = resolve_tp_and_sl(
                policy="CONSERVATIVE",
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=cur_sl,
                bar_open=bar_op,
                bar_close=bar_cl,
            )
            ambiguous = res.intrabar_ambiguous
            amb_type = res.ambiguity_type
            exit_price = res.exit_price
            exit_reason = cur_sl_reason if res.exit_reason in ("SL", "SL_INTRABAR_AMBIGUOUS") else res.exit_reason
            exit_bar_idx = i
            break
        elif hit_sl:
            exit_price, exit_reason, exit_bar_idx = cur_sl, cur_sl_reason, i
            break
        elif hit_tp:
            exit_price, exit_reason, exit_bar_idx = tp_price, "TP", i
            break

        # 5. Check MFE breakeven trigger (only if not already armed and neither TP nor SL hit above)
        if be_trigger_pct is not None and not be_armed:
            hit_trig = (h >= trigger_px) if is_buy else (l <= trigger_px)
            if hit_trig:
                # Conservative intrabar check: if bar also touches be_stop_px, exit conservatively at be_stop_px
                hit_be_same_bar = (l <= be_stop_px) if is_buy else (h >= be_stop_px)
                if hit_be_same_bar:
                    ambiguous = True
                    amb_type = "BE_TRIGGER_AND_STOP_SAME_BAR"
                    exit_price = be_stop_px
                    exit_reason = "BREAKEVEN_STOP"
                    exit_bar_idx = i
                    break
                be_armed = True
    else:
        exit_bar_idx = n_bars - 1
        exit_price = float(closes[exit_bar_idx])
        exit_reason = "FORCE_CLOSE"

    return LegOutcome(
        leg_id=leg_spec.leg_id,
        trade_id=leg_spec.trade_id,
        leg_type=leg_spec.leg_type,
        entry_idx=entry_idx,
        exit_bar_idx=exit_bar_idx,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        bars_held=exit_bar_idx - entry_idx,
        intrabar_ambiguous=ambiguous,
        intrabar_trailing_ambiguous=False,
        ambiguity_type=amb_type,
        ambiguity_policy="CONSERVATIVE",
    )


class Round2ResearchSimulator(UnifiedSimulator):
    """UnifiedSimulator extension supporting Round 2 target-combo custom exits, reset controller, and dev guard."""

    def __init__(
        self,
        *args: Any,
        c15_controller: Any,
        target_symbol: str | None = None,
        target_combo_id: int | None = None,
        target_tp_override_by_cms: dict[int, float] | None = None,
        target_sl_override_by_cms: dict[int, float] | None = None,
        target_fixed_tp_pct: float | None = None,
        target_fixed_sl_pct: float | None = None,
        target_max_bars_held: int | None = None,
        target_be_trigger_pct: float | None = None,
        target_be_offset_pct: float | None = None,
        target_regime_exit_by_side: dict[str, np.ndarray] | None = None,
        btc_c15_reset_ctrl: BtcC15ResetController | None = None,
        target_max_adverse_dev_pct: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.c15_controller = c15_controller
        self.target_symbol = target_symbol
        self.target_combo_id = int(target_combo_id) if target_combo_id is not None else None
        self.target_tp_override_by_cms = target_tp_override_by_cms
        self.target_sl_override_by_cms = target_sl_override_by_cms
        self.target_fixed_tp_pct = target_fixed_tp_pct
        self.target_fixed_sl_pct = target_fixed_sl_pct
        self.target_max_bars_held = target_max_bars_held
        self.target_be_trigger_pct = target_be_trigger_pct
        self.target_be_offset_pct = target_be_offset_pct
        self.target_regime_exit_by_side = target_regime_exit_by_side
        self.btc_c15_reset_ctrl = btc_c15_reset_ctrl
        self.target_max_adverse_dev_pct = target_max_adverse_dev_pct

    def _is_target(self, sig: Signal) -> bool:
        return (
            self.target_symbol is not None
            and self.target_combo_id is not None
            and sig.symbol == self.target_symbol
            and int(sig.combo_id) == self.target_combo_id
        )

    def _resolve_target_exit_policy(self, sig: Signal):
        ep = _resolve_exit_policy(sig, self.final14_exit_mode, self.trailing_act_pct, self.trailing_cb_pct)
        if not self._is_target(sig):
            return ep, False
        cms = int(sig.signal_close_ms)
        tp = float(ep.tp_pct)
        sl = float(ep.sl_pct)
        max_bars = ep.max_bars_held
        if self.target_fixed_tp_pct is not None:
            tp = self.target_fixed_tp_pct
        if self.target_fixed_sl_pct is not None:
            sl = self.target_fixed_sl_pct
        if self.target_tp_override_by_cms is not None and cms in self.target_tp_override_by_cms:
            tp = self.target_tp_override_by_cms[cms]
        if self.target_sl_override_by_cms is not None and cms in self.target_sl_override_by_cms:
            sl = self.target_sl_override_by_cms[cms]
        if self.target_max_bars_held is not None:
            max_bars = self.target_max_bars_held
        new_ep = dataclasses.replace(ep, tp_pct=tp, sl_pct=sl, max_bars_held=max_bars)
        return new_ep, True

    def _simulate_target_or_base_leg(
        self,
        sig: Signal,
        is_target: bool,
        bars_m1: M1BarArrays,
        entry_idx: int,
        entry_price: float,
        leg_spec: LegSpec,
        exit_rules: ExitRules,
    ) -> LegOutcome:
        if not is_target:
            return simulate_leg(
                bars_m1=bars_m1,
                entry_idx=entry_idx,
                entry_price=entry_price,
                side=sig.side,
                leg_spec=leg_spec,
                exit_rules=exit_rules,
                ambiguity_policy=self.ambiguity_policy,
            )
        reg_ms = self.target_regime_exit_by_side.get(sig.side) if self.target_regime_exit_by_side else None
        return simulate_round2_leg(
            bars_m1=bars_m1,
            entry_idx=entry_idx,
            entry_price=entry_price,
            side=sig.side,
            leg_spec=leg_spec,
            exit_rules=exit_rules,
            be_trigger_pct=self.target_be_trigger_pct,
            be_offset_pct=self.target_be_offset_pct,
            regime_exit_close_ms=reg_ms,
        )

    def _passes_entry_dev_guard(self, sig: Signal, raw_open_px: float) -> bool:
        if not self._is_target(sig) or self.target_max_adverse_dev_pct is None:
            return True
        ref_px = float(sig.entry_price)
        if ref_px <= 0:
            return True
        if sig.side == "BUY":
            return raw_open_px <= ref_px * (1.0 + self.target_max_adverse_dev_pct)
        return raw_open_px >= ref_px * (1.0 - self.target_max_adverse_dev_pct)

    def run_with_arrays(
        self,
        mode: str,
        signals: list[Signal],
        arrays_by_sym: dict[str, M1BarArrays],
        pre_filtered_decisions: list[SignalDecision] | None = None,
    ) -> SimulationResult:
        if self.c15_controller is not None and hasattr(self.c15_controller, "reset"):
            self.c15_controller.reset()
        if self.btc_c15_reset_ctrl is not None:
            self.btc_c15_reset_ctrl.open_until_by_side = {"BUY": -1, "SELL": -1}
        canonical_mode = _canonicalize_mode(mode)
        sorted_signals = sorted(signals, key=lambda s: (s.signal_close_ms, int(s.combo_id), s.side))
        if canonical_mode in ("ALL_SIGNALS", "ONE_TRADE_PER_COMBO"):
            res = self._run_virtual_mode(canonical_mode, sorted_signals, arrays_by_sym)
        else:
            res = self._run_portfolio_mode_fast(
                sorted_signals,
                arrays_by_sym,
                allow_multiple_per_combo=(canonical_mode == "ALL_SIGNALS_PORTFOLIO"),
            )
        if pre_filtered_decisions:
            merged = list(res.signal_decisions) + list(pre_filtered_decisions)
            merged.sort(key=lambda d: (d.signal_close_ms, int(d.combo_id), d.side, d.signal_id))
            res.signal_decisions = merged
        return res

    def _run_virtual_mode(
        self,
        canonical_mode: Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO"],
        sorted_signals: list[Signal],
        arrays_by_sym: dict[str, M1BarArrays],
    ) -> SimulationResult:
        policy = AllSignals() if canonical_mode == "ALL_SIGNALS" else OneTradePerCombo()
        trades: list[TradeRecord] = []
        events: list[ExecutionEvent] = []
        decisions: list[SignalDecision] = []
        accepted_sigs: list[Signal] = []
        seq = 0
        open_eth_c15_exits: list[tuple[int, str, str]] = []
        open_btc_c15_exits: list[tuple[int, str]] = []

        for sig in sorted_signals:
            sig_id = _resolve_signal_id(sig)
            arr = arrays_by_sym.get(sig.symbol)
            entry_idx = self._find_entry_idx(sig, arr) if arr is not None else None
            if arr is None or entry_idx is None or entry_idx < 0 or entry_idx >= arr.n_bars:
                decisions.append(
                    SignalDecision(
                        signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                        combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                        signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                        status="UNFILLABLE", reason="NO_M1_BAR_AFTER_SIGNAL",
                    )
                )
                continue

            entry_time_ms = int(arr.t_ms[entry_idx])
            while open_eth_c15_exits and open_eth_c15_exits[0][0] < entry_time_ms:
                ex_ms, ex_side, ex_reason = heapq.heappop(open_eth_c15_exits)
                self.c15_controller.on_trade_exit("ETH-USDT", 105, ex_side, ex_ms, ex_reason)
            if self.btc_c15_reset_ctrl is not None:
                while open_btc_c15_exits and open_btc_c15_exits[0][0] < entry_time_ms:
                    ex_ms, ex_side = heapq.heappop(open_btc_c15_exits)
                    self.btc_c15_reset_ctrl.on_trade_exit("BTC-USDT", 105, ex_side, ex_ms)

            if self.c15_controller.is_target(sig.symbol, sig.combo_id):
                ok, block_reason = self.c15_controller.should_allow(sig, entry_time_ms)
                if not ok:
                    decisions.append(
                        SignalDecision(
                            signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                            combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                            signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                            status="SKIPPED", reason=block_reason,
                            entry_bar_idx=entry_idx, entry_time_ms=entry_time_ms,
                        )
                    )
                    continue

            if self.btc_c15_reset_ctrl is not None and self.btc_c15_reset_ctrl.is_target(sig.symbol, sig.combo_id):
                ok, block_reason = self.btc_c15_reset_ctrl.should_allow(sig)
                if not ok:
                    decisions.append(
                        SignalDecision(
                            signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                            combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                            signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                            status="SKIPPED", reason=block_reason,
                            entry_bar_idx=entry_idx, entry_time_ms=entry_time_ms,
                        )
                    )
                    continue

            raw_entry_px = float(arr.opens[entry_idx]) if arr.opens is not None else sig.entry_price
            if not self._passes_entry_dev_guard(sig, raw_entry_px):
                decisions.append(
                    SignalDecision(
                        signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                        combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                        signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                        status="SKIPPED", reason="SKIPPED_BY_ENTRY_DEV_GUARD",
                        entry_bar_idx=entry_idx, entry_time_ms=entry_time_ms,
                    )
                )
                continue

            req_margin = self.risk.calc_margin(sig.symbol, self.risk.notional_per_trade)
            decision = policy.evaluate(
                signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                entry_idx=entry_idx, entry_time_ms=entry_time_ms, req_margin=req_margin,
            )
            decisions.append(decision)
            if decision.status != "ACCEPTED":
                continue

            accepted_sigs.append(sig)
            trade_id = sig_id
            entry_price = self.cost_model.fill_price(
                raw_entry_px, symbol=sig.symbol, side=sig.side, is_entry=True, order_type="entry_market"
            )
            exit_policy, is_tgt = self._resolve_target_exit_policy(sig)
            leg_specs = _build_leg_specs(trade_id, self.risk.notional_per_trade, exit_policy)
            exit_rules = _build_exit_rules(exit_policy)

            max_exit_idx = entry_idx
            for leg_spec in leg_specs:
                outcome = self._simulate_target_or_base_leg(
                    sig, is_tgt, arr, entry_idx, entry_price, leg_spec, exit_rules
                )
                if outcome.exit_bar_idx > max_exit_idx:
                    max_exit_idx = outcome.exit_bar_idx

                exit_ms = int(arr.t_ms[outcome.exit_bar_idx])
                if self.c15_controller.is_target(sig.symbol, sig.combo_id):
                    heapq.heappush(open_eth_c15_exits, (exit_ms, sig.side, outcome.exit_reason))
                if self.btc_c15_reset_ctrl is not None and self.btc_c15_reset_ctrl.is_target(sig.symbol, sig.combo_id):
                    heapq.heappush(open_btc_c15_exits, (exit_ms, sig.side))

                cb = self.cost_model.evaluate_leg(
                    symbol=sig.symbol, side=sig.side, notional=leg_spec.notional,
                    entry_px_raw=raw_entry_px, exit_px_trigger=outcome.exit_price,
                    exit_reason=outcome.exit_reason, entry_time_ms=entry_time_ms,
                    exit_time_ms=exit_ms, entry_px_fill_override=entry_price,
                )
                margin = self.risk.calc_margin(sig.symbol, leg_spec.notional)
                net_pnl = cb.net_pnl
                trades.append(
                    TradeRecord(
                        trade_id=trade_id, leg_id=leg_spec.leg_id, symbol=sig.symbol,
                        combo_id=sig.combo_id, combo_name=sig.combo_name, timeframe=sig.timeframe,
                        side=sig.side, leg_type=leg_spec.leg_type, entry_time_ms=entry_time_ms,
                        entry_time_dt=arr.index[entry_idx], entry_price=cb.entry_px_fill,
                        exit_time_ms=exit_ms, exit_time_dt=arr.index[outcome.exit_bar_idx],
                        exit_price=cb.exit_px_fill, exit_reason=outcome.exit_reason,
                        qty=cb.qty, notional=leg_spec.notional, margin=margin,
                        gross_pnl=cb.gross_pnl_fill, fee_total=cb.fee_total, net_pnl=net_pnl,
                        return_pct=net_pnl / leg_spec.notional if leg_spec.notional > 0 else 0.0,
                        intrabar_ambiguous=outcome.intrabar_ambiguous, bars_held=outcome.bars_held,
                        provider=str(arr.providers[entry_idx]) if len(arr.providers) else sig.provider,
                        signal_id=sig_id, intrabar_trailing_ambiguous=outcome.intrabar_trailing_ambiguous,
                        exit_policy_hash=exit_rules.exit_policy_hash, tp_pct=exit_rules.tp_pct,
                        sl_pct=exit_rules.sl_pct, trailing_act_pct=exit_rules.trailing_act_pct,
                        trailing_cb_pct=exit_rules.trailing_cb_pct, max_bars_held=exit_rules.max_bars_held,
                        entry_px_raw=cb.entry_px_raw, entry_px_fill=cb.entry_px_fill,
                        exit_px_trigger=cb.exit_px_trigger, exit_px_fill=cb.exit_px_fill,
                        fee_entry=cb.fee_entry, fee_exit=cb.fee_exit, spread_cost=cb.spread_cost,
                        slippage_cost=cb.slippage_cost, funding_cost=cb.funding_cost,
                        gross_pnl_raw=cb.gross_pnl_raw, ambiguity_type=outcome.ambiguity_type,
                        ambiguity_policy=self.ambiguity_policy, pnl_own_entry=net_pnl, pnl_vwap=net_pnl,
                    )
                )
            if isinstance(policy, OneTradePerCombo):
                policy.record_open(sig.symbol, sig.combo_id, trade_id, max_exit_idx)

        return self._build_result(
            mode=canonical_mode, trades=trades, events=events, decisions=decisions,
            accepted_sigs=accepted_sigs, snapshots=pd.DataFrame(), extra_summary={},
        )

    def _run_portfolio_mode_fast(
        self,
        sorted_signals: list[Signal],
        arrays_by_sym: dict[str, M1BarArrays],
        *,
        allow_multiple_per_combo: bool = False,
    ) -> SimulationResult:
        out_mode = "ALL_SIGNALS_PORTFOLIO" if allow_multiple_per_combo else "BINGX_PORTFOLIO"
        first_sym = next(iter(arrays_by_sym.keys()))
        t_ms_array = arrays_by_sym[first_sym].t_ms
        n_bars = len(t_ms_array)
        first_ts = int(t_ms_array[0])
        last_ts = int(t_ms_array[-1])

        opens_by_sym = {sym: arr.opens if arr.opens is not None else arr.closes for sym, arr in arrays_by_sym.items()}
        closes_by_sym = {sym: arr.closes for sym, arr in arrays_by_sym.items()}
        account = AccountLedger(
            initial_equity=self.risk.initial_equity, symbols=list(arrays_by_sym.keys()),
            opens_by_symbol=opens_by_sym, closes_by_symbol=closes_by_sym,
        )
        portfolio_policy = Portfolio(account, allow_multiple_per_combo=allow_multiple_per_combo)

        decisions: list[SignalDecision] = []
        signals_by_bar: dict[int, list[Signal]] = {}
        for sig in sorted_signals:
            sig_id = _resolve_signal_id(sig)
            earliest_exec_ms = sig.signal_close_ms + 1
            if sig.symbol not in arrays_by_sym or earliest_exec_ms < first_ts or earliest_exec_ms > last_ts:
                decisions.append(
                    SignalDecision(
                        signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                        combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                        signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                        status="UNFILLABLE", reason="NO_M1_BAR_AFTER_SIGNAL",
                    )
                )
                continue
            idx = int(np.searchsorted(t_ms_array, earliest_exec_ms))
            if 0 <= idx < n_bars:
                signals_by_bar.setdefault(idx, []).append(sig)

        legs_closing_at: dict[int, dict[tuple[str, str], list[_PendingPortfolioLeg]]] = {}
        closed_trades: list[TradeRecord] = []
        accepted_sigs: list[Signal] = []
        intrabar_tp_sl_count = 0
        positions_values = tuple(account.positions.values())

        for bar_idx in range(n_bars):
            ts = int(t_ms_array[bar_idx])
            bar_sigs = signals_by_bar.get(bar_idx)
            if bar_sigs:
                for sig in bar_sigs:
                    sig_id = _resolve_signal_id(sig)
                    if self.c15_controller.is_target(sig.symbol, sig.combo_id):
                        ok, block_reason = self.c15_controller.should_allow(sig, ts)
                        if not ok:
                            decisions.append(
                                SignalDecision(
                                    signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                                    combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                                    signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                                    status="SKIPPED", reason=block_reason,
                                    entry_bar_idx=bar_idx, entry_time_ms=ts,
                                )
                            )
                            continue
                    if self.btc_c15_reset_ctrl is not None and self.btc_c15_reset_ctrl.is_target(sig.symbol, sig.combo_id):
                        ok, block_reason = self.btc_c15_reset_ctrl.should_allow(sig)
                        if not ok:
                            decisions.append(
                                SignalDecision(
                                    signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                                    combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                                    signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                                    status="SKIPPED", reason=block_reason,
                                    entry_bar_idx=bar_idx, entry_time_ms=ts,
                                )
                            )
                            continue

                    raw_open_px = float(opens_by_sym[sig.symbol][bar_idx])
                    if not self._passes_entry_dev_guard(sig, raw_open_px):
                        decisions.append(
                            SignalDecision(
                                signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                                combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                                signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                                status="SKIPPED", reason="SKIPPED_BY_ENTRY_DEV_GUARD",
                                entry_bar_idx=bar_idx, entry_time_ms=ts,
                            )
                        )
                        continue

                    notional = self.risk.notional_per_trade
                    req_margin = self.risk.calc_margin(sig.symbol, notional)
                    decision = portfolio_policy.evaluate(
                        signal_id=sig_id, symbol=sig.symbol, combo_id=sig.combo_id,
                        combo_name=sig.combo_name, timeframe=sig.timeframe, side=sig.side,
                        signal_close_ms=sig.signal_close_ms, signal_close_dt=str(sig.signal_close_dt),
                        entry_idx=bar_idx, entry_time_ms=ts, req_margin=req_margin,
                    )
                    decisions.append(decision)
                    if decision.status != "ACCEPTED":
                        continue

                    accepted_sigs.append(sig)
                    arr = arrays_by_sym[sig.symbol]
                    entry_px = self.cost_model.fill_price(
                        raw_open_px, symbol=sig.symbol, side=sig.side, is_entry=True, order_type="entry_market"
                    )
                    entry_fee = notional * self.cost_model.params_for(sig.symbol).fee_rate_for("entry_market")
                    account.open_position(
                        symbol=sig.symbol, side=sig.side, notional=notional,
                        entry_price=entry_px, req_margin=req_margin, entry_fee=entry_fee,
                    )
                    trade_id = sig_id
                    exit_policy, is_tgt = self._resolve_target_exit_policy(sig)
                    leg_specs = _build_leg_specs(trade_id, notional, exit_policy)
                    exit_rules = _build_exit_rules(exit_policy)
                    provider = str(arr.providers[bar_idx]) if len(arr.providers) else sig.provider

                    for leg_spec in leg_specs:
                        w = leg_spec.notional / notional if notional > 0 else 1.0
                        outcome = self._simulate_target_or_base_leg(
                            sig, is_tgt, arr, bar_idx, entry_px, leg_spec, exit_rules
                        )
                        portfolio_policy.record_open_leg(sig.symbol, sig.combo_id, leg_spec.leg_id, trade_id)
                        pending = _PendingPortfolioLeg(
                            sig=sig, signal_id=sig_id, trade_id=trade_id, leg_spec=leg_spec,
                            leg_qty=leg_spec.notional / entry_px, leg_margin=req_margin * w,
                            leg_entry_fee=entry_fee * w, entry_price=entry_px, entry_px_raw=raw_open_px,
                            outcome=outcome, exit_rules=exit_rules, provider=provider,
                        )
                        legs_closing_at.setdefault(outcome.exit_bar_idx, {}).setdefault(
                            (sig.symbol, sig.side), []
                        ).append(pending)

            closing_for_bar = legs_closing_at.pop(bar_idx, None)
            if closing_for_bar:
                for pos_key in account.positions:
                    for pending in closing_for_bar.get(pos_key, ()):
                        outcome = pending.outcome
                        if outcome.intrabar_ambiguous:
                            intrabar_tp_sl_count += 1
                        sig = pending.sig
                        arr = arrays_by_sym[sig.symbol]
                        cb = self.cost_model.evaluate_leg(
                            symbol=sig.symbol, side=sig.side, notional=pending.leg_spec.notional,
                            entry_px_raw=pending.entry_px_raw, exit_px_trigger=outcome.exit_price,
                            exit_reason=outcome.exit_reason, entry_time_ms=int(arr.t_ms[outcome.entry_idx]),
                            exit_time_ms=ts, entry_px_fill_override=pending.entry_price,
                        )
                        effective_entry, gross_pnl, total_fee, net_pnl = account.close_leg(
                            symbol=sig.symbol, side=sig.side, leg_qty=pending.leg_qty,
                            leg_margin=pending.leg_margin, leg_entry_price=pending.entry_price,
                            exec_exit_price=cb.exit_px_fill, entry_fee=cb.fee_entry,
                            exit_fee=cb.fee_exit, funding_cost=cb.funding_cost,
                        )
                        portfolio_policy.record_close_leg(sig.symbol, sig.combo_id, pending.leg_spec.leg_id)
                        self.c15_controller.on_trade_exit(sig.symbol, sig.combo_id, sig.side, ts, outcome.exit_reason)
                        if self.btc_c15_reset_ctrl is not None:
                            self.btc_c15_reset_ctrl.on_trade_exit(sig.symbol, sig.combo_id, sig.side, ts)

                        closed_trades.append(
                            TradeRecord(
                                trade_id=pending.trade_id, leg_id=pending.leg_spec.leg_id, symbol=sig.symbol,
                                combo_id=sig.combo_id, combo_name=sig.combo_name, timeframe=sig.timeframe,
                                side=sig.side, leg_type=pending.leg_spec.leg_type,
                                entry_time_ms=int(arr.t_ms[outcome.entry_idx]),
                                entry_time_dt=arr.index[outcome.entry_idx], entry_price=effective_entry,
                                exit_time_ms=ts, exit_time_dt=arr.index[bar_idx],
                                exit_price=cb.exit_px_fill, exit_reason=outcome.exit_reason,
                                qty=pending.leg_qty, notional=pending.leg_spec.notional, margin=pending.leg_margin,
                                gross_pnl=gross_pnl, fee_total=total_fee, net_pnl=net_pnl,
                                return_pct=net_pnl / pending.leg_spec.notional if pending.leg_spec.notional > 0 else 0.0,
                                intrabar_ambiguous=outcome.intrabar_ambiguous, bars_held=outcome.bars_held,
                                provider=pending.provider, signal_id=pending.signal_id,
                                intrabar_trailing_ambiguous=outcome.intrabar_trailing_ambiguous,
                                exit_policy_hash=pending.exit_rules.exit_policy_hash,
                                tp_pct=pending.exit_rules.tp_pct, sl_pct=pending.exit_rules.sl_pct,
                                trailing_act_pct=pending.exit_rules.trailing_act_pct,
                                trailing_cb_pct=pending.exit_rules.trailing_cb_pct,
                                max_bars_held=pending.exit_rules.max_bars_held,
                                entry_px_raw=cb.entry_px_raw, entry_px_fill=cb.entry_px_fill,
                                exit_px_trigger=cb.exit_px_trigger, exit_px_fill=cb.exit_px_fill,
                                fee_entry=cb.fee_entry, fee_exit=cb.fee_exit, spread_cost=cb.spread_cost,
                                slippage_cost=cb.slippage_cost, funding_cost=cb.funding_cost,
                                gross_pnl_raw=cb.gross_pnl_raw, ambiguity_type=outcome.ambiguity_type,
                                ambiguity_policy=self.ambiguity_policy, pnl_own_entry=cb.net_pnl, pnl_vwap=net_pnl,
                            )
                        )

            unrealized_total = 0.0
            for pos in positions_values:
                if pos.qty > 1e-9:
                    cl_px = float(closes_by_sym[pos.symbol][bar_idx])
                    if pos.side == "BUY":
                        unrealized_total += (cl_px - pos.avg_price) * pos.qty
                    else:
                        unrealized_total += (pos.avg_price - cl_px) * pos.qty
            m2m_equity = account.balance + unrealized_total
            account.final_equity = m2m_equity
            if m2m_equity > account.peak_equity:
                account.peak_equity = m2m_equity
            dd_dollars = account.peak_equity - m2m_equity
            if dd_dollars > account.max_m2m_dd_dollars:
                account.max_m2m_dd_dollars = dd_dollars
            dd_pct = (dd_dollars / account.peak_equity) * 100.0 if account.peak_equity > 0 else 0.0
            if dd_pct > account.max_m2m_dd_pct:
                account.max_m2m_dd_pct = dd_pct

        closed_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
        acct_inv = verify_mode_c_accounting_invariant(
            closed_trades, initial_equity=self.risk.initial_equity, final_equity=account.final_equity
        )
        m2m_summary = {
            "status": "OK",
            "initial_equity": self.risk.initial_equity,
            "final_equity": round(account.final_equity, 4),
            "peak_equity": round(account.peak_equity, 4),
            "m2m_max_drawdown_dollars": round(account.max_m2m_dd_dollars, 4),
            "m2m_max_drawdown_pct": round(account.max_m2m_dd_pct, 2),
            "intrabar_tp_sl_ambiguous_count": intrabar_tp_sl_count,
            "accounting_invariant": acct_inv,
        }
        return self._build_result(
            mode=out_mode, trades=closed_trades, events=[], decisions=decisions,
            accepted_sigs=accepted_sigs, snapshots=pd.DataFrame(), extra_summary=m2m_summary,
        )
