"""Multi-window evaluation regimes: Development, Validation, and Holdout."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import pandas as pd

from autotrade_v3.analysis.metrics import BacktestMetrics, calculate_metrics
from autotrade_v3.engine.execution_simulator import TradeRecord

REGIMES = {
    "DEVELOPMENT": {
        "start": "2023-11-01T00:00:00Z",
        "end": "2025-10-31T23:59:59.999Z",
        "start_ms": 1698796800000,
        "end_ms": 1761955199999,
        "description": "24-month strategy development and training window",
    },
    "VALIDATION": {
        "start": "2025-11-01T00:00:00Z",
        "end": "2026-04-30T23:59:59.999Z",
        "start_ms": 1761955200000,
        "end_ms": 1777593599999,
        "description": "6-month out-of-sample validation window",
    },
    "HOLDOUT": {
        "start": "2026-05-01T00:00:00Z",
        "end": "2026-10-31T23:59:59.999Z",
        "start_ms": 1777593600000,
        "end_ms": 1793404799999,
        "description": "6-month strictly untouched final holdout window",
    },
}


def classify_timestamp(ts_ms: int) -> str:
    """Classify a timestamp into one of the evaluation regimes."""
    for regime_name, cfg in REGIMES.items():
        if cfg["start_ms"] <= ts_ms <= cfg["end_ms"]:
            return regime_name
    return "OUT_OF_BOUNDS"


def split_trades_by_regime(trades: list[TradeRecord]) -> dict[str, list[TradeRecord]]:
    """Group trade records by the regime of their entry time."""
    buckets: dict[str, list[TradeRecord]] = {
        "DEVELOPMENT": [],
        "VALIDATION": [],
        "HOLDOUT": [],
        "OUT_OF_BOUNDS": [],
    }
    for t in trades:
        reg = classify_timestamp(t.entry_time_ms)
        buckets[reg].append(t)
    return buckets


def evaluate_regimes(
    trades: list[TradeRecord],
    initial_equity: float = 1000.0,
) -> dict[str, dict]:
    """Evaluate performance metrics independently across each regime window."""
    splits = split_trades_by_regime(trades)
    results = {}

    for reg_name in ("DEVELOPMENT", "VALIDATION", "HOLDOUT"):
        reg_trades = splits[reg_name]
        m = calculate_metrics(reg_trades, initial_equity=initial_equity)
        d = m.to_dict()
        d["window_info"] = REGIMES[reg_name]
        results[reg_name] = d

    return results
