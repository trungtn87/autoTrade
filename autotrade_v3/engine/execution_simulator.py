"""Execution simulator supporting Virtual Combo Trade and Exchange Hedge Merged modes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Literal
import numpy as np
import pandas as pd

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal


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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_time_dt"] = str(self.entry_time_dt)
        d["exit_time_dt"] = str(self.exit_time_dt)
        return d


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

    def get_execution_events(self) -> list[ExecutionEvent]:
        return list(self.execution_events)

    def simulate_virtual_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame,
    ) -> list[TradeRecord]:
        """Simulate trades in Virtual Combo Isolated mode where each combo manages its own trade."""
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

            # Concurrency check: if combo is already in an open trade, skip
            if combo_free_bar.get(sig.combo_id, -1) > entry_idx:
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
        """Simulate single virtual trade, returning 1 leg for NEW6 or 2 legs for FINAL14 50/50 split."""
        notional = self.risk.notional_per_trade

        # FIX P1-02: Executed at open of the entry M1 bar (plus slippage)
        raw_entry_px = float(opens[entry_idx]) if opens is not None else sig.entry_price
        entry_price = self.risk.apply_slippage(raw_entry_px, sig.side, is_entry=True)

        is_buy = sig.side == "BUY"
        tp_price = entry_price * (1.0 + sig.tp_pct) if is_buy else entry_price * (1.0 - sig.tp_pct)
        sl_price = entry_price * (1.0 - sig.sl_pct) if is_buy else entry_price * (1.0 + sig.sl_pct)

        # Check exit mode: NEW6 is strictly 100% hard TP/SL.
        # FINAL14 uses 50% TP + 50% Trailing if mode is 50TP_50TRAIL, else 100% hard TP/SL.
        use_trailing = (not sig.is_new6) and (self.final14_exit_mode == "50TP_50TRAIL")

        if not use_trailing:
            # 100% hard single leg
            leg = self._simulate_single_leg(
                trade_id=trade_id,
                leg_id=f"{trade_id}_L1",
                leg_type="SINGLE",
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                leg_notional=notional,
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
            )
            return [leg]
        else:
            # FIX P1-03: Separate Leg 1 (50% TP) and Leg 2 (50% Trailing)
            half_notional = notional / 2.0
            leg1 = self._simulate_single_leg(
                trade_id=trade_id,
                leg_id=f"{trade_id}_L1",
                leg_type="TP_HALF",
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                leg_notional=half_notional,
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
            )

            leg2 = self._simulate_trailing_leg(
                trade_id=trade_id,
                leg_id=f"{trade_id}_L2",
                leg_type="TRAIL_HALF",
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                leg_notional=half_notional,
                is_buy=is_buy,
                sl_price=sl_price,
                opens=opens,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
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
    ) -> TradeRecord:
        """Simulate Trailing Stop leg (FIX P1-03) evaluated strictly across M1 bars."""
        leg_qty = leg_notional / entry_price
        leg_margin = self.risk.calc_margin(sig.symbol, leg_notional)
        entry_fee = self.risk.calc_fee(leg_notional, is_taker=True)

        act_price = (
            entry_price * (1.0 + self.trailing_act_pct)
            if is_buy
            else entry_price * (1.0 - self.trailing_act_pct)
        )

        trail_active = False
        trail_stop = sl_price
        peak_price = entry_price

        exit_bar_idx = n_bars - 1
        exit_price = 0.0
        exit_reason = ""
        ambiguous = False

        for i in range(entry_idx, n_bars):
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

            if hit_stop:
                exit_price = effective_stop
                exit_reason = "TRAIL" if trail_active else "SL"
                exit_bar_idx = i
                break

            # Update trailing stop for subsequent bars
            if is_buy:
                if not trail_active and h >= act_price:
                    trail_active = True
                    peak_price = h
                    trail_stop = max(sl_price, peak_price * (1.0 - self.trailing_cb_pct))
                elif trail_active and h > peak_price:
                    peak_price = h
                    trail_stop = max(trail_stop, peak_price * (1.0 - self.trailing_cb_pct))
            else:
                if not trail_active and l <= act_price:
                    trail_active = True
                    peak_price = l
                    trail_stop = min(sl_price, peak_price * (1.0 + self.trailing_cb_pct))
                elif trail_active and l < peak_price:
                    peak_price = l
                    trail_stop = min(trail_stop, peak_price * (1.0 + self.trailing_cb_pct))
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

    def simulate_portfolio_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame,
    ) -> tuple[list[TradeRecord], pd.DataFrame]:
        """Simulate Exchange Hedge Mode with BingX Merged Positions (FIX P1-04)."""
        virtual_trades = self.simulate_virtual_trades(signals, df_bars)
        return self.simulate_portfolio_from_trades(virtual_trades)
