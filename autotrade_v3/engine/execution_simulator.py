"""Execution simulator supporting Virtual Combo Trade and Exchange Hedge Merged modes."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Literal
import numpy as np
import pandas as pd

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    entry_time_ms: int
    entry_time_dt: pd.Timestamp
    entry_price: float
    exit_time_ms: int
    exit_time_dt: pd.Timestamp
    exit_price: float
    exit_reason: str
    notional: float
    margin: float
    gross_pnl: float
    fee_total: float
    net_pnl: float
    return_pct: float
    intrabar_ambiguous: bool
    bars_held: int
    provider: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time_dt"] = str(self.entry_time_dt)
        d["exit_time_dt"] = str(self.exit_time_dt)
        return d


class ExecutionSimulator:
    """Full lifecycle trade simulator with conservative intrabar ambiguity handling."""

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

    def simulate_virtual_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame,
    ) -> list[TradeRecord]:
        """Simulate trades in Virtual Combo Isolated mode where each combo manages its own trade."""
        if not signals:
            return []

        # Map timestamps to index positions in df_bars
        t_ms_array = df_bars.index.asi8 // 1_000_000
        pos_map = {int(ts): i for i, ts in enumerate(t_ms_array)}
        highs = df_bars["high"].to_numpy(dtype=float)
        lows = df_bars["low"].to_numpy(dtype=float)
        closes = df_bars["close"].to_numpy(dtype=float)
        n_bars = len(df_bars)

        # Sort signals strictly by execution timestamp
        sorted_signals = sorted(signals, key=lambda s: (s.signal_close_ms, s.combo_id, s.side))

        trades: list[TradeRecord] = []
        # Track when each combo becomes free: combo_id -> free_at_bar_index
        combo_free_bar: dict[int, int] = {}
        trade_counter = 0

        for sig in sorted_signals:
            # Map signal to candle bar index in df_bars
            entry_idx = -1
            if hasattr(sig, "bar_open_ms") and sig.bar_open_ms in pos_map:
                entry_idx = pos_map[sig.bar_open_ms]
            elif sig.signal_close_ms in pos_map:
                entry_idx = pos_map[sig.signal_close_ms]
            else:
                # Try 15m candle open
                approx_open = sig.signal_close_ms + 1 - 900_000
                if approx_open in pos_map:
                    entry_idx = pos_map[approx_open]
                elif 0 <= getattr(sig, "bar_position", -1) < n_bars:
                    entry_idx = sig.bar_position

            if entry_idx < 0:
                continue
            # If combo is already in an open trade, skip (no multiple simultaneous positions for same combo)
            if combo_free_bar.get(sig.combo_id, -1) > entry_idx:
                continue

            # Cannot simulate if entry is on the very last bar
            if entry_idx >= n_bars - 1:
                continue

            trade_counter += 1
            trade_id = f"T_{sig.symbol}_{sig.combo_id}_{trade_counter:05d}"
            rec = self._simulate_one_virtual_trade(
                trade_id=trade_id,
                sig=sig,
                entry_idx=entry_idx,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
            )
            if rec is not None:
                trades.append(rec)
                exit_idx = pos_map.get(rec.exit_time_ms, entry_idx + rec.bars_held)
                combo_free_bar[sig.combo_id] = exit_idx

        return trades

    def _simulate_one_virtual_trade(
        self,
        trade_id: str,
        sig: Signal,
        entry_idx: int,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        n_bars: int,
        t_ms_array: np.ndarray,
        df_bars: pd.DataFrame,
    ) -> TradeRecord | None:
        """Simulate single virtual trade lifecycle with conservative intrabar resolution."""
        notional = self.risk.notional_per_trade
        margin = self.risk.calc_margin(sig.symbol, notional)

        # Apply slippage on entry
        entry_price = self.risk.apply_slippage(sig.entry_price, sig.side, is_entry=True)
        qty = notional / entry_price
        entry_fee = self.risk.calc_fee(notional, is_taker=True)

        is_buy = sig.side == "BUY"
        tp_price = entry_price * (1.0 + sig.tp_pct) if is_buy else entry_price * (1.0 - sig.tp_pct)
        sl_price = entry_price * (1.0 - sig.sl_pct) if is_buy else entry_price * (1.0 + sig.sl_pct)

        # Check exit mode: NEW6 is strictly 100% hard TP/SL.
        # FINAL14 uses 50% TP + 50% Trailing if mode is 50TP_50TRAIL, else 100% hard TP/SL.
        use_trailing = (not sig.is_new6) and (self.final14_exit_mode == "50TP_50TRAIL")

        if not use_trailing:
            return self._run_100_hard_exit(
                trade_id=trade_id,
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                qty=qty,
                notional=notional,
                margin=margin,
                entry_fee=entry_fee,
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=sl_price,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
            )
        else:
            return self._run_50_50_hybrid_exit(
                trade_id=trade_id,
                sig=sig,
                entry_idx=entry_idx,
                entry_price=entry_price,
                qty=qty,
                notional=notional,
                margin=margin,
                entry_fee=entry_fee,
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=sl_price,
                highs=highs,
                lows=lows,
                closes=closes,
                n_bars=n_bars,
                t_ms_array=t_ms_array,
                df_bars=df_bars,
            )

    def _run_100_hard_exit(
        self,
        trade_id: str,
        sig: Signal,
        entry_idx: int,
        entry_price: float,
        qty: float,
        notional: float,
        margin: float,
        entry_fee: float,
        is_buy: bool,
        tp_price: float,
        sl_price: float,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        n_bars: int,
        t_ms_array: np.ndarray,
        df_bars: pd.DataFrame,
    ) -> TradeRecord:
        """100% Hard TP / 100% Hard SL with conservative intrabar ambiguity handling."""
        ambiguous = False
        exit_price = 0.0
        exit_reason = ""
        exit_bar_idx = n_bars - 1

        for i in range(entry_idx + 1, n_bars):
            h = highs[i]
            l = lows[i]

            if is_buy:
                hit_tp = h >= tp_price
                hit_sl = l <= sl_price
            else:
                hit_tp = l <= tp_price
                hit_sl = h >= sl_price

            if hit_tp and hit_sl:
                # Intrabar ambiguity: conservative rule prioritizes Stop-Loss first!
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
            # Reached end of data while position still open -> FORCE_CLOSE at last candle close
            exit_bar_idx = n_bars - 1
            exit_price = float(closes[exit_bar_idx])
            exit_reason = "FORCE_CLOSE"

        # Apply slippage on exit
        exec_exit_price = self.risk.apply_slippage(exit_price, sig.side, is_entry=False)
        exit_notional = qty * exec_exit_price
        exit_fee = self.risk.calc_fee(exit_notional, is_taker=True)
        fee_total = entry_fee + exit_fee

        if is_buy:
            gross_pnl = (exec_exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exec_exit_price) * qty

        net_pnl = gross_pnl - fee_total
        return_pct = net_pnl / notional

        entry_dt = df_bars.index[entry_idx]
        exit_dt = df_bars.index[exit_bar_idx]
        provider = str(df_bars.iloc[exit_bar_idx].get("provider", sig.provider))

        return TradeRecord(
            trade_id=trade_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            side=sig.side,
            entry_time_ms=int(t_ms_array[entry_idx]),
            entry_time_dt=entry_dt,
            entry_price=entry_price,
            exit_time_ms=int(t_ms_array[exit_bar_idx]),
            exit_time_dt=exit_dt,
            exit_price=exec_exit_price,
            exit_reason=exit_reason,
            notional=notional,
            margin=margin,
            gross_pnl=gross_pnl,
            fee_total=fee_total,
            net_pnl=net_pnl,
            return_pct=return_pct,
            intrabar_ambiguous=ambiguous,
            bars_held=exit_bar_idx - entry_idx,
            provider=provider,
        )

    def _run_50_50_hybrid_exit(
        self,
        trade_id: str,
        sig: Signal,
        entry_idx: int,
        entry_price: float,
        qty: float,
        notional: float,
        margin: float,
        entry_fee: float,
        is_buy: bool,
        tp_price: float,
        sl_price: float,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        n_bars: int,
        t_ms_array: np.ndarray,
        df_bars: pd.DataFrame,
    ) -> TradeRecord:
        """50% fixed TP/SL + 50% Trailing Stop exit simulation."""
        half_qty = qty / 2.0
        half_notional = notional / 2.0

        # Half 1: Fixed TP & SL
        h1_live = True
        h1_exit_px = 0.0
        h1_reason = ""
        h1_ambiguous = False

        # Half 2: Trailing Stop
        h2_live = True
        h2_exit_px = 0.0
        h2_reason = ""
        h2_ambiguous = False
        h2_active = False
        h2_trail_stop = sl_price
        h2_peak = entry_price

        # Trailing activation trigger
        act_price = entry_price * (1.0 + self.trailing_act_pct) if is_buy else entry_price * (1.0 - self.trailing_act_pct)

        exit_bar_idx = n_bars - 1

        for i in range(entry_idx + 1, n_bars):
            h = highs[i]
            l = lows[i]

            # --- Check Half 1 (Fixed TP/SL) ---
            if h1_live:
                if is_buy:
                    h1_hit_tp = h >= tp_price
                    h1_hit_sl = l <= sl_price
                else:
                    h1_hit_tp = l <= tp_price
                    h1_hit_sl = h >= sl_price

                if h1_hit_tp and h1_hit_sl:
                    h1_ambiguous = True
                    h1_exit_px = sl_price
                    h1_reason = "SL_INTRABAR_AMBIGUOUS"
                    h1_live = False
                elif h1_hit_sl:
                    h1_exit_px = sl_price
                    h1_reason = "SL"
                    h1_live = False
                elif h1_hit_tp:
                    h1_exit_px = tp_price
                    h1_reason = "TP"
                    h1_live = False

            # --- Check Half 2 (Trailing stop effective at start of bar) ---
            if h2_live:
                effective_h2_stop = h2_trail_stop if h2_active else sl_price
                if is_buy:
                    h2_hit_stop = l <= effective_h2_stop
                else:
                    h2_hit_stop = h >= effective_h2_stop

                if h2_hit_stop:
                    h2_exit_px = effective_h2_stop
                    h2_reason = "TRAIL" if h2_active else "SL"
                    h2_live = False

            if not h1_live and not h2_live:
                exit_bar_idx = i
                break

            # --- Update Half 2 trailing state for subsequent bars ---
            if h2_live:
                if is_buy:
                    if not h2_active and h >= act_price:
                        h2_active = True
                        h2_peak = h
                        h2_trail_stop = max(sl_price, h2_peak * (1.0 - self.trailing_cb_pct))
                    elif h2_active and h > h2_peak:
                        h2_peak = h
                        h2_trail_stop = max(h2_trail_stop, h2_peak * (1.0 - self.trailing_cb_pct))
                else:
                    if not h2_active and l <= act_price:
                        h2_active = True
                        h2_peak = l
                        h2_trail_stop = min(sl_price, h2_peak * (1.0 + self.trailing_cb_pct))
                    elif h2_active and l < h2_peak:
                        h2_peak = l
                        h2_trail_stop = min(h2_trail_stop, h2_peak * (1.0 + self.trailing_cb_pct))
        else:
            # Force close any remaining open half at end of data
            exit_bar_idx = n_bars - 1
            last_close = float(closes[exit_bar_idx])
            if h1_live:
                h1_exit_px = last_close
                h1_reason = "FORCE_CLOSE"
            if h2_live:
                h2_exit_px = last_close
                h2_reason = "FORCE_CLOSE"

        # Apply slippage on both half exits
        exec_h1_px = self.risk.apply_slippage(h1_exit_px, sig.side, is_entry=False)
        exec_h2_px = self.risk.apply_slippage(h2_exit_px, sig.side, is_entry=False)

        h1_notional = half_qty * exec_h1_px
        h2_notional = half_qty * exec_h2_px
        exit_fees = self.risk.calc_fee(h1_notional + h2_notional, is_taker=True)
        fee_total = entry_fee + exit_fees

        if is_buy:
            gross_pnl = (exec_h1_px - entry_price) * half_qty + (exec_h2_px - entry_price) * half_qty
        else:
            gross_pnl = (entry_price - exec_h1_px) * half_qty + (entry_price - exec_h2_px) * half_qty

        net_pnl = gross_pnl - fee_total
        return_pct = net_pnl / notional

        # Blended exit price
        blended_exit_px = (exec_h1_px + exec_h2_px) / 2.0
        combined_reason = f"{h1_reason}+{h2_reason}"

        entry_dt = df_bars.index[entry_idx]
        exit_dt = df_bars.index[exit_bar_idx]
        provider = str(df_bars.iloc[exit_bar_idx].get("provider", sig.provider))

        return TradeRecord(
            trade_id=trade_id,
            symbol=sig.symbol,
            combo_id=sig.combo_id,
            combo_name=sig.combo_name,
            timeframe=sig.timeframe,
            side=sig.side,
            entry_time_ms=int(t_ms_array[entry_idx]),
            entry_time_dt=entry_dt,
            entry_price=entry_price,
            exit_time_ms=int(t_ms_array[exit_bar_idx]),
            exit_time_dt=exit_dt,
            exit_price=blended_exit_px,
            exit_reason=combined_reason,
            notional=notional,
            margin=margin,
            gross_pnl=gross_pnl,
            fee_total=fee_total,
            net_pnl=net_pnl,
            return_pct=return_pct,
            intrabar_ambiguous=(h1_ambiguous or h2_ambiguous),
            bars_held=exit_bar_idx - entry_idx,
            provider=provider,
        )

    def simulate_portfolio_trades(
        self,
        signals: list[Signal],
        df_bars: pd.DataFrame,
    ) -> tuple[list[TradeRecord], pd.DataFrame]:
        """Simulate Exchange-Compatible Portfolio mode with BingX Hedge Mode merged positions.

        - Signals on same side (e.g. BUY) merge into a single position with weighted average entry price.
        - Returns individual trade ledger (reconciled against exchange execution) and portfolio time series.
        """
        # Step 1: Run virtual combo executions to determine trade exits
        virtual_trades = self.simulate_virtual_trades(signals, df_bars)
        if not virtual_trades:
            return [], pd.DataFrame()

        # Step 2: Build exchange merged timeline
        # Events: (timestamp, 'ENTRY'/'EXIT', trade)
        events = []
        for t in virtual_trades:
            events.append((t.entry_time_ms, "ENTRY", t))
            events.append((t.exit_time_ms, "EXIT", t))

        # Sort events chronologically (ENTRIES before EXITS at same timestamp)
        events.sort(key=lambda x: (x[0], 0 if x[1] == "ENTRY" else 1))

        # Position tracking per symbol and side
        # Key: (symbol, side) -> {'qty': float, 'avg_price': float, 'margin': float}
        positions: dict[tuple[str, str], dict] = {}
        ledger: list[TradeRecord] = []
        portfolio_snapshots = []

        current_equity = self.risk.initial_equity

        for ts, ev_type, trade in events:
            key = (trade.symbol, trade.side)
            pos = positions.setdefault(key, {"qty": 0.0, "avg_price": 0.0, "margin": 0.0})

            if ev_type == "ENTRY":
                new_qty = trade.notional / trade.entry_price
                tot_qty = pos["qty"] + new_qty
                if tot_qty > 0:
                    pos["avg_price"] = (pos["avg_price"] * pos["qty"] + trade.entry_price * new_qty) / tot_qty
                pos["qty"] = tot_qty
                pos["margin"] += trade.margin

            elif ev_type == "EXIT":
                closing_qty = trade.notional / trade.entry_price
                effective_entry = pos["avg_price"] if pos["avg_price"] > 0 else trade.entry_price

                # Recompute realized PnL using the exchange merged entry price
                if trade.side == "BUY":
                    merged_gross = (trade.exit_price - effective_entry) * closing_qty
                else:
                    merged_gross = (effective_entry - trade.exit_price) * closing_qty

                merged_net = merged_gross - trade.fee_total
                current_equity += merged_net

                # Update merged position
                pos["qty"] = max(0.0, pos["qty"] - closing_qty)
                pos["margin"] = max(0.0, pos["margin"] - trade.margin)
                if pos["qty"] <= 1e-9:
                    pos["avg_price"] = 0.0

                # Create reconciled trade record
                reconciled = TradeRecord(
                    trade_id=f"PORT_{trade.trade_id}",
                    symbol=trade.symbol,
                    combo_id=trade.combo_id,
                    combo_name=trade.combo_name,
                    timeframe=trade.timeframe,
                    side=trade.side,
                    entry_time_ms=trade.entry_time_ms,
                    entry_time_dt=trade.entry_time_dt,
                    entry_price=effective_entry,
                    exit_time_ms=trade.exit_time_ms,
                    exit_time_dt=trade.exit_time_dt,
                    exit_price=trade.exit_price,
                    exit_reason=trade.exit_reason,
                    notional=trade.notional,
                    margin=trade.margin,
                    gross_pnl=merged_gross,
                    fee_total=trade.fee_total,
                    net_pnl=merged_net,
                    return_pct=merged_net / trade.notional,
                    intrabar_ambiguous=trade.intrabar_ambiguous,
                    bars_held=trade.bars_held,
                    provider=trade.provider,
                )
                ledger.append(reconciled)

            portfolio_snapshots.append(
                {
                    "timestamp_ms": ts,
                    "equity": current_equity,
                    "symbol": trade.symbol,
                    "side": trade.side,
                    "net_qty": pos["qty"],
                    "avg_price": pos["avg_price"],
                }
            )

        df_snapshots = pd.DataFrame(portfolio_snapshots)
        return ledger, df_snapshots
