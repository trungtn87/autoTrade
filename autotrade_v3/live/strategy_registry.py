"""L2 strategy registry: which combo-symbols the live engine may trade, and how.

Immutable registry versions, chosen by ``V3_STRATEGY_REGISTRY``:

* ``V3_LIVE_20_POSITIVE_FIXED_V1`` (default): the 20 positive combo-symbols of
  Owner audit ``AUDIT_20_POSITIVE_FIXED_TPSL_20261116`` (Fixed TP/SL, lab
  ``v3_lab`` 5c471903) = the 18 below + BTC-USDT/C9 + ETH-USDT/C6.
* ``V3_LIVE_18_H1_V2``: the 18 combo-symbols of baseline
  ``V3_18_COMBO_H1_BASELINE_V1`` (Owner lab ``runs/v3_18_baseline_v2``), with
  ETH-USDT/C1 = H1_BOTH_1. Spec data is the frozen JSON next to this module.
* ``20FINAL``: the previous 20 combos, computed by the untouched V2 engine
  (``autotrade_v2.app.strategy``). Rollback = set the variable to ``20FINAL``
  and restart; no order, position or protection is touched by switching.

Exit policy is the live FIXED_TP_SL for every new entry (one TP + one SL on the
real fill) in both versions. The registry never closes, reverses or reopens a
position: it only decides which NEW signals L2 emits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autotrade_v2.app.strategy.final14_config import case_name, enabled_combos, enabled_new6_combos, get_case

REGISTRY_V20 = "V3_LIVE_20_POSITIVE_FIXED_V1"
REGISTRY_V18 = "V3_LIVE_18_H1_V2"
REGISTRY_20FINAL = "20FINAL"
REGISTRY_V3_LAB_2 = "V3_LIVE_V3_LAB_2_V1"
DEFAULT_REGISTRY = REGISTRY_V20
JSON_REGISTRIES = (REGISTRY_V20, REGISTRY_V18, REGISTRY_V3_LAB_2)
KNOWN_REGISTRIES = (REGISTRY_V20, REGISTRY_V18, REGISTRY_20FINAL, REGISTRY_V3_LAB_2)
_PROVENANCE_KEYS = ("baseline_id", "baseline_source", "baseline_registry_sha256", "baseline_signals_sha256",
                    "c1_eth_research_source_sha256", "audit_id", "audit_source", "audit_source_run",
                    "audit_repro_run", "lab_git_sha", "audit_sha256", "audit_signal_spec_hash",
                    "parent_registry", "added_vs_parent")

_DIR = Path(__file__).resolve().parent / "registries"


def canonical_sha256(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        """Same shape as ``final14_config.get_case`` (what L3 needs for TP/SL)."""
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


def _load_json(version: str) -> StrategyRegistry:
    raw = json.loads((_DIR / f"{version}.json").read_text(encoding="utf-8"))
    if raw["registry_version"] != version:
        raise ValueError(f"registry file {version}.json declares {raw['registry_version']!r}")
    combos = tuple(
        ComboSpec(symbol=c["symbol"], combo_id=int(c["combo_id"]), native_timeframe=c["native_timeframe"],
                  is_new6=bool(c["is_new6"]), entry_variant=c["entry_variant"], entry=dict(c["entry"]),
                  layer2=c["layer2"], tp_pct=float(c["tp_pct"]), sl_pct=float(c["sl_pct"]),
                  signal_variant=c.get("signal_variant", "BASELINE"))
        for c in raw["combos"])
    keys = [(c.symbol, c.combo_id) for c in combos]
    if len(set(keys)) != len(keys):
        raise ValueError(f"registry {version}: duplicate (symbol, combo_id)")
    prov = {k: raw[k] for k in _PROVENANCE_KEYS if k in raw}
    return StrategyRegistry(version=raw["registry_version"], combos=combos,
                            new6_signal_mode=raw["new6_signal_mode"], h1_both_1=dict(raw["h1_both_1"]),
                            provenance=prov)


def _load_20final() -> StrategyRegistry:
    from autotrade_v2.app.strategy.final14_research.layer3_long import NATIVE
    combos = []
    for sym in ("BTC-USDT", "ETH-USDT"):
        for cid in enabled_combos(sym):
            cfg = get_case(sym, cid) or {}
            combos.append(ComboSpec(symbol=sym, combo_id=cid, native_timeframe=NATIVE[case_name(cid)],
                                    is_new6=False, entry_variant=cfg["entry_variant"], entry=dict(cfg.get("entry", {})),
                                    layer2=cfg["layer2"], tp_pct=float(cfg["tp_pct"]), sl_pct=float(cfg["sl_pct"])))
        for cid in enabled_new6_combos(sym):
            cfg = get_case(sym, cid) or {}
            combos.append(ComboSpec(symbol=sym, combo_id=cid, native_timeframe="1h", is_new6=True,
                                    entry_variant=cfg["entry_variant"], entry={"indicator": cfg.get("indicator")},
                                    layer2=cfg["layer2"], tp_pct=float(cfg["tp_pct"]), sl_pct=float(cfg["sl_pct"])))
    return StrategyRegistry(version=REGISTRY_20FINAL, combos=tuple(combos), new6_signal_mode="virtual_position",
                            provenance={"source": "autotrade_v2.app.strategy (FINAL14 + NEW6, frozen)"})


_CACHE: dict[str, StrategyRegistry] = {}


def load_registry(version: str | None = None) -> StrategyRegistry:
    v = (version or DEFAULT_REGISTRY).strip().upper()
    if v not in KNOWN_REGISTRIES:
        raise ValueError(f"V3_STRATEGY_REGISTRY không hợp lệ: {version!r} (chỉ nhận {', '.join(KNOWN_REGISTRIES)})")
    if v not in _CACHE:
        _CACHE[v] = _load_json(v) if v in JSON_REGISTRIES else _load_20final()
    return _CACHE[v]


def case_for(symbol: str, combo_id: int, registry: StrategyRegistry | None = None) -> dict | None:
    """TP/SL config for L3: the active registry first, then the frozen 20FINAL table."""
    if registry is not None:
        spec = registry.get(symbol, combo_id)
        if spec is not None:
            return spec.as_case()
    return get_case(symbol, combo_id)
