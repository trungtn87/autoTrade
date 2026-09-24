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
    Layer 4 observation is injected as a callback and never affects execution.
    """

    def __init__(self, settings: Settings, store: ExecutionStore, event_cb=None):
        self.settings = settings
        self.store = store
        self.executor = Final14Executor(settings)
        self.event_cb = event_cb
        self.credentials_verified = False
        self.preflight_error: str | None = None

    def _emit(self, key: str, severity: str, message: str, intent: TradeIntent | None = None, **extra) -> None:
        if not self.event_cb:
            return
        try:
            kwargs = {
                "event_id": intent.event_id if intent else None,
                "symbol": intent.symbol if intent else None,
                "combo": intent.combo if intent else None,
                "order_id": extra.pop("order_id", None),
                "details": extra or {},
            }
            self.event_cb(key, severity, message, **kwargs)
        except Exception:
            return

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
            self._emit(
                "L3.EXEC.DUPLICATE_SKIPPED",
                "INFO",
                "event_id already processed; order not resent",
                intent,
            )
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="duplicate_skipped",
                details={"reason": "event_id already processed"},
            )

        if not self.settings.execution_enabled:
            self._emit(
                "L3.EXEC.DISABLED",
                "WARNING",
                "execution disabled; order not sent",
                intent,
            )
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="execution_disabled",
                details={"network_called": False},
            )

        if self.settings.dry_run:
            self._emit(
                "L3.EXEC.DRY_RUN",
                "INFO",
                "dry-run; order not sent",
                intent,
            )
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="dry_run",
                details={"network_called": False},
            )

        if not self.credentials_verified:
            self._emit(
                "L3.EXEC.PREFLIGHT_BLOCK",
                "CRITICAL",
                "BingX credentials were not verified; order blocked",
                intent,
                error=self.preflight_error or "BingX credentials not verified",
            )
            return ExecutionResult(
                event_id=intent.event_id,
                accepted=False,
                status="credential_preflight_required",
                details={
                    "network_called": False,
                    "error": self.preflight_error or "BingX credentials not verified",
                },
            )

        self._emit(
            "L3.EXEC.ORDER_REQUEST",
            "INFO",
            "FINAL14 TradeIntent handed to BingX executor",
            intent,
            side=intent.side,
            entry=intent.entry,
            tp=intent.tp,
            sl=intent.sl,
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
        order_id = str(result.get("order_id") or "") or None

        if processed:
            self.store.mark(intent.event_id, result)

        if result.get("ok"):
            common = {
                "order_id": order_id,
                "avg_price": result.get("avg_price"),
                "executed_qty": result.get("executed_qty"),
                "tp": result.get("tp"),
                "sl": result.get("sl"),
            }
            self._emit("L3.EXEC.ORDER_SENT", "INFO", "market entry accepted by BingX", intent, **common)
            self._emit("L3.EXEC.ORDER_FILLED", "INFO", "market entry fill confirmed", intent, **common)
            self._emit("L3.EXEC.TP_PLACED", "INFO", "hard take-profit placed", intent, **common)
            self._emit("L3.EXEC.SL_PLACED", "INFO", "hard stop-loss placed", intent, **common)
            self._emit("L3.EXEC.ORDER_COMPLETE", "INFO", "entry and protections completed", intent, **common)
        else:
            stage = str(result.get("stage") or "failed")
            severity = "CRITICAL" if result.get("entry_accepted") else "ERROR"
            key = "L3.EXEC.ORDER_FAIL"
            if stage == "entry_fill_check":
                key = "L3.EXEC.FILL_NOT_CONFIRMED"
            elif stage == "fill_outside_final14_range_closed":
                key = "L3.EXEC.EMERGENCY_CLOSE"
            self._emit(
                key,
                severity,
                str(result.get("error") or stage),
                intent,
                order_id=order_id,
                stage=stage,
                entry_accepted=bool(result.get("entry_accepted")),
                entry_filled=bool(result.get("entry_filled")),
            )

        return ExecutionResult(
            event_id=intent.event_id,
            accepted=accepted,
            status=str(result.get("stage") or ("ok" if result.get("ok") else "failed")),
            order_id=order_id,
            details=result,
        )
