from __future__ import annotations

import logging
import time
from dataclasses import asdict

from .config import Settings
from .control.errors import classify_error
from .data.service import DataService
from .execution.service import ExecutionService
from .strategy.engine import StrategyEngine

log = logging.getLogger(__name__)


class Orchestrator:
    """Thin pipeline only. Contains no market, strategy, or order logic."""

    def __init__(
        self,
        settings: Settings,
        data: DataService,
        strategy: StrategyEngine,
        execution: ExecutionService,
    ):
        self.settings = settings
        self.data = data
        self.strategy = strategy
        self.execution = execution

    def run_symbol(self, symbol: str) -> dict:
        started = time.monotonic()
        stage = "data"
        try:
            snapshot = self.data.refresh(symbol)
            stage = "strategy"
            intents = self.strategy.calculate(snapshot)
            stage = "execution"
            results = [self.execution.execute(intent) for intent in intents]
            out = {
                "ok": True,
                "symbol": symbol,
                "snapshot_close": snapshot.latest_close_time,
                "intents": [asdict(x) for x in intents],
                "execution": [asdict(x) for x in results],
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            }
            log.info(
                "PIPELINE_DONE symbol=%s intents=%s execution_enabled=%s elapsed_ms=%s",
                symbol, len(intents), self.settings.execution_enabled, out["elapsed_ms"],
            )
            return out
        except Exception as exc:
            category = classify_error(exc)
            log.exception(
                "PIPELINE_ERROR symbol=%s stage=%s category=%s error=%s",
                symbol, stage, category, exc,
            )
            return {
                "ok": False,
                "symbol": symbol,
                "stage": stage,
                "category": category,
                "error": str(exc),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            }

    def run_all(self) -> dict:
        return {
            "ok": True,
            "results": [self.run_symbol(symbol) for symbol in self.settings.symbols],
        }
