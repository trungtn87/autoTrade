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


class ProtectionReconcilePending(RuntimeError):
    """Submission may have reached BingX; cannot establish one owned order."""


class ProtectionJournal:
    TYPES={"sl":"STOP_MARKET","tp":"TAKE_PROFIT_MARKET","trailing":"TRAILING_STOP_MARKET"}

    def __init__(self,client,state:dict,persist,verify):
        self.client=client
        self.state=state
        self.persist=persist
        self.verify=verify

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
        verified=self.verify(raw,kind,position_id)
        next_state={**self.state}
        protection=dict(next_state.get("protection") or {})
        protection[kind]=verified
        next_state["protection"]=protection
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
            "trigger":self._number(trigger),
            "position_id":str(position_id or ""),
        }
        if kind=="trailing":
            spec["callback_rate"]=self._number(callback_rate)
        if kind not in self.TYPES:
            raise ValueError("unknown protection kind")
        journal=(self.state.get("protection_journal") or {}).get(kind)
        if journal is not None:
            if journal.get("spec")!=spec:
                raise ProtectionReconcilePending(f"{kind}: prepared order parameters changed across restart")
            # Never re-POST a conditional order whose status is uncertain.
            candidates=self._candidates(spec)
            previous=set(map(str,journal.get("baseline_ids") or []))
            delta=[o for o in candidates if str(o.get("orderID") or o.get("orderId")) not in previous]
            if len(delta)!=1:
                raise ProtectionReconcilePending(
                    f"{kind}: unresolved conditional POST, {len(delta)} new exact matches; "
                    "manual/exchange reconciliation required, no duplicate sent"
                )
            return self._confirm(kind,{"code":0,"data":{"order":delta[0]}},position_id)

        # Read the BEFORE snapshot while exchange state is still unmodified.
        before=self._candidates(spec)
        ids=[str(o.get("orderID") or o.get("orderId")) for o in before]
        next_state={**self.state}
        next_journal=dict(next_state.get("protection_journal") or {})
        next_journal[kind]={"spec":spec,"baseline_ids":ids,"phase":"prepared"}
        next_state["protection_journal"]=next_journal
        self._persist(next_state)
        try:
            raw=send()
            return self._confirm(kind,raw,position_id)
        except Exception as exc:
            # POST response lost / verification timeout / DB write failure.
            # A single unambiguous new order can be recovered; otherwise no resend.
            try:
                return self.place(kind,symbol,entry_side,qty,trigger,position_id,send,callback_rate)
            except ProtectionReconcilePending as recon_exc:
                raise ProtectionReconcilePending(f"{recon_exc}; original failure: {type(exc).__name__}: {exc}") from exc
