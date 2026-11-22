"""Ánh xạ khóa 1-1 giữa event_id (Live) và signal_id (Lab) cùng kiểm tra bảng combo."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any

from autotrade_v3.strategy_core.api import ComboSpec
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry


class KeyMappingError(ValueError):
    """Lỗi định dạng event_id hoặc signal_id."""


class ComboVerificationError(ValueError):
    """Lỗi không khớp combo hoặc tham số TP/SL giữa Live và Lab."""


_EVENT_RE = re.compile(r"^([A-Z0-9]+-[A-Z0-9]+)\|([0-9]+[mh])\|([A-Z0-9_]+)\|(BUY|SELL)\|(\d+)$")
_SIGNAL_RE = re.compile(r"^SIG_([A-Z0-9]+)_([A-Z0-9_]+)_([0-9]+[mh])_(BUY|SELL)_(\d+)$")


@dataclass(frozen=True)
class ParsedKey:
    symbol: str
    timeframe: str
    combo_name: str
    side: str
    close_ms: int

    @property
    def event_id(self) -> str:
        return f"{self.symbol}|{self.timeframe}|{self.combo_name}|{self.side}|{self.close_ms}"

    @property
    def signal_id(self) -> str:
        sym_clean = self.symbol.replace("-", "").replace("_", "")
        return f"SIG_{sym_clean}_{self.combo_name}_{self.timeframe}_{self.side}_{self.close_ms}"


def _restore_symbol(sym_clean: str) -> str:
    if sym_clean.endswith("USDT") and "-" not in sym_clean:
        return f"{sym_clean[:-4]}-USDT"
    return sym_clean


def parse_event_id(event_id: str) -> ParsedKey:
    m = _EVENT_RE.match(str(event_id).strip())
    if not m:
        raise KeyMappingError(f"Định dạng event_id không hợp lệ: {event_id!r}")
    return ParsedKey(
        symbol=m.group(1),
        timeframe=m.group(2),
        combo_name=m.group(3),
        side=m.group(4),
        close_ms=int(m.group(5)),
    )


def parse_signal_id(signal_id: str) -> ParsedKey:
    m = _SIGNAL_RE.match(str(signal_id).strip())
    if not m:
        raise KeyMappingError(f"Định dạng signal_id không hợp lệ: {signal_id!r}")
    return ParsedKey(
        symbol=_restore_symbol(m.group(1)),
        combo_name=m.group(2),
        timeframe=m.group(3),
        side=m.group(4),
        close_ms=int(m.group(5)),
    )


def event_id_to_signal_id(event_id: str) -> str:
    """Chuyển event_id Live sang signal_id Lab theo ánh xạ 1-1."""
    return parse_event_id(event_id).signal_id


def signal_id_to_event_id(signal_id: str) -> str:
    """Chuyển signal_id Lab sang event_id Live theo ánh xạ 1-1."""
    return parse_signal_id(signal_id).event_id


def build_combo_table() -> dict[tuple[str, str], ComboSpec]:
    """Xây dựng bảng tra cứu (symbol, combo_name) -> ComboSpec từ 20FINAL@1 và V3_LAB_11_PRUNE23."""
    from autotrade_v3.reconcile_v3.era_engines import ensure_fixed_v1_registered
    from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy

    table: dict[tuple[str, str], ComboSpec] = {}
    strat_20 = StrategyCoreRegistry.get("20FINAL", "1")
    for c in strat_20.spec().combos:
        table[(c.symbol, c.combo_name)] = c
    ensure_fixed_v1_registered()
    strat_23 = V3Lab11Prune23Strategy()
    for c in strat_23.spec().combos:
        table.setdefault((c.symbol, c.combo_name), c)
    return table


def verify_live_signals_against_combos(
    live_signals: list[dict[str, Any]],
    combo_table: dict[tuple[str, str], ComboSpec] | None = None,
    *,
    pct_tol: Decimal = Decimal("0.00001"),
) -> dict[str, ComboSpec]:
    """Xác minh mọi tín hiệu live đều khớp tên, timeframe và tham số TP/SL của combo Lab.

    Trả về map event_id -> ComboSpec. Dừng và báo lỗi ngay nếu có combo không khớp.
    """
    table = combo_table if combo_table is not None else build_combo_table()
    resolved: dict[str, ComboSpec] = {}

    for row in live_signals:
        eid = str(row["event_id"])
        parsed = parse_event_id(eid)
        # Kiểm tra nhất quán giữa cột và event_id
        if str(row.get("symbol")) != parsed.symbol or str(row.get("combo")) != parsed.combo_name or str(row.get("side")) != parsed.side:
            raise ComboVerificationError(
                f"Cột dữ liệu không khớp event_id {eid}: "
                f"symbol={row.get('symbol')}, combo={row.get('combo')}, side={row.get('side')}"
            )

        key = (parsed.symbol, parsed.combo_name)
        if key not in table:
            raise ComboVerificationError(
                f"Combo live không có trong bảng Lab 20FINAL: {key} (event_id={eid})"
            )
        spec = table[key]
        if parsed.timeframe != spec.native_timeframe:
            raise ComboVerificationError(
                f"Sai timeframe cho {key}: live={parsed.timeframe}, lab={spec.native_timeframe} (event_id={eid})"
            )

        # Kiểm tra % TP / SL từ giá tín hiệu
        entry = Decimal(str(row["entry"]))
        tp = Decimal(str(row["tp"]))
        sl = Decimal(str(row["sl"]))
        if entry <= 0:
            raise ComboVerificationError(f"Giá entry không hợp lệ ({entry}) tại {eid}")

        actual_tp_pct = abs(tp - entry) / entry
        actual_sl_pct = abs(sl - entry) / entry
        expected_tp = Decimal(str(spec.exit_policy.tp_pct))
        expected_sl = Decimal(str(spec.exit_policy.sl_pct))

        if abs(actual_tp_pct - expected_tp) > pct_tol:
            raise ComboVerificationError(
                f"Sai lệch tp_pct cho {key} ({eid}): live={actual_tp_pct}, lab={expected_tp}"
            )
        if abs(actual_sl_pct - expected_sl) > pct_tol:
            raise ComboVerificationError(
                f"Sai lệch sl_pct cho {key} ({eid}): live={actual_sl_pct}, lab={expected_sl}"
            )

        # Kiểm tra chiều TP/SL theo side
        if parsed.side == "BUY" and not (tp > entry > sl):
            raise ComboVerificationError(f"Chiều TP/SL BUY không hợp lệ tại {eid}: entry={entry}, tp={tp}, sl={sl}")
        if parsed.side == "SELL" and not (sl > entry > tp):
            raise ComboVerificationError(f"Chiều TP/SL SELL không hợp lệ tại {eid}: entry={entry}, tp={tp}, sl={sl}")

        resolved[eid] = spec

    return resolved
