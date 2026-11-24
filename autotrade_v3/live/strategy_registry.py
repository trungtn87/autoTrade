"""L2 strategy registry: which combo-symbols the live engine may trade, and how.

Supported registries:
- ``V3_LIVE_44_PLUS44_V1`` (default ``V3_STRATEGY_REGISTRY``): 23 PRUNE23 combos
  (``l2_lab10``) + 21 TV21 HTF combos (``l2_tv21``) = 44 combos with Fixed TP/SL.
- ``V3_LIVE_23_LAB_11_V1`` (rollback): the 23 PRUNE23 combos of
  ``V3_LAB_11_PRUNE23@11.0.0`` with Fixed TP/SL.

Exit policy is the live FIXED_TP_SL for every new entry (one TP + one SL on the real fill). The registry never
closes, reverses or reopens a position: it only decides which NEW signals L2 emits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REGISTRY_LAB11_23 = "V3_LIVE_23_LAB_11_V1"
REGISTRY_PLUS44_44 = "V3_LIVE_44_PLUS44_V1"
DEFAULT_REGISTRY = REGISTRY_PLUS44_44
KNOWN_REGISTRIES = (REGISTRY_PLUS44_44, REGISTRY_LAB11_23)
DEFAULT_WINDOW_BARS: dict[str, int] = {
    "BTC-USDT": 3_400,
    "ETH-USDT": 12_000,
}
MAX_WINDOW_BARS: dict[str, int] = {
    "BTC-USDT": 13_440,
    "ETH-USDT": 25_920,
}

_DIR = Path(__file__).resolve().parent / "registries"


def default_window_bars_for(symbol: str) -> int:
    return DEFAULT_WINDOW_BARS.get(str(symbol).upper(), 3_400)


def max_window_bars_for(symbol: str) -> int:
    return MAX_WINDOW_BARS.get(str(symbol).upper(), 13_440)


def canonical_sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def case_name(combo: int) -> str:
    """Live token for a combo id: C1..C10, TIER (11), C11..C16 (101..106), TV01..TV99 (31001..31099)."""
    combo = int(combo)
    if 101 <= combo <= 106:
        return f"C{combo - 90}"
    if 31001 <= combo <= 31099:
        return f"TV{combo - 31000:02d}"
    return "TIER" if combo == 11 else f"C{combo}"


@dataclass(frozen=True)
class ComboSpec:
    symbol: str
    combo_id: int
    native_timeframe: str
    is_new6: bool
    entry_variant: str
    entry: dict
    layer2: str
    tp_pct: float
    sl_pct: float
    signal_variant: str = "BASELINE"
    window_bars: int | None = None

    def __post_init__(self) -> None:
        if self.window_bars is not None:
            w = int(self.window_bars)
            max_w = max_window_bars_for(self.symbol)
            if w <= 0 or w > max_w:
                raise ValueError(
                    f"{self.symbol}/{self.combo_id}: window_bars={w} phải trong khoảng 1..{max_w}"
                )

    @property
    def effective_window_bars(self) -> int:
        return int(self.window_bars) if self.window_bars is not None else default_window_bars_for(self.symbol)

    @property
    def token(self) -> str:
        """Live name used in event_id / Discord / DB (C1..C10, TIER, C11..C16)."""
        return case_name(self.combo_id)

    @property
    def key(self) -> str:
        return f"{self.symbol}/{self.combo_id}"

    def signal_spec(self) -> dict:
        return {"symbol": self.symbol, "combo_id": self.combo_id, "native_timeframe": self.native_timeframe,
                "is_new6": self.is_new6, "entry_variant": self.entry_variant, "entry": self.entry,
                "layer2": self.layer2, "tp_pct": self.tp_pct, "sl_pct": self.sl_pct,
                "signal_variant": self.signal_variant, "window_bars": self.effective_window_bars}

    @property
    def spec_hash(self) -> str:
        return canonical_sha256(self.signal_spec())

    def as_case(self) -> dict:
        """Per-combo entry/TP/SL config (what L3 needs for TP/SL)."""
        return {"entry_variant": self.entry_variant, "entry": dict(self.entry), "layer2": self.layer2,
                "tp_pct": self.tp_pct, "sl_pct": self.sl_pct}


@dataclass(frozen=True)
class StrategyRegistry:
    version: str
    combos: tuple[ComboSpec, ...]
    new6_signal_mode: str = "virtual_position"
    h1_both_1: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)

    def for_symbol(self, symbol: str) -> list[ComboSpec]:
        sym = symbol.upper()
        return [c for c in self.combos if c.symbol == sym]

    def get(self, symbol: str, combo_id: int) -> ComboSpec | None:
        sym = symbol.upper()
        for c in self.combos:
            if c.symbol == sym and c.combo_id == int(combo_id):
                return c
        return None

    def allowed(self) -> set[tuple[str, int]]:
        return {(c.symbol, c.combo_id) for c in self.combos}

    @property
    def spec_hash(self) -> str:
        return canonical_sha256({"version": self.version, "new6_signal_mode": self.new6_signal_mode,
                                 "h1_both_1": self.h1_both_1,
                                 "combos": sorted((c.signal_spec() for c in self.combos),
                                                  key=lambda x: (x["symbol"], x["combo_id"]))})

    def summary(self) -> dict:
        out: dict[str, list[str]] = {}
        for c in sorted(self.combos, key=lambda x: (x.symbol, x.combo_id)):
            out.setdefault(c.symbol, []).append(c.token + (f"[{c.signal_variant}]" if c.signal_variant != "BASELINE" else ""))
        return {"version": self.version, "count": len(self.combos), "spec_hash": self.spec_hash,
                "new6_signal_mode": self.new6_signal_mode, "exit_policy": "FIXED_TP_SL", "combos": out,
                "signal_variants": {c.key: c.signal_variant for c in self.combos if c.signal_variant != "BASELINE"}}


def _load_lab10_23() -> StrategyRegistry:
    """23 combo-symbols of V3_LAB_11_PRUNE23@11.0.0 (file ``V3_LIVE_23_LAB_11_V1.json``)."""
    raw = json.loads((_DIR / f"{REGISTRY_LAB11_23}.json").read_text(encoding="utf-8"))
    if raw["registry_version"] != REGISTRY_LAB11_23:
        raise ValueError(f"registry file declares {raw['registry_version']!r}")
    combos = tuple(
        ComboSpec(symbol=c["symbol"], combo_id=int(c["combo_id"]), native_timeframe=c["timeframe"],
                  is_new6=False, entry_variant=str(c["execution_version"]), entry={}, layer2=str(c["layer2"]),
                  tp_pct=float(c["tp_pct"]), sl_pct=float(c["sl_pct"]), signal_variant="LAB_10",
                  window_bars=(int(c["window_bars"]) if c.get("window_bars") is not None else None))
        for c in raw["combos"])
    if len(combos) != 23 or len({(c.symbol, c.combo_id) for c in combos}) != 23:
        raise ValueError(f"{REGISTRY_LAB11_23} phải có đúng 23 combo-symbol duy nhất")
    return StrategyRegistry(version=f"{REGISTRY_LAB11_23}@1.0.0", combos=combos, new6_signal_mode="virtual_position",
                            provenance={"source": "autotrade_v3.live.l2_lab10 (V3_LAB_11_PRUNE23@11.0.0)",
                                        "parent_source": dict(raw["parent_source"])})


def _load_plus44_44() -> StrategyRegistry:
    """44 combo-symbols of PLUS44 (23 PRUNE23 + 21 TV21, file ``V3_LIVE_44_PLUS44_V1.json``)."""
    from .l2_tv21 import TV21_CONFIG_MAP

    raw = json.loads((_DIR / f"{REGISTRY_PLUS44_44}.json").read_text(encoding="utf-8"))
    if raw["registry_version"] != REGISTRY_PLUS44_44:
        raise ValueError(f"registry file declares {raw['registry_version']!r}")
    specs: list[ComboSpec] = []
    for c in raw["combos"]:
        cid = int(c["combo_id"])
        token = case_name(cid)
        if 31001 <= cid <= 31099:
            cfg = TV21_CONFIG_MAP.get(token)
            if cfg is None or cfg.symbol != c["symbol"] or cfg.combo_id != cid or cfg.timeframe != c["timeframe"]:
                raise ValueError(f"{REGISTRY_PLUS44_44}: TV21 config mismatch for {c['symbol']}/{cid}")
            specs.append(
                ComboSpec(
                    symbol=c["symbol"],
                    combo_id=cid,
                    native_timeframe=c["timeframe"],
                    is_new6=False,
                    entry_variant=str(c["execution_version"]),
                    entry={
                        "strategy_id": cfg.strategy_id,
                        "internal_variant": cfg.internal_variant,
                        "wrapper_variant": cfg.wrapper_variant,
                        "warmup_bars": cfg.warmup_bars,
                    },
                    layer2=str(c["layer2"]),
                    tp_pct=float(c["tp_pct"]),
                    sl_pct=float(c["sl_pct"]),
                    signal_variant="TV21",
                    window_bars=(int(c["window_bars"]) if c.get("window_bars") is not None else None),
                )
            )
        else:
            specs.append(
                ComboSpec(
                    symbol=c["symbol"],
                    combo_id=cid,
                    native_timeframe=c["timeframe"],
                    is_new6=False,
                    entry_variant=str(c["execution_version"]),
                    entry={},
                    layer2=str(c["layer2"]),
                    tp_pct=float(c["tp_pct"]),
                    sl_pct=float(c["sl_pct"]),
                    signal_variant="LAB_10",
                    window_bars=(int(c["window_bars"]) if c.get("window_bars") is not None else None),
                )
            )
    combos = tuple(specs)
    if len(combos) != 44 or len({(c.symbol, c.combo_id) for c in combos}) != 44:
        raise ValueError(f"{REGISTRY_PLUS44_44} phải có đúng 44 combo-symbol duy nhất")
    return StrategyRegistry(
        version=f"{REGISTRY_PLUS44_44}@1.0.0",
        combos=combos,
        new6_signal_mode="virtual_position",
        provenance={
            "source": "autotrade_v3.live.l2_lab10 + autotrade_v3.live.l2_tv21 (PLUS44)",
            "parent_source": dict(raw["parent_source"]),
        },
    )


_CACHE: dict[str, StrategyRegistry] = {}


def load_registry(version: str | None = None) -> StrategyRegistry:
    v = (version or DEFAULT_REGISTRY).strip().upper()
    if v not in KNOWN_REGISTRIES:
        raise ValueError(f"V3_STRATEGY_REGISTRY không hợp lệ: {version!r} (chỉ nhận {', '.join(KNOWN_REGISTRIES)})")
    if v not in _CACHE:
        _CACHE[v] = _load_plus44_44() if v == REGISTRY_PLUS44_44 else _load_lab10_23()
    return _CACHE[v]


def case_for(symbol: str, combo_id: int, registry: StrategyRegistry | None = None) -> dict | None:
    """TP/SL config for L3 from the active registry only (None if the combo is not in it)."""
    if registry is None:
        return None
    spec = registry.get(symbol, combo_id)
    return spec.as_case() if spec is not None else None
