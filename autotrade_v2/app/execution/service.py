from __future__ import annotations

from dataclasses import asdict
import threading

from ..config import Settings
from ..contracts import ExecutionResult, TradeIntent
from .final14_executor import Final14Executor
from .store import ExecutionStore


class ExecutionService:
    """Layer 3 only: TradeIntent -> reconciled BingX execution result."""

    def __init__(self, settings: Settings, store: ExecutionStore, event_cb=None):
        self.settings = settings
        self.store = store
        self.executor = Final14Executor(settings)
        self.event_cb = event_cb
        self.credentials_verified = False
        self.preflight_error: str | None = None
        self._execution_lock = threading.RLock()

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
    def _state_payload(intent:TradeIntent,result:dict,terminal:bool)->dict:
        return {
            "terminal":bool(terminal),
            "intent":asdict(intent),
            **result,
        }

    @staticmethod
    def _intent_from_state(state:dict)->TradeIntent|None:
        raw=state.get("intent")
        if not isinstance(raw,dict):
            return None
        try:
            return TradeIntent(
                event_id=str(raw["event_id"]),
                symbol=str(raw["symbol"]),
                combo=int(raw["combo"]),
                side=str(raw["side"]),
                timeframe=str(raw["timeframe"]),
                close_time=int(raw["close_time"]),
                entry=float(raw["entry"]),
                tp=float(raw["tp"]),
                sl=float(raw["sl"]),
                smc_dir=int(raw["smc_dir"]),
            )
        except Exception:
            return None

    def recover_pending(self)->list[dict]:
        """Reconcile persisted non-terminal events after restart.

        Recovery never creates a fresh entry. It may only discover/reconcile an
        order that already exists on BingX, complete protection, or confirm that
        no exchange order exists for the persisted event.
        """
        if (
            not self.settings.execution_enabled
            or self.settings.dry_run
            or not self.credentials_verified
        ):
            return []

        recovered=[]
        for event_id,state in self.store.list_states():
            if bool(state.get("terminal")):
                continue
            intent=self._intent_from_state(state)
            if intent is None:
                self._emit(
                    "L3.EXEC.RECOVERY_STATE_INVALID",
                    "ERROR",
                    "persisted execution state cannot be reconstructed",
                    None,
                    event_id=event_id,
                )
                continue
            result=self.execute(intent,recovery_only=True)
            recovered.append({
                "event_id":event_id,
                "status":result.status,
                "accepted":result.accepted,
            })
        return recovered

    def execute(self, intent: TradeIntent, recovery_only:bool=False) -> ExecutionResult:
        with self._execution_lock:
            return self._execute_unlocked(intent,recovery_only=recovery_only)

    def _execute_unlocked(self, intent: TradeIntent, recovery_only:bool=False) -> ExecutionResult:
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

        state=self.store.get_state(intent.event_id)
        pending=bool(state and not state.get("terminal"))

        if self.store.seen(intent.event_id) and not pending:
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

        client_order_id=self.executor.client_order_id(intent.event_id,"entry")
        if not state:
            self.store.save_state(
                intent.event_id,
                {
                    "terminal":False,
                    "stage":"entry_intent",
                    "client_order_id":client_order_id,
                    "intent":asdict(intent),
                },
            )
        else:
            self._emit(
                "L3.EXEC.RECONCILE_START",
                "WARNING",
                "persisted non-terminal execution state is being reconciled",
                intent,
                client_order_id=client_order_id,
                previous_stage=state.get("stage"),
            )

        self._emit(
            "L3.EXEC.ORDER_REQUEST",
            "INFO",
            (
                "reconciling FINAL14 order"
                if (pending or recovery_only)
                else "FINAL14 TradeIntent handed to BingX executor"
            ),
            intent,
            side=intent.side,
            entry=intent.entry,
            tp=intent.tp,
            sl=intent.sl,
            client_order_id=client_order_id,
            recovery_only=bool(recovery_only),
        )

        result = self.executor.execute_safe(
            intent,
            resume_state=state,
            allow_new_entry=not (pending or recovery_only),
        )
        accepted = bool(result.get("entry_accepted") or result.get("ok"))
        terminal = bool(result.get("processed"))
        order_id = str(result.get("order_id") or "") or None

        self.store.save_state(
            intent.event_id,
            self._state_payload(intent,result,terminal),
        )
        if terminal:
            self.store.mark(intent.event_id, result)

        if result.get("ok"):
            common = {
                "order_id": order_id,
                "client_order_id":result.get("client_order_id"),
                "avg_price": result.get("avg_price"),
                "executed_qty": result.get("executed_qty"),
                "tp": result.get("tp"),
                "sl": result.get("sl"),
                "protection":result.get("protection"),
            }
            self._emit("L3.EXEC.ORDER_SENT", "INFO", "market entry confirmed on BingX", intent, **common)
            self._emit("L3.EXEC.ORDER_FILLED", "INFO", "market entry FILLED confirmed", intent, **common)
            self._emit("L3.EXEC.SL_PLACED", "INFO", "hard stop-loss confirmed", intent, **common)
            self._emit("L3.EXEC.TP_PLACED", "INFO", "hard take-profit confirmed", intent, **common)
            self._emit("L3.EXEC.ORDER_COMPLETE", "INFO", "entry and protections reconciled", intent, **common)
        else:
            stage = str(result.get("stage") or "failed")
            severity = "CRITICAL" if result.get("entry_accepted") else "ERROR"
            key = "L3.EXEC.ORDER_FAIL"

            if stage in {"entry_unknown_reconcile_required","reconcile_error"}:
                key = "L3.EXEC.RECONCILE_REQUIRED"
                severity = "CRITICAL"
            elif stage=="entry_absent_reconciled":
                key = "L3.EXEC.RECONCILE_CLEARED"
                severity = "INFO"
            elif stage=="entry_not_filled_terminal":
                key = "L3.EXEC.ENTRY_NOT_FILLED"
                severity = "WARNING"
            elif stage=="partial_fill_closed":
                key = "L3.EXEC.EMERGENCY_CLOSE"
                severity = "WARNING"
            elif stage=="unsafe_partial_entry":
                key = "L3.EXEC.UNSAFE_OPEN_POSITION"
                severity = "CRITICAL"
            elif stage in (
                "fill_outside_final14_range_closed",
                "protection_sl_failed_closed",
            ):
                key = "L3.EXEC.EMERGENCY_CLOSE"
            elif stage in (
                "closed_by_sl_during_protection",
                "closed_by_tp_during_protection",
            ):
                key = "L3.EXEC.PROTECTION_EXIT_CONFIRMED"
                severity = "WARNING"
            elif stage in ("sl_execution_in_progress","tp_execution_in_progress"):
                key = "L3.EXEC.PROTECTION_RECONCILE_REQUIRED"
                severity = "CRITICAL"
            elif stage == "orphan_sl_cleanup_required":
                key = "L3.EXEC.ORPHAN_PROTECTION_CLEANUP"
                severity = "CRITICAL"
            elif stage == "protection_tp_failed_sl_active":
                key = "L3.EXEC.TP_FAILED_SL_ACTIVE"
                severity = "ERROR"
            elif stage == "unsafe_open_position":
                key = "L3.EXEC.UNSAFE_OPEN_POSITION"
                severity = "CRITICAL"

            self._emit(
                key,
                severity,
                str(result.get("error") or stage),
                intent,
                order_id=order_id,
                client_order_id=result.get("client_order_id"),
                stage=stage,
                terminal=terminal,
                entry_accepted=bool(result.get("entry_accepted")),
                entry_filled=bool(result.get("entry_filled")),
                executed_qty=result.get("executed_qty"),
                status=result.get("status"),
            )

        return ExecutionResult(
            event_id=intent.event_id,
            accepted=accepted,
            status=str(result.get("stage") or ("ok" if result.get("ok") else "failed")),
            order_id=order_id,
            details=result,
        )
