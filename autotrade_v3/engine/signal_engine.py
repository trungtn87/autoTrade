"""Signal generator bridging independent strategy_core to V3 execution engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
import pandas as pd

from autotrade_v3.strategy_core.api import ComboSpec, SignalEvent, Strategy
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


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


def signal_from_event(
    ev: SignalEvent,
    combo: ComboSpec,
    exit_policy_obj: Any = None,
    exit_policy_hash: str = "",
) -> Signal:
    """Convert a pure Strategy Core `SignalEvent` + `ComboSpec` into the 23-column `Signal`."""
    entry_px = float(ev.ref_price)
    tp_pct = float(combo.exit_policy.tp_pct)
    sl_pct = float(combo.exit_policy.sl_pct)
    if ev.side == "BUY":
        tp_px = entry_px * (1.0 + tp_pct)
        sl_px = entry_px * (1.0 - sl_pct)
    else:
        tp_px = entry_px * (1.0 - tp_pct)
        sl_px = entry_px * (1.0 + sl_pct)

    sig_dt = pd.to_datetime(ev.signal_close_ms, unit="ms", utc=True)
    tags = ev.tags
    return Signal(
        symbol=ev.symbol,
        combo_id=int(combo.combo_id),
        combo_name=combo.combo_name,
        timeframe=combo.native_timeframe,
        side=ev.side,
        signal_close_ms=int(ev.signal_close_ms),
        signal_close_dt=sig_dt,
        bar_position=int(tags.get("bar_position", 0)),
        bar_open_ms=int(tags.get("bar_open_ms", ev.native_open_ms)),
        entry_price=entry_px,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=tp_px,
        sl_price=sl_px,
        approved=bool(tags.get("approved", True)),
        warmup_ready=bool(tags.get("warmup_ready", True)),
        smc_dir=int(tags.get("smc_dir", 0)),
        ob_blocked=bool(tags.get("ob_blocked", False)),
        provider=str(tags.get("provider", "UNKNOWN")),
        is_new6=bool(combo.is_new6),
        exit_policy=exit_policy_obj,
        exit_policy_hash=exit_policy_hash,
        signal_id=ev.signal_id,
    )


class SignalEngine:
    """Wraps independent Strategy Core signal generation and attaches execution parameters."""

    def __init__(self, core_strategy: Strategy | None = None):
        self._core = core_strategy or StrategyCoreRegistry.get("20FINAL", "1")
        self._refresh_cases()

    def _refresh_cases(self) -> None:
        spec = self._core.spec()
        self.final14_cases: dict[str, dict[int, dict[str, Any]]] = {"BTC-USDT": {}, "ETH-USDT": {}}
        self.new6_cases: dict[str, dict[int, dict[str, Any]]] = {"BTC-USDT": {}, "ETH-USDT": {}}
        self._combo_by_sym_id: dict[tuple[str, int], ComboSpec] = {}
        self._combo_by_key: dict[str, ComboSpec] = {}

        for c in spec.combos:
            self._combo_by_sym_id[(c.symbol, c.combo_id)] = c
            self._combo_by_key[c.combo_key] = c
            cfg = {
                "symbol": c.symbol,
                "combo": c.combo_id,
                "name": c.combo_name,
                "native": c.native_timeframe,
                "entry_variant": c.entry_variant,
                "entry": dict(c.entry),
                "layer2": c.layer2,
                "tp_pct": c.exit_policy.tp_pct,
                "sl_pct": c.exit_policy.sl_pct,
                "is_new6": c.is_new6,
                "exit_policy": c.exit_policy.to_dict(),
            }
            if c.is_new6:
                self.new6_cases.setdefault(c.symbol, {})[c.combo_id] = cfg
            else:
                self.final14_cases.setdefault(c.symbol, {})[c.combo_id] = cfg

    @property
    def core_strategy(self) -> Strategy:
        return self._core

    def get_combo_spec(self, symbol: str, combo_id: int) -> ComboSpec:
        sym = "BTC-USDT" if "BTC" in symbol.upper() else ("ETH-USDT" if "ETH" in symbol.upper() else symbol)
        key = (sym, int(combo_id))
        if key not in self._combo_by_sym_id:
            raise KeyError(f"Combo {combo_id} not found in strategy config for {symbol}")
        return self._combo_by_sym_id[key]

    def get_combo_config(self, symbol: str, combo_id: int) -> dict[str, Any]:
        sym = "BTC-USDT" if "BTC" in symbol.upper() else ("ETH-USDT" if "ETH" in symbol.upper() else symbol)
        if combo_id in self.final14_cases.get(sym, {}):
            return dict(self.final14_cases[sym][combo_id])
        if combo_id in self.new6_cases.get(sym, {}):
            return dict(self.new6_cases[sym][combo_id])
        raise KeyError(f"Combo {combo_id} not found in strategy config for {symbol}")

    def generate_signals(
        self,
        symbol: str,
        df_m15: pd.DataFrame,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[Signal]:
        """Generate signals via Strategy Core on M15 DataFrame and enrich with entry/TP/SL prices."""
        sym_canonical = "BTC-USDT" if "BTC" in symbol.upper() else ("ETH-USDT" if "ETH" in symbol.upper() else symbol)
        events = self._core.generate(
            {sym_canonical: df_m15},
            symbols=[sym_canonical],
            only_approved=only_approved,
            only_warmup_ready=only_warmup_ready,
        )
        signals: list[Signal] = []
        for ev in events:
            combo = self._combo_by_key[ev.combo_key]
            sig = signal_from_event(ev, combo)
            if symbol != sym_canonical:
                sig = Signal(
                    symbol=symbol,
                    combo_id=sig.combo_id,
                    combo_name=sig.combo_name,
                    timeframe=sig.timeframe,
                    side=sig.side,
                    signal_close_ms=sig.signal_close_ms,
                    signal_close_dt=sig.signal_close_dt,
                    bar_position=sig.bar_position,
                    bar_open_ms=sig.bar_open_ms,
                    entry_price=sig.entry_price,
                    tp_pct=sig.tp_pct,
                    sl_pct=sig.sl_pct,
                    tp_price=sig.tp_price,
                    sl_price=sig.sl_price,
                    approved=sig.approved,
                    warmup_ready=sig.warmup_ready,
                    smc_dir=sig.smc_dir,
                    ob_blocked=sig.ob_blocked,
                    provider=sig.provider,
                    is_new6=sig.is_new6,
                    exit_policy=sig.exit_policy,
                    exit_policy_hash=sig.exit_policy_hash,
                    signal_id=sig.signal_id,
                )
            signals.append(sig)

        signals.sort(key=lambda s: (s.signal_close_ms, s.combo_id, s.side))
        return signals
