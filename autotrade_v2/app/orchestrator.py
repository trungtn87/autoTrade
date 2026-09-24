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
        event_cb=None,
    ):
        self.settings = settings
        self.data = data
        self.strategy = strategy
        self.execution = execution
        self.event_cb = event_cb

    def _emit(self, key: str, severity: str, message: str, **kwargs) -> None:
        if not self.event_cb:
            return
        try:
            self.event_cb(key, severity, message, **kwargs)
        except Exception:
            return

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
            self._emit(
                "L1.DATA.BOOTSTRAP_OK",
                "INFO",
                "historical/store bootstrap ready",
                symbol=symbol,
                details={
                    "candles":out["candles"],
                    "snapshot_close":out["snapshot_close"],
                    "elapsed_ms":out["elapsed_ms"],
                },
            )
            return out
        except Exception as exc:
            category=classify_error(exc)
            log.exception("BOOTSTRAP_ERROR symbol=%s category=%s error=%s",symbol,category,exc)
            self._emit(
                "L1.DATA.BOOTSTRAP_FAIL",
                "ERROR",
                str(exc),
                symbol=symbol,
                details={"category":category,"stage":"bootstrap"},
            )
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
            self._emit(
                "L1.DATA.CANDLE_INGESTED",
                "INFO",
                "closed 15m candle persisted and validated",
                symbol=symbol,
                details={
                    "latest_open_time":snapshot.latest_open_time,
                    "latest_close_time":snapshot.latest_close_time,
                    "candles":len(snapshot.candles),
                },
            )

            stage="strategy"
            intents=self.strategy.calculate(snapshot)
            for intent in intents:
                self._emit(
                    "L2.FINAL14.SIGNAL",
                    "INFO",
                    "FINAL14 produced TradeIntent",
                    event_id=intent.event_id,
                    symbol=intent.symbol,
                    combo=intent.combo,
                    details={
                        "side":intent.side,
                        "timeframe":intent.timeframe,
                        "close_time":intent.close_time,
                        "entry":intent.entry,
                        "tp":intent.tp,
                        "sl":intent.sl,
                        "smc_dir":intent.smc_dir,
                    },
                )

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
            if stage=="data":
                key="L1.DATA.PIPELINE_FAIL"
            elif stage=="strategy":
                key="L2.FINAL14.CALC_FAIL"
            else:
                key="L3.EXEC.PIPELINE_FAIL"
            self._emit(
                key,
                "ERROR",
                str(exc),
                symbol=symbol,
                details={"stage":stage,"category":category},
            )
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
