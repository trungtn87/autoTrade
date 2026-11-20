"""L2 strategy registry: which combo-symbols the live engine may trade, and how.

Single registry: ``V3_LIVE_23_LAB_10_V1`` (default and only value of ``V3_STRATEGY_REGISTRY``).
The 23 positive combo-symbols of lab ``V3_LAB_10@10.0.0`` with Fixed TP/SL. Signals come from the flat lab
module ``autotrade_v3.live.l2_lab10_flat`` through ``l2_lab5.Lab5Engine``.

Exit policy is the live FIXED_TP_SL for every new entry (one TP + one SL on the real fill). The registry never
closes, reverses or reopens a position: it only decides which NEW signals L2 emits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REGISTRY_LAB10_23 = "V3_LIVE_23_LAB_10_V1"
DEFAULT_REGISTRY = REGISTRY_LAB10_23
KNOWN_REGISTRIES = (REGISTRY_LAB10_23,)

_DIR = Path(__file__).resolve().parent / "registries"


def canonical_sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def case_name(combo: int) -> str:
    """Live token for a combo id: C1..C10, TIER (C11 in the lab), C11..C16 (NEW6 ids 101..106)."""
    combo = int(combo)
    if 101 <= combo <= 106:
        return f"C{combo - 90}"
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
                "signal_variant": self.signal_variant}

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
    """23 combo-symbols of V3_LAB_10@10.0.0 (file ``V3_LIVE_23_LAB_10_V1.json``)."""
    raw = json.loads((_DIR / f"{REGISTRY_LAB10_23}.json").read_text(encoding="utf-8"))
    if raw["registry_version"] != REGISTRY_LAB10_23:
        raise ValueError(f"registry file declares {raw['registry_version']!r}")
    combos = tuple(
        ComboSpec(symbol=c["symbol"], combo_id=int(c["combo_id"]), native_timeframe=c["timeframe"],
                  is_new6=False, entry_variant=str(c["execution_version"]), entry={}, layer2=str(c["layer2"]),
                  tp_pct=float(c["tp_pct"]), sl_pct=float(c["sl_pct"]), signal_variant="LAB_10")
        for c in raw["combos"])
    if len(combos) != 23 or len({(c.symbol, c.combo_id) for c in combos}) != 23:
        raise ValueError(f"{REGISTRY_LAB10_23} phải có đúng 23 combo-symbol duy nhất")
    return StrategyRegistry(version=f"{REGISTRY_LAB10_23}@1.0.0", combos=combos, new6_signal_mode="virtual_position",
                            provenance={"source": "autotrade_v3.live.l2_lab10_flat (V3_LAB_10@10.0.0)",
                                        "parent_source": dict(raw["parent_source"])})


_CACHE: dict[str, StrategyRegistry] = {}


def load_registry(version: str | None = None) -> StrategyRegistry:
    v = (version or DEFAULT_REGISTRY).strip().upper()
    if v not in KNOWN_REGISTRIES:
        raise ValueError(f"V3_STRATEGY_REGISTRY không hợp lệ: {version!r} (chỉ nhận {', '.join(KNOWN_REGISTRIES)})")
    if v not in _CACHE:
        _CACHE[v] = _load_lab10_23()
    return _CACHE[v]


def case_for(symbol: str, combo_id: int, registry: StrategyRegistry | None = None) -> dict | None:
    """TP/SL config for L3 from the active registry only (None if the combo is not in it)."""
    if registry is None:
        return None
    spec = registry.get(symbol, combo_id)
    return spec.as_case() if spec is not None else None
