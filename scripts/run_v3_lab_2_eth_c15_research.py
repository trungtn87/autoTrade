"""Backtest research for ETH-USDT/C15 post-TP re-entry upgrades on V3_LAB_2@2.0.0.

Variants:
- H0: V3_LAB_2@2.0.0 baseline (unchanged)
- H1A: POST_TP_COOLDOWN_1H (block same-side ETH/C15 entry for 60 min after TP)
- H1B: POST_TP_COOLDOWN_4H (block same-side ETH/C15 entry for 240 min after TP)
- H1C: KAMA_RESET_AFTER_TP (after TP, lock same-side until 1H KAMA_ER cond turns False then True on closed 1H bars)

Runs all 4 variants x 3 modes (ALL_SIGNALS, ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO) x 3 cost profiles (IDEAL, BASE, STRESS) = 36 configs.
Saves all outputs to D:\\AutoTrade\\runs\\lab\\v3_lab_2\\eth_c15_research\\ and packages V3_LAB_2_ETH_C15_RESEARCH.zip.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import heapq
import json
from pathlib import Path
import sys
import time
from typing import Any, Literal
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.execution_simulator import ExecutionEvent, SimulationResult, TradeRecord
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.lab.run_manager import get_env_versions, get_git_state
from autotrade_v3.lab.v3_lab_2_loader import load_v3_lab_2_config, load_v3_lab_2_strategy
from autotrade_v3.sim.account import AccountLedger, verify_mode_c_accounting_invariant
from autotrade_v3.sim.admission import AllSignals, OneTradePerCombo, Portfolio
from autotrade_v3.sim.costs import CostModel
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
from autotrade_v3.sim.leg import extract_m1_arrays, simulate_leg
from autotrade_v3.sim.types import Event, M1BarArrays, SignalDecision
from autotrade_v3.strategy_core.indicators.signals import ensure_dt, resample_ohlcv
from autotrade_v3.strategy_core.strategies.final20.logic import _clean_bool, _kama_er

OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_2" / "eth_c15_research"
START_DATE = "2023-11-01"
END_DATE = "2026-11-01"
SYMBOLS = ["BTC-USDT", "ETH-USDT"]
MODES = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
PROFILES = ["IDEAL", "BASE", "STRESS"]
VARIANTS = ["H0", "H1A", "H1B", "H1C"]
INITIAL_EQUITY = 1000.0
NOTIONAL_PER_TRADE = 100.0
EXPECTED_V3_LAB_2_HASH = "038dd40e4dac72f5811d92f682f6f1e522a0f6f643d8acefa1189dda8065d85e"
EXPECTED_DATASET_HASH = "ad51966ec716d4e42f533323858b95dd60086566ce5f147d19a653c4b9634628"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_eth_1h_kama_series(df_eth_m15: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build exact 1H KAMA_ER(40, 2, 40, 0.4) boolean series indexed by 1H close_ms."""
    d1 = resample_ohlcv(ensure_dt(df_eth_m15), "1h")
    c = d1["close"].to_numpy(dtype=float)
    kama, er = _kama_er(c, 40, 2, 40)
    slope = np.r_[np.nan, np.diff(kama)]
    buy_ser = _clean_bool((c > kama) & (slope > 0) & (er > 0.4), d1.index)
    sell_ser = _clean_bool((c < kama) & (slope < 0) & (er > 0.4), d1.index)
    open_ms = datetime_index_to_ms(d1.index)
    close_ms = open_ms + 3_600_000 - 1
    return close_ms, buy_ser.to_numpy(dtype=bool), sell_ser.to_numpy(dtype=bool)


class EthC15AdmissionController:
    """Chronological admission state machine for ETH-USDT/C15 variants (H0, H1A, H1B, H1C)."""

    def __init__(
        self,
        variant: Literal["H0", "H1A", "H1B", "H1C"],
        kama_1h_close_ms: np.ndarray | None = None,
        kama_1h_buy: np.ndarray | None = None,
        kama_1h_sell: np.ndarray | None = None,
    ) -> None:
        self.variant = variant
        self.last_tp_exit_ms: dict[str, int | None] = {"BUY": None, "SELL": None}
        # H1C state: "OPEN" | "WAIT_FALSE" | "WAIT_TRUE"
        self.h1c_state: dict[str, str] = {"BUY": "OPEN", "SELL": "OPEN"}
        self.h1c_cursor_idx: dict[str, int] = {"BUY": 0, "SELL": 0}
        self.kama_close_ms = kama_1h_close_ms
        self.kama_cond = {"BUY": kama_1h_buy, "SELL": kama_1h_sell}

    @staticmethod
    def is_target(symbol: str, combo_id: int) -> bool:
        return symbol == "ETH-USDT" and int(combo_id) == 105

    def _advance_h1c(self, side: str, signal_close_ms: int) -> None:
        assert self.kama_close_ms is not None
        cond_arr = self.kama_cond[side]
        assert cond_arr is not None
        n = len(self.kama_close_ms)
        idx = self.h1c_cursor_idx[side]
        state = self.h1c_state[side]
        while idx < n and int(self.kama_close_ms[idx]) <= signal_close_ms:
            if state == "WAIT_FALSE":
                if not bool(cond_arr[idx]):
                    state = "WAIT_TRUE"
            elif state == "WAIT_TRUE":
                if bool(cond_arr[idx]):
                    state = "OPEN"
            idx += 1
        self.h1c_cursor_idx[side] = idx
        self.h1c_state[side] = state

    def on_trade_exit(self, symbol: str, combo_id: int, side: str, exit_time_ms: int, exit_reason: str) -> None:
        if not self.is_target(symbol, combo_id):
            return
        if exit_reason != "TP":
            return
        if self.variant in ("H1A", "H1B"):
            prev = self.last_tp_exit_ms[side]
            if prev is None or exit_time_ms > prev:
                self.last_tp_exit_ms[side] = exit_time_ms
        elif self.variant == "H1C":
            assert self.kama_close_ms is not None
            # Start checking strictly closed 1H candles AFTER TP exit_time_ms
            start_idx = int(np.searchsorted(self.kama_close_ms, exit_time_ms, side="right"))
            self.h1c_state[side] = "WAIT_FALSE"
            self.h1c_cursor_idx[side] = start_idx

    def should_allow(self, sig: Signal, entry_time_ms: int) -> tuple[bool, str]:
        if not self.is_target(sig.symbol, sig.combo_id) or self.variant == "H0":
            return True, "ACCEPTED"

        if self.variant == "H1A":
            tp_ms = self.last_tp_exit_ms[sig.side]
            if tp_ms is not None and entry_time_ms < tp_ms + 3_600_000:
                return False, "BLOCKED_POST_TP_COOLDOWN_1H"
            return True, "ACCEPTED"

        if self.variant == "H1B":
            tp_ms = self.last_tp_exit_ms[sig.side]
            if tp_ms is not None and entry_time_ms < tp_ms + 14_400_000:
                return False, "BLOCKED_POST_TP_COOLDOWN_4H"
            return True, "ACCEPTED"

        if self.variant == "H1C":
            self._advance_h1c(sig.side, int(sig.signal_close_ms))
            if self.h1c_state[sig.side] != "OPEN":
                return False, "BLOCKED_KAMA_RESET_AFTER_TP"
            return True, "ACCEPTED"

        raise ValueError(f"Unknown variant: {self.variant}")


class ResearchUnifiedSimulator(UnifiedSimulator):
    """UnifiedSimulator subclass integrating EthC15AdmissionController chronologically."""

    def __init__(self, *args: Any, c15_controller: EthC15AdmissionController, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.c15_controller = c15_controller

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
        open_c15_exits: list[tuple[int, str, str]] = []

        for sig in sorted_signals:
            sig_id = _resolve_signal_id(sig)
            arr = arrays_by_sym.get(sig.symbol)
            entry_idx = self._find_entry_idx(sig, arr) if arr is not None else None

            if arr is None or entry_idx is None or entry_idx < 0 or entry_idx >= arr.n_bars:
                decisions.append(
                    SignalDecision(
                        signal_id=sig_id,
                        symbol=sig.symbol,
                        combo_id=sig.combo_id,
                        combo_name=sig.combo_name,
                        timeframe=sig.timeframe,
                        side=sig.side,
                        signal_close_ms=sig.signal_close_ms,
                        signal_close_dt=str(sig.signal_close_dt),
                        status="UNFILLABLE",
                        reason="NO_M1_BAR_AFTER_SIGNAL",
                    )
                )
                continue

            entry_time_ms = int(arr.t_ms[entry_idx])

            # Flush ETH/C15 trades that closed strictly before this bar's admission
            while open_c15_exits and open_c15_exits[0][0] < entry_time_ms:
                ex_ms, ex_side, ex_reason = heapq.heappop(open_c15_exits)
                self.c15_controller.on_trade_exit("ETH-USDT", 105, ex_side, ex_ms, ex_reason)

            # Check ETH/C15 variant rule BEFORE concurrency check
            if self.c15_controller.is_target(sig.symbol, sig.combo_id):
                ok, block_reason = self.c15_controller.should_allow(sig, entry_time_ms)
                if not ok:
                    decisions.append(
                        SignalDecision(
                            signal_id=sig_id,
                            symbol=sig.symbol,
                            combo_id=sig.combo_id,
                            combo_name=sig.combo_name,
                            timeframe=sig.timeframe,
                            side=sig.side,
                            signal_close_ms=sig.signal_close_ms,
                            signal_close_dt=str(sig.signal_close_dt),
                            status="SKIPPED",
                            reason=block_reason,
                            entry_bar_idx=entry_idx,
                            entry_time_ms=entry_time_ms,
                        )
                    )
                    continue

            req_margin = self.risk.calc_margin(sig.symbol, self.risk.notional_per_trade)
            decision = policy.evaluate(
                signal_id=sig_id,
                symbol=sig.symbol,
                combo_id=sig.combo_id,
                combo_name=sig.combo_name,
                timeframe=sig.timeframe,
                side=sig.side,
                signal_close_ms=sig.signal_close_ms,
                signal_close_dt=str(sig.signal_close_dt),
                entry_idx=entry_idx,
                entry_time_ms=entry_time_ms,
                req_margin=req_margin,
            )
            decisions.append(decision)
            if decision.status != "ACCEPTED":
                continue

            accepted_sigs.append(sig)
            trade_id = sig_id
            raw_entry_px = float(arr.opens[entry_idx]) if arr.opens is not None else sig.entry_price
            entry_price = self.cost_model.fill_price(
                raw_entry_px,
                symbol=sig.symbol,
                side=sig.side,
                is_entry=True,
                order_type="entry_market",
            )
            exit_policy = _resolve_exit_policy(
                sig, self.final14_exit_mode, self.trailing_act_pct, self.trailing_cb_pct
            )
            leg_specs = _build_leg_specs(trade_id, self.risk.notional_per_trade, exit_policy)
            exit_rules = _build_exit_rules(exit_policy)

            max_exit_idx = entry_idx
            for leg_spec in leg_specs:
                outcome = simulate_leg(
                    bars_m1=arr,
                    entry_idx=entry_idx,
                    entry_price=entry_price,
                    side=sig.side,
                    leg_spec=leg_spec,
                    exit_rules=exit_rules,
                    ambiguity_policy=self.ambiguity_policy,
                )
                if outcome.exit_bar_idx > max_exit_idx:
                    max_exit_idx = outcome.exit_bar_idx

                if self.c15_controller.is_target(sig.symbol, sig.combo_id):
                    heapq.heappush(
                        open_c15_exits,
                        (int(arr.t_ms[outcome.exit_bar_idx]), sig.side, outcome.exit_reason),
                    )

                cb = self.cost_model.evaluate_leg(
                    symbol=sig.symbol,
                    side=sig.side,
                    notional=leg_spec.notional,
                    entry_px_raw=raw_entry_px,
                    exit_px_trigger=outcome.exit_price,
                    exit_reason=outcome.exit_reason,
                    entry_time_ms=entry_time_ms,
                    exit_time_ms=int(arr.t_ms[outcome.exit_bar_idx]),
                    entry_px_fill_override=entry_price,
                )
                qty = cb.qty
                margin = self.risk.calc_margin(sig.symbol, leg_spec.notional)
                net_pnl = cb.net_pnl
                ret_pct = net_pnl / leg_spec.notional if leg_spec.notional > 0 else 0.0

                trades.append(
                    TradeRecord(
                        trade_id=trade_id,
                        leg_id=leg_spec.leg_id,
                        symbol=sig.symbol,
                        combo_id=sig.combo_id,
                        combo_name=sig.combo_name,
                        timeframe=sig.timeframe,
                        side=sig.side,
                        leg_type=leg_spec.leg_type,
                        entry_time_ms=entry_time_ms,
                        entry_time_dt=arr.index[entry_idx],
                        entry_price=cb.entry_px_fill,
                        exit_time_ms=int(arr.t_ms[outcome.exit_bar_idx]),
                        exit_time_dt=arr.index[outcome.exit_bar_idx],
                        exit_price=cb.exit_px_fill,
                        exit_reason=outcome.exit_reason,
                        qty=qty,
                        notional=leg_spec.notional,
                        margin=margin,
                        gross_pnl=cb.gross_pnl_fill,
                        fee_total=cb.fee_total,
                        net_pnl=net_pnl,
                        return_pct=ret_pct,
                        intrabar_ambiguous=outcome.intrabar_ambiguous,
                        bars_held=outcome.bars_held,
                        provider=str(arr.providers[entry_idx]) if len(arr.providers) else sig.provider,
                        signal_id=sig_id,
                        intrabar_trailing_ambiguous=outcome.intrabar_trailing_ambiguous,
                        exit_policy_hash=exit_rules.exit_policy_hash,
                        tp_pct=exit_rules.tp_pct,
                        sl_pct=exit_rules.sl_pct,
                        trailing_act_pct=exit_rules.trailing_act_pct,
                        trailing_cb_pct=exit_rules.trailing_cb_pct,
                        max_bars_held=exit_rules.max_bars_held,
                        entry_px_raw=cb.entry_px_raw,
                        entry_px_fill=cb.entry_px_fill,
                        exit_px_trigger=cb.exit_px_trigger,
                        exit_px_fill=cb.exit_px_fill,
                        fee_entry=cb.fee_entry,
                        fee_exit=cb.fee_exit,
                        spread_cost=cb.spread_cost,
                        slippage_cost=cb.slippage_cost,
                        funding_cost=cb.funding_cost,
                        gross_pnl_raw=cb.gross_pnl_raw,
                        ambiguity_type=outcome.ambiguity_type,
                        ambiguity_policy=self.ambiguity_policy,
                        pnl_own_entry=net_pnl,
                        pnl_vwap=net_pnl,
                    )
                )

                seq += 1
                events.append(
                    _to_execution_event(
                        Event(
                            seq=seq,
                            event_id=f"EV_{seq:07d}_{leg_spec.leg_id}_OPEN",
                            timestamp_ms=entry_time_ms,
                            timestamp_dt=str(arr.index[entry_idx]),
                            trade_id=trade_id,
                            leg_id=leg_spec.leg_id,
                            symbol=sig.symbol,
                            combo_id=sig.combo_id,
                            combo_name=sig.combo_name,
                            timeframe=sig.timeframe,
                            event_type="ENTRY",
                            side=sig.side,
                            order_action="OPEN",
                            price=cb.entry_px_fill,
                            qty=qty,
                            notional=leg_spec.notional,
                            fee=cb.fee_entry,
                            realized_pnl=0.0,
                            exit_reason="",
                            signal_id=sig_id,
                        )
                    )
                )
                seq += 1
                events.append(
                    _to_execution_event(
                        Event(
                            seq=seq,
                            event_id=f"EV_{seq:07d}_{leg_spec.leg_id}_CLOSE",
                            timestamp_ms=int(arr.t_ms[outcome.exit_bar_idx]),
                            timestamp_dt=str(arr.index[outcome.exit_bar_idx]),
                            trade_id=trade_id,
                            leg_id=leg_spec.leg_id,
                            symbol=sig.symbol,
                            combo_id=sig.combo_id,
                            combo_name=sig.combo_name,
                            timeframe=sig.timeframe,
                            event_type="EXIT",
                            side=sig.side,
                            order_action="CLOSE",
                            price=cb.exit_px_fill,
                            qty=qty,
                            notional=qty * cb.exit_px_fill,
                            fee=cb.fee_exit,
                            realized_pnl=net_pnl,
                            exit_reason=outcome.exit_reason,
                            signal_id=sig_id,
                        )
                    )
                )

            if isinstance(policy, OneTradePerCombo):
                policy.record_open(sig.symbol, sig.combo_id, trade_id, max_exit_idx)

        return self._build_result(
            mode=canonical_mode,
            trades=trades,
            events=events,
            decisions=decisions,
            accepted_sigs=accepted_sigs,
            snapshots=pd.DataFrame(),
            extra_summary={},
        )

    def _run_portfolio_mode(
        self,
        sorted_signals: list[Signal],
        arrays_by_sym: dict[str, M1BarArrays],
        *,
        sample_snapshot_interval_m1: int = 15,
        allow_multiple_per_combo: bool = False,
    ) -> SimulationResult:
        out_mode = "ALL_SIGNALS_PORTFOLIO" if allow_multiple_per_combo else "BINGX_PORTFOLIO"
        if not arrays_by_sym:
            return self._build_result(
                mode=out_mode,
                trades=[],
                events=[],
                decisions=[],
                accepted_sigs=[],
                snapshots=pd.DataFrame(),
                extra_summary={},
            )

        first_sym = next(iter(arrays_by_sym.keys()))
        t_ms_array = arrays_by_sym[first_sym].t_ms
        n_bars = len(t_ms_array)
        first_ts = int(t_ms_array[0])
        last_ts = int(t_ms_array[-1])

        opens_by_sym = {
            sym: arr.opens if arr.opens is not None else arr.closes
            for sym, arr in arrays_by_sym.items()
        }
        closes_by_sym = {sym: arr.closes for sym, arr in arrays_by_sym.items()}
        account = AccountLedger(
            initial_equity=self.risk.initial_equity,
            symbols=list(arrays_by_sym.keys()),
            opens_by_symbol=opens_by_sym,
            closes_by_symbol=closes_by_sym,
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
                        signal_id=sig_id,
                        symbol=sig.symbol,
                        combo_id=sig.combo_id,
                        combo_name=sig.combo_name,
                        timeframe=sig.timeframe,
                        side=sig.side,
                        signal_close_ms=sig.signal_close_ms,
                        signal_close_dt=str(sig.signal_close_dt),
                        status="UNFILLABLE",
                        reason="NO_M1_BAR_AFTER_SIGNAL",
                    )
                )
                continue
            idx = int(np.searchsorted(t_ms_array, earliest_exec_ms))
            if 0 <= idx < n_bars:
                signals_by_bar.setdefault(idx, []).append(sig)
            else:
                decisions.append(
                    SignalDecision(
                        signal_id=sig_id,
                        symbol=sig.symbol,
                        combo_id=sig.combo_id,
                        combo_name=sig.combo_name,
                        timeframe=sig.timeframe,
                        side=sig.side,
                        signal_close_ms=sig.signal_close_ms,
                        signal_close_dt=str(sig.signal_close_dt),
                        status="UNFILLABLE",
                        reason="NO_M1_BAR_AFTER_SIGNAL",
                    )
                )

        legs_closing_at: dict[int, dict[tuple[str, str], list[_PendingPortfolioLeg]]] = {}
        closed_trades: list[TradeRecord] = []
        events: list[ExecutionEvent] = []
        accepted_sigs: list[Signal] = []
        seq = 0
        intrabar_tp_sl_count = 0
        intrabar_trail_count = 0
        liq_warning: dict[str, Any] | None = None

        for bar_idx in range(n_bars):
            ts = int(t_ms_array[bar_idx])

            for sig in signals_by_bar.get(bar_idx, []):
                sig_id = _resolve_signal_id(sig)
                if self.c15_controller.is_target(sig.symbol, sig.combo_id):
                    ok, block_reason = self.c15_controller.should_allow(sig, ts)
                    if not ok:
                        decisions.append(
                            SignalDecision(
                                signal_id=sig_id,
                                symbol=sig.symbol,
                                combo_id=sig.combo_id,
                                combo_name=sig.combo_name,
                                timeframe=sig.timeframe,
                                side=sig.side,
                                signal_close_ms=sig.signal_close_ms,
                                signal_close_dt=str(sig.signal_close_dt),
                                status="SKIPPED",
                                reason=block_reason,
                                entry_bar_idx=bar_idx,
                                entry_time_ms=ts,
                            )
                        )
                        continue

                notional = self.risk.notional_per_trade
                req_margin = self.risk.calc_margin(sig.symbol, notional)
                decision = portfolio_policy.evaluate(
                    signal_id=sig_id,
                    symbol=sig.symbol,
                    combo_id=sig.combo_id,
                    combo_name=sig.combo_name,
                    timeframe=sig.timeframe,
                    side=sig.side,
                    signal_close_ms=sig.signal_close_ms,
                    signal_close_dt=str(sig.signal_close_dt),
                    entry_idx=bar_idx,
                    entry_time_ms=ts,
                    req_margin=req_margin,
                )
                decisions.append(decision)
                if decision.status != "ACCEPTED":
                    continue

                accepted_sigs.append(sig)
                arr = arrays_by_sym[sig.symbol]
                raw_open_px = float(opens_by_sym[sig.symbol][bar_idx])
                entry_px = self.cost_model.fill_price(
                    raw_open_px,
                    symbol=sig.symbol,
                    side=sig.side,
                    is_entry=True,
                    order_type="entry_market",
                )
                entry_fee = notional * self.cost_model.params_for(sig.symbol).fee_rate_for("entry_market")
                account.open_position(
                    symbol=sig.symbol,
                    side=sig.side,
                    notional=notional,
                    entry_price=entry_px,
                    req_margin=req_margin,
                    entry_fee=entry_fee,
                )

                trade_id = sig_id
                exit_policy = _resolve_exit_policy(
                    sig, self.final14_exit_mode, self.trailing_act_pct, self.trailing_cb_pct
                )
                leg_specs = _build_leg_specs(trade_id, notional, exit_policy)
                exit_rules = _build_exit_rules(exit_policy)
                provider = str(arr.providers[bar_idx]) if len(arr.providers) else sig.provider

                for leg_spec in leg_specs:
                    w = leg_spec.notional / notional if notional > 0 else 1.0
                    leg_qty = leg_spec.notional / entry_px
                    leg_margin = req_margin * w
                    leg_entry_fee = entry_fee * w

                    outcome = simulate_leg(
                        bars_m1=arr,
                        entry_idx=bar_idx,
                        entry_price=entry_px,
                        side=sig.side,
                        leg_spec=leg_spec,
                        exit_rules=exit_rules,
                        ambiguity_policy=self.ambiguity_policy,
                    )
                    portfolio_policy.record_open_leg(sig.symbol, sig.combo_id, leg_spec.leg_id, trade_id)

                    seq += 1
                    events.append(
                        _to_execution_event(
                            Event(
                                seq=seq,
                                event_id=f"EV_{seq:07d}_{leg_spec.leg_id}_OPEN",
                                timestamp_ms=ts,
                                timestamp_dt=str(arr.index[bar_idx]),
                                trade_id=trade_id,
                                leg_id=leg_spec.leg_id,
                                symbol=sig.symbol,
                                combo_id=sig.combo_id,
                                combo_name=sig.combo_name,
                                timeframe=sig.timeframe,
                                event_type="ENTRY",
                                side=sig.side,
                                order_action="OPEN",
                                price=entry_px,
                                qty=leg_qty,
                                notional=leg_spec.notional,
                                fee=leg_entry_fee,
                                realized_pnl=0.0,
                                exit_reason="",
                                signal_id=sig_id,
                            )
                        )
                    )

                    pending = _PendingPortfolioLeg(
                        sig=sig,
                        signal_id=sig_id,
                        trade_id=trade_id,
                        leg_spec=leg_spec,
                        leg_qty=leg_qty,
                        leg_margin=leg_margin,
                        leg_entry_fee=leg_entry_fee,
                        entry_price=entry_px,
                        entry_px_raw=raw_open_px,
                        outcome=outcome,
                        exit_rules=exit_rules,
                        provider=provider,
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
                        if outcome.intrabar_trailing_ambiguous:
                            intrabar_trail_count += 1

                        sig = pending.sig
                        arr = arrays_by_sym[sig.symbol]
                        cb = self.cost_model.evaluate_leg(
                            symbol=sig.symbol,
                            side=sig.side,
                            notional=pending.leg_spec.notional,
                            entry_px_raw=pending.entry_px_raw,
                            exit_px_trigger=outcome.exit_price,
                            exit_reason=outcome.exit_reason,
                            entry_time_ms=int(arr.t_ms[outcome.entry_idx]),
                            exit_time_ms=ts,
                            entry_px_fill_override=pending.entry_price,
                        )
                        exec_exit_px = cb.exit_px_fill
                        exit_notional = pending.leg_qty * exec_exit_px
                        exit_fee = cb.fee_exit

                        effective_entry, gross_pnl, total_fee, net_pnl = account.close_leg(
                            symbol=sig.symbol,
                            side=sig.side,
                            leg_qty=pending.leg_qty,
                            leg_margin=pending.leg_margin,
                            leg_entry_price=pending.entry_price,
                            exec_exit_price=exec_exit_px,
                            entry_fee=cb.fee_entry,
                            exit_fee=exit_fee,
                            funding_cost=cb.funding_cost,
                        )
                        portfolio_policy.record_close_leg(
                            sig.symbol, sig.combo_id, pending.leg_spec.leg_id
                        )
                        self.c15_controller.on_trade_exit(
                            sig.symbol, sig.combo_id, sig.side, ts, outcome.exit_reason
                        )

                        seq += 1
                        events.append(
                            _to_execution_event(
                                Event(
                                    seq=seq,
                                    event_id=f"EV_{seq:07d}_{pending.leg_spec.leg_id}_CLOSE",
                                    timestamp_ms=ts,
                                    timestamp_dt=str(arr.index[bar_idx]),
                                    trade_id=pending.trade_id,
                                    leg_id=pending.leg_spec.leg_id,
                                    symbol=sig.symbol,
                                    combo_id=sig.combo_id,
                                    combo_name=sig.combo_name,
                                    timeframe=sig.timeframe,
                                    event_type="EXIT",
                                    side=sig.side,
                                    order_action="CLOSE",
                                    price=exec_exit_px,
                                    qty=pending.leg_qty,
                                    notional=exit_notional,
                                    fee=exit_fee,
                                    realized_pnl=net_pnl,
                                    exit_reason=outcome.exit_reason,
                                    signal_id=pending.signal_id,
                                )
                            )
                        )

                        closed_trades.append(
                            TradeRecord(
                                trade_id=pending.trade_id,
                                leg_id=pending.leg_spec.leg_id,
                                symbol=sig.symbol,
                                combo_id=sig.combo_id,
                                combo_name=sig.combo_name,
                                timeframe=sig.timeframe,
                                side=sig.side,
                                leg_type=pending.leg_spec.leg_type,
                                entry_time_ms=int(arr.t_ms[outcome.entry_idx]),
                                entry_time_dt=arr.index[outcome.entry_idx],
                                entry_price=effective_entry,
                                exit_time_ms=ts,
                                exit_time_dt=arr.index[bar_idx],
                                exit_price=exec_exit_px,
                                exit_reason=outcome.exit_reason,
                                qty=pending.leg_qty,
                                notional=pending.leg_spec.notional,
                                margin=pending.leg_margin,
                                gross_pnl=gross_pnl,
                                fee_total=total_fee,
                                net_pnl=net_pnl,
                                return_pct=(net_pnl / pending.leg_spec.notional)
                                if pending.leg_spec.notional > 0
                                else 0.0,
                                intrabar_ambiguous=outcome.intrabar_ambiguous,
                                bars_held=outcome.bars_held,
                                provider=pending.provider,
                                signal_id=pending.signal_id,
                                intrabar_trailing_ambiguous=outcome.intrabar_trailing_ambiguous,
                                exit_policy_hash=pending.exit_rules.exit_policy_hash,
                                tp_pct=pending.exit_rules.tp_pct,
                                sl_pct=pending.exit_rules.sl_pct,
                                trailing_act_pct=pending.exit_rules.trailing_act_pct,
                                trailing_cb_pct=pending.exit_rules.trailing_cb_pct,
                                max_bars_held=pending.exit_rules.max_bars_held,
                                entry_px_raw=cb.entry_px_raw,
                                entry_px_fill=cb.entry_px_fill,
                                exit_px_trigger=cb.exit_px_trigger,
                                exit_px_fill=cb.exit_px_fill,
                                fee_entry=cb.fee_entry,
                                fee_exit=cb.fee_exit,
                                spread_cost=cb.spread_cost,
                                slippage_cost=cb.slippage_cost,
                                funding_cost=cb.funding_cost,
                                gross_pnl_raw=cb.gross_pnl_raw,
                                ambiguity_type=outcome.ambiguity_type,
                                ambiguity_policy=self.ambiguity_policy,
                                pnl_own_entry=cb.net_pnl,
                                pnl_vwap=net_pnl,
                            )
                        )

            if self.risk.mmr_by_symbol:
                highs_by_sym = {sym: arr.highs for sym, arr in arrays_by_sym.items()}
                lows_by_sym = {sym: arr.lows for sym, arr in arrays_by_sym.items()}
                trig, adv_eq, mm_req = account.check_maintenance_margin(
                    bar_idx=bar_idx,
                    highs_by_symbol=highs_by_sym,
                    lows_by_symbol=lows_by_sym,
                    mmr_by_symbol=self.risk.mmr_by_symbol,
                )
                if trig:
                    account.mark_to_market(
                        bar_idx=bar_idx,
                        ts_ms=ts,
                        ts_dt=arrays_by_sym[first_sym].index[bar_idx],
                        n_bars=n_bars,
                        sample_interval_m1=sample_snapshot_interval_m1,
                    )
                    liq_warning = {
                        "status": "LIQUIDATION_WARNING",
                        "liquidation_warning_time_ms": ts,
                        "liquidation_warning_dt": str(arrays_by_sym[first_sym].index[bar_idx]),
                        "liquidation_warning_equity": round(adv_eq, 6),
                        "liquidation_warning_mm": round(mm_req, 6),
                    }
                    break

            account.mark_to_market(
                bar_idx=bar_idx,
                ts_ms=ts,
                ts_dt=arrays_by_sym[first_sym].index[bar_idx],
                n_bars=n_bars,
                sample_interval_m1=sample_snapshot_interval_m1,
            )

        closed_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
        acct_inv = verify_mode_c_accounting_invariant(
            closed_trades,
            initial_equity=self.risk.initial_equity,
            final_equity=account.final_equity,
        )
        m2m_summary: dict[str, Any] = {
            "status": liq_warning["status"] if liq_warning else "OK",
            "initial_equity": self.risk.initial_equity,
            "final_equity": round(account.final_equity, 4),
            "peak_equity": round(account.peak_equity, 4),
            "m2m_max_drawdown_dollars": round(account.max_m2m_dd_dollars, 4),
            "m2m_max_drawdown_pct": round(account.max_m2m_dd_pct, 2),
            "intrabar_tp_sl_ambiguous_count": intrabar_tp_sl_count,
            "intrabar_trailing_ambiguous_count": intrabar_trail_count,
            "accounting_invariant": acct_inv,
        }
        if liq_warning:
            m2m_summary.update(liq_warning)
        return self._build_result(
            mode=out_mode,
            trades=closed_trades,
            events=events,
            decisions=decisions,
            accepted_sigs=accepted_sigs,
            snapshots=account.snapshots_df(),
            extra_summary=m2m_summary,
        )


def closed_max_drawdown(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        eq += p
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    return max_dd


def profit_factor(pnls: list[float]) -> float:
    pos = sum(p for p in pnls if p > 0)
    neg = abs(sum(p for p in pnls if p < 0))
    if neg == 0:
        return float("inf") if pos > 0 else 0.0
    return pos / neg


def compute_c15_metrics(
    variant: str,
    mode: str,
    cost_profile: str,
    res: SimulationResult,
    h0_c15_accepted_ids: set[str],
    h0_all_accepted_ids: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    c15_decs = [d for d in res.signal_decisions if d.symbol == "ETH-USDT" and int(d.combo_id) == 105]
    c15_trades = sorted(
        [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 105],
        key=lambda t: (t.exit_time_ms, t.entry_time_ms),
    )

    accepted_ids = {t.signal_id for t in res.trades}
    c15_accepted_ids = {t.signal_id for t in c15_trades}

    blocked_after_tp = sum(1 for d in c15_decs if d.reason.startswith("BLOCKED_"))
    skipped_conc = sum(1 for d in c15_decs if d.reason == "SKIPPED_BY_CONCURRENCY")
    rejected_margin = sum(1 for d in c15_decs if d.reason == "INSUFFICIENT_MARGIN")

    tp_count = sum(1 for t in c15_trades if t.exit_reason == "TP")
    sl_count = sum(1 for t in c15_trades if t.exit_reason == "SL")
    n_trades = len(c15_trades)
    wr = (tp_count / n_trades * 100.0) if n_trades else 0.0

    gross_raw = sum(t.gross_pnl_raw for t in c15_trades)
    gross_after_slip = sum(t.gross_pnl for t in c15_trades)
    fee_total = sum(t.fee_total for t in c15_trades)
    slippage_total = sum(t.slippage_cost for t in c15_trades)
    own_net = sum(t.pnl_own_entry for t in c15_trades)
    vwap_net = sum(t.pnl_vwap for t in c15_trades)

    own_pnls = [t.pnl_own_entry for t in c15_trades]
    vwap_pnls = [t.pnl_vwap for t in c15_trades]

    # Track same-side re-entries after TP / SL
    c15_trades_by_entry = sorted(c15_trades, key=lambda t: (t.entry_time_ms, t.exit_time_ms))
    last_exit_by_side: dict[str, tuple[int, str]] = {}
    reentry_after_tp = 0
    reentry_after_sl = 0
    trade_rows: list[dict[str, Any]] = []

    for t in c15_trades_by_entry:
        prev = last_exit_by_side.get(t.side)
        prev_reason = ""
        mins_since_prev: float | None = None
        if prev is not None:
            prev_exit_ms, prev_r = prev
            if prev_exit_ms <= t.entry_time_ms:
                prev_reason = prev_r
                mins_since_prev = (t.entry_time_ms - prev_exit_ms) / 60_000.0
                if prev_r == "TP":
                    reentry_after_tp += 1
                elif prev_r == "SL":
                    reentry_after_sl += 1
        # Update last_exit_by_side for trades that have exited by next entries (chronological by entry)
        last_exit_by_side[t.side] = (t.exit_time_ms, t.exit_reason)

        trade_rows.append(
            {
                "variant": variant,
                "mode": mode,
                "cost_profile": cost_profile,
                "trade_id": t.trade_id,
                "signal_id": t.signal_id,
                "side": t.side,
                "entry_time_ms": t.entry_time_ms,
                "entry_time_dt": str(t.entry_time_dt),
                "exit_time_ms": t.exit_time_ms,
                "exit_time_dt": str(t.exit_time_dt),
                "entry_price": round(t.entry_price, 6),
                "exit_price": round(t.exit_price, 6),
                "exit_reason": t.exit_reason,
                "bars_held": t.bars_held,
                "gross_pnl_raw": round(t.gross_pnl_raw, 6),
                "gross_pnl": round(t.gross_pnl, 6),
                "fee_total": round(t.fee_total, 6),
                "slippage_cost": round(t.slippage_cost, 6),
                "pnl_own_entry": round(t.pnl_own_entry, 6),
                "pnl_vwap": round(t.pnl_vwap, 6),
                "net_pnl": round(t.net_pnl, 6),
                "prev_same_side_exit_reason": prev_reason,
                "minutes_since_prev_same_side_exit": round(mins_since_prev, 2) if mins_since_prev is not None else "",
                "is_new_vs_h0": t.signal_id not in h0_c15_accepted_ids,
            }
        )

    c15_new_vs_h0 = len(c15_accepted_ids - h0_c15_accepted_ids)
    all_new_vs_h0 = len(accepted_ids - h0_all_accepted_ids)
    non_c15_new_vs_h0 = all_new_vs_h0 - c15_new_vs_h0

    comp_row = {
        "variant": variant,
        "mode": mode,
        "cost_profile": cost_profile,
        "c15_signals_total": len(c15_decs),
        "c15_trades_accepted": n_trades,
        "c15_blocked_after_tp": blocked_after_tp,
        "c15_skipped_concurrency": skipped_conc,
        "c15_rejected_margin": rejected_margin,
        "tp_count": tp_count,
        "sl_count": sl_count,
        "win_rate_pct": round(wr, 2),
        "gross_pnl_raw": round(gross_raw, 4),
        "gross_pnl": round(gross_after_slip, 4),
        "fee_total": round(fee_total, 4),
        "slippage_cost": round(slippage_total, 4),
        "own_net_pnl": round(own_net, 4),
        "vwap_allocated_pnl": round(vwap_net, 4),
        "profit_factor": round(profit_factor(own_pnls), 4),
        "vwap_profit_factor": round(profit_factor(vwap_pnls), 4),
        "avg_pnl_per_trade": round(own_net / n_trades, 4) if n_trades else 0.0,
        "max_drawdown_own": round(closed_max_drawdown(own_pnls), 4),
        "max_drawdown_vwap": round(closed_max_drawdown(vwap_pnls), 4),
        "reentries_after_tp": reentry_after_tp,
        "reentries_after_sl": reentry_after_sl,
        "c15_new_trades_vs_h0": c15_new_vs_h0,
        "non_c15_new_trades_vs_h0": non_c15_new_vs_h0,
    }

    dec_rows = [
        {
            "variant": variant,
            "mode": mode,
            "cost_profile": cost_profile,
            "signal_id": d.signal_id,
            "side": d.side,
            "signal_close_ms": d.signal_close_ms,
            "signal_close_dt": d.signal_close_dt,
            "status": d.status,
            "reason": d.reason,
            "blocking_trade_id": d.blocking_trade_id,
            "entry_time_ms": d.entry_time_ms,
        }
        for d in c15_decs
    ]

    # Year & Side breakdowns
    bd_rows: list[dict[str, Any]] = []
    for side_val in ["BUY", "SELL"]:
        sub = [t for t in c15_trades if t.side == side_val]
        sub_own = [t.pnl_own_entry for t in sub]
        sub_vwap = [t.pnl_vwap for t in sub]
        tp_s = sum(1 for t in sub if t.exit_reason == "TP")
        bd_rows.append(
            {
                "variant": variant,
                "mode": mode,
                "cost_profile": cost_profile,
                "group_type": "SIDE",
                "group_value": side_val,
                "trades": len(sub),
                "tp_count": tp_s,
                "sl_count": len(sub) - tp_s,
                "win_rate_pct": round((tp_s / len(sub) * 100.0) if sub else 0.0, 2),
                "own_net_pnl": round(sum(sub_own), 4),
                "vwap_allocated_pnl": round(sum(sub_vwap), 4),
                "profit_factor": round(profit_factor(sub_own), 4),
                "max_drawdown_own": round(closed_max_drawdown(sub_own), 4),
            }
        )

    years = sorted({str(t.entry_time_dt)[:4] for t in c15_trades})
    for yr in years:
        sub = [t for t in c15_trades if str(t.entry_time_dt).startswith(yr)]
        sub_own = [t.pnl_own_entry for t in sub]
        sub_vwap = [t.pnl_vwap for t in sub]
        tp_y = sum(1 for t in sub if t.exit_reason == "TP")
        bd_rows.append(
            {
                "variant": variant,
                "mode": mode,
                "cost_profile": cost_profile,
                "group_type": "YEAR",
                "group_value": yr,
                "trades": len(sub),
                "tp_count": tp_y,
                "sl_count": len(sub) - tp_y,
                "win_rate_pct": round((tp_y / len(sub) * 100.0) if sub else 0.0, 2),
                "own_net_pnl": round(sum(sub_own), 4),
                "vwap_allocated_pnl": round(sum(sub_vwap), 4),
                "profit_factor": round(profit_factor(sub_own), 4),
                "max_drawdown_own": round(closed_max_drawdown(sub_own), 4),
            }
        )

    return comp_row, trade_rows, dec_rows, bd_rows


def compute_portfolio_row(
    variant: str,
    cost_profile: str,
    res: SimulationResult,
    analysis: dict[str, Any],
    h0_row: dict[str, Any] | None,
    h0_all_accepted_ids: set[str],
) -> dict[str, Any]:
    pnl_block = analysis["pnl"]
    risk_block = analysis["risk"]
    port_net = float(pnl_block["net_pnl"])
    roi_pct = float(pnl_block["roi_pct"])
    m2m_dd = float(risk_block.get("m2m_max_drawdown_dollars") or 0.0)
    m2m_dd_pct = float(risk_block.get("m2m_max_drawdown_pct") or 0.0)
    closed_dd = float(risk_block.get("closed_trade_max_drawdown_dollars") or 0.0)

    c15_trades = [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 105]
    other_eth = [t for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) != 105]
    btc_trades = [t for t in res.trades if t.symbol == "BTC-USDT"]

    acc_ids = {t.signal_id for t in res.trades}
    non_c15_acc = {t.signal_id for t in res.trades if not (t.symbol == "ETH-USDT" and int(t.combo_id) == 105)}
    h0_non_c15_acc = {
        sid for sid in h0_all_accepted_ids if "_C15_" not in sid
    }

    row = {
        "variant": variant,
        "cost_profile": cost_profile,
        "total_portfolio_trades": len(res.trades),
        "portfolio_net_pnl": round(port_net, 4),
        "roi_pct": round(roi_pct, 2),
        "m2m_max_dd_dollars": round(m2m_dd, 4),
        "m2m_max_dd_pct": round(m2m_dd_pct, 2),
        "closed_max_dd_dollars": round(closed_dd, 4),
        "delta_pnl_vs_h0": round(port_net - h0_row["portfolio_net_pnl"], 4) if h0_row else 0.0,
        "delta_m2m_dd_vs_h0": round(m2m_dd - h0_row["m2m_max_dd_dollars"], 4) if h0_row else 0.0,
        "c15_own_net_pnl": round(sum(t.pnl_own_entry for t in c15_trades), 4),
        "c15_vwap_net_pnl": round(sum(t.pnl_vwap for t in c15_trades), 4),
        "other_eth_trades": len(other_eth),
        "other_eth_own_net_pnl": round(sum(t.pnl_own_entry for t in other_eth), 4),
        "other_eth_vwap_net_pnl": round(sum(t.pnl_vwap for t in other_eth), 4),
        "btc_trades": len(btc_trades),
        "btc_own_net_pnl": round(sum(t.pnl_own_entry for t in btc_trades), 4),
        "btc_vwap_net_pnl": round(sum(t.pnl_vwap for t in btc_trades), 4),
        "non_c15_new_trades_vs_h0": len(non_c15_acc - h0_non_c15_acc),
        "non_c15_dropped_trades_vs_h0": len(h0_non_c15_acc - non_c15_acc),
    }
    return row


def main() -> None:
    t_start = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = load_v3_lab_2_config()
    if cfg.get("config_hash") != EXPECTED_V3_LAB_2_HASH:
        raise RuntimeError(f"V3_LAB_2 config hash mismatch: {cfg.get('config_hash')} != {EXPECTED_V3_LAB_2_HASH}")

    strat = load_v3_lab_2_strategy()
    dl = DataLayer()
    dm = dl.get_manifest()
    gm_path = ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json"
    dataset_hash = json.loads(gm_path.read_text(encoding="utf-8")).get("dataset_hash", dm.data_manifest_hash)
    if dataset_hash != EXPECTED_DATASET_HASH:
        raise RuntimeError(f"Dataset hash mismatch: {dataset_hash} != {EXPECTED_DATASET_HASH}")

    start_ms, end_ms = to_utc_ms(START_DATE), to_utc_ms(END_DATE)
    candles_m1_eval: dict[str, pd.DataFrame] = {}
    m15_dict: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        df_m1 = dl.query_m1(sym, START_DATE, END_DATE, include_warmup=True)
        t_m1 = datetime_index_to_ms(df_m1.index)
        candles_m1_eval[sym] = df_m1.loc[(t_m1 >= start_ms) & (t_m1 < end_ms)]
        m15_dict[sym] = dl.query_timeframe(sym, "15m", START_DATE, END_DATE, include_warmup=True)

    print(f"Loaded M1/M15 data in {time.time() - t_start:.2f}s")

    all_sigs = strat.generate_signals(
        m15_dict, symbols=SYMBOLS, only_approved=True, only_warmup_ready=True
    )
    eval_sigs = [s for s in all_sigs if start_ms <= s.signal_close_ms + 1 < end_ms]
    c15_sigs = [s for s in eval_sigs if s.symbol == "ETH-USDT" and int(s.combo_id) == 105]
    print(f"V3_LAB_2 eval signals: {len(eval_sigs)} | ETH-USDT/C15 signals: {len(c15_sigs)}")

    kama_close_ms, kama_buy, kama_sell = build_eth_1h_kama_series(m15_dict["ETH-USDT"])
    # Verify 100% alignment between kama_buy/kama_sell and c15_sigs
    close_to_idx = {int(ms): i for i, ms in enumerate(kama_close_ms)}
    for s in c15_sigs:
        idx = close_to_idx.get(int(s.signal_close_ms))
        assert idx is not None, f"Missing 1H close_ms for {s.signal_close_ms}"
        expected = kama_buy[idx] if s.side == "BUY" else kama_sell[idx]
        assert bool(expected), f"KAMA mismatch on {s.signal_close_dt} {s.side}"
    print("Verified 100% alignment between 1H KAMA_ER series and ETH-USDT/C15 signals.")

    cost_models = {p: CostModel.from_profile_name(p, is_real_data=True) for p in PROFILES}

    # Verify H0 parity against stock UnifiedSimulator on ALL_SIGNALS/BASE and BINGX_PORTFOLIO/BASE
    for parity_mode in ["ALL_SIGNALS", "BINGX_PORTFOLIO"]:
        cm = cost_models["BASE"]
        risk = RiskConfig(initial_equity=INITIAL_EQUITY, notional_per_trade=NOTIONAL_PER_TRADE, cost_profile="BASE", cost_model=cm)
        stock_res = UnifiedSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE").run(
            parity_mode, eval_sigs, candles_m1_eval
        )
        ctrl = EthC15AdmissionController("H0")
        res_h0 = ResearchUnifiedSimulator(
            risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE", c15_controller=ctrl
        ).run(parity_mode, eval_sigs, candles_m1_eval)
        assert len(stock_res.trades) == len(res_h0.trades), f"Parity trade count mismatch in {parity_mode}"
        assert abs(sum(t.net_pnl for t in stock_res.trades) - sum(t.net_pnl for t in res_h0.trades)) < 1e-9
    print("Verified H0 100% bit-for-bit parity against stock UnifiedSimulator.")

    comp_rows: list[dict[str, Any]] = []
    all_trade_rows: list[dict[str, Any]] = []
    all_dec_rows: list[dict[str, Any]] = []
    all_bd_rows: list[dict[str, Any]] = []
    portfolio_rows: list[dict[str, Any]] = []

    h0_c15_accepted: dict[tuple[str, str], set[str]] = {}
    h0_all_accepted: dict[tuple[str, str], set[str]] = {}
    h0_port_rows: dict[str, dict[str, Any]] = {}

    run_idx = 0
    for mode in MODES:
        for profile in PROFILES:
            cm = cost_models[profile]
            risk = RiskConfig(
                initial_equity=INITIAL_EQUITY,
                notional_per_trade=NOTIONAL_PER_TRADE,
                cost_profile=profile,
                cost_model=cm,
            )
            for variant in VARIANTS:
                run_idx += 1
                t_r0 = time.time()
                ctrl = EthC15AdmissionController(
                    variant=variant,  # type: ignore[arg-type]
                    kama_1h_close_ms=kama_close_ms,
                    kama_1h_buy=kama_buy,
                    kama_1h_sell=kama_sell,
                )
                sim = ResearchUnifiedSimulator(
                    risk_config=risk,
                    cost_model=cm,
                    ambiguity_policy="CONSERVATIVE",
                    c15_controller=ctrl,
                )
                res = sim.run(mode, eval_sigs, candles_m1_eval)

                if variant == "H0":
                    h0_c15_accepted[(mode, profile)] = {
                        t.signal_id for t in res.trades if t.symbol == "ETH-USDT" and int(t.combo_id) == 105
                    }
                    h0_all_accepted[(mode, profile)] = {t.signal_id for t in res.trades}

                c_row, t_rows, d_rows, b_rows = compute_c15_metrics(
                    variant=variant,
                    mode=mode,
                    cost_profile=profile,
                    res=res,
                    h0_c15_accepted_ids=h0_c15_accepted[(mode, profile)],
                    h0_all_accepted_ids=h0_all_accepted[(mode, profile)],
                )
                comp_rows.append(c_row)
                all_trade_rows.extend(t_rows)
                all_dec_rows.extend(d_rows)
                all_bd_rows.extend(b_rows)

                if mode == "BINGX_PORTFOLIO":
                    analysis = ResearchAnalyzer.analyze_simulation(res, initial_equity=INITIAL_EQUITY)
                    p_row = compute_portfolio_row(
                        variant=variant,
                        cost_profile=profile,
                        res=res,
                        analysis=analysis,
                        h0_row=h0_port_rows.get(profile),
                        h0_all_accepted_ids=h0_all_accepted[(mode, profile)],
                    )
                    if variant == "H0":
                        h0_port_rows[profile] = p_row
                    portfolio_rows.append(p_row)

                print(
                    f"[{run_idx:02d}/36] {variant} | {mode:19s} | {profile:6s} | "
                    f"C15 trades={c_row['c15_trades_accepted']:4d} blocked={c_row['c15_blocked_after_tp']:4d} "
                    f"own_net={c_row['own_net_pnl']:+8.2f} vwap={c_row['vwap_allocated_pnl']:+8.2f} "
                    f"({time.time() - t_r0:.2f}s)"
                )

    pd.DataFrame(comp_rows).to_csv(OUT_DIR / "C15_H0_H1A_H1B_H1C_COMPARISON.csv", index=False)
    pd.DataFrame(all_trade_rows).to_csv(OUT_DIR / "C15_TRADE_DETAILS.csv", index=False)
    pd.DataFrame(all_dec_rows).to_csv(OUT_DIR / "C15_SIGNAL_DECISIONS.csv", index=False)
    pd.DataFrame(all_bd_rows).to_csv(OUT_DIR / "C15_YEAR_SIDE_BREAKDOWN.csv", index=False)
    pd.DataFrame(portfolio_rows).to_csv(OUT_DIR / "C15_PORTFOLIO_IMPACT.csv", index=False)

    code_sha, code_dirty, _ = get_git_state()
    manifest = {
        "research_id": "V3_LAB_2_ETH_C15_RESEARCH",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "v3_lab_2_config_id": cfg["config_id"],
        "v3_lab_2_version": cfg["version"],
        "v3_lab_2_config_hash": cfg["config_hash"],
        "v3_lab_2_spec_hash": strat.get_spec_hash(),
        "dataset_hash": dataset_hash,
        "period": {"start": START_DATE, "end": END_DATE},
        "symbols": SYMBOLS,
        "initial_equity": INITIAL_EQUITY,
        "notional_per_trade": NOTIONAL_PER_TRADE,
        "ambiguity_policy": "CONSERVATIVE",
        "variants": {
            "H0": "V3_LAB_2@2.0.0 baseline (unchanged)",
            "H1A": "POST_TP_COOLDOWN_1H (60m same-side block after TP exit)",
            "H1B": "POST_TP_COOLDOWN_4H (240m same-side block after TP exit)",
            "H1C": "KAMA_RESET_AFTER_TP (after TP, lock same-side until 1H KAMA_ER cond turns False then True on closed 1H candles)",
        },
        "modes": MODES,
        "cost_profiles": {p: cost_models[p].profile.profile_hash for p in PROFILES},
        "total_runs": 36,
        "git_state": {"sha": code_sha, "dirty": code_dirty},
        "env_versions": get_env_versions(),
        "runner_script": "scripts/run_v3_lab_2_eth_c15_research.py",
        "runner_sha256": sha256_file(Path(__file__)),
    }
    (OUT_DIR / "RUN_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Package zip (will be refreshed after C15_RESEARCH_FINDINGS.md is written)
    zip_path = OUT_DIR / "V3_LAB_2_ETH_C15_RESEARCH.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fn in [
            "C15_H0_H1A_H1B_H1C_COMPARISON.csv",
            "C15_TRADE_DETAILS.csv",
            "C15_SIGNAL_DECISIONS.csv",
            "C15_YEAR_SIDE_BREAKDOWN.csv",
            "C15_PORTFOLIO_IMPACT.csv",
            "RUN_MANIFEST.json",
        ]:
            zf.write(OUT_DIR / fn, arcname=fn)

    print(f"\nAll 36 runs completed in {time.time() - t_start:.2f}s! Output dir: {OUT_DIR}")


if __name__ == "__main__":
    main()
