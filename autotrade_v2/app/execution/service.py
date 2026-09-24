from __future__ import annotations

from ..config import Settings
from ..contracts import ExecutionResult, TradeIntent
from ..strategy.strategy import Signal
from .final14_executor import Final14Executor
from .store import ExecutionStore


class ExecutionService:
    """Layer 3 only: TradeIntent -> BingX execution result.

    The service is disabled by default. No strategy calculations or market-data
    fetching are allowed here.
    """

    def __init__(self, settings: Settings, store: ExecutionStore):
        self.settings = settings
        self.store = store
        self.executor = Final14Executor(settings)

    @staticmethod
    def _signal(intent: TradeIntent) -> Signal:
        return Signal(
            symbol=intent.symbol,
            combo=intent.combo,
            side=intent.side,
            timeframe=intent.timeframe,
            close_time=intent.close_time,
            entry=intent.entry,
            tp=intent.tp,
            sl=intent.sl,
            smc_dir=intent.smc_dir,
        )

    def execute(self, intent: TradeIntent) -> ExecutionResult:
        if self.store.seen(intent.event_id):
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="duplicate_skipped",
                details={"reason": "event_id already processed"},
            )

        if not self.settings.execution_enabled:
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="execution_disabled",
                details={"network_called": False},
            )

        if self.settings.dry_run:
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="dry_run",
                details={"network_called": False},
            )

        signal = self._signal(intent)
        result = self.executor.send_target(
            signal,
            "bingx_account",
            "direct://bingx",
            100.0,
        )
        accepted = bool(result.get("entry_accepted") or result.get("ok"))
        processed = bool(result.get("processed") or accepted)
        if processed:
            self.store.mark(intent.event_id, result)

        return ExecutionResult(
            event_id=intent.event_id,
            accepted=accepted,
            status=str(result.get("stage") or ("ok" if result.get("ok") else "failed")),
            order_id=str(result.get("order_id") or "") or None,
            details=result,
        )
