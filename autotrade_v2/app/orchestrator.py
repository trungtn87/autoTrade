from __future__ import annotations

import logging
import time
from dataclasses import asdict

import pandas as pd

from .config import Settings
from .control.errors import classify_error
from .data.service import DataService
from .execution.service import ExecutionService
from .strategy.engine import StrategyEngine

log = logging.getLogger(__name__)


class Orchestrator:
    """Thin pipeline only. No market-fetch or strategy logic lives here."""

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

    def bootstrap_symbol(self, symbol: str) -> dict:
        started=time.monotonic()
        try:
            snapshot=self.data.bootstrap(symbol)
            out={
                "ok":True,
                "symbol":symbol,
                "snapshot_close":snapshot.latest_close_time,
                "candles":len(snapshot.candles),
                "elapsed_ms":round((time.monotonic()-started)*1000,1),
            }
            log.info("BOOTSTRAP_DONE symbol=%s candles=%s elapsed_ms=%s",symbol,out["candles"],out["elapsed_ms"])
            return out
        except Exception as exc:
            category=classify_error(exc)
            log.exception("BOOTSTRAP_ERROR symbol=%s category=%s error=%s",symbol,category,exc)
            return {
                "ok":False,
                "symbol":symbol,
                "stage":"bootstrap",
                "category":category,
                "error":str(exc),
                "elapsed_ms":round((time.monotonic()-started)*1000,1),
            }

    def on_closed_candle(self, symbol: str, candle: pd.DataFrame) -> dict:
        started=time.monotonic()
        stage="data"
        try:
            snapshot=self.data.ingest_closed_candle(symbol,candle)
            stage="strategy"
            intents=self.strategy.calculate(snapshot)
            stage="execution"
            results=[self.execution.execute(intent) for intent in intents]
            out={
                "ok":True,
                "symbol":symbol,
                "snapshot_close":snapshot.latest_close_time,
                "intents":[asdict(x) for x in intents],
                "execution":[asdict(x) for x in results],
                "elapsed_ms":round((time.monotonic()-started)*1000,1),
            }
            log.info(
                "PIPELINE_DONE symbol=%s intents=%s execution_enabled=%s elapsed_ms=%s",
                symbol,len(intents),self.settings.execution_enabled,out["elapsed_ms"],
            )
            return out
        except Exception as exc:
            category=classify_error(exc)
            log.exception("PIPELINE_ERROR symbol=%s stage=%s category=%s error=%s",symbol,stage,category,exc)
            return {
                "ok":False,
                "symbol":symbol,
                "stage":stage,
                "category":category,
                "error":str(exc),
                "elapsed_ms":round((time.monotonic()-started)*1000,1),
            }

    def evaluate_store(self, symbol: str) -> dict:
        """No-network deterministic evaluation for tests/replay."""
        snapshot=self.data.snapshot_from_store(symbol)
        intents=self.strategy.calculate(snapshot)
        return {
            "ok":True,
            "symbol":symbol,
            "snapshot_close":snapshot.latest_close_time,
            "intents":[asdict(x) for x in intents],
        }
