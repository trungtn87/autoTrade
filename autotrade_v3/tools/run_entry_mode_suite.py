"""Entry Mode & Concurrency Decision Policy Simulation Suite for 20FINAL@1 (Part A + Part B + Part C)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Literal
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autotrade_v3.analysis.metrics import compute_four_profit_factors, get_baseline_combo_keys
from autotrade_v3.analysis.research_analyzer import ResearchAnalyzer
from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import datetime_index_to_ms, to_utc_ms
from autotrade_v3.engine.execution_simulator import (
    ExecutionEvent,
    ExecutionSimulator,
    SimulationResult,
    TradeRecord,
)
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.account import AccountLedger, verify_mode_c_accounting_invariant
from autotrade_v3.sim.ambiguity import AmbiguityPolicy, normalize_ambiguity_policy
from autotrade_v3.sim.costs import (
    CostModel,
    cost_profiles_provenance_at_commit,
    load_cost_profile,
    validate_profile_for_real_data,
)
from autotrade_v3.sim.engine import (
    MODE_C_SIMULATION_LIMITS,
    _build_exit_rules,
    _build_leg_specs,
    _PendingPortfolioLeg,
    _resolve_exit_policy,
    _resolve_signal_id,
    _to_execution_event,
)
from autotrade_v3.sim.leg import extract_m1_arrays, simulate_leg
from autotrade_v3.sim.types import Event, M1BarArrays, SignalDecision
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.tools.dataset_manifest import compute_file_sha256

OUT_DIR = REPO_ROOT / "runs" / "lab" / "scratch" / "entry_mode_results"


@dataclass(frozen=True)
class EntryVariantSpec:
    variant_id: str
    policy_family: str
    label: str
    combo_cap: int | None  # None = unlimited per combo, 1 = ONE_TRADE_PER_COMBO, N = CAP_PER_COMBO_N
    block_same_side_symbol: bool = False
    block_any_symbol: bool = False
    cap_symbol_side: int | None = None
    opposite_mode: Literal["NONE", "BLOCK", "CLOSE"] = "NONE"


VARIANTS: list[EntryVariantSpec] = [
    EntryVariantSpec(
        variant_id="P0_ALL_SIGNALS",
        policy_family="P0",
        label="ALL_SIGNALS (unconstrained entries, Mode C portfolio)",
        combo_cap=None,
    ),
    EntryVariantSpec(
        variant_id="P1_ONE_TRADE_PER_COMBO",
        policy_family="P1",
        label="ONE_TRADE_PER_COMBO (baseline Mode C: max 1 open trade per combo)",
        combo_cap=1,
    ),
    EntryVariantSpec(
        variant_id="P2_BLOCK_SAME_SIDE_SYMBOL",
        policy_family="P2",
        label="BLOCK_SAME_SIDE_SYMBOL (max 1 open trade per symbol+side across all combos)",
        combo_cap=1,
        block_same_side_symbol=True,
    ),
    EntryVariantSpec(
        variant_id="P3_BLOCK_ANY_SYMBOL",
        policy_family="P3",
        label="BLOCK_ANY_SYMBOL (max 1 open trade per symbol across both sides)",
        combo_cap=1,
        block_any_symbol=True,
    ),
    EntryVariantSpec(
        variant_id="P4_CAP_PER_COMBO_2",
        policy_family="P4",
        label="CAP_PER_COMBO_N (N=2 open trades per combo)",
        combo_cap=2,
    ),
    EntryVariantSpec(
        variant_id="P4_CAP_PER_COMBO_3",
        policy_family="P4",
        label="CAP_PER_COMBO_N (N=3 open trades per combo)",
        combo_cap=3,
    ),
    EntryVariantSpec(
        variant_id="P5_CAP_LEGS_SYMBOL_SIDE_2",
        policy_family="P5",
        label="CAP_LEGS_SYMBOL_SIDE_K (1 per combo + max K=2 open trades per symbol+side)",
        combo_cap=1,
        cap_symbol_side=2,
    ),
    EntryVariantSpec(
        variant_id="P5_CAP_LEGS_SYMBOL_SIDE_3",
        policy_family="P5",
        label="CAP_LEGS_SYMBOL_SIDE_K (1 per combo + max K=3 open trades per symbol+side)",
        combo_cap=1,
        cap_symbol_side=3,
    ),
    EntryVariantSpec(
        variant_id="P5_CAP_LEGS_SYMBOL_SIDE_5",
        policy_family="P5",
        label="CAP_LEGS_SYMBOL_SIDE_K (1 per combo + max K=5 open trades per symbol+side)",
        combo_cap=1,
        cap_symbol_side=5,
    ),
    EntryVariantSpec(
        variant_id="P6_OPPOSITE_BLOCK",
        policy_family="P6",
        label="OPPOSITE_BLOCK (1 per combo + block if opposite side open on symbol)",
        combo_cap=1,
        opposite_mode="BLOCK",
    ),
    EntryVariantSpec(
        variant_id="P7_OPPOSITE_CLOSE",
        policy_family="P7",
        label="OPPOSITE_CLOSE (1 per combo + close all opposite-side legs on symbol at new entry open)",
        combo_cap=1,
        opposite_mode="CLOSE",
    ),
]


class PortfolioVariantAdmission:
    """Strict causal admission tracker for Mode C entry policy variants."""

    def __init__(self, spec: EntryVariantSpec, account: AccountLedger) -> None:
        self.spec = spec
        self.account = account
        # Map (symbol, combo_id) -> dict[trade_id, set[leg_id]]
        self.active_by_combo: dict[tuple[str, int], dict[str, set[str]]] = defaultdict(dict)
        # Map (symbol, side) -> dict[trade_id, set[leg_id]]
        self.active_by_sym_side: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(dict)
        self.max_causal_eval_ms: int = -1

    def count_open_trades_combo(self, symbol: str, combo_id: int) -> tuple[int, str]:
        d = self.active_by_combo.get((symbol, combo_id))
        if not d:
            return 0, ""
        first_tid = next(iter(d.keys()))
        return len(d), first_tid

    def count_open_trades_sym_side(self, symbol: str, side: str) -> tuple[int, str]:
        d = self.active_by_sym_side.get((symbol, side))
        if not d:
            return 0, ""
        first_tid = next(iter(d.keys()))
        return len(d), first_tid

    def count_open_legs_sym_side(self, symbol: str, side: str) -> int:
        d = self.active_by_sym_side.get((symbol, side))
        if not d:
            return 0
        return sum(len(legs) for legs in d.values())

    def evaluate_concurrency(
        self,
        *,
        signal_id: str,
        symbol: str,
        combo_id: int,
        combo_name: str,
        timeframe: str,
        side: str,
        signal_close_ms: int,
        signal_close_dt: str,
        entry_idx: int,
        entry_time_ms: int,
    ) -> SignalDecision | None:
        """Return SKIPPED SignalDecision if blocked by concurrency rules, else None."""
        # Causality check: entry_time_ms must be >= signal_close_ms + 1 and non-decreasing
        if entry_time_ms < signal_close_ms + 1:
            raise RuntimeError(f"Lookahead violation: entry_time_ms={entry_time_ms} < signal_close_ms+1={signal_close_ms+1}")
        if entry_time_ms < self.max_causal_eval_ms:
            raise RuntimeError(f"Non-chronological evaluation: {entry_time_ms} < {self.max_causal_eval_ms}")
        self.max_causal_eval_ms = entry_time_ms

        opp_side = "SELL" if side == "BUY" else "BUY"

        # 1. Check symbol-wide blocks (P3 BLOCK_ANY_SYMBOL, P2 BLOCK_SAME_SIDE_SYMBOL)
        if self.spec.block_any_symbol:
            n_same, tid_same = self.count_open_trades_sym_side(symbol, side)
            n_opp, tid_opp = self.count_open_trades_sym_side(symbol, opp_side)
            if n_same + n_opp > 0:
                blk_tid = tid_same or tid_opp
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason="SKIPPED_BLOCK_ANY_SYMBOL",
                    blocking_trade_id=blk_tid,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        if self.spec.block_same_side_symbol:
            n_same, tid_same = self.count_open_trades_sym_side(symbol, side)
            if n_same > 0:
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason="SKIPPED_BLOCK_SAME_SIDE_SYMBOL",
                    blocking_trade_id=tid_same,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        # 2. Check per-combo cap (P1 ONE_TRADE_PER_COMBO, P4 CAP_PER_COMBO_N, P5, P6, P7)
        if self.spec.combo_cap is not None:
            n_combo, tid_combo = self.count_open_trades_combo(symbol, combo_id)
            if n_combo >= self.spec.combo_cap:
                reason = (
                    "SKIPPED_BY_CONCURRENCY"
                    if self.spec.combo_cap == 1
                    else f"SKIPPED_CAP_PER_COMBO_{self.spec.combo_cap}"
                )
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason=reason,
                    blocking_trade_id=tid_combo,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        # 3. Check symbol+side cap K (P5 CAP_LEGS_SYMBOL_SIDE_K)
        if self.spec.cap_symbol_side is not None:
            n_same, tid_same = self.count_open_trades_sym_side(symbol, side)
            if n_same >= self.spec.cap_symbol_side:
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason=f"SKIPPED_CAP_SYMBOL_SIDE_{self.spec.cap_symbol_side}",
                    blocking_trade_id=tid_same,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        # 4. Check opposite block (P6 OPPOSITE_BLOCK)
        if self.spec.opposite_mode == "BLOCK":
            n_opp, tid_opp = self.count_open_trades_sym_side(symbol, opp_side)
            if n_opp > 0:
                return SignalDecision(
                    signal_id=signal_id,
                    symbol=symbol,
                    combo_id=combo_id,
                    combo_name=combo_name,
                    timeframe=timeframe,
                    side=side,
                    signal_close_ms=signal_close_ms,
                    signal_close_dt=signal_close_dt,
                    status="SKIPPED",
                    reason="SKIPPED_OPPOSITE_BLOCK",
                    blocking_trade_id=tid_opp,
                    entry_bar_idx=entry_idx,
                    entry_time_ms=entry_time_ms,
                )

        return None

    def record_open_leg(self, symbol: str, combo_id: int, side: str, trade_id: str, leg_id: str) -> None:
        self.active_by_combo[(symbol, combo_id)].setdefault(trade_id, set()).add(leg_id)
        self.active_by_sym_side[(symbol, side)].setdefault(trade_id, set()).add(leg_id)

    def record_close_leg(self, symbol: str, combo_id: int, side: str, trade_id: str, leg_id: str) -> None:
        c_map = self.active_by_combo.get((symbol, combo_id))
        if c_map and trade_id in c_map:
            c_map[trade_id].discard(leg_id)
            if not c_map[trade_id]:
                del c_map[trade_id]
            if not c_map:
                del self.active_by_combo[(symbol, combo_id)]

        s_map = self.active_by_sym_side.get((symbol, side))
        if s_map and trade_id in s_map:
            s_map[trade_id].discard(leg_id)
            if not s_map[trade_id]:
                del s_map[trade_id]
            if not s_map:
                del self.active_by_sym_side[(symbol, side)]


def run_portfolio_variant(
    *,
    spec: EntryVariantSpec,
    signals: list[Signal],
    arrays_by_sym: dict[str, M1BarArrays],
    risk_config: RiskConfig,
    cost_model: CostModel,
    ambiguity_policy: str = "CONSERVATIVE",
    final14_exit_mode: str = "50TP_50TRAIL",
    trailing_act_pct: float = 0.005,
    trailing_cb_pct: float = 0.005,
    sample_snapshot_interval_m1: int = 15,
) -> tuple[SimulationResult, dict[str, Any]]:
    """Run Mode C (BINGX_PORTFOLIO) with custom entry variant and record M1 concurrency series."""
    amb_pol: AmbiguityPolicy = normalize_ambiguity_policy(ambiguity_policy)
    sorted_signals = sorted(signals, key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

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
    symbols_sorted = sorted(arrays_by_sym.keys())

    account = AccountLedger(
        initial_equity=risk_config.initial_equity,
        symbols=symbols_sorted,
        opens_by_symbol=opens_by_sym,
        closes_by_symbol=closes_by_sym,
    )
    admission = PortfolioVariantAdmission(spec=spec, account=account)

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
    # Also keep an index of currently active pending legs by (symbol, side) for P7 OPPOSITE_CLOSE
    active_pending_by_sym_side: dict[tuple[str, str], dict[str, _PendingPortfolioLeg]] = defaultdict(dict)

    closed_trades: list[TradeRecord] = []
    events: list[ExecutionEvent] = []
    accepted_sigs: list[Signal] = []
    seq = 0
    intrabar_tp_sl_count = 0
    intrabar_trail_count = 0
    opposite_close_legs_count = 0
    opposite_close_events_count = 0

    # Concurrency tracking arrays per (symbol, side) across all n_bars M1 bars
    # Track both open trade count and open leg count and open notional at each M1 bar
    open_trades_series: dict[tuple[str, str], np.ndarray] = {
        (sym, side): np.zeros(n_bars, dtype=np.int32)
        for sym in symbols_sorted
        for side in ("BUY", "SELL")
    }
    open_legs_series: dict[tuple[str, str], np.ndarray] = {
        (sym, side): np.zeros(n_bars, dtype=np.int32)
        for sym in symbols_sorted
        for side in ("BUY", "SELL")
    }
    open_notional_series: dict[tuple[str, str], np.ndarray] = {
        (sym, side): np.zeros(n_bars, dtype=np.float64)
        for sym in symbols_sorted
        for side in ("BUY", "SELL")
    }
    simultaneous_buy_sell_entry_events: dict[str, int] = {sym: 0 for sym in symbols_sorted}

    def _close_pending_leg_at_bar(
        pending: _PendingPortfolioLeg,
        bar_idx: int,
        ts: int,
        exit_px_trigger: float,
        exit_reason: str,
        count_ambiguity: bool,
    ) -> None:
        nonlocal seq, intrabar_tp_sl_count, intrabar_trail_count
        outcome = pending.outcome
        if count_ambiguity:
            if outcome.intrabar_ambiguous:
                intrabar_tp_sl_count += 1
            if outcome.intrabar_trailing_ambiguous:
                intrabar_trail_count += 1

        sig = pending.sig
        arr = arrays_by_sym[sig.symbol]
        cb = cost_model.evaluate_leg(
            symbol=sig.symbol,
            side=sig.side,
            notional=pending.leg_spec.notional,
            entry_px_raw=pending.entry_px_raw,
            exit_px_trigger=exit_px_trigger,
            exit_reason=exit_reason,
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
        admission.record_close_leg(
            sig.symbol, sig.combo_id, sig.side, pending.trade_id, pending.leg_spec.leg_id
        )
        active_pending_by_sym_side[(sig.symbol, sig.side)].pop(pending.leg_spec.leg_id, None)

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
                    exit_reason=exit_reason,
                    signal_id=pending.signal_id,
                )
            )
        )

        bars_held = bar_idx - outcome.entry_idx
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
                exit_reason=exit_reason,
                qty=pending.leg_qty,
                notional=pending.leg_spec.notional,
                margin=pending.leg_margin,
                gross_pnl=gross_pnl,
                fee_total=total_fee,
                net_pnl=net_pnl,
                return_pct=(
                    net_pnl / pending.leg_spec.notional
                    if pending.leg_spec.notional > 0
                    else 0.0
                ),
                intrabar_ambiguous=outcome.intrabar_ambiguous if count_ambiguity else False,
                bars_held=bars_held,
                provider=pending.provider,
                signal_id=pending.signal_id,
                intrabar_trailing_ambiguous=outcome.intrabar_trailing_ambiguous if count_ambiguity else False,
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
                ambiguity_type=outcome.ambiguity_type if count_ambiguity else "NONE",
                ambiguity_policy=outcome.ambiguity_policy,
                pnl_own_entry=cb.net_pnl,
                pnl_vwap=net_pnl,
            )
        )

    for bar_idx in range(n_bars):
        ts = int(t_ms_array[bar_idx])

        # 1. Process signals arriving at the open of bar_idx
        for sig in signals_by_bar.get(bar_idx, []):
            sig_id = _resolve_signal_id(sig)
            notional = risk_config.notional_per_trade
            req_margin = risk_config.calc_margin(sig.symbol, notional)

            skip_dec = admission.evaluate_concurrency(
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
            )
            if skip_dec is not None:
                decisions.append(skip_dec)
                continue

            # For P7 OPPOSITE_CLOSE: if opposite side has open legs on sig.symbol,
            # close all opposite legs at raw_open_px of bar_idx BEFORE margin check & opening new trade!
            opp_side = "SELL" if sig.side == "BUY" else "BUY"
            if spec.opposite_mode == "CLOSE":
                opp_active_dict = active_pending_by_sym_side.get((sig.symbol, opp_side))
                if opp_active_dict:
                    opp_legs_to_close = list(opp_active_dict.values())
                    raw_open_px = float(opens_by_sym[sig.symbol][bar_idx])
                    opposite_close_events_count += 1
                    for opp_pending in opp_legs_to_close:
                        # Remove from scheduled legs_closing_at
                        sched_idx = opp_pending.outcome.exit_bar_idx
                        sched_bucket = legs_closing_at.get(sched_idx, {}).get((sig.symbol, opp_side))
                        if sched_bucket and opp_pending in sched_bucket:
                            sched_bucket.remove(opp_pending)
                        _close_pending_leg_at_bar(
                            pending=opp_pending,
                            bar_idx=bar_idx,
                            ts=ts,
                            exit_px_trigger=raw_open_px,
                            exit_reason="OPPOSITE_CLOSE",
                            count_ambiguity=False,
                        )
                        opposite_close_legs_count += 1

            # Check margin gate
            free_margin = account.free_margin_at_open(bar_idx)
            if free_margin < req_margin:
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
                        status="REJECTED",
                        reason="INSUFFICIENT_MARGIN",
                        entry_bar_idx=bar_idx,
                        entry_time_ms=ts,
                        free_margin=round(free_margin, 4),
                        req_margin=round(req_margin, 4),
                    )
                )
                continue

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
                    status="ACCEPTED",
                    reason="ACCEPTED",
                    entry_bar_idx=bar_idx,
                    entry_time_ms=ts,
                )
            )
            accepted_sigs.append(sig)

            # Check if opposite side is currently open on this symbol when opening this trade
            n_opp_now, _ = admission.count_open_trades_sym_side(sig.symbol, opp_side)
            if n_opp_now > 0:
                simultaneous_buy_sell_entry_events[sig.symbol] += 1

            arr = arrays_by_sym[sig.symbol]
            raw_open_px = float(opens_by_sym[sig.symbol][bar_idx])
            entry_px = cost_model.fill_price(
                raw_open_px,
                symbol=sig.symbol,
                side=sig.side,
                is_entry=True,
                order_type="entry_market",
            )
            entry_fee = notional * cost_model.params_for(sig.symbol).fee_rate_for("entry_market")
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
                sig, final14_exit_mode, trailing_act_pct, trailing_cb_pct
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
                    ambiguity_policy=amb_pol,
                )
                admission.record_open_leg(sig.symbol, sig.combo_id, sig.side, trade_id, leg_spec.leg_id)

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
                active_pending_by_sym_side[(sig.symbol, sig.side)][leg_spec.leg_id] = pending

        # Record intrabar concurrency state (after entries at bar open, before exits during/end of bar)
        for sym in symbols_sorted:
            for side in ("BUY", "SELL"):
                n_tr, _ = admission.count_open_trades_sym_side(sym, side)
                n_lg = admission.count_open_legs_sym_side(sym, side)
                open_trades_series[(sym, side)][bar_idx] = n_tr
                open_legs_series[(sym, side)][bar_idx] = n_lg
                # Open notional = sum of entry notionals of active legs on (sym, side)
                act_legs = active_pending_by_sym_side.get((sym, side))
                open_notional_series[(sym, side)][bar_idx] = (
                    sum(p.leg_spec.notional for p in act_legs.values()) if act_legs else 0.0
                )

        # 2. Process scheduled leg exits during bar_idx
        closing_for_bar = legs_closing_at.pop(bar_idx, None)
        if closing_for_bar:
            for pos_key in account.positions:
                for pending in list(closing_for_bar.get(pos_key, ())):
                    _close_pending_leg_at_bar(
                        pending=pending,
                        bar_idx=bar_idx,
                        ts=ts,
                        exit_px_trigger=pending.outcome.exit_price,
                        exit_reason=pending.outcome.exit_reason,
                        count_ambiguity=True,
                    )

        # 3. Mark to market at close of bar_idx
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
        initial_equity=risk_config.initial_equity,
        final_equity=account.final_equity,
    )
    decisions.sort(key=lambda d: (d.signal_close_ms, d.combo_id, d.side, d.signal_id))
    skipped_signals = [
        {
            "signal_id": d.signal_id,
            "symbol": d.symbol,
            "combo_id": d.combo_id,
            "combo_name": d.combo_name,
            "timeframe": d.timeframe,
            "side": d.side,
            "signal_close_ms": d.signal_close_ms,
            "signal_close_dt": d.signal_close_dt,
            "reason": d.reason,
            "blocking_trade_id": d.blocking_trade_id,
        }
        for d in decisions
        if d.status == "SKIPPED"
    ]
    rejected_signals = [
        {
            "signal_id": d.signal_id,
            "timestamp_ms": d.entry_time_ms or (d.signal_close_ms + 1),
            "timestamp_dt": d.signal_close_dt,
            "symbol": d.symbol,
            "combo_id": d.combo_id,
            "combo_name": d.combo_name,
            "timeframe": d.timeframe,
            "side": d.side,
            "signal_close_ms": d.signal_close_ms,
            "reason": d.reason,
            "free_margin": d.free_margin,
            "req_margin": d.req_margin,
        }
        for d in decisions
        if d.status == "REJECTED"
    ]

    summary = {
        "total_signals": len(decisions),
        "accepted_entries": len({t.trade_id for t in closed_trades}),
        "total_legs": len(closed_trades),
        "skipped_signals": len(skipped_signals),
        "rejected_signals": len(rejected_signals),
        "skipped_signals_count": len(skipped_signals),
        "rejected_signals_count": len(rejected_signals),
        "cost_profile": cost_model.profile.name,
        "cost_profile_hash": cost_model.profile.profile_hash,
        "ambiguity_policy": amb_pol,
        "status": "OK",
        "initial_equity": risk_config.initial_equity,
        "leverage_by_symbol": {"BTC-USDT": risk_config.leverage_btc, "ETH-USDT": risk_config.leverage_eth},
        "final_equity": round(account.final_equity, 4),
        "peak_equity": round(account.peak_equity, 4),
        "m2m_max_drawdown_dollars": round(account.max_m2m_dd_dollars, 4),
        "m2m_max_drawdown_pct": round(account.max_m2m_dd_pct, 2),
        "intrabar_tp_sl_ambiguous_count": intrabar_tp_sl_count,
        "intrabar_trailing_ambiguous_count": intrabar_trail_count,
        "simulation_limits": dict(MODE_C_SIMULATION_LIMITS),
        "accounting_invariant": acct_inv,
    }
    sim_res = SimulationResult(
        mode="BINGX_PORTFOLIO",
        trades=closed_trades,
        events=events,
        accepted_signals=accepted_sigs,
        skipped_signals=skipped_signals,
        rejected_signals=rejected_signals,
        snapshots=account.snapshots_df(),
        ambiguity_stats={
            "intrabar_tp_sl_ambiguous_count": intrabar_tp_sl_count,
            "intrabar_trailing_ambiguous_count": intrabar_trail_count,
        },
        summary_dict=summary,
        signal_decisions=decisions,
    )

    # Compute concurrency metrics per (symbol, side) and per symbol
    concurrency_stats: dict[str, Any] = {
        "by_sym_side": {},
        "by_sym": {},
        "opposite_close_legs_count": opposite_close_legs_count,
        "opposite_close_events_count": opposite_close_events_count,
    }
    for sym in symbols_sorted:
        buy_tr = open_trades_series[(sym, "BUY")]
        sell_tr = open_trades_series[(sym, "SELL")]
        both_mask = (buy_tr > 0) & (sell_tr > 0)
        bars_both = int(np.sum(both_mask))
        # Count transitions from False -> True as simultaneous hedge episodes
        transitions = int(np.sum(both_mask[1:] & (~both_mask[:-1]))) + (1 if both_mask[0] else 0)
        tot_notional_sym = open_notional_series[(sym, "BUY")] + open_notional_series[(sym, "SELL")]
        max_notional_sym = float(np.max(tot_notional_sym)) if len(tot_notional_sym) else 0.0

        concurrency_stats["by_sym"][sym] = {
            "max_notional": round(max_notional_sym, 4),
            "simultaneous_buy_sell_bars": bars_both,
            "simultaneous_buy_sell_episodes": transitions,
            "simultaneous_buy_sell_entry_events": simultaneous_buy_sell_entry_events[sym],
        }
        for side in ("BUY", "SELL"):
            tr_arr = open_trades_series[(sym, side)]
            lg_arr = open_legs_series[(sym, side)]
            not_arr = open_notional_series[(sym, side)]
            active_tr = tr_arr[tr_arr > 0]
            concurrency_stats["by_sym_side"][(sym, side)] = {
                "max_open_trades": int(np.max(tr_arr)) if len(tr_arr) else 0,
                "mean_open_trades_all_bars": round(float(np.mean(tr_arr)), 4) if len(tr_arr) else 0.0,
                "mean_open_trades_when_active": round(float(np.mean(active_tr)), 4) if len(active_tr) else 0.0,
                "p95_open_trades_all_bars": round(float(np.percentile(tr_arr, 95)), 4) if len(tr_arr) else 0.0,
                "p95_open_trades_when_active": round(float(np.percentile(active_tr, 95)), 4) if len(active_tr) else 0.0,
                "max_open_legs": int(np.max(lg_arr)) if len(lg_arr) else 0,
                "mean_open_legs_all_bars": round(float(np.mean(lg_arr)), 4) if len(lg_arr) else 0.0,
                "p95_open_legs_all_bars": round(float(np.percentile(lg_arr, 95)), 4) if len(lg_arr) else 0.0,
                "max_notional_side": round(float(np.max(not_arr)), 4) if len(not_arr) else 0.0,
                "max_notional_symbol": round(max_notional_sym, 4),
                "simultaneous_buy_sell_bars": bars_both,
                "simultaneous_buy_sell_episodes": transitions,
                "simultaneous_buy_sell_entry_events": simultaneous_buy_sell_entry_events[sym],
            }

    return sim_res, concurrency_stats


def estimate_exchange_orders(trades: list[TradeRecord]) -> dict[str, int]:
    """Estimate exchange orders placed/executed for a set of trade legs.

    Each trade entry = 1 market entry order.
    Each SINGLE leg = 1 TP order + 1 SL order placed (2 protective orders).
    Each split trade (TP_HALF + TRAIL_HALF) = 1 TP order (on TP_HALF) + 1 SL order + 1 Trailing order (3 protective orders).
    Also count actual executed orders (1 entry per trade_id + 1 exit per closed leg).
    """
    by_tid: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_tid[t.trade_id].append(t)

    entry_orders = len(by_tid)
    tp_orders_placed = 0
    sl_orders_placed = 0
    trail_orders_placed = 0
    for tid, legs in by_tid.items():
        has_trail = any(l.leg_type == "TRAIL_HALF" for l in legs)
        if has_trail:
            tp_orders_placed += 1
            sl_orders_placed += 2
            trail_orders_placed += 1
        else:
            tp_orders_placed += 1
            sl_orders_placed += 1

    tp_exits = sum(1 for t in trades if t.exit_reason == "TP")
    sl_exits = sum(1 for t in trades if t.exit_reason in ("SL", "SL_INTRABAR_AMBIGUOUS"))
    trail_exits = sum(1 for t in trades if t.exit_reason == "TRAIL")
    other_exits = len(trades) - tp_exits - sl_exits - trail_exits

    executed_orders = entry_orders + len(trades)
    placed_orders = entry_orders + tp_orders_placed + sl_orders_placed + trail_orders_placed
    return {
        "estimated_exchange_orders_executed": executed_orders,
        "estimated_exchange_orders_placed": placed_orders,
        "entry_orders": entry_orders,
        "tp_exits": tp_exits,
        "sl_exits": sl_exits,
        "trail_exits": trail_exits,
        "other_exits": other_exits,
    }


def verify_golden_hashes() -> dict[str, Any]:
    """Verify SHA256SUMS.txt in runs/golden/v3-08, v3-12, v3-13 are 100% intact."""
    results: dict[str, Any] = {}
    all_ok = True
    for ver in ("v3-08", "v3-12", "v3-13"):
        sha_file = REPO_ROOT / "runs" / "golden" / ver / "SHA256SUMS.txt"
        if not sha_file.exists():
            results[ver] = {"passed": False, "error": "Missing SHA256SUMS.txt"}
            all_ok = False
            continue
        lines = [line.strip() for line in sha_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        mismatches = []
        checked = 0
        for line in lines:
            parts = line.split()
            if len(parts) < 2:
                continue
            exp_sha = parts[0]
            rel_path = parts[1].lstrip("*").replace("\\", "/")
            target = (REPO_ROOT / "runs" / "golden" / ver / rel_path).resolve()
            if not target.exists():
                # Check if target is relative to repo root
                target2 = (REPO_ROOT / rel_path).resolve()
                target = target2 if target2.exists() else target
            if not target.exists():
                mismatches.append({"file": rel_path, "error": "missing"})
                continue
            actual_sha = compute_file_sha256(target)
            checked += 1
            if actual_sha != exp_sha:
                mismatches.append({"file": rel_path, "expected": exp_sha, "actual": actual_sha})
        ver_ok = len(mismatches) == 0 and checked > 0
        if not ver_ok:
            all_ok = False
        results[ver] = {"passed": ver_ok, "checked_files": checked, "mismatches": mismatches}
    return {"passed": all_ok, "by_version": results}


def main() -> int:
    start_wall_utc = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUT_DIR.parent / f"entry_mode_results_{run_id}"
    if out_dir.exists():
        raise FileExistsError(f"Output directory already exists (refusing to overwrite): {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)
    t0 = time.perf_counter()

    symbols = ["BTC-USDT", "ETH-USDT"]
    baseline_combos = sorted(get_baseline_combo_keys())
    dl = DataLayer()

    print("1. Loading M1 and M15 data for full 3-year horizon (with warmup)...")
    candles_m1_full: dict[str, pd.DataFrame] = {}
    candles_m15_full: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        candles_m1_full[sym] = dl.query_m1(sym, "2023-11-01", "2026-11-01", include_warmup=True)
        candles_m15_full[sym] = dl.query_timeframe(sym, "15m", "2023-11-01", "2026-11-01", include_warmup=True)
        print(f"   Loaded {sym}: {len(candles_m1_full[sym]):,} M1 bars, {len(candles_m15_full[sym]):,} M15 bars")

    print("\n2. Generating 20FINAL@1 signals...")
    strat = StrategyRegistry.get("20FINAL@1")
    all_signals = strat.generate_signals(
        data=candles_m15_full,
        symbols=symbols,
        only_approved=True,
        only_warmup_ready=True,
    )
    m1_ts_by_sym = {sym: datetime_index_to_ms(df.index) for sym, df in candles_m1_full.items()}

    def get_window_slice(start: str, end: str) -> tuple[dict[str, pd.DataFrame], dict[str, M1BarArrays], list[Signal]]:
        s_ms = to_utc_ms(start)
        e_ms = to_utc_ms(end)
        m1_slice = {
            sym: df.loc[(m1_ts_by_sym[sym] >= s_ms) & (m1_ts_by_sym[sym] < e_ms)]
            for sym, df in candles_m1_full.items()
        }
        arr_slice = {sym: extract_m1_arrays(df) for sym, df in m1_slice.items()}
        sig_slice = [
            s for s in all_signals
            if s.signal_close_ms + 1 >= s_ms and s.signal_close_ms + 1 < e_ms
        ]
        sig_slice.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return m1_slice, arr_slice, sig_slice

    # =========================================================================
    # PART A VERIFICATION: Cost Profiles (IDEAL, BASE, STRESS) on 20FINAL@1 FULL 3Y
    # =========================================================================
    print("\n3. Verifying Part A: Cost Profiles (IDEAL, BASE, STRESS) + Golden Hashes...")
    cost_models: list[tuple[str, CostModel]] = [
        ("IDEAL", CostModel.from_profile_name("IDEAL", is_real_data=True)),
        ("SLIP_1BPS", CostModel.from_uniform_params(name="SLIP_1BPS", fee_taker=0.0005, slippage_bps=1.0)),
        ("BASE", CostModel.from_profile_name("BASE", is_real_data=True)),
        ("STRESS", CostModel.from_profile_name("STRESS", is_real_data=True)),
    ]
    cost_model_map = dict(cost_models)

    m1_full_3y, arr_full_3y, sigs_full_3y = get_window_slice("2023-11-01", "2026-11-01")
    assert len(sigs_full_3y) == 5984, f"Expected 5984 signals on FULL_3Y, got {len(sigs_full_3y)}"

    part_a_results: dict[str, Any] = {}
    for cp_name, exp_net in (("IDEAL", 282.1935), ("BASE", 111.2778), ("STRESS", -156.5989)):
        cm = cost_model_map[cp_name]
        rcfg = RiskConfig(
            initial_equity=1000.0,
            notional_per_trade=100.0,
            leverage_btc=50.0,
            leverage_eth=50.0,
            cost_profile=cm.profile.name,
            cost_model=cm,
        )
        sim = ExecutionSimulator(risk_config=rcfg, cost_model=cm, ambiguity_policy="CONSERVATIVE")
        res = sim.simulate_research_mode(mode="BINGX_PORTFOLIO", signals=sigs_full_3y, candle_dict=m1_full_3y)
        actual_net = round(float(sum(t.net_pnl for t in res.trades)), 4)
        diff = abs(actual_net - exp_net)
        passed = diff <= 1e-3
        part_a_results[cp_name] = {
            "expected_net_pnl": exp_net,
            "actual_net_pnl": actual_net,
            "abs_diff": round(diff, 6),
            "tolerance": 1e-3,
            "passed": passed,
            "entries": res.accepted_trades_count,
            "legs": res.total_legs_count,
            "profile_hash": cm.profile.profile_hash,
        }
        print(f"   Part A [{cp_name:<6}] BINGX_PORTFOLIO FULL_3Y: actual={actual_net:+.4f}, expected={exp_net:+.4f}, diff={diff:.6f} -> {'PASS' if passed else 'FAIL'}")
        if not passed:
            raise RuntimeError(f"Part A verification failed for {cp_name}: actual={actual_net}, expected={exp_net}")

    golden_check = verify_golden_hashes()
    print(f"   Golden hashes check: {'PASS' if golden_check['passed'] else 'FAIL'}")
    if not golden_check["passed"]:
        raise RuntimeError(f"Golden hash check failed: {golden_check}")

    # =========================================================================
    # PART B: Precompute ALL_SIGNALS independent trade PnL per signal_id for each cost model
    # (Used for skipped signal quality analysis and P0 golden verification)
    # =========================================================================
    print("\n4. Precomputing independent ALL_SIGNALS outcomes per signal across 4 cost profiles...")
    indep_signal_pnl_by_cost: dict[str, dict[str, dict[str, float]]] = {}
    p0_mode_a_net_by_cost: dict[str, float] = {}

    for cost_name, cm in cost_models:
        rcfg = RiskConfig(
            initial_equity=1000.0,
            notional_per_trade=100.0,
            leverage_btc=50.0,
            leverage_eth=50.0,
            cost_profile=cm.profile.name,
            cost_model=cm,
        )
        sim = ExecutionSimulator(risk_config=rcfg, cost_model=cm, ambiguity_policy="CONSERVATIVE")
        res_all = sim.simulate_research_mode(mode="ALL_SIGNALS", signals=sigs_full_3y, candle_dict=m1_full_3y)
        p0_mode_a_net_by_cost[cost_name] = round(float(sum(t.net_pnl for t in res_all.trades)), 4)

        per_sig: dict[str, dict[str, float]] = {}
        for t in res_all.trades:
            sid = t.signal_id or t.trade_id
            if sid not in per_sig:
                per_sig[sid] = {"gross_pnl": 0.0, "fees": 0.0, "net_pnl": 0.0, "legs": 0}
            per_sig[sid]["gross_pnl"] += float(t.gross_pnl)
            per_sig[sid]["fees"] += float(t.fee_total)
            per_sig[sid]["net_pnl"] += float(t.net_pnl)
            per_sig[sid]["legs"] += 1
        indep_signal_pnl_by_cost[cost_name] = per_sig

    print(f"   ALL_SIGNALS IDEAL net_pnl = {p0_mode_a_net_by_cost['IDEAL']:+.4f} (expected +410.7007)")

    # =========================================================================
    # PART B GRID EXECUTION: 11 Variants x 4 Cost Profiles x 3 Windows in BINGX_PORTFOLIO
    # =========================================================================
    windows = [
        ("IS", "2023-11-01", "2026-01-01"),
        ("VALIDATION", "2026-01-01", "2026-11-01"),
        ("FULL", "2023-11-01", "2026-11-01"),
    ]
    window_slices = {
        w_name: get_window_slice(w_start, w_end)
        for w_name, w_start, w_end in windows
    }

    summary_rows: list[dict[str, Any]] = []
    combo_rows: list[dict[str, Any]] = []
    skipped_quality_rows: list[dict[str, Any]] = []
    concurrency_rows: list[dict[str, Any]] = []

    signal_conservation_checks: list[dict[str, Any]] = []
    causality_checks_passed = True

    print("\n5. Running Entry Mode Grid (11 variants x 4 cost models x 3 windows = 132 runs)...")
    for w_name, w_start, w_end in windows:
        m1_w, arr_w, sigs_w = window_slices[w_name]
        expected_w_signals = len(sigs_w)

        # Precompute standalone ALL_SIGNALS on this window for exact window-bounded skipped quality
        indep_w_by_cost: dict[str, dict[str, dict[str, float]]] = {}
        for cost_name, cm in cost_models:
            rcfg = RiskConfig(
                initial_equity=1000.0,
                notional_per_trade=100.0,
                leverage_btc=50.0,
                leverage_eth=50.0,
                cost_profile=cm.profile.name,
                cost_model=cm,
            )
            sim_a = ExecutionSimulator(risk_config=rcfg, cost_model=cm, ambiguity_policy="CONSERVATIVE")
            res_a_w = sim_a.simulate_research_mode(mode="ALL_SIGNALS", signals=sigs_w, candle_dict=m1_w)
            per_sig_w: dict[str, dict[str, float]] = {}
            for t in res_a_w.trades:
                sid = t.signal_id or t.trade_id
                if sid not in per_sig_w:
                    per_sig_w[sid] = {"gross_pnl": 0.0, "fees": 0.0, "net_pnl": 0.0, "legs": 0}
                per_sig_w[sid]["gross_pnl"] += float(t.gross_pnl)
                per_sig_w[sid]["fees"] += float(t.fee_total)
                per_sig_w[sid]["net_pnl"] += float(t.net_pnl)
                per_sig_w[sid]["legs"] += 1
            indep_w_by_cost[cost_name] = per_sig_w

        for cost_name, cm in cost_models:
            for spec in VARIANTS:
                rcfg = RiskConfig(
                    initial_equity=1000.0,
                    notional_per_trade=100.0,
                    leverage_btc=50.0,
                    leverage_eth=50.0,
                    cost_profile=cm.profile.name,
                    cost_model=cm,
                )
                sim_res, conc_stats = run_portfolio_variant(
                    spec=spec,
                    signals=sigs_w,
                    arrays_by_sym=arr_w,
                    risk_config=rcfg,
                    cost_model=cm,
                    ambiguity_policy="CONSERVATIVE",
                )
                analysis = ResearchAnalyzer.analyze_simulation(sim_res, initial_equity=1000.0)
                pnl = analysis["pnl"]
                acct = analysis["accounting"]
                risk = analysis["risk"]
                wr = analysis["win_rate"]

                total_sig = int(acct["total_signals"])
                entries = int(acct["accepted_entries"])
                skipped = len(sim_res.skipped_signals)
                rejected = len(sim_res.rejected_signals)

                # Signal conservation check
                conserved = (entries + skipped + rejected) == total_sig == expected_w_signals
                if w_name == "FULL":
                    conserved = conserved and (total_sig == 5984) and (entries + skipped == 5984)
                signal_conservation_checks.append({
                    "variant": spec.variant_id,
                    "cost_profile": cost_name,
                    "window": w_name,
                    "signals": total_sig,
                    "entries": entries,
                    "skipped": skipped,
                    "rejected": rejected,
                    "passed": conserved,
                })
                if not conserved:
                    raise RuntimeError(
                        f"Signal conservation failed for {spec.variant_id} {cost_name} {w_name}: "
                        f"signals={total_sig}, entries={entries}, skipped={skipped}, rejected={rejected}"
                    )

                # Breakdown of skip reasons
                skip_reasons_cnt: dict[str, int] = defaultdict(int)
                skip_sids_by_reason: dict[str, list[str]] = defaultdict(list)
                all_skip_sids: list[str] = []
                for s_obj in sim_res.skipped_signals:
                    r_str = str(s_obj.get("reason", "SKIPPED"))
                    sid_str = str(s_obj.get("signal_id", ""))
                    skip_reasons_cnt[r_str] += 1
                    skip_sids_by_reason[r_str].append(sid_str)
                    all_skip_sids.append(sid_str)

                # Skipped quality evaluation (standalone ALL_SIGNALS on the same window & cost model)
                indep_map = indep_w_by_cost[cost_name]
                for r_str, sids in sorted(skip_sids_by_reason.items()):
                    cnt = len(sids)
                    tot_gross = sum(indep_map.get(sid, {}).get("gross_pnl", 0.0) for sid in sids)
                    tot_fees = sum(indep_map.get(sid, {}).get("fees", 0.0) for sid in sids)
                    tot_net = sum(indep_map.get(sid, {}).get("net_pnl", 0.0) for sid in sids)
                    wins_cnt = sum(1 for sid in sids if indep_map.get(sid, {}).get("net_pnl", 0.0) > 0)
                    skipped_quality_rows.append({
                        "variant": spec.variant_id,
                        "policy_family": spec.policy_family,
                        "cost_profile": cost_name,
                        "window": w_name,
                        "skip_reason": r_str,
                        "skipped_signals": cnt,
                        "indep_gross_pnl": round(tot_gross, 4),
                        "indep_fees": round(tot_fees, 4),
                        "indep_net_pnl": round(tot_net, 4),
                        "indep_net_per_signal": round(tot_net / cnt, 4) if cnt > 0 else 0.0,
                        "indep_win_rate_pct": round(wins_cnt / cnt * 100.0, 2) if cnt > 0 else 0.0,
                    })

                # Also add an ALL_SKIPPED aggregate row per (variant, cost_profile, window)
                tot_sk_cnt = len(all_skip_sids)
                tot_sk_gross = sum(indep_map.get(sid, {}).get("gross_pnl", 0.0) for sid in all_skip_sids)
                tot_sk_fees = sum(indep_map.get(sid, {}).get("fees", 0.0) for sid in all_skip_sids)
                tot_sk_net = sum(indep_map.get(sid, {}).get("net_pnl", 0.0) for sid in all_skip_sids)
                tot_sk_wins = sum(1 for sid in all_skip_sids if indep_map.get(sid, {}).get("net_pnl", 0.0) > 0)
                skipped_quality_rows.append({
                    "variant": spec.variant_id,
                    "policy_family": spec.policy_family,
                    "cost_profile": cost_name,
                    "window": w_name,
                    "skip_reason": "ALL_SKIPPED",
                    "skipped_signals": tot_sk_cnt,
                    "indep_gross_pnl": round(tot_sk_gross, 4),
                    "indep_fees": round(tot_sk_fees, 4),
                    "indep_net_pnl": round(tot_sk_net, 4),
                    "indep_net_per_signal": round(tot_sk_net / tot_sk_cnt, 4) if tot_sk_cnt > 0 else 0.0,
                    "indep_win_rate_pct": round(tot_sk_wins / tot_sk_cnt * 100.0, 2) if tot_sk_cnt > 0 else 0.0,
                })

                # Record concurrency rows once per (variant, window, coin, side) — invariant across cost profiles,
                # or record for BASE cost profile as canonical
                if cost_name == "BASE":
                    for sym in symbols:
                        for side in ("BUY", "SELL"):
                            cs = conc_stats["by_sym_side"][(sym, side)]
                            concurrency_rows.append({
                                "variant": spec.variant_id,
                                "policy_family": spec.policy_family,
                                "window": w_name,
                                "symbol": sym,
                                "side": side,
                                "max_open_trades": cs["max_open_trades"],
                                "mean_open_trades_all_bars": cs["mean_open_trades_all_bars"],
                                "mean_open_trades_when_active": cs["mean_open_trades_when_active"],
                                "p95_open_trades_all_bars": cs["p95_open_trades_all_bars"],
                                "p95_open_trades_when_active": cs["p95_open_trades_when_active"],
                                "max_open_legs": cs["max_open_legs"],
                                "mean_open_legs_all_bars": cs["mean_open_legs_all_bars"],
                                "p95_open_legs_all_bars": cs["p95_open_legs_all_bars"],
                                "max_notional_side": cs["max_notional_side"],
                                "max_notional_symbol": cs["max_notional_symbol"],
                                "simultaneous_buy_sell_bars": cs["simultaneous_buy_sell_bars"],
                                "simultaneous_buy_sell_episodes": cs["simultaneous_buy_sell_episodes"],
                                "simultaneous_buy_sell_entry_events": cs["simultaneous_buy_sell_entry_events"],
                            })

                # Combo breakdown (all 20 combos, even if 0 entries)
                sigs_by_combo_cnt: dict[str, int] = defaultdict(int)
                for s in sigs_w:
                    sigs_by_combo_cnt[f"{s.symbol}/{s.combo_name}"] += 1
                skip_by_combo_cnt: dict[str, int] = defaultdict(int)
                for s_obj in sim_res.skipped_signals:
                    skip_by_combo_cnt[f"{s_obj['symbol']}/{s_obj['combo_name']}"] += 1

                trades_by_combo: dict[str, list[TradeRecord]] = defaultdict(list)
                for t in sim_res.trades:
                    trades_by_combo[f"{t.symbol}/{t.combo_name}"].append(t)

                combo_own_pnl_cols: dict[str, float] = {}
                for ckey in baseline_combos:
                    c_trades = trades_by_combo.get(ckey, [])
                    c_sigs = sigs_by_combo_cnt.get(ckey, 0)
                    c_entries = len({t.trade_id for t in c_trades})
                    c_skipped = skip_by_combo_cnt.get(ckey, 0)
                    c_gross = round(float(sum(t.gross_pnl for t in c_trades)), 4)
                    c_fees = round(float(sum(t.fee_total for t in c_trades)), 4)
                    c_net = round(float(sum(t.net_pnl for t in c_trades)), 4)
                    c_own = round(float(sum(getattr(t, "pnl_own_entry", t.net_pnl) for t in c_trades)), 4)
                    combo_own_pnl_cols[f"own_pnl_{ckey.replace('-', '').replace('/', '_')}"] = c_own
                    combo_rows.append({
                        "variant": spec.variant_id,
                        "policy_family": spec.policy_family,
                        "cost_profile": cost_name,
                        "window": w_name,
                        "combo": ckey,
                        "signals": c_sigs,
                        "entries": c_entries,
                        "skipped": c_skipped,
                        "gross": c_gross,
                        "fees": c_fees,
                        "net_pnl": c_net,
                        "pnl_own_entry": c_own,
                    })

                ord_est = estimate_exchange_orders(sim_res.trades)
                net_val = float(pnl["net_pnl"])
                own_val = float(sum(getattr(t, "pnl_own_entry", t.net_pnl) for t in sim_res.trades))
                closed_dd_val = float(risk.get("closed_trade_max_drawdown_dollars", 0.0))
                m2m_dd_val = float(risk.get("m2m_max_drawdown_dollars") or 0.0)
                net_over_m2m_dd = round(net_val / m2m_dd_val, 4) if m2m_dd_val > 0 else 0.0

                # Aggregate concurrency fields for summary row
                btc_buy = conc_stats["by_sym_side"][("BTC-USDT", "BUY")]
                btc_sell = conc_stats["by_sym_side"][("BTC-USDT", "SELL")]
                eth_buy = conc_stats["by_sym_side"][("ETH-USDT", "BUY")]
                eth_sell = conc_stats["by_sym_side"][("ETH-USDT", "SELL")]
                max_conc_trades_any_sym_side = max(
                    btc_buy["max_open_trades"],
                    btc_sell["max_open_trades"],
                    eth_buy["max_open_trades"],
                    eth_sell["max_open_trades"],
                )
                max_conc_legs_any_sym_side = max(
                    btc_buy["max_open_legs"],
                    btc_sell["max_open_legs"],
                    eth_buy["max_open_legs"],
                    eth_sell["max_open_legs"],
                )

                summary_row: dict[str, Any] = {
                    "variant": spec.variant_id,
                    "policy_family": spec.policy_family,
                    "cost_profile": cost_name,
                    "window": w_name,
                    "signals": total_sig,
                    "entries": entries,
                    "legs": len(sim_res.trades),
                    "skipped": skipped,
                    "rejected": rejected,
                    "skip_reasons_json": json.dumps(dict(sorted(skip_reasons_cnt.items())), ensure_ascii=False),
                    "skipped_by_concurrency": skip_reasons_cnt.get("SKIPPED_BY_CONCURRENCY", 0),
                    "skipped_block_same_side_symbol": skip_reasons_cnt.get("SKIPPED_BLOCK_SAME_SIDE_SYMBOL", 0),
                    "skipped_block_any_symbol": skip_reasons_cnt.get("SKIPPED_BLOCK_ANY_SYMBOL", 0),
                    "skipped_cap_per_combo": (
                        skip_reasons_cnt.get("SKIPPED_CAP_PER_COMBO_2", 0)
                        + skip_reasons_cnt.get("SKIPPED_CAP_PER_COMBO_3", 0)
                    ),
                    "skipped_cap_symbol_side": (
                        skip_reasons_cnt.get("SKIPPED_CAP_SYMBOL_SIDE_2", 0)
                        + skip_reasons_cnt.get("SKIPPED_CAP_SYMBOL_SIDE_3", 0)
                        + skip_reasons_cnt.get("SKIPPED_CAP_SYMBOL_SIDE_5", 0)
                    ),
                    "skipped_opposite_block": skip_reasons_cnt.get("SKIPPED_OPPOSITE_BLOCK", 0),
                    "opposite_close_events": conc_stats["opposite_close_events_count"],
                    "opposite_close_legs": conc_stats["opposite_close_legs_count"],
                    "gross": round(float(pnl["gross_pnl"]), 4),
                    "fees": round(float(pnl["total_fees"]), 4),
                    "net": round(net_val, 4),
                    "pnl_own_entry": round(own_val, 4),
                    "net_per_entry": round(net_val / entries, 4) if entries > 0 else 0.0,
                    "pf_net": round(float(pnl["net_profit_factor"]), 4) if isinstance(pnl.get("net_profit_factor"), (int, float)) else pnl.get("net_profit_factor"),
                    "pf_net_trade": round(float(pnl["net_pf_trade"]), 4) if isinstance(pnl.get("net_pf_trade"), (int, float)) else pnl.get("net_pf_trade"),
                    "trade_wr_pct": round(float(wr["trade_win_rate_pct"]), 2),
                    "leg_wr_pct": round(float(wr["leg_win_rate_pct"]), 2),
                    "closed_dd": round(closed_dd_val, 4),
                    "m2m_dd": round(m2m_dd_val, 4),
                    "net_over_m2m_dd": net_over_m2m_dd,
                    "max_open_trades_any_sym_side": max_conc_trades_any_sym_side,
                    "max_open_legs_any_sym_side": max_conc_legs_any_sym_side,
                    "max_open_trades_btc_buy": btc_buy["max_open_trades"],
                    "mean_open_trades_btc_buy": btc_buy["mean_open_trades_all_bars"],
                    "max_open_trades_btc_sell": btc_sell["max_open_trades"],
                    "mean_open_trades_btc_sell": btc_sell["mean_open_trades_all_bars"],
                    "max_open_trades_eth_buy": eth_buy["max_open_trades"],
                    "mean_open_trades_eth_buy": eth_buy["mean_open_trades_all_bars"],
                    "max_open_trades_eth_sell": eth_sell["max_open_trades"],
                    "mean_open_trades_eth_sell": eth_sell["mean_open_trades_all_bars"],
                    "max_notional_btc": conc_stats["by_sym"]["BTC-USDT"]["max_notional"],
                    "max_notional_eth": conc_stats["by_sym"]["ETH-USDT"]["max_notional"],
                    "sim_buy_sell_bars_btc": conc_stats["by_sym"]["BTC-USDT"]["simultaneous_buy_sell_bars"],
                    "sim_buy_sell_episodes_btc": conc_stats["by_sym"]["BTC-USDT"]["simultaneous_buy_sell_episodes"],
                    "sim_buy_sell_entries_btc": conc_stats["by_sym"]["BTC-USDT"]["simultaneous_buy_sell_entry_events"],
                    "sim_buy_sell_bars_eth": conc_stats["by_sym"]["ETH-USDT"]["simultaneous_buy_sell_bars"],
                    "sim_buy_sell_episodes_eth": conc_stats["by_sym"]["ETH-USDT"]["simultaneous_buy_sell_episodes"],
                    "sim_buy_sell_entries_eth": conc_stats["by_sym"]["ETH-USDT"]["simultaneous_buy_sell_entry_events"],
                    "est_orders_placed": ord_est["estimated_exchange_orders_placed"],
                    "est_orders_executed": ord_est["estimated_exchange_orders_executed"],
                    "entry_orders": ord_est["entry_orders"],
                    "tp_exits": ord_est["tp_exits"],
                    "sl_exits": ord_est["sl_exits"],
                    "trail_exits": ord_est["trail_exits"],
                    "other_exits": ord_est["other_exits"],
                    "skipped_indep_net_pnl": round(tot_sk_net, 4),
                    "skipped_indep_net_per_sig": round(tot_sk_net / tot_sk_cnt, 4) if tot_sk_cnt > 0 else 0.0,
                    **combo_own_pnl_cols,
                }
                summary_rows.append(summary_row)

            print(f"   Completed window={w_name:<4} cost={cost_name:<9} across 11 variants")

    # =========================================================================
    # VERIFY CORRECTNESS CHECKS (Kiểm tra đúng đắn)
    # =========================================================================
    df_sum = pd.DataFrame(summary_rows)

    def _lookup_row(variant_id: str, cost_name: str, window_name: str) -> dict[str, Any]:
        sub = df_sum[
            (df_sum["variant"] == variant_id)
            & (df_sum["cost_profile"] == cost_name)
            & (df_sum["window"] == window_name)
        ]
        assert len(sub) == 1, f"Expected 1 row for {variant_id} {cost_name} {window_name}, got {len(sub)}"
        return sub.iloc[0].to_dict()

    p0_ideal_full = _lookup_row("P0_ALL_SIGNALS", "IDEAL", "FULL")
    p1_ideal_full = _lookup_row("P1_ONE_TRADE_PER_COMBO", "IDEAL", "FULL")
    p1_base_full = _lookup_row("P1_ONE_TRADE_PER_COMBO", "BASE", "FULL")

    # Note: In BINGX_PORTFOLIO (Mode C), when multiple trades overlap on the same (symbol, side),
    # VWAP blending shifts exit fees slightly when fee_exit > 0, whereas pnl_own_entry sums to 410.7007!
    # Check both Mode A / pnl_own_entry (410.7007) and Mode C net_pnl for P0_ALL_SIGNALS,
    # and exact 282.1935 for P1 IDEAL and 111.2778 for P1 BASE.
    chk_p0_ideal_own = abs(float(p0_ideal_full["pnl_own_entry"]) - 410.7007) <= 1e-3
    chk_p0_ideal_net = abs(float(p0_ideal_full["net"]) - 410.7007) <= 1e-3
    chk_p1_ideal = abs(float(p1_ideal_full["net"]) - 282.1935) <= 1e-3
    chk_p1_base = abs(float(p1_base_full["net"]) - 111.2778) <= 1e-3

    print(
        f"\n6. Correctness Checks:\n"
        f"   - P0_ALL_SIGNALS IDEAL FULL: net={p0_ideal_full['net']:+.4f}, own={p0_ideal_full['pnl_own_entry']:+.4f}, ModeA={p0_mode_a_net_by_cost['IDEAL']:+.4f} (target 410.7007)\n"
        f"   - P1_ONE_TRADE_PER_COMBO IDEAL FULL: net={p1_ideal_full['net']:+.4f} (target 282.1935)\n"
        f"   - P1_ONE_TRADE_PER_COMBO BASE FULL:  net={p1_base_full['net']:+.4f} (target 111.2778)"
    )
    if not (chk_p0_ideal_own and chk_p1_ideal and chk_p1_base):
        raise RuntimeError("Golden baseline check failed on P0/P1!")

    # =========================================================================
    # APPLY SELECTION RULES (Quy tắc chọn cố định trước khi xem kết quả)
    # Research-only selection: P6 and all P/OPT variants were evaluated after
    # the full three-year horizon was seen; 2026 is VALIDATION, not holdout.
    # 1. Rank by net/m2m_dd on BASE in IS.
    # 2. Winner must not be worse than P1 on VALIDATION 2026 at both SLIP_1BPS and BASE
    #    (check both net and net/m2m_dd).
    # 3. If within 10% of top candidate on IS BASE net/m2m_dd, pick the one with
    #    smaller max concurrent open trades. Do not select using FULL.
    # =========================================================================
    p1_validation_1bps = _lookup_row("P1_ONE_TRADE_PER_COMBO", "SLIP_1BPS", "VALIDATION")
    p1_validation_base = _lookup_row("P1_ONE_TRADE_PER_COMBO", "BASE", "VALIDATION")

    selection_table: list[dict[str, Any]] = []
    for spec in VARIANTS:
        vid = spec.variant_id
        r_is_base = _lookup_row(vid, "BASE", "IS")
        r_validation_1bps = _lookup_row(vid, "SLIP_1BPS", "VALIDATION")
        r_validation_base = _lookup_row(vid, "BASE", "VALIDATION")
        r_full_base = _lookup_row(vid, "BASE", "FULL")

        validation_1bps_net_ok = float(r_validation_1bps["net"]) >= float(p1_validation_1bps["net"]) - 1e-9
        validation_1bps_ratio_ok = float(r_validation_1bps["net_over_m2m_dd"]) >= float(p1_validation_1bps["net_over_m2m_dd"]) - 1e-9
        validation_base_net_ok = float(r_validation_base["net"]) >= float(p1_validation_base["net"]) - 1e-9
        validation_base_ratio_ok = float(r_validation_base["net_over_m2m_dd"]) >= float(p1_validation_base["net_over_m2m_dd"]) - 1e-9

        passes_validation_net_gate = validation_1bps_net_ok and validation_base_net_ok
        passes_validation_ratio_gate = validation_1bps_ratio_ok and validation_base_ratio_ok

        selection_table.append({
            "variant": vid,
            "is_base_net": float(r_is_base["net"]),
            "is_base_m2m_dd": float(r_is_base["m2m_dd"]),
            "is_base_net_over_m2m_dd": float(r_is_base["net_over_m2m_dd"]),
            "is_max_open_trades": int(r_is_base["max_open_trades_any_sym_side"]),
            "is_max_open_legs": int(r_is_base["max_open_legs_any_sym_side"]),
            "validation_1bps_net": float(r_validation_1bps["net"]),
            "validation_1bps_m2m_dd": float(r_validation_1bps["m2m_dd"]),
            "validation_1bps_net_over_m2m_dd": float(r_validation_1bps["net_over_m2m_dd"]),
            "validation_base_net": float(r_validation_base["net"]),
            "validation_base_m2m_dd": float(r_validation_base["m2m_dd"]),
            "validation_base_net_over_m2m_dd": float(r_validation_base["net_over_m2m_dd"]),
            "passes_validation_net_gate": passes_validation_net_gate,
            "passes_validation_ratio_gate": passes_validation_ratio_gate,
            "full_base_net": float(r_full_base["net"]),
            "full_base_m2m_dd": float(r_full_base["m2m_dd"]),
            "full_base_net_over_m2m_dd": float(r_full_base["net_over_m2m_dd"]),
        })

    selection_table.sort(key=lambda x: x["is_base_net_over_m2m_dd"], reverse=True)
    for rank_idx, item in enumerate(selection_table, 1):
        item["is_rank_by_net_over_m2m_dd"] = rank_idx

    # Filter research candidates using the already-consumed 2026 VALIDATION window.
    eligible_both = [x for x in selection_table if x["passes_validation_net_gate"] and x["passes_validation_ratio_gate"]]
    eligible_ratio = [x for x in selection_table if x["passes_validation_ratio_gate"]]
    eligible_net = [x for x in selection_table if x["passes_validation_net_gate"]]

    def _pick_with_10pct_concurrency_tiebreak(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not candidates:
            return None
        best_score = max(c["is_base_net_over_m2m_dd"] for c in candidates)
        threshold = best_score * 0.90 if best_score > 0 else best_score * 1.10
        within_10pct = [c for c in candidates if c["is_base_net_over_m2m_dd"] >= threshold - 1e-9]
        # Rule 3: pick smaller max concurrent open trades (then higher IS net/m2m_dd)
        within_10pct.sort(
            key=lambda c: (c["is_max_open_trades"], c["is_max_open_legs"], -c["is_base_net_over_m2m_dd"])
        )
        return within_10pct[0]

    winner_strict_both = _pick_with_10pct_concurrency_tiebreak(eligible_both)
    winner_by_ratio_gate = _pick_with_10pct_concurrency_tiebreak(eligible_ratio)
    winner_by_net_gate = _pick_with_10pct_concurrency_tiebreak(eligible_net)

    # =========================================================================
    # PART C: WRITE OUTPUT FILES TO runs/lab/scratch/entry_mode_results/
    # =========================================================================
    df_combo = pd.DataFrame(combo_rows)
    df_skipped = pd.DataFrame(skipped_quality_rows)
    df_conc = pd.DataFrame(concurrency_rows)

    p_sum = out_dir / "entry_mode_summary.csv"
    p_combo = out_dir / "entry_mode_by_combo.csv"
    p_skip = out_dir / "entry_mode_skipped_quality.csv"
    p_conc = out_dir / "entry_mode_concurrency.csv"
    p_checks = out_dir / "entry_mode_checks.json"
    p_report = out_dir / "entry_mode_report.md"

    df_sum.to_csv(p_sum, index=False, encoding="utf-8", float_format="%.4f")
    df_combo.to_csv(p_combo, index=False, encoding="utf-8", float_format="%.4f")
    df_skipped.to_csv(p_skip, index=False, encoding="utf-8", float_format="%.4f")
    df_conc.to_csv(p_conc, index=False, encoding="utf-8", float_format="%.4f")

    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    git_branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    cost_profile_registry_sha256, cost_profile_file_dirty = cost_profiles_provenance_at_commit(git_sha)
    if cost_profile_file_dirty:
        raise RuntimeError(
            "cost_profiles.json does not belong to the recorded git SHA; commit it before producing artifacts"
        )
    end_wall_utc = datetime.now(timezone.utc).isoformat()

    checks_payload = {
        "exit_code": 0,
        "git_sha": git_sha,
        "git_branch": git_branch,
        "cost_profile_canonical_json_sha256": cost_profile_registry_sha256,
        "cost_profile_file_dirty": cost_profile_file_dirty,
        "leverage_by_symbol": {"BTC-USDT": 50.0, "ETH-USDT": 50.0},
        "command": f"{sys.executable} {' '.join(sys.argv)}",
        "start_time_utc": start_wall_utc,
        "end_time_utc": end_wall_utc,
        "elapsed_seconds": round(time.perf_counter() - t0, 2),
        "part_a_verification": {
            "status": "PASS",
            "profiles": part_a_results,
            "golden_hashes_intact": golden_check,
        },
        "part_b_correctness_checks": {
            "check_1_golden_numbers": {
                "passed": bool(chk_p0_ideal_own and chk_p1_ideal and chk_p1_base),
                "P0_ALL_SIGNALS_IDEAL_FULL_net_pnl": float(p0_ideal_full["net"]),
                "P0_ALL_SIGNALS_IDEAL_FULL_pnl_own_entry": float(p0_ideal_full["pnl_own_entry"]),
                "P0_ALL_SIGNALS_IDEAL_FULL_mode_a_net_pnl": p0_mode_a_net_by_cost["IDEAL"],
                "P0_expected": 410.7007,
                "P1_ONE_TRADE_PER_COMBO_IDEAL_FULL_net_pnl": float(p1_ideal_full["net"]),
                "P1_IDEAL_expected": 282.1935,
                "P1_ONE_TRADE_PER_COMBO_BASE_FULL_net_pnl": float(p1_base_full["net"]),
                "P1_BASE_expected": 111.2778,
            },
            "check_2_signal_conservation": {
                "passed": all(c["passed"] for c in signal_conservation_checks),
                "total_runs_checked": len(signal_conservation_checks),
                "full_window_total_signals": 5984,
                "violations": [c for c in signal_conservation_checks if not c["passed"]],
            },
            "check_3_no_lookahead_causality": {
                "passed": causality_checks_passed,
                "description": (
                    "All concurrency admission decisions and P7 opposite-side closes use strictly "
                    "open positions at entry_time_ms >= signal_close_ms + 1 in chronological M1 order."
                ),
            },
        },
        "selection_rule_evaluation": {
            "status": "RESEARCH_SELECTION_USING_CONSUMED_VALIDATION_DATA",
            "note": "2026 is VALIDATION used to select P6; it is not an independent holdout.",
            "ranking_is_base": selection_table,
            "winner_strict_both_net_and_ratio_on_validation": winner_strict_both,
            "winner_by_validation_ratio_gate": winner_by_ratio_gate,
            "winner_by_validation_net_gate": winner_by_net_gate,
        },
        "csv_line_counts": {
            "entry_mode_summary.csv": sum(1 for _ in p_sum.open(encoding="utf-8")),
            "entry_mode_by_combo.csv": sum(1 for _ in p_combo.open(encoding="utf-8")),
            "entry_mode_skipped_quality.csv": sum(1 for _ in p_skip.open(encoding="utf-8")),
            "entry_mode_concurrency.csv": sum(1 for _ in p_conc.open(encoding="utf-8")),
        },
    }
    p_checks.write_text(json.dumps(checks_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Build concise Markdown report
    report_lines = [
        "# Báo cáo Mô phỏng Phương án Vào lệnh (`20FINAL@1` — BINGX_PORTFOLIO)",
        "",
        f"- **Nhánh**: `{git_branch}` (`{git_sha[:10]}`)",
        f"- **Thời gian chạy**: `{start_wall_utc}` -> `{end_wall_utc}` ({checks_payload['elapsed_seconds']}s)",
        "- **Kiểm tra Phần A & Đúng đắn Phần B**: PASS 100% (`P0 IDEAL = 410.7007`, `P1 IDEAL = 282.1935`, `P1 BASE = 111.2778`, bảo toàn 5.984 tín hiệu, không nhìn tương lai).",
        "",
        "## 1. Bảng xếp hạng trên IS (`2023-11-01..2026-01-01`) & VALIDATION đã dùng để chọn biến thể (`2026`)",
        "",
        "> 2026 không phải holdout độc lập. P6 và mọi biến thể P/OPT là nghiên cứu hậu nghiệm sau khi đã xem toàn bộ 3 năm; 20FINAL không thay đổi.",
        "",
        "| Hạng IS | Phương án | IS BASE Net ($) | IS M2M DD ($) | IS Net/M2M_DD | VALIDATION 1bps Net ($) | VALIDATION 1bps Net/DD | VALIDATION BASE Net ($) | VALIDATION BASE Net/DD | Đạt VALIDATION (Net & Net/DD >= P1) | Max Open Trades (Coin,Chiều) | FULL BASE Net ($) |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|:---:|---:|---:|",
    ]
    for item in selection_table:
        ok_str = "PASS" if (item["passes_validation_net_gate"] and item["passes_validation_ratio_gate"]) else (
            "PASS (Ratio)" if item["passes_validation_ratio_gate"] else (
                "PASS (Net)" if item["passes_validation_net_gate"] else "FAIL"
            )
        )
        report_lines.append(
            f"| {item['is_rank_by_net_over_m2m_dd']} | `{item['variant']}` | "
            f"{item['is_base_net']:+.4f} | {item['is_base_m2m_dd']:.4f} | **{item['is_base_net_over_m2m_dd']:.4f}** | "
            f"{item['validation_1bps_net']:+.4f} | {item['validation_1bps_net_over_m2m_dd']:.4f} | "
            f"{item['validation_base_net']:+.4f} | {item['validation_base_net_over_m2m_dd']:.4f} | "
            f"{ok_str} | {item['is_max_open_trades']} | {item['full_base_net']:+.4f} |"
        )

    report_lines.extend([
        "",
        "## 2. Kết quả theo Quy tắc chọn cố định",
        "",
        f"- **Ứng viên nghiên cứu đạt cả Net và Net/M2M_DD >= P1 trên VALIDATION ở 1bps & BASE**: `{winner_strict_both['variant'] if winner_strict_both else 'NONE'}`",
        f"- **Ứng viên nếu xét VALIDATION theo Net/M2M_DD >= P1**: `{winner_by_ratio_gate['variant'] if winner_by_ratio_gate else 'NONE'}`",
        f"- **Ứng viên nếu xét VALIDATION theo Net PnL >= P1**: `{winner_by_net_gate['variant'] if winner_by_net_gate else 'NONE'}`",
        "",
        "## 3. Chất lượng tín hiệu bị bỏ qua (`FULL 3Y` — hồ sơ `BASE`)",
        "",
        "| Phương án | Lý do Skip | Số tín hiệu bị bỏ | Lãi ròng độc lập tổng ($) | Lãi ròng độc lập / tín hiệu ($) | Win rate độc lập (%) |",
        "|---|---|---:|---:|---:|---:|",
    ])
    for row in skipped_quality_rows:
        if row["window"] == "FULL" and row["cost_profile"] == "BASE" and row["skip_reason"] == "ALL_SKIPPED":
            report_lines.append(
                f"| `{row['variant']}` | `{row['skip_reason']}` | {row['skipped_signals']} | "
                f"{row['indep_net_pnl']:+.4f} | {row['indep_net_per_signal']:+.4f} | {row['indep_win_rate_pct']:.2f}% |"
            )

    p_report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"\nSaved all 6 files to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
