"""Signal generator bridging exact 20FINAL logic to V3 execution engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Literal
import pandas as pd

from autotrade_v3.data_backtest.replay_20final import modules, replay_symbol


@dataclass(frozen=True)
class Signal:
    symbol: str
    combo_id: int
    combo_name: str
    timeframe: str
    side: Literal["BUY", "SELL"]
    signal_close_ms: int
    signal_close_dt: pd.Timestamp
    bar_position: int
    bar_open_ms: int
    entry_price: float
    tp_pct: float
    sl_pct: float
    tp_price: float
    sl_price: float
    approved: bool
    warmup_ready: bool
    smc_dir: int
    ob_blocked: bool
    provider: str
    is_new6: bool
    exit_policy: Any = None
    exit_policy_hash: str = ""
    signal_id: str = ""

    def __post_init__(self) -> None:
        if not self.signal_id:
            sym_clean = self.symbol.replace("-", "").replace("_", "")
            object.__setattr__(
                self,
                "signal_id",
                f"SIG_{sym_clean}_{self.combo_name}_{self.timeframe}_{self.side}_{self.signal_close_ms}",
            )


class SignalEngine:
    """Wraps 20FINAL signal extraction and attaches execution parameters."""

    def __init__(self):
        self._m = modules()
        self.final14_cases = self._m["FINAL14_CASES"]
        self.new6_cases = self._m["NEW6_CASES"]

    def get_combo_config(self, symbol: str, combo_id: int) -> dict:
        sym = "BTC-USDT" if "BTC" in symbol.upper() else "ETH-USDT"
        if combo_id in self.final14_cases.get(sym, {}):
            cfg = dict(self.final14_cases[sym][combo_id])
            cfg["is_new6"] = False
            return cfg
        if combo_id in self.new6_cases.get(sym, {}):
            cfg = dict(self.new6_cases[sym][combo_id])
            cfg["is_new6"] = True
            return cfg
        raise KeyError(f"Combo {combo_id} not found in 20FINAL config for {symbol}")

    def generate_signals(
        self,
        symbol: str,
        df_m15: pd.DataFrame,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Replay exact 20FINAL signals on M15 DataFrame and enrich with entry/TP/SL prices."""
        sym_canonical = "BTC-USDT" if "BTC" in symbol.upper() else "ETH-USDT"
        events, counts = replay_symbol(sym_canonical, df_m15, self._m)
        signals: list[Signal] = []

        for ev in events:
            if only_approved and not ev["approved"]:
                continue
            if only_warmup_ready and not ev["warmup_ready"]:
                continue

            bar_pos = ev["bar_position"]
            bar_open_ms = int(df_m15.index[bar_pos].value // 1_000_000)
            entry_px = float(df_m15.iloc[bar_pos]["close"])
            cfg = self.get_combo_config(symbol, ev["combo_id"])
            tp_pct = float(cfg["tp_pct"])
            sl_pct = float(cfg["sl_pct"])
            side = ev["side"]

            if side == "BUY":
                tp_px = entry_px * (1.0 + tp_pct)
                sl_px = entry_px * (1.0 - sl_pct)
            else:
                tp_px = entry_px * (1.0 - tp_pct)
                sl_px = entry_px * (1.0 + sl_pct)

            sig_dt = pd.to_datetime(ev["signal_close_ms"], unit="ms", utc=True)

            sig = Signal(
                symbol=symbol,
                combo_id=ev["combo_id"],
                combo_name=ev["combo"],
                timeframe=ev["timeframe"],
                side=side,
                signal_close_ms=ev["signal_close_ms"],
                signal_close_dt=sig_dt,
                bar_position=bar_pos,
                bar_open_ms=bar_open_ms,
                entry_price=entry_px,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                tp_price=tp_px,
                sl_price=sl_px,
                approved=ev["approved"],
                warmup_ready=ev["warmup_ready"],
                smc_dir=ev["smc_dir"],
                ob_blocked=ev["ob_blocked"],
                provider=ev["price_provider_at_close"],
                is_new6=cfg["is_new6"],
            )
            signals.append(sig)

        signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return signals
