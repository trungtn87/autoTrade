"""Public Plugin API and contracts for AutoTrade V3 Strategy Core (V3-11).

Strictly independent: imports only Python standard library, numpy, and pandas.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Literal
import pandas as pd


@dataclass(frozen=True)
class ExitPolicy:
    """Exit policy defining TP, SL, and Trailing parameters for a combo."""

    tp_pct: float
    sl_pct: float
    split_legs: bool = True
    trailing_act_pct: float = 0.005
    trailing_cb_pct: float = 0.005
    leg_weights: tuple[float, ...] = (0.5, 0.5)
    max_bars_held: int | None = None
    force_close_on_window_end: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.leg_weights, tuple):
            object.__setattr__(self, "leg_weights", tuple(float(w) for w in self.leg_weights))

    def get_policy_hash(self) -> str:
        """Deterministic SHA256 of the ExitPolicy parameters."""
        data = asdict(self)
        raw = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["policy_hash"] = self.get_policy_hash()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExitPolicy:
        return cls(
            tp_pct=float(data["tp_pct"]),
            sl_pct=float(data["sl_pct"]),
            split_legs=bool(data.get("split_legs", True)),
            trailing_act_pct=float(data.get("trailing_act_pct", 0.005)),
            trailing_cb_pct=float(data.get("trailing_cb_pct", 0.005)),
            leg_weights=tuple(float(x) for x in data.get("leg_weights", (0.5, 0.5))),
            max_bars_held=int(data["max_bars_held"]) if data.get("max_bars_held") is not None else None,
            force_close_on_window_end=bool(data.get("force_close_on_window_end", False)),
        )


@dataclass(frozen=True)
class ComboSpec:
    """Full parameter specification for a single strategy combo."""

    combo_key: str
    symbol: str
    combo_id: int
    combo_name: str
    native_timeframe: str
    entry_variant: str
    entry: dict[str, Any]
    layer2: str
    exit_policy: ExitPolicy
    is_new6: bool = False

    def to_dict(self) -> dict[str, Any]:
        ep_dict = asdict(self.exit_policy)
        ep_dict["leg_weights"] = list(self.exit_policy.leg_weights)
        return {
            "combo_key": self.combo_key,
            "symbol": self.symbol,
            "combo_id": self.combo_id,
            "combo_name": self.combo_name,
            "native_timeframe": self.native_timeframe,
            "is_new6": self.is_new6,
            "entry_variant": self.entry_variant,
            "entry": dict(self.entry),
            "layer2": self.layer2,
            "exit_policy": ep_dict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComboSpec:
        cid = int(data["combo_id"])
        return cls(
            combo_key=str(data["combo_key"]),
            symbol=str(data["symbol"]),
            combo_id=cid,
            combo_name=str(data["combo_name"]),
            native_timeframe=str(data["native_timeframe"]),
            entry_variant=str(data["entry_variant"]),
            entry=dict(data.get("entry", {})),
            layer2=str(data["layer2"]),
            exit_policy=ExitPolicy.from_dict(data["exit_policy"]),
            is_new6=bool(data.get("is_new6", cid >= 101)),
        )


@dataclass(frozen=True)
class SignalEvent:
    """Canonical signal event emitted by Strategy.generate() and Strategy.on_bar_close().

    Contains no tp_price or sl_price; exit levels belong strictly to ExitPolicy/Simulator.
    """

    signal_id: str
    combo_key: str
    symbol: str
    side: Literal["BUY", "SELL"]
    native_open_ms: int
    signal_close_ms: int
    ref_price: float
    tags: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signal_id:
            sym_clean = self.symbol.replace("-", "").replace("_", "")
            cname = self.tags.get("combo_name") or self.combo_key.split("/")[-1]
            tf = self.tags.get("timeframe", "15m")
            object.__setattr__(
                self,
                "signal_id",
                f"SIG_{sym_clean}_{cname}_{tf}_{self.side}_{self.signal_close_ms}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "combo_key": self.combo_key,
            "symbol": self.symbol,
            "side": self.side,
            "native_open_ms": self.native_open_ms,
            "signal_close_ms": self.signal_close_ms,
            "ref_price": self.ref_price,
            "tags": dict(self.tags),
        }


@dataclass(frozen=True)
class StrategySpec:
    """Immutable specification of a versioned strategy and its three cryptographic hashes."""

    strategy_id: str
    version: str
    combos: tuple[ComboSpec, ...]
    timeframes: tuple[str, ...]
    warmup_bars: int
    signal_spec_hash: str
    exit_spec_hash: str
    spec_hash: str
    parent: str | None = None

    def get_combo(self, symbol: str, combo_id_or_key: int | str) -> ComboSpec:
        sym = symbol.upper()
        for c in self.combos:
            if isinstance(combo_id_or_key, int):
                if c.symbol.upper() == sym and c.combo_id == combo_id_or_key:
                    return c
            else:
                key_str = str(combo_id_or_key)
                if c.combo_key == key_str or (c.symbol.upper() == sym and c.combo_name == key_str):
                    return c
        raise KeyError(f"Combo {combo_id_or_key!r} for {symbol} not found in {self.strategy_id}@{self.version}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "parent": self.parent,
            "warmup_bars": self.warmup_bars,
            "timeframes": list(self.timeframes),
            "signal_spec_hash": self.signal_spec_hash,
            "exit_spec_hash": self.exit_spec_hash,
            "spec_hash": self.spec_hash,
            "combos": [c.to_dict() for c in self.combos],
        }


class Strategy(ABC):
    """Plugin interface for all strategies in AutoTrade V3 Strategy Core."""

    @abstractmethod
    def spec(self) -> StrategySpec:
        """Return the immutable StrategySpec (combos, timeframes, warmup_bars, hashes)."""

    @abstractmethod
    def generate(
        self,
        bars: dict[str, pd.DataFrame] | pd.DataFrame,
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[SignalEvent]:
        """Batch signal generation over closed M15 candles per symbol."""

    @abstractmethod
    def on_bar_close(
        self,
        state: Any,
        bar: Any,
    ) -> list[SignalEvent]:
        """Incremental bar-by-bar signal generation on each closed M15 candle."""
