"""Unified M1 execution simulator engine for Modes A, B, and C."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
import numpy as np
import pandas as pd

from autotrade_v3.engine.execution_simulator import ExecutionEvent, SimulationResult, TradeRecord
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.account import AccountLedger
from autotrade_v3.sim.admission import AllSignals, OneTradePerCombo, Portfolio
from autotrade_v3.sim.ambiguity import AmbiguityPolicy, normalize_ambiguity_policy
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.leg import extract_m1_arrays, simulate_leg
from autotrade_v3.sim.types import Event, ExitRules, LegOutcome, LegSpec, M1BarArrays, SignalDecision
from autotrade_v3.strategy.base import ExitPolicy


@dataclass
class _PendingPortfolioLeg:
    sig: Signal
    signal_id: str
    trade_id: str
    leg_spec: LegSpec
    leg_qty: float
    leg_margin: float
    leg_entry_fee: float
    entry_price: float
    entry_px_raw: float
    outcome: LegOutcome
    exit_rules: ExitRules
    provider: str


def _resolve_signal_id(sig: Signal) -> str:
    sig_id = getattr(sig, "signal_id", "")
    if sig_id:
        return str(sig_id)
    sym_clean = sig.symbol.replace("-", "").replace("_", "")
    return f"SIG_{sym_clean}_{sig.combo_name}_{sig.timeframe}_{sig.side}_{sig.signal_close_ms}"


def _resolve_exit_policy(
    sig: Signal,
    final14_exit_mode: str,
    trailing_act_pct: float,
    trailing_cb_pct: float,
) -> ExitPolicy:
    if getattr(sig, "exit_policy", None) is not None:
        return sig.exit_policy
    use_trailing = (not sig.is_new6) and (final14_exit_mode == "50TP_50TRAIL")
    return ExitPolicy(
        tp_pct=sig.tp_pct,
        sl_pct=sig.sl_pct,
        split_legs=use_trailing,
        trailing_act_pct=trailing_act_pct,
        trailing_cb_pct=trailing_cb_pct,
        leg_weights=(0.5, 0.5) if use_trailing else (1.0,),
    )


def _build_leg_specs(trade_id: str, notional: float, policy: ExitPolicy) -> list[LegSpec]:
    if not policy.split_legs:
        w = policy.leg_weights[0] if policy.leg_weights else 1.0
        return [
            LegSpec(
                leg_id=f"{trade_id}#L1",
                trade_id=trade_id,
                leg_type="SINGLE",
                notional=notional * w,
                is_trailing=False,
            )
        ]
    w1 = policy.leg_weights[0] if len(policy.leg_weights) > 0 else 0.5
    w2 = policy.leg_weights[1] if len(policy.leg_weights) > 1 else 0.5
    return [
        LegSpec(
            leg_id=f"{trade_id}#L1",
            trade_id=trade_id,
            leg_type="TP_HALF",
            notional=notional * w1,
            is_trailing=False,
        ),
        LegSpec(
            leg_id=f"{trade_id}#L2",
            trade_id=trade_id,
            leg_type="TRAIL_HALF",
            notional=notional * w2,
            is_trailing=True,
        ),
    ]


def _build_exit_rules(policy: ExitPolicy) -> ExitRules:
    return ExitRules(
        tp_pct=policy.tp_pct,
        sl_pct=policy.sl_pct,
        trailing_act_pct=policy.trailing_act_pct,
        trailing_cb_pct=policy.trailing_cb_pct,
        max_bars_held=policy.max_bars_held,
        exit_policy_hash=policy.get_policy_hash(),
    )


def _to_execution_event(ev: Event) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=ev.event_id,
        timestamp_ms=ev.timestamp_ms,
        timestamp_dt=ev.timestamp_dt,
        trade_id=ev.trade_id,
        leg_id=ev.leg_id,
        symbol=ev.symbol,
        combo_id=ev.combo_id,
        combo_name=ev.combo_name,
        timeframe=ev.timeframe,
        event_type=ev.event_type,
        side=ev.side,
        order_action=ev.order_action,
        price=ev.price,
        qty=ev.qty,
        notional=ev.notional,
        fee=ev.fee,
        realized_pnl=ev.realized_pnl,
        exit_reason=ev.exit_reason,
        signal_id=ev.signal_id,
    )


def _canonicalize_mode(mode: str) -> Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]:
    mode_clean = mode.upper().strip()
    if mode_clean in ("A", "MODE_A", "ALL_SIGNALS"):
        return "ALL_SIGNALS"
    if mode_clean in ("B", "MODE_B", "ONE_TRADE_PER_COMBO", "VIRTUAL_ISOLATED"):
        return "ONE_TRADE_PER_COMBO"
    if mode_clean in ("C", "MODE_C", "BINGX_PORTFOLIO", "PORTFOLIO", "EXCHANGE_HEDGE_MERGED"):
        return "BINGX_PORTFOLIO"
    raise ValueError(
        f"Unknown research simulation mode: {mode}. "
        "Expected ALL_SIGNALS, ONE_TRADE_PER_COMBO, or BINGX_PORTFOLIO"
    )


class UnifiedSimulator:
    """Unified M1 execution simulator coordinating signal -> decision -> leg -> ledger -> artifacts."""

    def __init__(
        self,
        risk_config: RiskConfig | None = None,
        final14_exit_mode: Literal["50TP_50TRAIL", "100_HARD"] = "50TP_50TRAIL",
        trailing_act_pct: float = 0.005,
        trailing_cb_pct: float = 0.005,
        cost_model: CostModel | None = None,
        ambiguity_policy: str = "LEGACY",
    ) -> None:
        self.risk = risk_config or RiskConfig()
        self.final14_exit_mode = final14_exit_mode
        self.trailing_act_pct = trailing_act_pct
        self.trailing_cb_pct = trailing_cb_pct
        self.ambiguity_policy: AmbiguityPolicy = normalize_ambiguity_policy(ambiguity_policy)
        if cost_model is not None:
            self.cost_model = cost_model
        elif getattr(self.risk, "cost_model", None) is not None:
            self.cost_model = self.risk.cost_model
        elif self.risk.slippage_rate == 0.0 and self.risk.taker_fee_rate == 0.0005 and self.risk.cost_profile.upper() == "IDEAL":
            self.cost_model = CostModel.from_profile_name("IDEAL", is_real_data=False)
        else:
            self.cost_model = CostModel.from_uniform_params(
                name=self.risk.cost_profile or "CUSTOM",
                fee_taker=self.risk.taker_fee_rate,
                fee_maker=self.risk.maker_fee_rate,
                slippage_bps=self.risk.slippage_rate * 10000.0,
            )

    def run(
        self,
        mode: str,
        signals: list[Signal],
        candle_dict: dict[str, pd.DataFrame],
        sample_snapshot_interval_m1: int = 15,
    ) -> SimulationResult:
        canonical_mode = _canonicalize_mode(mode)
        arrays_by_sym = {
            sym: extract_m1_arrays(df)
            for sym, df in candle_dict.items()
            if not df.empty
        }
        sorted_signals = sorted(signals, key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

        if canonical_mode in ("ALL_SIGNALS", "ONE_TRADE_PER_COMBO"):
            return self._run_virtual_mode(canonical_mode, sorted_signals, arrays_by_sym)
        return self._run_portfolio_mode(
            sorted_signals, arrays_by_sym, sample_snapshot_interval_m1=sample_snapshot_interval_m1
        )

    def _find_entry_idx(
        self,
        sig: Signal,
        arrays: M1BarArrays,
    ) -> int | None:
        earliest_exec_ms = sig.signal_close_ms + 1
        entry_idx = int(np.searchsorted(arrays.t_ms, earliest_exec_ms))
        if 0 <= entry_idx < arrays.n_bars:
            return entry_idx
        return None

    def _run_virtual_mode(
        self,
        canonical_mode: Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO"],
        sorted_signals: list[Signal],
        arrays_by_sym: dict[str, M1BarArrays],
    ) -> SimulationResult:
        policy = (
            AllSignals()
            if canonical_mode == "ALL_SIGNALS"
            else OneTradePerCombo()
        )
        trades: list[TradeRecord] = []
        events: list[ExecutionEvent] = []
        decisions: list[SignalDecision] = []
        accepted_sigs: list[Signal] = []
        seq = 0

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

                exit_time_ms = int(arr.t_ms[outcome.exit_bar_idx])
                leg_margin = self.risk.calc_margin(sig.symbol, leg_spec.notional)
                cb = self.cost_model.evaluate_leg(
                    symbol=sig.symbol,
                    side=sig.side,
                    notional=leg_spec.notional,
                    entry_px_raw=raw_entry_px,
                    exit_px_trigger=outcome.exit_price,
                    exit_reason=outcome.exit_reason,
                    entry_time_ms=entry_time_ms,
                    exit_time_ms=exit_time_ms,
                    entry_px_fill_override=entry_price,
                )
                leg_qty = cb.qty
                entry_fee = cb.fee_entry
                exec_exit_px = cb.exit_px_fill
                exit_notional = leg_qty * exec_exit_px
                exit_fee = cb.fee_exit
                fee_total = cb.fee_total
                gross_pnl = cb.gross_pnl_fill
                net_pnl = cb.net_pnl
                return_pct = net_pnl / leg_spec.notional

                entry_dt = arr.index[entry_idx]
                exit_dt = arr.index[outcome.exit_bar_idx]
                provider = str(arr.providers[outcome.exit_bar_idx]) if len(arr.providers) else sig.provider

                seq += 1
                events.append(
                    _to_execution_event(
                        Event(
                            seq=seq,
                            event_id=f"EV_{seq:07d}_{leg_spec.leg_id}_OPEN",
                            timestamp_ms=entry_time_ms,
                            timestamp_dt=str(entry_dt),
                            trade_id=trade_id,
                            leg_id=leg_spec.leg_id,
                            symbol=sig.symbol,
                            combo_id=sig.combo_id,
                            combo_name=sig.combo_name,
                            timeframe=sig.timeframe,
                            event_type="ENTRY",
                            side=sig.side,
                            order_action="OPEN",
                            price=entry_price,
                            qty=leg_qty,
                            notional=leg_spec.notional,
                            fee=entry_fee,
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
                            timestamp_dt=str(exit_dt),
                            trade_id=trade_id,
                            leg_id=leg_spec.leg_id,
                            symbol=sig.symbol,
                            combo_id=sig.combo_id,
                            combo_name=sig.combo_name,
                            timeframe=sig.timeframe,
                            event_type="EXIT",
                            side=sig.side,
                            order_action="CLOSE",
                            price=exec_exit_px,
                            qty=leg_qty,
                            notional=exit_notional,
                            fee=exit_fee,
                            realized_pnl=net_pnl,
                            exit_reason=outcome.exit_reason,
                            signal_id=sig_id,
                        )
                    )
                )

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
                        entry_time_dt=entry_dt,
                        entry_price=entry_price,
                        exit_time_ms=int(arr.t_ms[outcome.exit_bar_idx]),
                        exit_time_dt=exit_dt,
                        exit_price=exec_exit_px,
                        exit_reason=outcome.exit_reason,
                        qty=leg_qty,
                        notional=leg_spec.notional,
                        margin=leg_margin,
                        gross_pnl=gross_pnl,
                        fee_total=fee_total,
                        net_pnl=net_pnl,
                        return_pct=return_pct,
                        intrabar_ambiguous=outcome.intrabar_ambiguous,
                        bars_held=outcome.bars_held,
                        provider=provider,
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
                        ambiguity_policy=outcome.ambiguity_policy,
                    )
                )

            if isinstance(policy, OneTradePerCombo):
                policy.record_open(sig.symbol, sig.combo_id, trade_id, max_exit_idx)

        trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
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
    ) -> SimulationResult:
        if not arrays_by_sym:
            return self._build_result(
                mode="BINGX_PORTFOLIO",
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
        portfolio_policy = Portfolio(account)

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

        for bar_idx in range(n_bars):
            ts = int(t_ms_array[bar_idx])

            for sig in signals_by_bar.get(bar_idx, []):
                sig_id = _resolve_signal_id(sig)
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
                                return_pct=(
                                    net_pnl / pending.leg_spec.notional
                                    if pending.leg_spec.notional > 0
                                    else 0.0
                                ),
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
                                ambiguity_policy=outcome.ambiguity_policy,
                            )
                        )

            account.mark_to_market(
                bar_idx=bar_idx,
                ts_ms=ts,
                ts_dt=arrays_by_sym[first_sym].index[bar_idx],
                n_bars=n_bars,
                sample_interval_m1=sample_snapshot_interval_m1,
            )

        closed_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
        m2m_summary = {
            "initial_equity": self.risk.initial_equity,
            "final_equity": round(account.final_equity, 4),
            "peak_equity": round(account.peak_equity, 4),
            "m2m_max_drawdown_dollars": round(account.max_m2m_dd_dollars, 4),
            "m2m_max_drawdown_pct": round(account.max_m2m_dd_pct, 2),
            "intrabar_tp_sl_ambiguous_count": intrabar_tp_sl_count,
            "intrabar_trailing_ambiguous_count": intrabar_trail_count,
        }
        return self._build_result(
            mode="BINGX_PORTFOLIO",
            trades=closed_trades,
            events=events,
            decisions=decisions,
            accepted_sigs=accepted_sigs,
            snapshots=account.snapshots_df(),
            extra_summary=m2m_summary,
        )

    def _build_result(
        self,
        *,
        mode: str,
        trades: list[TradeRecord],
        events: list[ExecutionEvent],
        decisions: list[SignalDecision],
        accepted_sigs: list[Signal],
        snapshots: pd.DataFrame,
        extra_summary: dict[str, Any],
    ) -> SimulationResult:
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

        tp_sl_amb = sum(1 for t in trades if t.intrabar_ambiguous)
        trail_amb = sum(1 for t in trades if getattr(t, "intrabar_trailing_ambiguous", False))
        summary = {
            "total_signals": len(decisions),
            "accepted_entries": len({t.trade_id for t in trades}),
            "total_legs": len(trades),
            "skipped_signals": len(skipped_signals),
            "rejected_signals": len(rejected_signals),
            "skipped_signals_count": len(skipped_signals),
            "rejected_signals_count": len(rejected_signals),
            "cost_profile": self.cost_model.profile.name,
            "cost_profile_hash": self.cost_model.profile.profile_hash,
            "ambiguity_policy": self.ambiguity_policy,
            **extra_summary,
        }
        return SimulationResult(
            mode=mode,
            trades=trades,
            events=events,
            accepted_signals=accepted_sigs,
            skipped_signals=skipped_signals,
            rejected_signals=rejected_signals,
            snapshots=snapshots,
            ambiguity_stats={
                "intrabar_tp_sl_ambiguous_count": extra_summary.get(
                    "intrabar_tp_sl_ambiguous_count", tp_sl_amb
                ),
                "intrabar_trailing_ambiguous_count": extra_summary.get(
                    "intrabar_trailing_ambiguous_count", trail_amb
                ),
            },
            summary_dict=summary,
            signal_decisions=decisions,
        )
