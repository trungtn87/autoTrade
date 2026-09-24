from __future__ import annotations

from ..config import Settings
from ..contracts import ExecutionResult, TradeIntent
from ..strategy.strategy import Signal
from .final14_executor import Final14Executor
from .store import ExecutionStore


class ExecutionService:
    """Layer 3 only: TradeIntent -> BingX execution result.

    Live execution is fail-closed until a read-only authenticated BingX
    credential preflight has succeeded in the current process.
    """

    def __init__(self, settings: Settings, store: ExecutionStore):
        self.settings = settings
        self.store = store
        self.executor = Final14Executor(settings)
        self.credentials_verified = False
        self.preflight_error: str | None = None

    def preflight(self) -> dict:
        if not self.settings.bingx_api_key or not self.settings.bingx_api_secret:
            self.credentials_verified = False
            self.preflight_error = "missing BINGX_API_KEY/BINGX_API_SECRET"
            return {
                "ok": False,
                "credentials_verified": False,
                "error": self.preflight_error,
            }
        try:
            payload = self.executor.client.query_balance()
            self.credentials_verified = True
            self.preflight_error = None
            data = payload.get("data") if isinstance(payload, dict) else None
            asset_count = len(data) if isinstance(data, list) else None
            return {
                "ok": True,
                "credentials_verified": True,
                "asset_count": asset_count,
            }
        except Exception as exc:
            self.credentials_verified = False
            self.preflight_error = str(exc)
            return {
                "ok": False,
                "credentials_verified": False,
                "error": self.preflight_error,
            }

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

        if not self.credentials_verified:
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="credential_preflight_required",
                details={
                    "network_called": False,
                    "error": self.preflight_error or "BingX credentials not verified",
                },
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
