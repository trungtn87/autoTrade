"""Execution simulator supporting Virtual Combo Trade and Exchange Hedge Merged modes."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Literal
import numpy as np
import pandas as pd

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy


@dataclass
class ExecutionEvent:
    event_id: str
    timestamp_ms: int
    timestamp_dt: str
    trade_id: str
    leg_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    event_type: Literal["ENTRY", "EXIT"]
    side: Literal["BUY", "SELL"]
    order_action: Literal["OPEN", "CLOSE"]
    price: float
    qty: float
    notional: float
    fee: float
    realized_pnl: float
    exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradeRecord:
    trade_id: str
    leg_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    leg_type: str  # "SINGLE", "TP_HALF", "TRAIL_HALF"
    entry_time_ms: int
    entry_time_dt: pd.Timestamp
    entry_price: float
    exit_time_ms: int
    exit_time_dt: pd.Timestamp
    exit_price: float
    exit_reason: str
    qty: float
    notional: float
    margin: float
    gross_pnl: float
    fee_total: float
    net_pnl: float
    return_pct: float
    intrabar_ambiguous: bool
    bars_held: int
    provider: str
    intrabar_trailing_ambiguous: bool = False
    exit_policy_hash: str = ""
    tp_pct: float = 0.0
    sl_pct: float = 0.0
    trailing_act_pct: float = 0.0
    trailing_cb_pct: float = 0.0
    max_bars_held: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_time_dt"] = str(self.entry_time_dt)
        d["exit_time_dt"] = str(self.exit_time_dt)
        return d


@dataclass
class SimulationResult:
    """Encapsulates execution output across research modes (A, B, C)."""
    mode: str
    trades: list[TradeRecord]
    events: list[ExecutionEvent]
    accepted_signals: list[Signal]
    skipped_signals: list[dict[str, Any]]
    rejected_signals: list[dict[str, Any]]
    snapshots: pd.DataFrame
    ambiguity_stats: dict[str, int]
    summary_dict: dict[str, Any] = field(default_factory=dict)

    @property
    def total_signals_count(self) -> int:
        return len(self.accepted_signals) + len(self.skipped_signals) + len(self.rejected_signals)

    @property
    def accepted_trades_count(self) -> int:
        return len({t.trade_id for t in self.trades})

    @property
    def total_legs_count(self) -> int:
        return len(self.trades)




@dataclass
class _PortfolioActiveLeg:
    leg_id: str
    trade_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    leg_type: str
    entry_time_ms: int
    entry_time_dt: pd.Timestamp
    entry_price: float
    qty: float
    notional: float
    margin: float
    entry_fee: float
    is_trailing: bool
    tp_price: float
    sl_price: float
    act_price: float = 0.0
    trailing_active: bool = False
    trail_stop: float = 0.0
    peak_price: float = 0.0
    provider: str = "BINANCE"
    trailing_cb_pct: float = 0.005
    max_bars_held: int | None = None
    force_close_on_window_end: bool = False
    exit_policy_hash: str = ""
    tp_pct: float = 0.0
    sl_pct: float = 0.0
    trailing_act_pct: float = 0.0


@dataclass
class _PortfolioMergedPosition:
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float = 0.0
    avg_price: float = 0.0
    used_margin: float = 0.0
    legs: list[_PortfolioActiveLeg] = field(default_factory=list)

class ExecutionSimulator:
    """Full lifecycle trade simulator with conservative intrabar ambiguity handling and M1 execution."""

    def __init__(
        self,
        risk_config: RiskConfig | None = None,
        final14_exit_mode: Literal["50TP_50TRAIL", "100_HARD"] = "50TP_50TRAIL",
        trailing_act_pct: float = 0.005,  # 0.5% favorable move to activate trailing
        trailing_cb_pct: float = 0.005,   # 0.5% callback
    ):
        self.risk = risk_config or RiskConfig()
        self.final14_exit_mode = final14_exit_mode
        self.trailing_act_pct = trailing_act_pct
        self.trailing_cb_pct = trailing_cb_pct
        self.execution_events: list[ExecutionEvent] = []
        self.last_skipped_signals: list[dict[str, Any]] = []
        self.last_rejected_signals: list[dict[str, Any]] = []

    def get_execution_events(self) -> list[ExecutionEvent]:
        return list(self.execution_events)

    def simulate_virtual_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame,
        allow_concurrency: bool = False,
        clear_skipped: bool = True,
    ) -> list[TradeRecord]:
        """Simulate trades in Virtual Combo Isolated mode where each combo manages its own trade.
        
        Args:
            signals: List of signals to simulate.
            df_bars: Continuous M1 bars.
            allow_concurrency: If True (MODE A: ALL_SIGNALS), every valid signal enters as an
                independent trade regardless of active combo positions. If False (MODE B:
                ONE_TRADE_PER_COMBO), overlapping signals are skipped with SKIPPED_BY_CONCURRENCY.
            clear_skipped: Whether to reset last_skipped_signals before simulating.
        """
        if clear_skipped:
            self.last_skipped_signals = []

        if not signals or df_bars.empty:
            return []

        # Map timestamps to index positions in df_bars
        t_ms_array = df_bars.index.asi8 // 1_000_000
        pos_map = {int(ts): i for i, ts in enumerate(t_ms_array)}
        opens = df_bars["open"].to_numpy(dtype=float) if "open" in df_bars else None
        highs = df_bars["high"].to_numpy(dtype=float)
        lows = df_bars["low"].to_numpy(dtype=float)
        closes = df_bars["close"].to_numpy(dtype=float)
        n_bars = len(df_bars)

        # Sort signals strictly by confirmation timestamp
        sorted_signals = sorted(signals, key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

        trades: list[TradeRecord] = []
        # Track when each combo becomes free (by bar index or timestamp_ms)
        combo_free_bar: dict[int, int] = {}
        trade_counter = 0

        for sig in sorted_signals:
            # FIX P1-02: Signal confirmed at signal_close_ms.
            # Earliest executable bar is open of next candle: timestamp >= signal_close_ms + 1
            earliest_exec_ms = sig.signal_close_ms + 1
            entry_idx = int(np.searchsorted(t_ms_array, earliest_exec_ms))

            if entry_idx >= n_bars:
                # Fallback for synthetic/legacy fixtures where signal_close_ms may match bar open
                if hasattr(sig, "bar_open_ms") and sig.bar_open_ms in pos_map:
                    entry_idx = pos_map[sig.bar_open_ms]
                elif sig.signal_close_ms in pos_map:
                    entry_idx = pos_map[sig.signal_close_ms]
                elif 0 <= getattr(sig, "bar_position", -1) < n_bars:
                    entry_idx = sig.bar_position
                else:
                    continue

            if entry_idx < 0 or entry_idx >= n_bars:
                continue

            # Concurrency check: if not allowing concurrency and combo already in trade, skip
            if not allow_concurrency and combo_free_bar.get(sig.combo_id, -1) > entry_idx:
                self.last_skipped_signals.append({
                    "signal_id": f"{sig.symbol}_{sig.combo_id}_{sig.signal_close_ms}",
                    "symbol": sig.symbol,
                    "combo_id": sig.combo_id,
                    "combo_name": sig.combo_name,
                    "timeframe": sig.timeframe,
                    "side": sig.side,
                    "signal_close_ms": sig.signal_close_ms,
                    "signal_close_dt": str(sig.signal_close_dt),
                    "reason": "SKIPPED_BY_CONCURRENCY",
                })
                continue

            trade_counter += 1
            trade_id = f"T_{sig.symbol.replace('-', '')}_{sig.combo_id}_{trade_counter:05d}"

            sig_trades = self._simulate_one_virtual_trade(
                trade_id=trade_id,
                sig=sig,
                entry_idx=entry_idx,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
            )

            if sig_trades:
                trades.extend(sig_trades)
                max_exit_idx = max(
                    pos_map.get(t.exit_time_ms, entry_idx + t.bars_held)
                    for t in sig_trades
                )
                combo_free_bar[sig.combo_id] = max_exit_idx

        return trades

    def resolve_exit_policy(self, sig: Signal) -> ExitPolicy:
        """Resolve and freeze ExitPolicy for the signal at entry time.
        
        Prioritizes sig.exit_policy provided by the strategy.
        Falls back to backward-compatible defaults only if sig.exit_policy is None.
        """
        if getattr(sig, "exit_policy", None) is not None:
            return sig.exit_policy

        use_trailing = (not sig.is_new6) and (self.final14_exit_mode == "50TP_50TRAIL")
        return ExitPolicy(
            tp_pct=sig.tp_pct,
            sl_pct=sig.sl_pct,
            split_legs=use_trailing,
            trailing_act_pct=self.trailing_act_pct,
            trailing_cb_pct=self.trailing_cb_pct,
            leg_weights=(0.5, 0.5) if use_trailing else (1.0,),
        )

    def _simulate_one_virtual_trade(
        self,
        trade_id: str,
        sig: Signal,
        entry_idx: int,
        opens: np.ndarray | None,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        n_bars: int,
        t_ms_array: np.ndarray,
        df_bars: pd.DataFrame,
    ) -> list[TradeRecord]:
        """Simulate single virtual trade using resolved ExitPolicy frozen at entry time."""
        notional = self.risk.notional_per_trade
        policy = self.resolve_exit_policy(sig)

        # FIX P1-02: Executed at open of the entry M1 bar (plus slippage)
        raw_entry_px = float(opens[entry_idx]) if opens is not None else sig.entry_price
        entry_price = self.risk.apply_slippage(raw_entry_px, sig.side, is_entry=True)

        is_buy = sig.side == "BUY"
        tp_price = entry_price * (1.0 + policy.tp_pct) if is_buy else entry_price * (1.0 - policy.tp_pct)
        sl_price = entry_price * (1.0 - policy.sl_pct) if is_buy else entry_price * (1.0 + policy.sl_pct)

        use_trailing = policy.split_legs

        if not use_trailing:
            w = policy.leg_weights[0] if policy.leg_weights else 1.0
            leg = self._simulate_single_leg(
                trade_id=trade_id,
                leg_id=f"{trade_id}_L1",
                leg_type="SINGLE",
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                leg_notional=notional * w,
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=sl_price,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
                policy=policy,
            )
            return [leg]
        else:
            w1 = policy.leg_weights[0] if len(policy.leg_weights) > 0 else 0.5
            w2 = policy.leg_weights[1] if len(policy.leg_weights) > 1 else 0.5
            leg1 = self._simulate_single_leg(
                trade_id=trade_id,
                leg_id=f"{trade_id}_L1",
                leg_type="TP_HALF",
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                leg_notional=notional * w1,
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=sl_price,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
                policy=policy,
            )

            leg2 = self._simulate_trailing_leg(
                trade_id=trade_id,
                leg_id=f"{trade_id}_L2",
                leg_type="TRAIL_HALF",
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                leg_notional=notional * w2,
                is_buy=is_buy,
                sl_price=sl_price,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
                policy=policy,
            )
            return [leg1, leg2]

    def _simulate_single_leg(
        self,
        trade_id: str,
        leg_id: str,
        leg_type: str,
        sig: Signal,
        entry_idx: int,
        entry_price: float,
        leg_notional: float,
        is_buy: bool,
        tp_price: float,
        sl_price: float,
        opens: np.ndarray | None,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        n_bars: int,
        t_ms_array: np.ndarray,
        df_bars: pd.DataFrame,
        policy: ExitPolicy | None = None,
    ) -> TradeRecord:
        """Simulate single fixed TP/SL leg with conservative intrabar ambiguity handling and price gap detection."""
        leg_qty = leg_notional / entry_price
        leg_margin = self.risk.calc_margin(sig.symbol, leg_notional)
        entry_fee = self.risk.calc_fee(leg_notional, is_taker=True)

        ambiguous = False
        exit_price = 0.0
        exit_reason = ""
        exit_bar_idx = n_bars - 1

        # Check subsequent bars starting from entry_idx
        # If entry happens at open of entry_idx, check intrabar high/low of entry_idx as well
        start_eval_bar = entry_idx

        for i in range(start_eval_bar, n_bars):
            # Check maximum holding period if configured in policy
            if policy is not None and policy.max_bars_held is not None and (i - entry_idx) >= policy.max_bars_held:
                exit_price = float(opens[i]) if opens is not None else float(closes[i])
                exit_reason = "MAX_BARS_HELD"
                exit_bar_idx = i
                break

            # Check gap at open for bars after entry
            if i > entry_idx and opens is not None:
                op = float(opens[i])
                if is_buy:
                    if op <= sl_price:
                        # Price gapped below SL
                        exit_price = op
                        exit_reason = "SL"
                        exit_bar_idx = i
                        break
                    elif op >= tp_price:
                        # Price gapped above TP
                        exit_price = op
                        exit_reason = "TP"
                        exit_bar_idx = i
                        break
                else:
                    if op >= sl_price:
                        exit_price = op
                        exit_reason = "SL"
                        exit_bar_idx = i
                        break
                    elif op <= tp_price:
                        exit_price = op
                        exit_reason = "TP"
                        exit_bar_idx = i
                        break

            h = float(highs[i])
            l = float(lows[i])

            if is_buy:
                hit_tp = h >= tp_price
                hit_sl = l <= sl_price
            else:
                hit_tp = l <= tp_price
                hit_sl = h >= sl_price

            if hit_tp and hit_sl:
                # FIX P1-02: Intrabar ambiguity: conservative rule prioritizes Stop-Loss first!
                ambiguous = True
                exit_price = sl_price
                exit_reason = "SL_INTRABAR_AMBIGUOUS"
                exit_bar_idx = i
                break
            elif hit_sl:
                exit_price = sl_price
                exit_reason = "SL"
                exit_bar_idx = i
                break
            elif hit_tp:
                exit_price = tp_price
                exit_reason = "TP"
                exit_bar_idx = i
                break
        else:
            # Reached end of data -> FORCE_CLOSE at last candle close
            exit_bar_idx = n_bars - 1
            exit_price = float(closes[exit_bar_idx])
            exit_reason = "FORCE_CLOSE"

        # Apply slippage on exit
        exec_exit_price = self.risk.apply_slippage(exit_price, sig.side, is_entry=False)
        exit_notional = leg_qty * exec_exit_price
        exit_fee = self.risk.calc_fee(exit_notional, is_taker=True)
        fee_total = entry_fee + exit_fee

        if is_buy:
            gross_pnl = (exec_exit_price - entry_price) * leg_qty
        else:
            gross_pnl = (entry_price - exec_exit_price) * leg_qty

        net_pnl = gross_pnl - fee_total
        return_pct = net_pnl / leg_notional

        entry_dt = df_bars.index[entry_idx]
        exit_dt = df_bars.index[exit_bar_idx]
        provider = str(df_bars.iloc[exit_bar_idx].get("provider", sig.provider))

        # Record execution events
        ev_open = ExecutionEvent(
            event_id=f"EV_{leg_id}_OPEN",
            timestamp_ms=int(t_ms_array[entry_idx]),
            timestamp_dt=str(entry_dt),
            trade_id=trade_id,
            leg_id=leg_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            event_type="ENTRY",
            side=sig.side,
            order_action="OPEN",
            price=entry_price,
            qty=leg_qty,
            notional=leg_notional,
            fee=entry_fee,
            realized_pnl=0.0,
            exit_reason="",
        )
        ev_close = ExecutionEvent(
            event_id=f"EV_{leg_id}_CLOSE",
            timestamp_ms=int(t_ms_array[exit_bar_idx]),
            timestamp_dt=str(exit_dt),
            trade_id=trade_id,
            leg_id=leg_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            event_type="EXIT",
            side=sig.side,
            order_action="CLOSE",
            price=exec_exit_price,
            qty=leg_qty,
            notional=exit_notional,
            fee=exit_fee,
            realized_pnl=net_pnl,
            exit_reason=exit_reason,
        )
        self.execution_events.extend([ev_open, ev_close])

        return TradeRecord(
            trade_id=trade_id,
            leg_id=leg_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            side=sig.side,
            leg_type=leg_type,
            entry_time_ms=int(t_ms_array[entry_idx]),
            entry_time_dt=entry_dt,
            entry_price=entry_price,
            exit_time_ms=int(t_ms_array[exit_bar_idx]),
            exit_time_dt=exit_dt,
            exit_price=exec_exit_price,
            exit_reason=exit_reason,
            qty=leg_qty,
            notional=leg_notional,
            margin=leg_margin,
            gross_pnl=gross_pnl,
            fee_total=fee_total,
            net_pnl=net_pnl,
            return_pct=return_pct,
            intrabar_ambiguous=ambiguous,
            bars_held=exit_bar_idx - entry_idx,
            provider=provider,
            intrabar_trailing_ambiguous=False,
            exit_policy_hash=policy.get_policy_hash() if policy else "",
            tp_pct=policy.tp_pct if policy else sig.tp_pct,
            sl_pct=policy.sl_pct if policy else sig.sl_pct,
            trailing_act_pct=policy.trailing_act_pct if policy else 0.0,
            trailing_cb_pct=policy.trailing_cb_pct if policy else 0.0,
            max_bars_held=policy.max_bars_held if policy else None,
        )

    def _simulate_trailing_leg(
        self,
        trade_id: str,
        leg_id: str,
        leg_type: str,
        sig: Signal,
        entry_idx: int,
        entry_price: float,
        leg_notional: float,
        is_buy: bool,
        sl_price: float,
        opens: np.ndarray | None,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        n_bars: int,
        t_ms_array: np.ndarray,
        df_bars: pd.DataFrame,
        policy: ExitPolicy | None = None,
    ) -> TradeRecord:
        """Simulate Trailing Stop leg (FIX P1-03) evaluated strictly across M1 bars using resolved ExitPolicy."""
        leg_qty = leg_notional / entry_price
        leg_margin = self.risk.calc_margin(sig.symbol, leg_notional)
        entry_fee = self.risk.calc_fee(leg_notional, is_taker=True)

        trailing_act_pct = policy.trailing_act_pct if policy is not None else self.trailing_act_pct
        trailing_cb_pct = policy.trailing_cb_pct if policy is not None else self.trailing_cb_pct

        act_price = (
            entry_price * (1.0 + trailing_act_pct)
            if is_buy
            else entry_price * (1.0 - trailing_act_pct)
        )

        trail_active = False
        trail_stop = sl_price
        peak_price = entry_price

        exit_bar_idx = n_bars - 1
        exit_price = 0.0
        exit_reason = ""
        ambiguous = False

        for i in range(entry_idx, n_bars):
            # Check maximum holding period if configured in policy
            if policy is not None and policy.max_bars_held is not None and (i - entry_idx) >= policy.max_bars_held:
                exit_price = float(opens[i]) if opens is not None else float(closes[i])
                exit_reason = "MAX_BARS_HELD"
                exit_bar_idx = i
                break

            effective_stop = trail_stop if trail_active else sl_price

            # Gap check on bar open
            if i > entry_idx and opens is not None:
                op = float(opens[i])
                if is_buy and op <= effective_stop:
                    exit_price = op
                    exit_reason = "TRAIL" if trail_active else "SL"
                    exit_bar_idx = i
                    break
                elif not is_buy and op >= effective_stop:
                    exit_price = op
                    exit_reason = "TRAIL" if trail_active else "SL"
                    exit_bar_idx = i
                    break

            h = float(highs[i])
            l = float(lows[i])

            if is_buy:
                hit_stop = l <= effective_stop
            else:
                hit_stop = h >= effective_stop

            trailing_ambiguous = False
            if is_buy:
                if not trail_active:
                    # Check if both SL and activation touched in the same M1 bar
                    if l <= sl_price and h >= act_price:
                        ambiguous = True
                        exit_price = sl_price
                        exit_reason = "SL_INTRABAR_AMBIGUOUS"
                        exit_bar_idx = i
                        break
                    elif h >= act_price:
                        trail_active = True
                        peak_price = h
                        trail_stop = max(sl_price, peak_price * (1.0 - trailing_cb_pct))
                        if l <= trail_stop:
                            trailing_ambiguous = True
                            exit_price = trail_stop
                            exit_reason = "TRAIL"
                            exit_bar_idx = i
                            break
                    elif l <= sl_price:
                        exit_price = sl_price
                        exit_reason = "SL"
                        exit_bar_idx = i
                        break
                else:
                    if l <= trail_stop:
                        exit_price = trail_stop
                        exit_reason = "TRAIL"
                        exit_bar_idx = i
                        break
                    elif h > peak_price:
                        peak_price = h
                        new_stop = max(trail_stop, peak_price * (1.0 - trailing_cb_pct))
                        trail_stop = new_stop
                        if l <= new_stop:
                            trailing_ambiguous = True
                            exit_price = new_stop
                            exit_reason = "TRAIL"
                            exit_bar_idx = i
                            break
            else:  # SELL
                if not trail_active:
                    if h >= sl_price and l <= act_price:
                        ambiguous = True
                        exit_price = sl_price
                        exit_reason = "SL_INTRABAR_AMBIGUOUS"
                        exit_bar_idx = i
                        break
                    elif l <= act_price:
                        trail_active = True
                        peak_price = l
                        trail_stop = min(sl_price, peak_price * (1.0 + trailing_cb_pct))
                        if h >= trail_stop:
                            trailing_ambiguous = True
                            exit_price = trail_stop
                            exit_reason = "TRAIL"
                            exit_bar_idx = i
                            break
                    elif h >= sl_price:
                        exit_price = sl_price
                        exit_reason = "SL"
                        exit_bar_idx = i
                        break
                else:
                    if h >= trail_stop:
                        exit_price = trail_stop
                        exit_reason = "TRAIL"
                        exit_bar_idx = i
                        break
                    elif l < peak_price:
                        peak_price = l
                        new_stop = min(trail_stop, peak_price * (1.0 + trailing_cb_pct))
                        trail_stop = new_stop
                        if h >= new_stop:
                            trailing_ambiguous = True
                            exit_price = new_stop
                            exit_reason = "TRAIL"
                            exit_bar_idx = i
                            break
        else:
            exit_bar_idx = n_bars - 1
            exit_price = float(closes[exit_bar_idx])
            exit_reason = "FORCE_CLOSE"

        # Apply slippage on exit
        exec_exit_price = self.risk.apply_slippage(exit_price, sig.side, is_entry=False)
        exit_notional = leg_qty * exec_exit_price
        exit_fee = self.risk.calc_fee(exit_notional, is_taker=True)
        fee_total = entry_fee + exit_fee

        if is_buy:
            gross_pnl = (exec_exit_price - entry_price) * leg_qty
        else:
            gross_pnl = (entry_price - exec_exit_price) * leg_qty

        net_pnl = gross_pnl - fee_total
        return_pct = net_pnl / leg_notional

        entry_dt = df_bars.index[entry_idx]
        exit_dt = df_bars.index[exit_bar_idx]
        provider = str(df_bars.iloc[exit_bar_idx].get("provider", sig.provider))

        # Record execution events
        ev_open = ExecutionEvent(
            event_id=f"EV_{leg_id}_OPEN",
            timestamp_ms=int(t_ms_array[entry_idx]),
            timestamp_dt=str(entry_dt),
            trade_id=trade_id,
            leg_id=leg_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            event_type="ENTRY",
            side=sig.side,
            order_action="OPEN",
            price=entry_price,
            qty=leg_qty,
            notional=leg_notional,
            fee=entry_fee,
            realized_pnl=0.0,
            exit_reason="",
        )
        ev_close = ExecutionEvent(
            event_id=f"EV_{leg_id}_CLOSE",
            timestamp_ms=int(t_ms_array[exit_bar_idx]),
            timestamp_dt=str(exit_dt),
            trade_id=trade_id,
            leg_id=leg_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            event_type="EXIT",
            side=sig.side,
            order_action="CLOSE",
            price=exec_exit_price,
            qty=leg_qty,
            notional=exit_notional,
            fee=exit_fee,
            realized_pnl=net_pnl,
            exit_reason=exit_reason,
        )
        self.execution_events.extend([ev_open, ev_close])

        return TradeRecord(
            trade_id=trade_id,
            leg_id=leg_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            side=sig.side,
            leg_type=leg_type,
            entry_time_ms=int(t_ms_array[entry_idx]),
            entry_time_dt=entry_dt,
            entry_price=entry_price,
            exit_time_ms=int(t_ms_array[exit_bar_idx]),
            exit_time_dt=exit_dt,
            exit_price=exec_exit_price,
            exit_reason=exit_reason,
            qty=leg_qty,
            notional=leg_notional,
            margin=leg_margin,
            gross_pnl=gross_pnl,
            fee_total=fee_total,
            net_pnl=net_pnl,
            return_pct=return_pct,
            intrabar_ambiguous=ambiguous,
            bars_held=exit_bar_idx - entry_idx,
            provider=provider,
            intrabar_trailing_ambiguous=trailing_ambiguous,
            exit_policy_hash=policy.get_policy_hash() if policy else "",
            tp_pct=policy.tp_pct if policy else sig.tp_pct,
            sl_pct=policy.sl_pct if policy else sig.sl_pct,
            trailing_act_pct=trailing_act_pct,
            trailing_cb_pct=trailing_cb_pct,
            max_bars_held=policy.max_bars_held if policy else None,
        )

    def simulate_portfolio_from_trades(
        self,
        virtual_trades: list[TradeRecord],
    ) -> tuple[list[TradeRecord], pd.DataFrame]:
        """Simulate Exchange Hedge Mode with BingX Merged Positions (FIX P1-04) from pre-calculated virtual trades.

        - Tracks volume-weighted average entry price per (symbol, side).
        - Correctly manages multiple combos contributing to one merged position.
        - Reduces position on individual combo exit without closing the entire position prematurely.
        - Strictly prevents negative quantities or overdrafting margin.
        - Calculates portfolio realized PnL and equity curve based on actual execution events.
        """
        if not virtual_trades:
            return [], pd.DataFrame()

        # Step 2: Build exchange merged timeline from execution events
        # Events: (timestamp_ms, 'ENTRY'/'EXIT', TradeRecord)
        events: list[tuple[int, str, TradeRecord]] = []
        for t in virtual_trades:
            events.append((t.entry_time_ms, "ENTRY", t))
            events.append((t.exit_time_ms, "EXIT", t))

        # Sort events chronologically (ENTRIES before EXITS at same millisecond)
        events.sort(key=lambda x: (x[0], 0 if x[1] == "ENTRY" else 1, x[2].trade_id))

        # Position tracking per (symbol, side)
        # Key: (symbol, side) -> {'qty': float, 'avg_price': float, 'margin': float, 'open_legs': int}
        positions: dict[tuple[str, str], dict[str, float]] = {}
        ledger: list[TradeRecord] = []
        portfolio_snapshots: list[dict[str, Any]] = []

        current_equity = self.risk.initial_equity
        peak_equity = current_equity

        for ts, ev_type, trade in events:
            key = (trade.symbol, trade.side)
            pos = positions.setdefault(
                key, {"qty": 0.0, "avg_price": 0.0, "margin": 0.0, "open_legs": 0}
            )

            if ev_type == "ENTRY":
                leg_qty = trade.qty
                tot_qty = pos["qty"] + leg_qty
                if tot_qty > 1e-9:
                    pos["avg_price"] = (
                        pos["avg_price"] * pos["qty"] + trade.entry_price * leg_qty
                    ) / tot_qty
                pos["qty"] = tot_qty
                pos["margin"] += trade.margin
                pos["open_legs"] += 1

            elif ev_type == "EXIT":
                closing_qty = min(trade.qty, pos["qty"]) if pos["qty"] > 0 else trade.qty
                effective_entry = pos["avg_price"] if pos["avg_price"] > 0 else trade.entry_price

                # Recompute realized PnL using the exchange merged entry price
                if trade.side == "BUY":
                    merged_gross = (trade.exit_price - effective_entry) * closing_qty
                else:
                    merged_gross = (effective_entry - trade.exit_price) * closing_qty

                merged_net = merged_gross - trade.fee_total
                current_equity += merged_net
                if current_equity > peak_equity:
                    peak_equity = current_equity

                # Update merged position safely
                pos["qty"] = max(0.0, pos["qty"] - closing_qty)
                pos["margin"] = max(0.0, pos["margin"] - trade.margin)
                pos["open_legs"] = max(0, pos["open_legs"] - 1)
                if pos["qty"] <= 1e-9 or pos["open_legs"] == 0:
                    pos["qty"] = 0.0
                    pos["avg_price"] = 0.0
                    pos["margin"] = 0.0

                # Create reconciled trade record
                reconciled = TradeRecord(
                    trade_id=f"PORT_{trade.trade_id}",
                    leg_id=f"PORT_{trade.leg_id}",
                    symbol=trade.symbol,
                    combo_id=trade.combo_id,
                    combo_name=trade.combo_name,
                    timeframe=trade.timeframe,
                    side=trade.side,
                    leg_type=trade.leg_type,
                    entry_time_ms=trade.entry_time_ms,
                    entry_time_dt=trade.entry_time_dt,
                    entry_price=effective_entry,
                    exit_time_ms=trade.exit_time_ms,
                    exit_time_dt=trade.exit_time_dt,
                    exit_price=trade.exit_price,
                    exit_reason=trade.exit_reason,
                    qty=closing_qty,
                    notional=trade.notional,
                    margin=trade.margin,
                    gross_pnl=merged_gross,
                    fee_total=trade.fee_total,
                    net_pnl=merged_net,
                    return_pct=merged_net / trade.notional if trade.notional > 0 else 0.0,
                    intrabar_ambiguous=trade.intrabar_ambiguous,
                    bars_held=trade.bars_held,
                    provider=trade.provider,
                )
                ledger.append(reconciled)

            total_used_margin = sum(p["margin"] for p in positions.values())
            free_margin = max(0.0, current_equity - total_used_margin)
            dd_dollars = peak_equity - current_equity
            dd_pct = (dd_dollars / peak_equity) * 100.0 if peak_equity > 0 else 0.0

            portfolio_snapshots.append(
                {
                    "timestamp_ms": ts,
                    "equity": round(current_equity, 4),
                    "used_margin": round(total_used_margin, 4),
                    "free_margin": round(free_margin, 4),
                    "drawdown_dollars": round(dd_dollars, 4),
                    "drawdown_pct": round(dd_pct, 2),
                    "symbol": trade.symbol,
                    "side": trade.side,
                    "net_qty": round(pos["qty"], 6),
                    "avg_entry_price": round(pos["avg_price"], 4),
                }
            )

        df_snapshots = pd.DataFrame(portfolio_snapshots)
        return ledger, df_snapshots

    def simulate_portfolio_independent(
        self,
        signals: list[Signal],
        candle_dict: dict[str, pd.DataFrame] | pd.DataFrame,
        sample_snapshot_interval_m1: int = 15,
    ) -> tuple[list[TradeRecord], pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
        """True independent portfolio event loop (FIX P1-04 & V3-05 Audit).

        - Runs directly from 20FINAL signals and M1 bar price action.
        - Does NOT depend on virtual mode exit outcomes.
        - Enforces strict account margin constraints (rejects signals if free margin < required margin).
        - Correctly manages merged hedge-mode positions per (symbol, side) using volume-weighted average price (VWAP).
        - Closes individual combo legs on fixed TP/SL and trailing stop triggers.
        - Recomputes realized PnL against current blended VWAP entry price at exit moment.
        - Continuous Mark-to-Market equity tracking and true intra-trade Max Drawdown.
        """
        if isinstance(candle_dict, pd.DataFrame):
            if not signals:
                return [], pd.DataFrame(), [], {}
            candle_dict = {signals[0].symbol: candle_dict}

        if not candle_dict:
            return [], pd.DataFrame(), [], {}

        first_sym = next(iter(candle_dict.keys()))
        t_ms_array = candle_dict[first_sym].index.asi8 // 1_000_000
        n_bars = len(t_ms_array)

        # Pre-extract numpy arrays for fast lookups
        bar_data = {}
        for sym, df in candle_dict.items():
            bar_data[sym] = {
                "open": df["open"].to_numpy(dtype=float) if "open" in df else df["close"].to_numpy(dtype=float),
                "high": df["high"].to_numpy(dtype=float),
                "low": df["low"].to_numpy(dtype=float),
                "close": df["close"].to_numpy(dtype=float),
                "dt": df.index,
                "provider": df["provider"].to_numpy() if "provider" in df else np.array(["BINANCE"] * len(df)),
            }

        # Map signals to earliest entry bar within this candle range
        signals_by_bar: dict[int, list[Signal]] = {}
        first_ts = int(t_ms_array[0])
        last_ts = int(t_ms_array[-1])
        for sig in sorted(signals, key=lambda s: (s.signal_close_ms, s.combo_id, s.side)):
            earliest_exec_ms = sig.signal_close_ms + 1
            if earliest_exec_ms < first_ts or earliest_exec_ms > last_ts:
                continue
            idx = int(np.searchsorted(t_ms_array, earliest_exec_ms))
            if 0 <= idx < n_bars:
                signals_by_bar.setdefault(idx, []).append(sig)

        # Account state
        balance = self.risk.initial_equity
        positions: dict[tuple[str, str], _PortfolioMergedPosition] = {}
        for sym in candle_dict:
            positions[(sym, "BUY")] = _PortfolioMergedPosition(symbol=sym, side="BUY")
            positions[(sym, "SELL")] = _PortfolioMergedPosition(symbol=sym, side="SELL")

        closed_trades: list[TradeRecord] = []
        rejected_signals: list[dict[str, Any]] = []
        skipped_signals: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        active_combos: dict[tuple[str, int], list[str]] = {}

        peak_equity = balance
        max_m2m_dd_dollars = 0.0
        max_m2m_dd_pct = 0.0

        trade_counter = 0
        intrabar_tp_sl_count = 0
        intrabar_trail_count = 0

        for bar_idx in range(n_bars):
            ts = int(t_ms_array[bar_idx])

            # 1. Process new signals scheduled for this bar
            new_sigs = signals_by_bar.get(bar_idx, [])
            for sig in new_sigs:
                combo_key = (sig.symbol, sig.combo_id)
                if active_combos.get(combo_key):
                    # Combo already in trade
                    skipped_signals.append({
                        "signal_id": f"{sig.symbol}_{sig.combo_id}_{sig.signal_close_ms}",
                        "symbol": sig.symbol,
                        "combo_id": sig.combo_id,
                        "combo_name": sig.combo_name,
                        "timeframe": sig.timeframe,
                        "side": sig.side,
                        "signal_close_ms": sig.signal_close_ms,
                        "signal_close_dt": str(sig.signal_close_dt),
                        "reason": "SKIPPED_BY_CONCURRENCY",
                    })
                    continue

                if sig.symbol not in bar_data:
                    continue

                notional = self.risk.notional_per_trade
                sym_bars = bar_data[sig.symbol]
                raw_open_px = float(sym_bars["open"][bar_idx])
                entry_px = self.risk.apply_slippage(raw_open_px, sig.side, is_entry=True)
                req_margin = self.risk.calc_margin(sig.symbol, notional)
                entry_fee = self.risk.calc_fee(notional, is_taker=True)

                # Real-time equity and free margin check
                unrealized_now = 0.0
                total_used_margin = 0.0
                for pos in positions.values():
                    if pos.qty > 1e-9:
                        total_used_margin += pos.used_margin
                        cur_px = float(bar_data[pos.symbol]["open"][bar_idx])
                        if pos.side == "BUY":
                            unrealized_now += (cur_px - pos.avg_price) * pos.qty
                        else:
                            unrealized_now += (pos.avg_price - cur_px) * pos.qty

                current_eq = balance + unrealized_now
                free_margin = current_eq - total_used_margin

                # Strict Margin Gate (FIX P1-04)
                if free_margin < req_margin:
                    rejected_signals.append({
                        "timestamp_ms": ts,
                        "timestamp_dt": str(sym_bars["dt"][bar_idx]),
                        "symbol": sig.symbol,
                        "combo_id": sig.combo_id,
                        "side": sig.side,
                        "reason": "INSUFFICIENT_MARGIN",
                        "free_margin": round(free_margin, 4),
                        "req_margin": round(req_margin, 4),
                    })
                    continue

                # Deduct fee from cash balance
                balance -= entry_fee
                pos = positions[(sig.symbol, sig.side)]
                leg_total_qty = notional / entry_px

                new_qty = pos.qty + leg_total_qty
                if new_qty > 1e-9:
                    pos.avg_price = (pos.avg_price * pos.qty + notional) / new_qty
                pos.qty = new_qty
                pos.used_margin += req_margin

                trade_counter += 1
                trade_id = f"PORT_{sig.symbol.replace('-', '')}_{sig.combo_id}_{trade_counter:05d}"

                is_buy = sig.side == "BUY"
                policy = self.resolve_exit_policy(sig)
                use_trailing = policy.split_legs

                sig_legs = []
                if not use_trailing:
                    w = policy.leg_weights[0] if policy.leg_weights else 1.0
                    leg_notional = notional * w
                    leg_qty = leg_notional / entry_px
                    leg_margin = req_margin * w
                    leg_fee = entry_fee * w
                    tp_px = entry_px * (1.0 + policy.tp_pct) if is_buy else entry_px * (1.0 - policy.tp_pct)
                    sl_px = entry_px * (1.0 - policy.sl_pct) if is_buy else entry_px * (1.0 + policy.sl_pct)
                    leg = _PortfolioActiveLeg(
                        leg_id=f"{trade_id}_L1", trade_id=trade_id, symbol=sig.symbol,
                        combo_id=sig.combo_id, combo_name=sig.combo_name, timeframe=sig.timeframe,
                        side=sig.side, leg_type="SINGLE", entry_time_ms=ts,
                        entry_time_dt=sym_bars["dt"][bar_idx], entry_price=entry_px,
                        qty=leg_qty, notional=leg_notional, margin=leg_margin,
                        entry_fee=leg_fee, is_trailing=False, tp_price=tp_px, sl_price=sl_px,
                        provider=str(sym_bars["provider"][bar_idx]),
                        trailing_cb_pct=policy.trailing_cb_pct,
                        max_bars_held=policy.max_bars_held,
                        force_close_on_window_end=policy.force_close_on_window_end,
                        exit_policy_hash=policy.get_policy_hash(),
                        tp_pct=policy.tp_pct,
                        sl_pct=policy.sl_pct,
                        trailing_act_pct=policy.trailing_act_pct,
                    )
                    sig_legs.append(leg)
                else:
                    w1 = policy.leg_weights[0] if len(policy.leg_weights) > 0 else 0.5
                    w2 = policy.leg_weights[1] if len(policy.leg_weights) > 1 else 0.5
                    l1_notional = notional * w1
                    l1_qty = l1_notional / entry_px
                    l1_margin = req_margin * w1
                    l1_fee = entry_fee * w1

                    l2_notional = notional * w2
                    l2_qty = l2_notional / entry_px
                    l2_margin = req_margin * w2
                    l2_fee = entry_fee * w2

                    # Leg 1: Fixed TP
                    tp_px = entry_px * (1.0 + policy.tp_pct) if is_buy else entry_px * (1.0 - policy.tp_pct)
                    sl_px = entry_px * (1.0 - policy.sl_pct) if is_buy else entry_px * (1.0 + policy.sl_pct)
                    l1 = _PortfolioActiveLeg(
                        leg_id=f"{trade_id}_L1", trade_id=trade_id, symbol=sig.symbol,
                        combo_id=sig.combo_id, combo_name=sig.combo_name, timeframe=sig.timeframe,
                        side=sig.side, leg_type="TP_HALF", entry_time_ms=ts,
                        entry_time_dt=sym_bars["dt"][bar_idx], entry_price=entry_px,
                        qty=l1_qty, notional=l1_notional, margin=l1_margin,
                        entry_fee=l1_fee, is_trailing=False, tp_price=tp_px, sl_price=sl_px,
                        provider=str(sym_bars["provider"][bar_idx]),
                        trailing_cb_pct=policy.trailing_cb_pct,
                        max_bars_held=policy.max_bars_held,
                        force_close_on_window_end=policy.force_close_on_window_end,
                        exit_policy_hash=policy.get_policy_hash(),
                        tp_pct=policy.tp_pct,
                        sl_pct=policy.sl_pct,
                        trailing_act_pct=policy.trailing_act_pct,
                    )
                    # Leg 2: Trailing Stop
                    act_px = entry_px * (1.0 + policy.trailing_act_pct) if is_buy else entry_px * (1.0 - policy.trailing_act_pct)
                    l2 = _PortfolioActiveLeg(
                        leg_id=f"{trade_id}_L2", trade_id=trade_id, symbol=sig.symbol,
                        combo_id=sig.combo_id, combo_name=sig.combo_name, timeframe=sig.timeframe,
                        side=sig.side, leg_type="TRAIL_HALF", entry_time_ms=ts,
                        entry_time_dt=sym_bars["dt"][bar_idx], entry_price=entry_px,
                        qty=l2_qty, notional=l2_notional, margin=l2_margin,
                        entry_fee=l2_fee, is_trailing=True, tp_price=0.0, sl_price=sl_px,
                        act_price=act_px, trailing_active=False, trail_stop=sl_px, peak_price=entry_px,
                        provider=str(sym_bars["provider"][bar_idx]),
                        trailing_cb_pct=policy.trailing_cb_pct,
                        max_bars_held=policy.max_bars_held,
                        force_close_on_window_end=policy.force_close_on_window_end,
                        exit_policy_hash=policy.get_policy_hash(),
                        tp_pct=policy.tp_pct,
                        sl_pct=policy.sl_pct,
                        trailing_act_pct=policy.trailing_act_pct,
                    )
                    sig_legs.extend([l1, l2])

                pos.legs.extend(sig_legs)
                active_combos.setdefault(combo_key, []).extend([l.leg_id for l in sig_legs])

            # 2. Check price action across all open positions
            for (sym, side), pos in positions.items():
                if not pos.legs:
                    continue

                sym_bars = bar_data[sym]
                op = float(sym_bars["open"][bar_idx])
                hi = float(sym_bars["high"][bar_idx])
                lo = float(sym_bars["low"][bar_idx])
                cl = float(sym_bars["close"][bar_idx])
                is_buy = side == "BUY"

                is_final_bar = (bar_idx == n_bars - 1)
                remaining_legs = []

                for leg in pos.legs:
                    exit_price = 0.0
                    exit_reason = ""
                    ambiguous = False
                    trailing_ambiguous = False

                    bars_held_so_far = bar_idx - int(np.searchsorted(t_ms_array, leg.entry_time_ms))

                    if is_final_bar:
                        exit_price = cl
                        exit_reason = "FORCE_CLOSE"
                    elif leg.max_bars_held is not None and bars_held_so_far >= leg.max_bars_held:
                        exit_price = op
                        exit_reason = "MAX_BARS_HELD"
                    elif not leg.is_trailing:
                        # Fixed TP/SL check
                        if is_buy and op <= leg.sl_price:
                            exit_price = op; exit_reason = "SL"
                        elif is_buy and op >= leg.tp_price:
                            exit_price = op; exit_reason = "TP"
                        elif not is_buy and op >= leg.sl_price:
                            exit_price = op; exit_reason = "SL"
                        elif not is_buy and op <= leg.tp_price:
                            exit_price = op; exit_reason = "TP"
                        else:
                            hit_tp = (hi >= leg.tp_price) if is_buy else (lo <= leg.tp_price)
                            hit_sl = (lo <= leg.sl_price) if is_buy else (hi >= leg.sl_price)
                            if hit_tp and hit_sl:
                                ambiguous = True
                                intrabar_tp_sl_count += 1
                                exit_price = leg.sl_price
                                exit_reason = "SL_INTRABAR_AMBIGUOUS"
                            elif hit_sl:
                                exit_price = leg.sl_price; exit_reason = "SL"
                            elif hit_tp:
                                exit_price = leg.tp_price; exit_reason = "TP"
                    else:
                        # Trailing Stop check
                        eff_stop = leg.trail_stop if leg.trailing_active else leg.sl_price
                        if is_buy and op <= eff_stop:
                            exit_price = op; exit_reason = "TRAIL" if leg.trailing_active else "SL"
                        elif not is_buy and op >= eff_stop:
                            exit_price = op; exit_reason = "TRAIL" if leg.trailing_active else "SL"
                        else:
                            if is_buy:
                                if not leg.trailing_active:
                                    if lo <= leg.sl_price and hi >= leg.act_price:
                                        ambiguous = True; intrabar_tp_sl_count += 1
                                        exit_price = leg.sl_price; exit_reason = "SL_INTRABAR_AMBIGUOUS"
                                    elif hi >= leg.act_price:
                                        leg.trailing_active = True
                                        leg.peak_price = hi
                                        leg.trail_stop = max(leg.sl_price, hi * (1.0 - leg.trailing_cb_pct))
                                        if lo <= leg.trail_stop:
                                            trailing_ambiguous = True; intrabar_trail_count += 1
                                            exit_price = leg.trail_stop; exit_reason = "TRAIL"
                                    elif lo <= leg.sl_price:
                                        exit_price = leg.sl_price; exit_reason = "SL"
                                else:
                                    if lo <= leg.trail_stop:
                                        exit_price = leg.trail_stop; exit_reason = "TRAIL"
                                    elif hi > leg.peak_price:
                                        leg.peak_price = hi
                                        new_stop = max(leg.trail_stop, hi * (1.0 - leg.trailing_cb_pct))
                                        leg.trail_stop = new_stop
                                        if lo <= new_stop:
                                            trailing_ambiguous = True; intrabar_trail_count += 1
                                            exit_price = new_stop; exit_reason = "TRAIL"
                            else:  # SELL
                                if not leg.trailing_active:
                                    if hi >= leg.sl_price and lo <= leg.act_price:
                                        ambiguous = True; intrabar_tp_sl_count += 1
                                        exit_price = leg.sl_price; exit_reason = "SL_INTRABAR_AMBIGUOUS"
                                    elif lo <= leg.act_price:
                                        leg.trailing_active = True
                                        leg.peak_price = lo
                                        leg.trail_stop = min(leg.sl_price, lo * (1.0 + leg.trailing_cb_pct))
                                        if hi >= leg.trail_stop:
                                            trailing_ambiguous = True; intrabar_trail_count += 1
                                            exit_price = leg.trail_stop; exit_reason = "TRAIL"
                                    elif hi >= leg.sl_price:
                                        exit_price = leg.sl_price; exit_reason = "SL"
                                else:
                                    if hi >= leg.trail_stop:
                                        exit_price = leg.trail_stop; exit_reason = "TRAIL"
                                    elif lo < leg.peak_price:
                                        leg.peak_price = lo
                                        new_stop = min(leg.trail_stop, lo * (1.0 + leg.trailing_cb_pct))
                                        leg.trail_stop = new_stop
                                        if hi >= new_stop:
                                            trailing_ambiguous = True; intrabar_trail_count += 1
                                            exit_price = new_stop; exit_reason = "TRAIL"

                    if exit_price > 0.0:
                        exec_exit_px = self.risk.apply_slippage(exit_price, leg.side, is_entry=False)
                        exit_fee = self.risk.calc_fee(leg.qty * exec_exit_px, is_taker=True)

                        # Compute realized PnL against the current blended VWAP of the merged position
                        effective_entry = pos.avg_price if pos.avg_price > 0 else leg.entry_price
                        if is_buy:
                            gross_pnl = (exec_exit_px - effective_entry) * leg.qty
                        else:
                            gross_pnl = (effective_entry - exec_exit_px) * leg.qty

                        total_fee = leg.entry_fee + exit_fee
                        net_pnl = gross_pnl - total_fee
                        balance += (gross_pnl - exit_fee)

                        # Update position
                        pos.qty = max(0.0, pos.qty - leg.qty)
                        pos.used_margin = max(0.0, pos.used_margin - leg.margin)
                        if pos.qty <= 1e-9:
                            pos.qty = 0.0; pos.avg_price = 0.0; pos.used_margin = 0.0

                        combo_key = (leg.symbol, leg.combo_id)
                        if combo_key in active_combos and leg.leg_id in active_combos[combo_key]:
                            active_combos[combo_key].remove(leg.leg_id)
                            if not active_combos[combo_key]:
                                del active_combos[combo_key]

                        rec = TradeRecord(
                            trade_id=leg.trade_id, leg_id=leg.leg_id, symbol=leg.symbol,
                            combo_id=leg.combo_id, combo_name=leg.combo_name, timeframe=leg.timeframe,
                            side=leg.side, leg_type=leg.leg_type, entry_time_ms=leg.entry_time_ms,
                            entry_time_dt=leg.entry_time_dt, entry_price=effective_entry,
                            exit_time_ms=ts, exit_time_dt=sym_bars["dt"][bar_idx],
                            exit_price=exec_exit_px, exit_reason=exit_reason,
                            qty=leg.qty, notional=leg.notional, margin=leg.margin,
                            gross_pnl=gross_pnl, fee_total=total_fee, net_pnl=net_pnl,
                            return_pct=net_pnl / leg.notional if leg.notional > 0 else 0.0,
                            intrabar_ambiguous=ambiguous,
                            bars_held=bar_idx - int(np.searchsorted(t_ms_array, leg.entry_time_ms)),
                            provider=leg.provider,
                            intrabar_trailing_ambiguous=trailing_ambiguous,
                            exit_policy_hash=leg.exit_policy_hash,
                            tp_pct=leg.tp_pct,
                            sl_pct=leg.sl_pct,
                            trailing_act_pct=leg.trailing_act_pct,
                            trailing_cb_pct=leg.trailing_cb_pct,
                            max_bars_held=leg.max_bars_held,
                        )
                        closed_trades.append(rec)
                    else:
                        remaining_legs.append(leg)

                pos.legs = remaining_legs

            # 3. Mark-to-Market Equity & Drawdown
            unrealized_total = 0.0
            used_margin_total = 0.0
            for pos in positions.values():
                if pos.qty > 1e-9:
                    used_margin_total += pos.used_margin
                    cl_px = float(bar_data[pos.symbol]["close"][bar_idx])
                    if pos.side == "BUY":
                        unrealized_total += (cl_px - pos.avg_price) * pos.qty
                    else:
                        unrealized_total += (pos.avg_price - cl_px) * pos.qty

            m2m_equity = balance + unrealized_total
            if m2m_equity > peak_equity:
                peak_equity = m2m_equity

            dd_dollars = peak_equity - m2m_equity
            dd_pct = (dd_dollars / peak_equity) * 100.0 if peak_equity > 0 else 0.0
            if dd_dollars > max_m2m_dd_dollars:
                max_m2m_dd_dollars = dd_dollars
            if dd_pct > max_m2m_dd_pct:
                max_m2m_dd_pct = dd_pct

            # Snapshot sampling: record when positions are open or at periodic interval
            has_open_pos = used_margin_total > 0.0
            if has_open_pos or (bar_idx % sample_snapshot_interval_m1 == 0) or (bar_idx == n_bars - 1):
                snapshots.append({
                    "timestamp_ms": ts,
                    "timestamp_dt": str(candle_dict[first_sym].index[bar_idx]),
                    "balance": round(balance, 4),
                    "unrealized_pnl": round(unrealized_total, 4),
                    "equity": round(m2m_equity, 4),
                    "used_margin": round(used_margin_total, 4),
                    "free_margin": round(max(0.0, m2m_equity - used_margin_total), 4),
                    "peak_equity": round(peak_equity, 4),
                    "drawdown_dollars": round(dd_dollars, 4),
                    "drawdown_pct": round(dd_pct, 2),
                })

        m2m_summary = {
            "initial_equity": self.risk.initial_equity,
            "final_equity": round(m2m_equity, 4),
            "peak_equity": round(peak_equity, 4),
            "m2m_max_drawdown_dollars": round(max_m2m_dd_dollars, 4),
            "m2m_max_drawdown_pct": round(max_m2m_dd_pct, 2),
            "rejected_signals_count": len(rejected_signals),
            "skipped_signals_count": len(skipped_signals),
            "intrabar_tp_sl_ambiguous_count": intrabar_tp_sl_count,
            "intrabar_trailing_ambiguous_count": intrabar_trail_count,
        }

        self.last_portfolio_summary = m2m_summary
        self.last_rejected_signals = rejected_signals
        self.last_skipped_signals = skipped_signals

        return closed_trades, pd.DataFrame(snapshots), rejected_signals, m2m_summary

    def simulate_portfolio_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame | dict[str, pd.DataFrame],
    ) -> tuple[list[TradeRecord], pd.DataFrame]:
        """Simulate Exchange Hedge Mode with BingX Merged Positions using true independent event loop."""
        closed_trades, df_snapshots, rejected, summary = self.simulate_portfolio_independent(signals, df_bars)
        return closed_trades, df_snapshots

    def simulate_research_mode(
        self,
        mode: Literal["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"] | str,
        signals: list[Signal],
        candle_dict: dict[str, pd.DataFrame],
    ) -> SimulationResult:
        """Run standardized simulation for one of the three research modes.

        MODE A (ALL_SIGNALS):
            - No concurrency skipping.
            - Every valid approved signal enters an independent trade.
            - No margin limitations applied.
        MODE B (ONE_TRADE_PER_COMBO):
            - At most one open trade per combo at any given time.
            - Overlapping signals for the same combo are skipped (SKIPPED_BY_CONCURRENCY).
            - No margin limitations applied.
        MODE C (BINGX_PORTFOLIO):
            - Hedge-mode merged positions by symbol and side with VWAP.
            - Strict margin enforcement (INSUFFICIENT_MARGIN).
            - Continuous M2M equity snapshots and drawdown.
        """
        mode_clean = mode.upper().strip()
        if mode_clean in ("A", "MODE_A", "ALL_SIGNALS"):
            canonical_mode = "ALL_SIGNALS"
        elif mode_clean in ("B", "MODE_B", "ONE_TRADE_PER_COMBO", "VIRTUAL_ISOLATED"):
            canonical_mode = "ONE_TRADE_PER_COMBO"
        elif mode_clean in ("C", "MODE_C", "BINGX_PORTFOLIO", "PORTFOLIO", "EXCHANGE_HEDGE_MERGED"):
            canonical_mode = "BINGX_PORTFOLIO"
        else:
            raise ValueError(f"Unknown research simulation mode: {mode}. Expected ALL_SIGNALS, ONE_TRADE_PER_COMBO, or BINGX_PORTFOLIO")

        self.execution_events = []

        if canonical_mode in ("ALL_SIGNALS", "ONE_TRADE_PER_COMBO"):
            allow_concurrency = (canonical_mode == "ALL_SIGNALS")
            all_trades: list[TradeRecord] = []
            all_skipped: list[dict[str, Any]] = []

            for sym, df_sym in candle_dict.items():
                sym_sigs = [s for s in signals if s.symbol == sym]
                trades = self.simulate_virtual_trades(
                    signals=sym_sigs,
                    df_bars=df_sym,
                    allow_concurrency=allow_concurrency,
                    clear_skipped=True,
                )
                all_trades.extend(trades)
                all_skipped.extend(self.last_skipped_signals)

            all_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
            events = self.get_execution_events()

            # Ambiguity stats
            tp_sl_amb = sum(1 for t in all_trades if t.intrabar_ambiguous)
            trail_amb = sum(1 for t in all_trades if getattr(t, "intrabar_trailing_ambiguous", False))

            accepted_trade_ids = {t.trade_id for t in all_trades}
            # For virtual modes, accepted signals map to distinct trades
            accepted_sigs = [s for s in signals if not any(
                sk["symbol"] == s.symbol and sk["combo_id"] == s.combo_id and sk["signal_close_ms"] == s.signal_close_ms
                for sk in all_skipped
            )]

            return SimulationResult(
                mode=canonical_mode,
                trades=all_trades,
                events=events,
                accepted_signals=accepted_sigs,
                skipped_signals=all_skipped,
                rejected_signals=[],
                snapshots=pd.DataFrame(),
                ambiguity_stats={
                    "intrabar_tp_sl_ambiguous_count": tp_sl_amb,
                    "intrabar_trailing_ambiguous_count": trail_amb,
                },
                summary_dict={
                    "total_signals": len(signals),
                    "accepted_entries": len({t.trade_id for t in all_trades}),
                    "total_legs": len(all_trades),
                    "skipped_signals": len(all_skipped),
                    "rejected_signals": 0,
                },
            )

        else:
            # MODE C: BINGX_PORTFOLIO
            closed_trades, df_snaps, rejected_sigs, m2m_summary = self.simulate_portfolio_independent(
                signals=signals,
                candle_dict=candle_dict,
            )
            closed_trades.sort(key=lambda t: (t.exit_time_ms, t.trade_id, t.leg_id))
            events = self.get_execution_events()

            skipped_sigs = list(self.last_skipped_signals)
            accepted_sigs = [
                s for s in signals
                if not any(sk["symbol"] == s.symbol and sk["combo_id"] == s.combo_id and sk["signal_close_ms"] == s.signal_close_ms for sk in skipped_sigs)
                and not any(rj["symbol"] == s.symbol and rj["combo_id"] == s.combo_id and rj["timestamp_ms"] == s.signal_close_ms + 1 for rj in rejected_sigs)
            ]

            return SimulationResult(
                mode="BINGX_PORTFOLIO",
                trades=closed_trades,
                events=events,
                accepted_signals=accepted_sigs,
                skipped_signals=skipped_sigs,
                rejected_signals=rejected_sigs,
                snapshots=df_snaps,
                ambiguity_stats={
                    "intrabar_tp_sl_ambiguous_count": m2m_summary.get("intrabar_tp_sl_ambiguous_count", 0),
                    "intrabar_trailing_ambiguous_count": m2m_summary.get("intrabar_trailing_ambiguous_count", 0),
                },
                summary_dict={
                    "total_signals": len(signals),
                    "accepted_entries": len({t.trade_id for t in closed_trades}),
                    "total_legs": len(closed_trades),
                    "skipped_signals": len(skipped_sigs),
                    "rejected_signals": len(rejected_sigs),
                    **m2m_summary,
                },
            )

