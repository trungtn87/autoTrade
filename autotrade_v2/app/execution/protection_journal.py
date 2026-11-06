"""Crash-safe write-ahead journal for BingX conditional exit orders.

BingX USD-M does not document clientOrderId for STOP_MARKET,
TAKE_PROFIT_MARKET or TRAILING_STOP_MARKET. Never retransmit a possibly
accepted conditional order after a timeout or worker crash.

This journal deliberately prefers a visible, persistent safety alert over
silently creating an extra stop/TP/trailing order. A journal with no matching
exchange order is NOT proof that no POST was accepted (or later triggered).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from ..control.errors import BingXApiError


class ProtectionReconcilePending(RuntimeError):
    """Submission may have reached BingX; cannot establish one owned order."""


class ProtectionAttemptsExhausted(RuntimeError):
    def __init__(self,kind):
        self.kind=kind
        super().__init__(f"{kind}: two definitively rejected protection POSTs; abort required")


# Only documented parameter/symbol/margin rejections, from the actual POST.
# Never classify throttles, network/server errors or verification GET errors.
# Source: github.com/BingX-API/api-ai-skills/blob/main/skills/references/error-codes.md
DEFINITIVE_REJECTIONS={"101204","109400","109425"}


class ProtectionJournal:
    TYPES={"sl":"STOP_MARKET","tp":"TAKE_PROFIT_MARKET","trailing":"TRAILING_STOP_MARKET"}

    def __init__(self,client,state:dict,persist,verify,before_retry=None):
        self.client=client
        self.state=state
        self.persist=persist
        self.verify=verify
        self.before_retry=before_retry

    @staticmethod
    def _number(value)->str:
        try:
            value=Decimal(str(value))
            if not value.is_finite() or value<=0:
                raise ValueError("quantity or trigger must be positive")
            return format(value.normalize(),"f")
        except (InvalidOperation,TypeError) as exc:
            raise ValueError("invalid protection quantity or trigger") from exc

    def _candidates(self,spec:dict)->list[dict]:
        """Snapshot all exact candidates, never choose an arbitrary LONG order."""
        symbol=spec["symbol"]
        rows=self.client.get_open_orders(symbol)
        found=[]
        for row in rows:
            if str(row.get("type") or "").upper()!=self.TYPES[spec["kind"]]:
                continue
            if str(row.get("side") or "").upper()!=spec["side"]:
                continue
            if str(row.get("positionSide") or "").upper()!=spec["position_side"]:
                continue
            if str(row.get("status") or "NEW").upper() not in {"NEW","PARTIALLY_FILLED"}:
                continue
            if spec["position_id"] and row.get("positionId") and str(row["positionId"])!=spec["position_id"]:
                continue
            try:
                qty=self._number(row.get("origQty") or row.get("quantity"))
                if qty!=spec["quantity"]:
                    continue
                if spec["kind"]=="trailing":
                    if self._number(row.get("activationPrice"))!=spec["trigger"]:
                        continue
                    if self._number(row.get("priceRate"))!=spec["callback_rate"]:
                        continue
                elif self._number(row.get("stopPrice"))!=spec["trigger"]:
                    continue
            except (ValueError,TypeError):
                continue
            oid=str(row.get("orderID") or row.get("orderId") or "")
            if not oid:
                raise ProtectionReconcilePending("matching BingX protection missing orderId")
            found.append(row)
        return found

    def _persist(self,next_state:dict)->None:
        # Fail before POST if Supabase cannot durably store the prepared intent.
        self.persist(next_state)
        self.state.clear()
        self.state.update(next_state)

    def _confirm(self,kind:str,raw:dict,position_id:str|None)->dict:
        data=raw.get("data") or {}
        row=data.get("order") or data
        acknowledged=str(row.get("orderID") or row.get("orderId") or "")
        if not acknowledged:
            raise ProtectionReconcilePending(f"{kind}: response lacks exact order ID")
        next_state={**self.state}
        journal=dict(next_state.get("protection_journal") or {})
        prepared=dict(journal.get(kind) or {})
        prepared["acknowledged_order_id"]=acknowledged
        journal[kind]=prepared
        next_state["protection_journal"]=journal
        self._persist(next_state)  # acknowledgement is durable before verification
        verified=self.verify(raw,kind,position_id)
        next_state={**self.state}
        protection=dict(next_state.get("protection") or {})
        protection[kind]=verified
        next_state["protection"]=protection
        attempts=dict(next_state.get("protection_attempts") or {})
        attempts[kind]={"posts":attempts.get(kind,{}).get("posts",1),**attempts.get(kind,{}),"verdict":"verified"}
        next_state["protection_attempts"]=attempts
        journal=dict(next_state.get("protection_journal") or {})
        journal.pop(kind,None)
        next_state["protection_journal"]=journal
        self._persist(next_state)
        return verified

    def place(
        self,kind:str,symbol:str,entry_side:str,qty,trigger,
        position_id:str|None,send,callback_rate=None,
    )->dict:
        entry_side=entry_side.upper()
        spec={
            "kind":kind,
            "symbol":symbol,
            "side":"SELL" if entry_side=="BUY" else "BUY",
            "position_side":"LONG" if entry_side=="BUY" else "SHORT",
            "quantity":self._number(qty),
            "trigger":self._number(getattr(self.client,"_fmt_price",lambda value:value)(trigger)),
            "position_id":str(position_id or ""),
        }
        if kind=="trailing":
            spec["callback_rate"]=self._number(callback_rate)
        if kind not in self.TYPES:
            raise ValueError("unknown protection kind")
        journal=(self.state.get("protection_journal") or {}).get(kind)
        if journal is not None and journal.get("spec")!=spec:
            raise ProtectionReconcilePending(f"{kind}: prepared order parameters changed across restart")
        attempts=(self.state.get("protection_attempts") or {}).get(kind) or {}
        posts=attempts.get("posts",1 if journal else 0)
        if isinstance(posts,bool) or not isinstance(posts,int) or posts not in (0,1,2):
            raise ProtectionReconcilePending(f"{kind}: invalid persisted POST budget; no submission")
        if journal is not None and journal.get("phase")=="rejected":
            if posts>=2:
                raise ProtectionAttemptsExhausted(kind)
            if self.before_retry is not None:
                self.before_retry()
            journal=None  # proof of rejection permits exactly one new POST
        if journal is not None:
            if journal.get("spec")!=spec:
                raise ProtectionReconcilePending(f"{kind}: prepared order parameters changed across restart")
            # Never re-POST a conditional order whose status is uncertain.
            candidates=self._candidates(spec)
            previous=set(map(str,journal.get("baseline_ids") or []))
            delta=[o for o in candidates if str(o.get("orderID") or o.get("orderId")) not in previous]
            acknowledged=str(journal.get("acknowledged_order_id") or "")
            # Price/quantity/position equality is not ownership: a manual order
            # or another combo may have identical parameters during a timeout.
            delta=[o for o in delta if str(o.get("orderID") or o.get("orderId"))==acknowledged]
            if not acknowledged or len(delta)!=1:
                raise ProtectionReconcilePending(
                    f"{kind}: unresolved conditional POST, {len(delta)} new exact matches; "
                    "manual/exchange reconciliation required, no duplicate sent"
                )
            return self._confirm(kind,{"code":0,"data":{"order":delta[0]}},position_id)

        if posts>=2:
            raise ProtectionAttemptsExhausted(kind)
        # Read the BEFORE snapshot while exchange state is still unmodified.
        before=self._candidates(spec)
        ids=[str(o.get("orderID") or o.get("orderId")) for o in before]
        next_state={**self.state}
        next_journal=dict(next_state.get("protection_journal") or {})
        next_journal[kind]={"spec":spec,"baseline_ids":ids,"phase":"prepared"}
        next_state["protection_journal"]=next_journal
        all_attempts=dict(next_state.get("protection_attempts") or {})
        all_attempts[kind]={"posts":posts+1,"verdict":"prepared"}
        next_state["protection_attempts"]=all_attempts
        self._persist(next_state)
        try:
            raw=send()
        except Exception as exc:
            if (isinstance(exc,BingXApiError) and exc.code in DEFINITIVE_REJECTIONS
                    and exc.endpoint=="/openApi/swap/v2/trade/order"
                    and str(exc.params.get("type") or "").upper()==self.TYPES[kind]
                    and exc.params.get("symbol")==symbol):
                rejected={**self.state}
                journal_copy=dict(rejected.get("protection_journal") or {})
                journal_copy[kind]={**journal_copy[kind],"phase":"rejected","code":exc.code}
                rejected["protection_journal"]=journal_copy
                attempt_copy=dict(rejected.get("protection_attempts") or {})
                attempt_copy[kind]={"posts":posts+1,"verdict":"rejected","code":exc.code}
                rejected["protection_attempts"]=attempt_copy
                self._persist(rejected)
                return self.place(kind,symbol,entry_side,qty,trigger,position_id,send,callback_rate)
            try:
                return self.place(kind,symbol,entry_side,qty,trigger,position_id,send,callback_rate)
            except ProtectionReconcilePending as pending:
                raise ProtectionReconcilePending(f"{pending}; original POST failure: {type(exc).__name__}: {exc}") from exc
        try:
            return self._confirm(kind,raw,position_id)
        except Exception as exc:
            # POST response lost / verification timeout / DB write failure.
            # A single unambiguous new order can be recovered; otherwise no resend.
            try:
                return self.place(kind,symbol,entry_side,qty,trigger,position_id,send,callback_rate)
            except ProtectionReconcilePending as recon_exc:
                raise ProtectionReconcilePending(f"{recon_exc}; original failure: {type(exc).__name__}: {exc}") from exc
