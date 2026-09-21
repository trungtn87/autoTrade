"""Locked RR_TP22 execution contract. Percentages in config are fractions."""
from __future__ import annotations
import math
from .final14_config import get_case

VERSION = "FINAL14_PARITY_V2"
SOURCE_COMMIT = "11c215db8cc1be1c0872360291f308bba7582cf7"
NOTIONAL = 100.0
LEVERAGE = 50

def levels(symbol: str, combo: int, side: str, entry: float) -> tuple[float, float]:
    cfg = get_case(symbol, combo)
    if cfg is None or side not in {"BUY", "SELL"}:
        raise ValueError("Unknown FINAL14 case or side")
    if not math.isfinite(entry) or entry <= 0:
        raise ValueError("Invalid entry")
    tp, sl = float(cfg["tp_pct"]), float(cfg["sl_pct"])
    if not (0 < tp <= .02 and 0 < sl <= .009):
        raise ValueError("Invalid FINAL14 fractional TP/SL")
    direction = 1 if side == "BUY" else -1
    return entry * (1 + direction * tp), entry * (1 - direction * sl)

def validate_signal(signal) -> None:
    tp, sl = levels(signal.symbol, signal.combo, signal.side, signal.entry)
    if not (math.isclose(signal.tp, tp, rel_tol=1e-12) and
            math.isclose(signal.sl, sl, rel_tol=1e-12)):
        raise ValueError("Signal TP/SL differs from locked backtest levels")
