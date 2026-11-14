"""Explicit execution cost model and profile loader for AutoTrade V3."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

DEFAULT_COST_PROFILES_PATH = Path(__file__).resolve().with_name("cost_profiles.json")

_EXIT_REASON_TO_ORDER_TYPE = {
    "TP": "tp",
    "SL": "stop",
    "SL_INTRABAR_AMBIGUOUS": "stop",
    "TRAIL": "trailing_stop",
    "MAX_BARS_HELD": "entry_market",
    "FORCE_CLOSE": "entry_market",
}


@dataclass(frozen=True)
class SymbolCostParams:
    """Per-symbol cost parameters (rates as fractions, spread/slippage in bps)."""

    fee_taker: float = 0.0005
    fee_maker: float = 0.0
    maker_order_types: tuple[str, ...] = ()
    half_spread_bps: float = 0.0
    slippage_entry_market_bps: float = 0.0
    slippage_tp_bps: float = 0.0
    slippage_stop_bps: float = 0.0
    slippage_trailing_stop_bps: float = 0.0
    funding_mode: str = "NOT_MODELED"
    funding_bps_per_8h: float | None = None

    def slippage_bps_for(self, order_type: str) -> float:
        ot = order_type.lower()
        if ot == "tp":
            return self.slippage_tp_bps
        if ot == "stop":
            return self.slippage_stop_bps
        if ot == "trailing_stop":
            return self.slippage_trailing_stop_bps
        return self.slippage_entry_market_bps

    def fee_rate_for(self, order_type: str) -> float:
        if order_type.lower() in {m.lower() for m in self.maker_order_types}:
            return self.fee_maker
        return self.fee_taker


@dataclass(frozen=True)
class CostProfile:
    """Loaded cost profile with provenance status and canonical hash."""

    name: str
    status: str
    source: str
    values: dict[str, Any]
    profile_hash: str


@dataclass(frozen=True)
class LegCostBreakdown:
    """Full itemized execution cost breakdown for a single trade leg."""

    entry_px_raw: float
    entry_px_fill: float
    exit_px_trigger: float
    exit_px_fill: float
    qty: float
    fee_entry: float
    fee_exit: float
    spread_cost: float
    slippage_cost: float
    funding_cost: float
    gross_pnl_raw: float
    gross_pnl_fill: float
    fee_total: float
    net_pnl: float


def compute_cost_profile_hash(profile_data: Mapping[str, Any]) -> str:
    """Compute canonical SHA256 hex digest of a cost profile definition."""
    payload = json.dumps(profile_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cost_profiles(path: Path | str | None = None) -> dict[str, CostProfile]:
    """Load all cost profiles from JSON."""
    p = Path(path) if path is not None else DEFAULT_COST_PROFILES_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    out: dict[str, CostProfile] = {}
    for key, obj in raw.items():
        name = str(obj.get("name", key))
        status = str(obj.get("status", "UNCONFIRMED"))
        source = str(obj.get("source", ""))
        values = dict(obj.get("values") or {})
        canonical = {"name": name, "status": status, "source": source, "values": values}
        out[name] = CostProfile(
            name=name,
            status=status,
            source=source,
            values=values,
            profile_hash=compute_cost_profile_hash(canonical),
        )
    return out


def load_cost_profile(name: str, path: Path | str | None = None) -> CostProfile:
    """Load a single named cost profile."""
    profiles = load_cost_profiles(path)
    key = name.upper()
    for pname, prof in profiles.items():
        if pname.upper() == key:
            return prof
    raise ValueError(f"Unknown cost profile: {name!r}. Available: {sorted(profiles)}")


def validate_profile_for_real_data(profile: CostProfile) -> None:
    """Reject UNCONFIRMED or TEST_ONLY profiles when running on real M1 data."""
    if profile.status == "UNCONFIRMED" or not profile.values:
        raise ValueError(
            f"Cost profile {profile.name!r} has status={profile.status!r} (BLOCKED_ON_OWNER) "
            "and cannot be used on real data."
        )
    if profile.status == "TEST_ONLY" or profile.name.upper() == "TEST_ONLY":
        raise ValueError(
            f"Cost profile {profile.name!r} is TEST_ONLY and cannot be used on real data."
        )


def _parse_symbol_params(raw_cfg: Mapping[str, Any]) -> SymbolCostParams:
    slip = raw_cfg.get("slippage_bps") or {}
    return SymbolCostParams(
        fee_taker=float(raw_cfg.get("fee_taker", 0.0005)),
        fee_maker=float(raw_cfg.get("fee_maker", 0.0)),
        maker_order_types=tuple(str(x) for x in (raw_cfg.get("maker_order_types") or ())),
        half_spread_bps=float(raw_cfg.get("half_spread_bps", 0.0)),
        slippage_entry_market_bps=float(slip.get("entry_market", 0.0)),
        slippage_tp_bps=float(slip.get("tp", 0.0)),
        slippage_stop_bps=float(slip.get("stop", 0.0)),
        slippage_trailing_stop_bps=float(slip.get("trailing_stop", 0.0)),
        funding_mode=str(raw_cfg.get("funding", "NOT_MODELED")),
        funding_bps_per_8h=(
            float(raw_cfg["funding_bps_per_8h"])
            if raw_cfg.get("funding_bps_per_8h") is not None
            else None
        ),
    )


def exit_reason_to_order_type(exit_reason: str) -> str:
    """Map leg exit_reason to CostModel order type."""
    return _EXIT_REASON_TO_ORDER_TYPE.get(exit_reason.upper(), "entry_market")


@dataclass
class CostModel:
    """Calculates adverse fill prices and itemized execution costs per symbol."""

    profile: CostProfile
    funding_table: pd.DataFrame | None = None
    _symbol_cache: dict[str, SymbolCostParams] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_profile_name(
        cls,
        name: str,
        *,
        is_real_data: bool = False,
        profiles_path: Path | str | None = None,
        funding_table: pd.DataFrame | None = None,
    ) -> "CostModel":
        prof = load_cost_profile(name, path=profiles_path)
        if is_real_data:
            validate_profile_for_real_data(prof)
        return cls(profile=prof, funding_table=funding_table)

    @classmethod
    def from_uniform_params(
        cls,
        *,
        name: str = "CUSTOM",
        fee_taker: float = 0.0005,
        fee_maker: float = 0.0,
        half_spread_bps: float = 0.0,
        slippage_bps: float = 0.0,
        maker_order_types: tuple[str, ...] = (),
        funding_bps_per_8h: float | None = None,
    ) -> "CostModel":
        values = {
            "default": {
                "fee_taker": float(fee_taker),
                "fee_maker": float(fee_maker),
                "maker_order_types": list(maker_order_types),
                "half_spread_bps": float(half_spread_bps),
                "slippage_bps": {
                    "entry_market": float(slippage_bps),
                    "tp": float(slippage_bps),
                    "stop": float(slippage_bps),
                    "trailing_stop": float(slippage_bps),
                },
                "funding": "RATE_PER_8H" if funding_bps_per_8h is not None else "NOT_MODELED",
                "funding_bps_per_8h": funding_bps_per_8h,
            }
        }
        canonical = {"name": name, "status": "CONFIRMED", "source": "programmatic", "values": values}
        prof = CostProfile(
            name=name,
            status="CONFIRMED",
            source="programmatic",
            values=values,
            profile_hash=compute_cost_profile_hash(canonical),
        )
        return cls(profile=prof)

    def params_for(self, symbol: str) -> SymbolCostParams:
        if symbol in self._symbol_cache:
            return self._symbol_cache[symbol]
        vals = self.profile.values
        if "default" in vals or "by_symbol" in vals:
            base = dict(vals.get("default") or {})
            sym_override = (vals.get("by_symbol") or {}).get(symbol) or {}
            merged = {**base, **sym_override}
            if "slippage_bps" in base or "slippage_bps" in sym_override:
                merged["slippage_bps"] = {
                    **(base.get("slippage_bps") or {}),
                    **(sym_override.get("slippage_bps") or {}),
                }
        else:
            merged = dict(vals)
        params = _parse_symbol_params(merged)
        self._symbol_cache[symbol] = params
        return params

    def fill_price(
        self,
        raw_price: float,
        *,
        symbol: str,
        side: str,
        is_entry: bool,
        order_type: str,
    ) -> float:
        """Apply adverse half-spread + order-type slippage to raw/trigger price."""
        p = self.params_for(symbol)
        total_bps = p.half_spread_bps + p.slippage_bps_for(order_type)
        if total_bps == 0.0:
            return float(raw_price)
        frac = total_bps / 10000.0
        is_buy_trade = side.upper() in ("BUY", "L")
        # Long entry or short exit buys at ask (+); short entry or long exit sells at bid (-)
        adverse_up = is_buy_trade if is_entry else (not is_buy_trade)
        return float(raw_price * (1.0 + frac)) if adverse_up else float(raw_price * (1.0 - frac))

    def compute_funding_cost(
        self,
        *,
        symbol: str,
        notional: float,
        entry_time_ms: int,
        exit_time_ms: int,
    ) -> float:
        """Compute funding cost if modeled; returns 0.0 when NOT_MODELED."""
        p = self.params_for(symbol)
        if self.funding_table is not None and not self.funding_table.empty:
            ft = self.funding_table
            mask = (
                (ft["symbol"] == symbol)
                & (ft["funding_time_ms"] > entry_time_ms)
                & (ft["funding_time_ms"] <= exit_time_ms)
            )
            if mask.any():
                return float((ft.loc[mask, "funding_rate"].astype(float) * notional).sum())
            return 0.0
        if p.funding_bps_per_8h is None or p.funding_mode == "NOT_MODELED":
            return 0.0
        held_ms = max(0, int(exit_time_ms) - int(entry_time_ms))
        return float(notional * (p.funding_bps_per_8h / 10000.0) * (held_ms / (8.0 * 3600.0 * 1000.0)))

    def evaluate_leg(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        entry_px_raw: float,
        exit_px_trigger: float,
        exit_reason: str,
        entry_time_ms: int = 0,
        exit_time_ms: int = 0,
        entry_px_fill_override: float | None = None,
    ) -> LegCostBreakdown:
        """Compute full itemized costs and PnL invariant for a closed leg."""
        p = self.params_for(symbol)
        is_buy = side.upper() in ("BUY", "L")
        entry_order_type = "entry_market"
        exit_order_type = exit_reason_to_order_type(exit_reason)

        entry_px_fill = (
            float(entry_px_fill_override)
            if entry_px_fill_override is not None
            else self.fill_price(
                entry_px_raw, symbol=symbol, side=side, is_entry=True, order_type=entry_order_type
            )
        )
        exit_px_fill = self.fill_price(
            exit_px_trigger, symbol=symbol, side=side, is_entry=False, order_type=exit_order_type
        )
        qty = float(notional / entry_px_fill)

        half_spread_frac = p.half_spread_bps / 10000.0
        entry_slip_frac = p.slippage_bps_for(entry_order_type) / 10000.0
        exit_slip_frac = p.slippage_bps_for(exit_order_type) / 10000.0

        spread_cost = float((entry_px_raw * half_spread_frac + exit_px_trigger * half_spread_frac) * qty)
        slippage_cost = float((entry_px_raw * entry_slip_frac + exit_px_trigger * exit_slip_frac) * qty)

        fee_entry = float(qty * entry_px_fill * p.fee_rate_for(entry_order_type))
        fee_exit = float(qty * exit_px_fill * p.fee_rate_for(exit_order_type))
        fee_total = float(fee_entry + fee_exit)

        funding_cost = self.compute_funding_cost(
            symbol=symbol,
            notional=notional,
            entry_time_ms=entry_time_ms,
            exit_time_ms=exit_time_ms,
        )

        if is_buy:
            gross_pnl_raw = float((exit_px_trigger - entry_px_raw) * qty)
            gross_pnl_fill = float((exit_px_fill - entry_px_fill) * qty)
        else:
            gross_pnl_raw = float((entry_px_raw - exit_px_trigger) * qty)
            gross_pnl_fill = float((entry_px_fill - exit_px_fill) * qty)

        net_pnl = float(
            gross_pnl_raw - fee_entry - fee_exit - spread_cost - slippage_cost - funding_cost
        )
        return LegCostBreakdown(
            entry_px_raw=float(entry_px_raw),
            entry_px_fill=entry_px_fill,
            exit_px_trigger=float(exit_px_trigger),
            exit_px_fill=exit_px_fill,
            qty=qty,
            fee_entry=fee_entry,
            fee_exit=fee_exit,
            spread_cost=spread_cost,
            slippage_cost=slippage_cost,
            funding_cost=funding_cost,
            gross_pnl_raw=gross_pnl_raw,
            gross_pnl_fill=gross_pnl_fill,
            fee_total=fee_total,
            net_pnl=net_pnl,
        )
