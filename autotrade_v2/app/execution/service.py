from __future__ import annotations

from decimal import Decimal
from dataclasses import asdict
from contextlib import nullcontext, contextmanager
import threading
import time

from ..config import Settings
from ..contracts import ExecutionResult, TradeIntent
from .final14_executor import Final14Executor
from .identity import entry_baseline
from .exit_cleanup import clean_one, eligible, oid, CleanupUncertain
from .store import ExecutionStore
from .merged import reconcile_side
from .force_close import ForceClose, side_key, TARGET as FORCE_TARGET


class FencedClient:
    """Check the exact coordinator session at each private mutation boundary."""
    def __init__(self,client,check):
        self.client=client
        self.check=check

    def __getattr__(self,name):
        value=getattr(self.client,name)
        if callable(value) and (name.startswith(("place_","cancel_")) or name=="close_market"):
            def fenced(*args,**kwargs):
                self.check()
                return value(*args,**kwargs)
            return fenced
        return value


class ExecutionService:
    """Layer 3 only: TradeIntent -> reconciled BingX execution result."""

    def __init__(self, settings: Settings, store: ExecutionStore, event_cb=None):
        self.settings = settings
        self.store = store
        self.executor = Final14Executor(settings)
        self.event_cb = event_cb
        self.credentials_verified = False
        # Separate ability to call BingX from admission of NEW Entry. Existing
        # recovery and emergency-close operations must remain usable after a
        # successful API check even when historical reconciliation fails.
        self.entry_recovery_pending = False
        self.preflight_error: str | None = None
        self._execution_lock = threading.RLock()
        self._exit_cleanup_scan_errors = {}
        self._last_reconciliation_check = {}
        self._last_reconciliation_alert = {}
        self._next_flat_reconciliation_poll = 0.0

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

    def _emit_operational(self,key,severity,message,symbol,**details):
        """System-level incident, preserving symbol without a TradeIntent."""
        if self.event_cb:
            try:
                self.event_cb(key,severity,message,symbol=symbol,details=details)
            except Exception:
                pass  # Observability never blocks live execution.

    @staticmethod
    def _symbol_flat(positions,opened,symbol):
        """A valid empty exchange exposure AND empty order book for this symbol."""
        if not isinstance(positions,list) or not isinstance(opened,dict):
            raise CleanupUncertain("invalid exchange position/order snapshot")
        if opened:
            return False
        for row in positions:
            if not isinstance(row,dict):
                raise CleanupUncertain("invalid BingX position row")
            if row.get("symbol")!=symbol:
                continue
            if str(row.get("positionSide") or "").upper() not in {"LONG","SHORT"}:
                raise CleanupUncertain("BingX positionSide unverified")
            try:
                quantity=Decimal(str(row["positionAmt"]))
            except (KeyError,ValueError,TypeError) as exc:
                raise CleanupUncertain("BingX positionAmt unverified") from exc
            if not quantity.is_finite():
                raise CleanupUncertain("nonfinite BingX positionAmt")
            if quantity!=0:
                return False
        return True

    @staticmethod
    def _unresolved_submission(states):
        """Flat snapshots cannot settle an ambiguous, possibly in-flight POST."""
        for _,state in states:
            if not state.get("terminal") and not state.get("entry_filled"):
                return True
            if (state.get("abort") or {}).get("phase") not in (None,"closed"):
                return True
        return False

    def _confirm_exchange_flat(self,symbol,positions,opened):
        """Two fresh independent exchange snapshots; no DB state is proof."""
        if not self._symbol_flat(positions,opened,symbol):
            return False,positions,opened
        time.sleep(0.25)
        second_positions=self.executor.client.get_positions(symbol)
        second_opened=self._order_map(
            self.executor.client.get_open_orders(symbol),symbol
        )
        return (self._symbol_flat(second_positions,second_opened,symbol),
                second_positions,second_opened)

    def _retire_flat_history(self,symbol,side,states,origin):
        """Retire exposure, never fabricate exit fills/ROI or erase audit."""
        if self._unresolved_submission(states):
            raise CleanupUncertain(
                "ambiguous in-flight submission; flat snapshot alone insufficient"
            )
        at_ms=int(time.time()*1000)
        to_save=[]
        pending=0
        for eid,state in states:
            if state.get("exchange_flat_reconciled"):
                continue
            older=dict(state)
            old_stage=str(state.get("stage") or "")
            old_risk=str(state.get("safety_status") or "")
            had_issue=bool(old_risk or not state.get("terminal")
                           or old_stage not in ("complete","entry_absent_reconciled"))
            if had_issue:
                pending+=1
            older["exchange_flat_reconciled"]={
                "status":"EXCHANGE_FLAT_RECONCILED",
                "at_ms":at_ms,
                "source":"two independent BingX position+openOrders snapshots",
                "origin":origin,
                "symbol":symbol,
                "side":side,
                "previous_stage":old_stage,
                "previous_safety_status":old_risk,
                "historical_accounting_pending":had_issue,
            }
            if had_issue:
                # Remaining PnL / historical exact-fill discrepancies survive.
                # They no longer imply a LIVE position or block a NEW epoch.
                older["stage"]="exchange_flat_reconciled"
                older["historical_reconcile_pending"]=True
                older.pop("safety_status",None)
            if state.get("entry_filled"):
                # The trade's live exposure is proven flat. Recover_pending
                # must no longer retry a historical entry after restart.
                older["terminal"]=True
            to_save.append((eid,older))
        self.store.save_states_atomic(to_save)
        if to_save:
            self._emit_operational(
                "L3.EXEC.EXCHANGE_FLAT_RECONCILED","WARNING",
                "BingX confirmed no positions/open orders; old exposure cleared; "
                "historical fill accounting remains for audit",
                symbol,side=side,closed_exposure_count=len(to_save),
                historical_pending=pending,origin=origin,
                entry_unblocked=True,
            )
        return len(to_save)

    @staticmethod
    def _has_blocking_reconciliation(state):
        """An exact-ID trailing parameter warning is not a side-wide entry ban.

        Recovery still retries this nonterminal trade by acknowledged order ID.
        Every *new* entry always reruns the fresh exchange/SL/ledger checks.
        """
        if (state.get("entry_filled")
                and state.get("stage")=="trailing_parameters_unverified"
                and state.get("safety_status")=="TRAILING_PARAMS_UNVERIFIED"):
            return False
        return bool(
            state.get("safety_status") not in (None, "", "EXIT_BUDGET_UNVERIFIED")
            or not state.get("terminal")
            or state.get("stage")=="protection_reconcile_required"
        )

    def _alert_stuck_reconciliation(self,symbol,side,states,reason):
        key=f"{symbol}|{side}"
        now=time.monotonic()
        if now-self._last_reconciliation_alert.get(key,-3601.0)<3600.0:
            return
        self._last_reconciliation_alert[key]=now
        pending=[eid for eid,state in states
                 if self._has_blocking_reconciliation(state)]
        self._emit_operational(
            "L3.EXEC.RECONCILIATION_STUCK","CRITICAL",
            "Trading may be blocked by unresolved live/historical reconciliation; "
            "exchange risk must be verified before increasing exposure",
            symbol,side=side,affected_count=len(pending),
            sample_events=pending[:5],reason=reason,
            reminder_minutes=60,
        )

    def _reconcile_idle_flat_locked(self):
        """Periodic zero-exposure recovery, independent of whether new signals occur."""
        if (not self.settings.execution_enabled or self.settings.dry_run
                or not self.credentials_verified):
            return []
        states=self.store.list_states()
        groups={}
        for eid,state in states:
            if state.get("exchange_flat_reconciled"):
                continue
            raw=state.get("intent") or {}
            symbol,side=raw.get("symbol"),raw.get("side")
            if symbol not in {"BTC-USDT","ETH-USDT"} or side not in {"BUY","SELL"}:
                continue
            groups.setdefault((symbol,side),[]).append((eid,state))
        now=time.monotonic()
        outcome=[]
        for (symbol,side),siblings in groups.items():
            # Do not add polling of healthy historical states. The signal path
            # independently checks flatness when creating its next entry.
            needs_reconcile=any(
                self._has_blocking_reconciliation(state)
                for _,state in siblings
            )
            emergency=self._emergency(symbol,side)
            if not needs_reconcile or (emergency and emergency.get("phase")!="closed"):
                continue
            key=f"{symbol}|{side}"
            if now-self._last_reconciliation_check.get(key,-301.0)<300.0:
                continue
            self._last_reconciliation_check[key]=now
            try:
                positions=self.executor.client.get_positions(symbol)
                opened=self._order_map(self.executor.client.get_open_orders(symbol),symbol)
                if self._unresolved_submission(siblings):
                    self._alert_stuck_reconciliation(
                        symbol,side,siblings,"PENDING_SUBMISSION_UNVERIFIED"
                    )
                    continue
                is_flat,_,_=self._confirm_exchange_flat(symbol,positions,opened)
                if is_flat:
                    count=self._retire_flat_history(
                        symbol,side,siblings,"periodic-reconciliation"
                    )
                    outcome.append({"symbol":symbol,"side":side,
                                    "status":"EXCHANGE_FLAT_RECONCILED","count":count})
                else:
                    self._alert_stuck_reconciliation(
                        symbol,side,siblings,"EXCHANGE_POSITION_OR_ORDER_REMAINS"
                    )
            except Exception as exc:
                self._alert_stuck_reconciliation(
                    symbol,side,siblings,f"EXCHANGE_VERIFICATION_FAILED:{type(exc).__name__}"
                )
        return outcome

    def _emergency(self,symbol,side):
        return self.store.get(side_key(symbol,side),FORCE_TARGET) if hasattr(self.store,"get") else None

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

    def preflight_and_recover(self) -> dict:
        """Reopen fresh Entry only after credential check and crash recovery.

        A retry must hold the execution lock from the first authenticated GET
        through recovery. Recovery of persisted entries is still permitted;
        a fresh Entry waits until the lock is released. Any failure remains
        fail-closed, rather than treating a successful balance GET as readiness.
        """
        with self._execution_lock:
            # If only persisted-state recovery failed, do not invalidate an
            # already authenticated API session by repeating an unnecessary
            # potentially timing-out GET. Existing emergency close must remain
            # available. Any new Entry is still blocked by the pending gate.
            already_verified = self.credentials_verified and self.entry_recovery_pending
            self.entry_recovery_pending = True
            preflight = (
                {"ok": True, "credentials_verified": True, "phase": "recovery_retry"}
                if already_verified else self.preflight()
            )
            if not preflight.get("ok"):
                return preflight
            try:
                recovered = self.recover_pending()
            except Exception as exc:
                # BingX access was verified. Keep it available for persisted
                # recovery / Force Close, but never admit a fresh Entry.
                self.preflight_error = (
                    f"execution recovery failed: {type(exc).__name__}: {exc}"
                )
                return {
                    "ok": False,
                    "credentials_verified": True,
                    "phase": "recovery",
                    "error": self.preflight_error,
                }
            self.entry_recovery_pending = False
            return {**preflight, "recovery": recovered}

    @property
    def entry_preflight_ready(self) -> bool:
        return bool(self.credentials_verified and not self.entry_recovery_pending)

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
        # An epoch survives a crash between per-combo terminal writes and the
        # final epoch commit. Recover it even if its initiator is now terminal.
        emergencies={}
        for eid,s in self.store.list_states():
            raw=s.get("intent") or {}
            epoch=self.store.get(side_key(raw.get("symbol"),raw.get("side")),FORCE_TARGET)
            if epoch and epoch.get("phase")!="closed":
                emergencies[epoch["epoch"]]=epoch
        for epoch in emergencies.values():
            if time.time()<float(epoch.get("next_at",0)):
                continue
            s=self.store.get_state(epoch["initiator"])
            intent=self._intent_from_state(s or {})
            if intent:
                result=self.execute(intent,recovery_only=True)
                recovered.append({"event_id":intent.event_id,"status":result.status,"accepted":result.accepted})
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
            recovery=state.get("recovery") or {}
            emergency=self.store.get(side_key(intent.symbol,intent.side),FORCE_TARGET)
            if emergency and emergency.get("phase")!="closed":
                continue  # above epoch recovery owns this side
            # Retry read/reconciliation indefinitely with bounded backoff.
            # Eight failed attempts require an alert, NOT a permanent lock.
            # The executor still uses the acknowledged order ID and does not
            # submit a second conditional order for an ambiguous POST.
            if time.time() < float(recovery.get("next_at",0)):
                continue
            try:
                result=self.execute(intent,recovery_only=True)
            except Exception as exc:
                # A single combo's read/parse/reconciliation failure does not
                # turn the successful account preflight into a global lock.
                # Fresh entries still need exact live-side ledger + SL proof.
                self._emit(
                    "L3.EXEC.RECOVERY_COMBO_FAILED","ERROR",
                    "persisted combo recovery failed; other symbol/side admission remains independently verified",
                    intent,impact_scope="combo",
                    recovery_error=f"{type(exc).__name__}: {exc}",
                    affected_event_ids=[event_id],
                    no_reentry=True,
                )
                recovered.append({
                    "event_id":event_id,"status":"recovery_combo_failed",
                    "accepted":bool(state.get("entry_accepted")),
                })
                continue
            recovered.append({
                "event_id":event_id,
                "status":result.status,
                "accepted":result.accepted,
            })
        return recovered

    def reconcile_flat_history(self)->list[dict]:
        """Read-only exchange checks even when orphan cleanup is disabled.

        Runs no more than once per minute unless a fresh Entry asks directly.
        """
        now=time.monotonic()
        if now < self._next_flat_reconciliation_poll:
            return []
        with self._execution_lock, self._trade_lock() as acquired:
            if not acquired:
                return []
            self._next_flat_reconciliation_poll=time.monotonic()+60.0
            return self._reconcile_idle_flat_locked()

    def cleanup_closed_exits(self)->list[dict]:
        with self._execution_lock, self._trade_lock() as acquired:
            if not acquired:
                return []
            flat_results=self._reconcile_idle_flat_locked()
            return flat_results+self._cleanup_closed_exits_locked()

    @contextmanager
    def _trade_lock(self):
        # Legacy offline doubles lack a store; production always has one.
        lock=self.store.trading_lock() if hasattr(self.store,"trading_lock") else nullcontext(True)
        with lock as acquired:
            client=self.executor.client
            check=getattr(self.store,"assert_trading_owner",lambda:None)
            previous=vars(client).get("mutation_check")
            if acquired:
                client.mutation_check=check
                self.executor.client=FencedClient(client,check)
            try:
                yield acquired
            finally:
                self.executor.client=client
                client.mutation_check=previous

    def _cleanup_closed_exits_locked(self)->list[dict]:
        """Bounded, read-first orphan cleanup; never resize or cancelReplace SL.

        Only states with a persisted positionId and verified SL/TP order IDs
        qualify. The trade transport's existing request guard paces *all*
        private BingX calls, including normal entry/protection traffic.
        """
        if (not self.settings.execution_enabled or self.settings.dry_run
                or not self.credentials_verified):
            return []
        with self._execution_lock:
            grouped={}
            all_states=self.store.list_states()
            for event_id,state in all_states:
                raw=state.get("intent") or {}
                emergency=self._emergency(raw.get("symbol"),raw.get("side"))
                if emergency and emergency.get("phase")!="closed":
                    continue  # the emergency epoch owns this entire side
                if eligible(state):
                    grouped.setdefault(state["intent"]["symbol"],[]).append((event_id,state))
            results=[]
            for symbol,states in grouped.items():
                try:
                    orders=self.executor.client.get_open_orders(symbol)
                    positions=self.executor.client.get_positions(symbol)
                    by_id={}
                    for row in orders:
                        order_id=oid(row)
                        if not order_id or order_id in by_id:
                            raise CleanupUncertain("ambiguous BingX open order list")
                        by_id[order_id]=row

                    # Historical lookups are ONE bounded batch per symbol,
                    # only when a known exit ID is absent from openOrders.
                    # Do not use the single GET /trade/order endpoint: BingX
                    # has returned unrelated IDs for historical orders.
                    missing=set()
                    for _,state in states:
                        already_terminal=(state.get("exit_cleanup") or {}).get("orders") or {}
                        for item in (state.get("protection") or {}).values():
                            if not isinstance(item,dict):
                                continue
                            target=str(item.get("order_id") or "")
                            if target and target not in by_id:
                                previous=already_terminal.get(target) or {}
                                if str(previous.get("status") or "").upper() not in {
                                    "FILLED","CANCELLED","CANCELED","EXPIRED","REJECTED"
                                }:
                                    missing.add(target)
                    history_by_id={}
                    if missing:
                        now_ms=int(time.time()*1000)
                        history=self.executor.client.get_order_history(
                            symbol,now_ms-6*86400_000,now_ms,required_ids=missing
                        )
                        for row in history:
                            order_id=oid(row)
                            if not order_id or order_id in history_by_id:
                                raise CleanupUncertain("ambiguous BingX historical order list")
                            if str(row.get("symbol") or "") != symbol:
                                raise CleanupUncertain("historical symbol mismatch")
                            history_by_id[order_id]=row
                except Exception as exc:
                    error=f"{type(exc).__name__}: {exc}"
                    if self._exit_cleanup_scan_errors.get(symbol)!=error:
                        self._emit("L3.EXEC.EXIT_CLEANUP_SCAN_FAIL","WARNING",
                                   "read-only BingX cleanup scan unavailable; no orders changed",
                                   None,symbol=symbol,error=error)
                        self._exit_cleanup_scan_errors[symbol]=error
                    continue
                self._exit_cleanup_scan_errors.pop(symbol,None)
                # If historical state corruption has assigned one protection ID
                # to multiple combos, none of those combos may be mutated.
                claimed={}
                collisions=set()
                for eid,state in states:
                    for item in (state.get("protection") or {}).values():
                        oid_ = str((item or {}).get("order_id") or "") if isinstance(item,dict) else ""
                        if not oid_:
                            continue
                        if oid_ in claimed and claimed[oid_] != eid:
                            collisions.update((eid,claimed[oid_]))
                        claimed[oid_]=eid
                side_reports={}
                for side in {s["intent"]["side"] for _,s in states}:
                    siblings=[(eid,s) for eid,s in all_states
                              if (s.get("intent") or {}).get("symbol")==symbol
                              and (s.get("intent") or {}).get("side")==side
                              and not s.get("exchange_flat_reconciled")]
                    try:
                        side_reports[side]=reconcile_side(siblings,positions,by_id,history_by_id)
                    except Exception as exc:
                        side_reports[side]={"status":"RECONCILE_REQUIRED","risks":[str(exc)]}
                for event_id,state in states:
                    intent=self._intent_from_state(state)
                    report=side_reports[state["intent"]["side"]]
                    own_report=(report.get("combos") or {}).get(event_id,{})
                    state["merged_reconcile"]={k:v for k,v in report.items() if k!="combos"}
                    if own_report.get("monitor"):
                        state["exit_cleanup"]=own_report["monitor"]
                    if (report["status"]=="ADMISSION_SAFE_TRAILING_PARAMS_UNVERIFIED"
                            and f"{event_id}:TRAILING_PARAMS_UNVERIFIED" in (report.get("warnings") or [])):
                        # Only the specific pending combo remains in warning
                        # status. Healthy peer combos retain their completion
                        # and may continue independent cleanup.
                        # Keep the WAL/terminal=false for strict recovery.
                        # The side ledger proved live quantities and hard SL,
                        # not callback or activation parameters.
                        if (state.get("safety_status")!="TRAILING_PARAMS_UNVERIFIED"
                                or state.get("stage")!="trailing_parameters_unverified"):
                            state["safety_status"]="TRAILING_PARAMS_UNVERIFIED"
                            state["stage"]="trailing_parameters_unverified"
                            state["last_safety_warning"]="|".join(report.get("warnings") or [])
                            self.store.save_state(event_id,state)
                            self._emit(
                                "L3.EXEC.TRAILING_PARAMS_UNVERIFIED","WARNING",
                                "trailing parameters still unverified; live hard SL and exit ledger structurally match; recovery continues",
                                intent,impact_scope="owned_combo",
                                affected_event_ids=[event_id],
                                warnings=report.get("warnings") or [],
                            )
                        # Never perform live orphan cleanup for an unfinished
                        # trailing journal, even when its current snapshot matches.
                        continue
                    if report["status"] not in {"VERIFIED","ADMISSION_SAFE_TRAILING_PARAMS_UNVERIFIED"}:
                        budget_only=set(report["risks"])=={"EXECUTABLE_EXIT_BUDGET_EXCEEDED"}
                        state["safety_status"]="EXIT_BUDGET_UNVERIFIED" if budget_only else "RECONCILE_REQUIRED"
                        own_risks=[r for r in report["risks"] if r.startswith(event_id+":")
                            or r in {"POSITION_LEDGER_MISMATCH","UNOWNED_EXIT_ORDER","UNTRACKED_ENTRY_ORDER"}
                            or (":" not in r and r!="EXECUTABLE_EXIT_BUDGET_EXCEEDED")]
                        if own_risks and Decimal(str(own_report.get("remaining_qty", "-1")))!=0:
                            state["ok"]=False
                            state["stage"]="protection_reconcile_required"
                        if budget_only and state.get("terminal") and own_report.get("monitor",{}).get("fills_verified"):
                            state["ok"]=True
                            state["stage"]="complete"
                        signature="|".join(report["risks"])
                        if state.get("last_safety_warning")!=signature:
                            self._emit("L3.EXEC.EXIT_CLEANUP_RISK","CRITICAL",signature,intent)
                            state["last_safety_warning"]=signature
                        self.store.save_state(event_id,state)
                        # Exact owned fills can authorize cleanup of a fully
                        # closed combo even if its peer lacks protection. All
                        # live-combo mutation remains blocked.
                        if Decimal(str(own_report.get("remaining_qty", "-1")))!=0 or not (own_report.get("monitor") or {}).get("fills_verified"):
                            continue
                    if state.get("abort") and state["abort"].get("phase")!="closed":
                        self.store.save_state(event_id,state)
                        continue  # abort coordinator owns all conditional/close mutations
                    if event_id in collisions or intent is None:
                        self._emit("L3.EXEC.EXIT_CLEANUP_RISK","CRITICAL",
                                   "ambiguous owned order IDs; cleanup refused",intent)
                        continue
                    def persist(value):
                        self.store.save_state(event_id,value)
                    def alert(key,severity,message):
                        self._emit(key,severity,message,intent)
                    try:
                        result=clean_one(
                            self.executor.client,state,by_id,positions,
                            persist,alert,historical_by_id=history_by_id
                        )
                        if result["cancel_requested"]:
                            self._emit("L3.EXEC.EXIT_ORPHAN_CANCEL_REQUESTED","WARNING",
                                       "BingX orphan exit cancel requested by exact orderId",
                                       intent,orders=result["cancel_requested"])
                        if result["cancel_requested"] or result["warnings"]:
                            results.append({"event_id":event_id,**result})
                    except Exception as exc:
                        # Fail closed on all exchange/status inconsistencies.
                        old=(state.get("exit_cleanup") or {}).get("last_inspection_error")
                        message=f"{type(exc).__name__}: {exc}"
                        if old!=message:
                            state.setdefault("exit_cleanup",{})["last_inspection_error"]=message
                            self.store.save_state(event_id,state)
                            self._emit("L3.EXEC.EXIT_CLEANUP_RISK","CRITICAL",
                                       "exit cleanup verification failed; no orders mutated",
                                       intent,error=message)
            return results

    def execute(self, intent: TradeIntent, recovery_only:bool=False) -> ExecutionResult:
        with self._execution_lock, self._trade_lock() as acquired:
            if not acquired:
                return ExecutionResult(event_id=intent.event_id, accepted=False,
                                       status="execution_lock_busy", details={"network_called":False})
            return self._execute_unlocked(intent,recovery_only=recovery_only)

    def _entry_admission(self,intent,include_current=False):
        """Read-only admission. Same symbol/side alone never blocks an entry."""
        positions=self.executor.client.get_positions(intent.symbol)
        baseline=entry_baseline(positions,intent.symbol,intent.side)
        opened=self._order_map(self.executor.client.get_open_orders(intent.symbol),intent.symbol)
        siblings=[(eid,s) for eid,s in self.store.list_states()
                  if (include_current or eid!=intent.event_id) and (s.get("intent") or {}).get("symbol")==intent.symbol
                  and (s.get("intent") or {}).get("side")==intent.side
                  and not s.get("exchange_flat_reconciled")]
        emergency=self._emergency(intent.symbol,intent.side)
        if (siblings and not include_current
                and not (emergency and emergency.get("phase")!="closed")):
            # A verified empty exchange account starts a fresh risk epoch.
            # Historic TP/SL gaps remain auditable but may not lock Entry.
            if not self._unresolved_submission(siblings):
                flat,positions,opened=self._confirm_exchange_flat(
                    intent.symbol,positions,opened
                )
                baseline=entry_baseline(positions,intent.symbol,intent.side)
                if flat:
                    self._retire_flat_history(
                        intent.symbol,intent.side,siblings,"entry-admission"
                    )
                    siblings=[]
        if not siblings:
            side="LONG" if intent.side=="BUY" else "SHORT"
            risks=[]
            if baseline:
                risks.append("UNTRACKED_POSITION")
            if any(str(row.get("positionSide") or "").upper()==side for row in opened.values()):
                risks.append("UNOWNED_OPEN_ORDER")
            return baseline,{"blocking_risks":risks,"shared":False}
        required=set()
        for _,s in siblings:
            cached=(s.get("exit_cleanup") or {}).get("orders") or {}
            for item in (s.get("protection") or {}).values():
                if not isinstance(item,dict):
                    continue
                order_id=str(item.get("order_id") or "")
                if order_id and order_id not in opened and not (cached.get(order_id) or {}).get("status") in {
                    "FILLED","CANCELED","CANCELLED","EXPIRED","REJECTED"}:
                    required.add(order_id)
        history={}
        if required:
            now=int(time.time()*1000)
            history=self._order_map(self.executor.client.get_order_history(
                intent.symbol,now-6*86400_000,now,required_ids=required),intent.symbol)
        report=reconcile_side(siblings,positions,opened,history)
        # Sum of resting native conditional quantities is a separate unresolved
        # design diagnostic, not proof a healthy same-side combo is unprotected.
        # It must remain explicit in the draft; do not use it as a disguised
        # blanket same-side entry switch.
        blockers=[r for r in report["risks"] if r!="EXECUTABLE_EXIT_BUDGET_EXCEEDED"]
        # Never claim the trailing callback/activation has been verified.
        # This warning means only that the exact owned trailing is currently
        # structurally present and the full hard SL/exposure ledger is healthy.
        if report.get("warnings") and not blockers:
            self._emit(
                "L3.EXEC.TRAILING_PARAMS_UNVERIFIED","WARNING",
                "exact acknowledged trailing is active but callback/activation cannot be verified; independent entry admission uses full hard-SL proof",
                intent,impact_scope="owned_combo",
                affected_event_ids=[w.split(":")[0] for w in report["warnings"]],
                evidence=report["warnings"],
                confirmation="NOT_FULLY_VERIFIED",
            )
        return baseline,{**{k:v for k,v in report.items() if k!="combos"},"blocking_risks":blockers}

    @staticmethod
    def _order_map(rows,symbol):
        result={}
        if not isinstance(rows,list):
            raise CleanupUncertain("order snapshot is not a list")
        for row in rows:
            order_id=oid(row)
            if not order_id or order_id in result or row.get("symbol")!=symbol:
                raise CleanupUncertain("missing/duplicate/wrong-symbol exchange order identity")
            result[order_id]=row
        return result

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
                side=intent.side,
                entry=intent.entry,
                tp=intent.tp,
                sl=intent.sl,
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
        emergency=self.store.get(side_key(intent.symbol,intent.side),FORCE_TARGET)
        if emergency and emergency.get("phase")!="closed":
            if intent.event_id!=emergency["initiator"]:
                return ExecutionResult(event_id=intent.event_id,accepted=False,
                    status="force_close_side_blocked",details={"epoch":emergency["epoch"]})
            result=ForceClose(self).run(intent,state,emergency["failed_kind"])
            return ExecutionResult(event_id=intent.event_id,accepted=True,status=result["stage"],details=result)
        # Credential validation and historical recovery are different gates.
        # Keep existing emergency epochs and recovery_only execution usable,
        # but never send a NEW Entry while reconciliation is unfinished.
        if self.entry_recovery_pending and not recovery_only:
            self._emit(
                "L3.EXEC.PREFLIGHT_BLOCK",
                "CRITICAL",
                "BingX API verified but persisted-entry recovery is unfinished",
                intent,side=intent.side,entry=intent.entry,tp=intent.tp,sl=intent.sl,
                error=self.preflight_error or "execution recovery pending",
            )
            return ExecutionResult(
                event_id=intent.event_id, accepted=False,
                status="preflight_recovery_pending",
                details={"network_called":False,
                         "error":self.preflight_error or "execution recovery pending"},
            )
        if state and state.get("exchange_flat_reconciled"):
            # Some legacy states did not reach bingx_account/seen before a
            # manual close. Never resend an old signal after retirement.
            return ExecutionResult(
                event_id=intent.event_id,accepted=False,
                status="historical_flat_skipped",
                details={"network_called":False,
                         "reconciled_at_ms":(state["exchange_flat_reconciled"] or {}).get("at_ms")},
            )
        pending=bool(state and not state.get("terminal"))
        if pending:
            recovery=state.get("recovery") or {}
            if time.time()<float(recovery.get("next_at",0)):
                return ExecutionResult(event_id=intent.event_id,
                    accepted=bool(state.get("entry_accepted")),
                    status="recovery_backoff",
                    order_id=state.get("order_id"),details={"network_called":False,"recovery":recovery})

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
            # A peer's presence is not a rejection reason. Reconcile its exact
            # fills/protections against the exchange before increasing exposure.
            try:
                existing=self.executor.client.find_order_by_client_id(intent.symbol,client_order_id)
                if existing is not None:
                    # A prior MARKET can outlive a lost local state write.
                    # Reconcile that exact ID; never admit another MARKET.
                    before_positions=None
                    admission={"blocking_risks":[],"existing_entry_recovery":True}
                    pending=True
                else:
                    before_positions,admission=self._entry_admission(intent)
            except Exception as exc:
                self._emit("L3.EXEC.POSITION_PREFLIGHT_BLOCK","CRITICAL",
                           "exchange exposure/protection evidence unavailable; entry not submitted",
                           intent,error=str(exc))
                return ExecutionResult(event_id=intent.event_id,accepted=False,
                    status="position_preflight_blocked",details={"network_called":True,"error":str(exc)})
            if admission["blocking_risks"]:
                self._emit("L3.EXEC.MERGED_RECONCILE_REQUIRED","CRITICAL",
                           "verified exposure/protection mismatch; entry not submitted",intent,
                           risks=admission["blocking_risks"])
                return ExecutionResult(event_id=intent.event_id,accepted=False,
                    status="merged_reconcile_required",details=admission)
            new_state={
                    "terminal":False,
                    "stage":"entry_intent",
                    # Snapshot is durable across an ambiguous MARKET POST/restart.
                    "pre_entry_positions":before_positions,
                    "shared_position":bool(before_positions),
                    "admission":admission,
                    "exit_mode":"split_trailing" if self.settings.tp_trailing_split_enabled else "hard_tp",
                    "client_order_id":client_order_id,
                    "intent":asdict(intent),
                }
            self.store.save_state(intent.event_id,new_state)
            state=new_state
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

        siblings=[s for eid,s in self.store.list_states() if eid!=intent.event_id
                  and (s.get("intent") or {}).get("symbol")==intent.symbol
                  and (s.get("intent") or {}).get("side")==intent.side
                  and not s.get("exchange_flat_reconciled")
                  and (s.get("entry_filled") or not s.get("terminal"))
                  and not ((s.get("exit_cleanup") or {}).get("fills_verified")
                           and Decimal(str((s.get("exit_cleanup") or {}).get("remaining_qty","-1")))==0)]
        if siblings:
            state["shared_position"]=True
            self.store.save_state(intent.event_id,state)

        # Write-ahead protection journal: each PREPARED state must be durable
        # before BingX receives a conditional POST. This prevents duplicates
        # when HTTP replies are lost or Render restarts mid-trade.
        def persist_protection(next_state):
            self.store.save_state(intent.event_id,next_state)

        def exposure_check(current):
            _,report=self._entry_admission(intent,include_current=True)
            # Missing current TP/trailing is expected while establishing them.
            # Proven peer fills/old oversized SLs do not erase current entry
            # ownership. Their repair/cleanup remains a separate exact-ID task.
            blockers=[r for r in report.get("risks",[]) if
                r!="EXECUTABLE_EXIT_BUDGET_EXCEEDED"
                and not r.startswith(intent.event_id+":")
                and not r.endswith(":SL_QUANTITY_MISMATCH")
                and not r.endswith(":CLOSED_COMBO_ACTIVE_EXIT")]
            current["merged_reconcile"]={k:v for k,v in report.items() if k!="blocking_risks"}
            self.store.save_state(intent.event_id,current)
            return blockers

        result = self.executor.execute_safe(
            intent,
            resume_state=state,
            allow_new_entry=not (pending or recovery_only),
            protection_persist=persist_protection,
            exposure_check=exposure_check,
            force_close=lambda current,kind:ForceClose(self).run(intent,current,kind),
        )
        accepted = bool(result.get("entry_accepted") or result.get("ok"))
        terminal = bool(result.get("processed"))
        order_id = str(result.get("order_id") or "") or None

        # Retain the pre-entry snapshot for recovery after an ambiguous
        # positionId lookup, API outage, or Render restart.
        if result.get("force_close_epoch"):
            state=self.store.get_state(intent.event_id) or state
        persisted={**(state or {}),**self._state_payload(intent,result,terminal)}
        # Do not create a post-entry SL lifecycle. BingX manages confirmed exits.
        if not terminal:
            attempts=int((state.get("recovery") or {}).get("attempts",0))+1
            persisted["recovery"]={"attempts":attempts,"manual_review_recommended":attempts>=8,
                "auto_retry_enabled":True,
                "next_at":time.time()+min(900,15*2**min(attempts,6))}
            if attempts==8:
                self._emit("L3.EXEC.RECOVERY_STILL_PENDING","CRITICAL",
                           "recovery still pending after eight attempts; automatic exact-ID reconciliation continues with bounded backoff",intent)
            persisted["safety_status"]="RECONCILE_REQUIRED"
        failure_signature=str(result.get("stage"))+":"+str(result.get("error"))
        repeated_failure=state.get("last_failure_signature")==failure_signature
        persisted["last_failure_signature"]=failure_signature if not result.get("ok") else None
        self.store.save_state(intent.event_id,persisted)
        if terminal:
            self.store.mark(intent.event_id, result)

        if result.get("force_close_epoch"):
            # The epoch emits a bounded, dedicated system alert with every
            # affected combo. Do not also enqueue an initiator-only trade failure.
            return ExecutionResult(event_id=intent.event_id,accepted=accepted,
                status=result["stage"],order_id=order_id,details=result)

        if result.get("ok"):
            common = {
                "order_id": order_id,
                # Human-facing Discord prices must come from the Layer-2
                # TradeIntent, not from exchange fill/reconciliation fields.
                "side": intent.side,
                "entry": intent.entry,
                "tp": intent.tp,
                "sl": intent.sl,
                "client_order_id":result.get("client_order_id"),
                "avg_price": result.get("avg_price"),
                "executed_qty": result.get("executed_qty"),
                "protection":result.get("protection"),
            }
            self._emit("L3.EXEC.ORDER_SENT", "INFO", "market entry confirmed on BingX", intent, **common)
            self._emit("L3.EXEC.ORDER_FILLED", "INFO", "market entry FILLED confirmed", intent, **common)
            self._emit("L3.EXEC.SL_PLACED", "INFO", "hard stop-loss confirmed", intent, **common)
            self._emit("L3.EXEC.TP_PLACED", "INFO", "hard take-profit confirmed", intent, **common)
            if result.get("protection",{}).get("trailing"):
                self._emit("L3.EXEC.TRAILING_PLACED", "INFO", "50% trailing-stop confirmed", intent, **common)
            self._emit("L3.EXEC.ORDER_COMPLETE", "INFO", "entry and protections reconciled", intent, **common)
        else:
            stage = str(result.get("stage") or "failed")
            severity = "CRITICAL" if result.get("entry_accepted") else "ERROR"
            key = "L3.EXEC.ORDER_FAIL"

            if stage in {"protection_setup_aborted_closed","force_close_confirmed"}:
                key="L3.EXEC.EMERGENCY_CLOSE"
                severity="WARNING"
            elif stage in {"protection_abort_reconcile_required","force_close_unresolved"}:
                key="L3.EXEC.UNSAFE_OPEN_POSITION"
                severity="CRITICAL"
            elif stage in {"entry_unknown_reconcile_required","reconcile_error"}:
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
            elif stage in ("protection_trailing_failed_sl_active","partial_exit_reconcile_required"):
                key = "L3.EXEC.PROTECTION_RECONCILE_REQUIRED"
                severity = "CRITICAL"
            elif stage == "protection_tp_failed_sl_active":
                key = "L3.EXEC.TP_FAILED_SL_ACTIVE"
                severity = "ERROR"
            elif stage == "unsafe_open_position":
                key = "L3.EXEC.UNSAFE_OPEN_POSITION"
                severity = "CRITICAL"

            failure_reason = str(result.get("error") or stage)
            if repeated_failure:
                return ExecutionResult(event_id=intent.event_id,accepted=accepted,
                                       status=stage,order_id=order_id,details=result)
            self._emit(
                key,
                severity,
                failure_reason,
                intent,
                order_id=order_id,
                side=intent.side,
                entry=intent.entry,
                tp=intent.tp,
                sl=intent.sl,
                error=failure_reason,
                client_order_id=result.get("client_order_id"),
                stage=stage,
                terminal=terminal,
                entry_accepted=bool(result.get("entry_accepted")),
                entry_filled=bool(result.get("entry_filled")),
                executed_qty=result.get("executed_qty"),
                status=result.get("status"),
                sl_protected=bool(result.get("sl_protected")),
                protection=result.get("protection"),
            )

        return ExecutionResult(
            event_id=intent.event_id,
            accepted=accepted,
            status=str(result.get("stage") or ("ok" if result.get("ok") else "failed")),
            order_id=order_id,
            details=result,
        )
