"""Emergency whole-container shutdown, serialized by ExecutionStore.trading_lock.

All exchange mutations follow durable intents. Ambiguous POSTs are read by CID,
never repeated. Native exits must be terminal before a MARKET can be submitted.
The epoch is authoritative even if a crash interrupts per-combo finalization.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import time

from .identity import entry_baseline, position_id, verified_entry_order
from .exit_cleanup import ACTIVE, TERMINAL, CleanupUncertain, inspect_one, oid, positive, validate_order

TARGET="bingx_force_close"


def side_key(symbol, side):
    return f"force-close|{symbol}|{side}"


class ForceClose:
    def __init__(self, service):
        self.service=service
        self.store=service.store
        self.client=service.executor.client

    def current(self, intent):
        return self.store.get(side_key(intent.symbol,intent.side),TARGET)

    def run(self, intent, state, kind):
        key=side_key(intent.symbol,intent.side)
        epoch=self.current(intent)
        if not epoch or epoch.get("phase")=="closed":
            epoch={"epoch":hashlib.sha256(f"{key}|{intent.event_id}".encode()).hexdigest()[:24],
                   "phase":"requested","symbol":intent.symbol,"side":intent.side,
                   "position_id":str(state["position_id"]),"initiator":intent.event_id,
                   "failed_kind":kind,"closes":[],"cancels":{},"affected":[],"created_at":time.time()}
            self.store.upsert(key,epoch,TARGET)

        def save(**fields):
            epoch.update(fields)
            self.store.upsert(key,epoch,TARGET)

        def result():
            closed=epoch["phase"]=="closed"
            return {"processed":closed,"ok":False,"entry_accepted":True,"entry_filled":True,
                "stage":"force_close_confirmed" if closed else "force_close_unresolved",
                "order_id":state.get("order_id"),"client_order_id":state.get("client_order_id"),
                "executed_qty":state.get("executed_qty"),"position_id":epoch["position_id"],
                "protection":state.get("protection") or {},"emergency_closed":closed,
                "force_close_epoch":epoch["epoch"],"error":epoch.get("error"),
                "affected_combos":epoch["affected"],"remaining_exposure":epoch.get("remaining_qty"),
                "close_orders":deepcopy(epoch["closes"])}

        def maps(rows):
            return self.service._order_map(rows,intent.symbol)

        try:
            self.store.assert_trading_owner()
            # Enumerate every tracked filled combo in this container, including
            # terminal COMPLETE peers. No history rows are overwritten.
            states={eid:s for eid,s in self.store.list_states()
                    if (s.get("intent") or {}).get("symbol")==intent.symbol
                    and (s.get("intent") or {}).get("side")==intent.side
                    and str(s.get("position_id"))==epoch["position_id"] and s.get("entry_filled")
                    and (not s.get("force_close_allocation") or s.get("force_close_epoch")==epoch["epoch"])}
            if not states or intent.event_id not in states:
                raise CleanupUncertain("emergency initiator/entry evidence missing")
            save(affected=sorted(states))
            owners={}
            for eid,s in states.items():
                verified_entry_order(s["verified_entry"],intent.symbol,intent.side,s["client_order_id"],s["order_id"])
                for k,item in (s.get("protection") or {}).items():
                    order_id=str((item or {}).get("order_id") or "")
                    if not order_id or order_id in owners:
                        raise CleanupUncertain("emergency exit ownership ambiguous")
                    owners[order_id]=(eid,k)
                for k,item in (s.get("protection_journal") or {}).items():
                    if item.get("phase")=="rejected":
                        continue
                    acknowledged=str(item.get("acknowledged_order_id") or "")
                    if not acknowledged:
                        raise CleanupUncertain("unknown protective POST outcome; intervention required")
                    if acknowledged in owners and owners[acknowledged]!=(eid,k):
                        raise CleanupUncertain("acknowledged exit ownership conflict")
                    owners[acknowledged]=(eid,k)
                    s.setdefault("protection",{})[k]={"order_id":acknowledged}

            def close_rows():
                total=Decimal(0)
                seen=set()
                for item in epoch["closes"]:
                    raw=self.client.find_order_by_client_id(intent.symbol,item["client_order_id"])
                    if raw is None:
                        raise CleanupUncertain("prepared emergency MARKET outcome unknown; never resent")
                    row=self.client.extract_order(raw)
                    clients={str(row[k]) for k in ("clientOrderId","clientOrderID") if row.get(k)}
                    if (not oid(row) or oid(row) in seen or clients!={item["client_order_id"]}
                            or row.get("symbol")!=intent.symbol or position_id(row)!=epoch["position_id"]
                            or str(row.get("side","")).upper()!=("SELL" if intent.side=="BUY" else "BUY")
                            or str(row.get("positionSide","")).upper()!=("LONG" if intent.side=="BUY" else "SHORT")
                            or str(row.get("type","")).upper()!="MARKET"
                            or str(row.get("status","")).upper() not in ACTIVE|TERMINAL):
                        raise CleanupUncertain("emergency MARKET identity unverified")
                    seen.add(oid(row))
                    original=positive(row.get("origQty") or row.get("quantity"),"close quantity")
                    filled=positive(row.get("executedQty"),"close executed quantity")
                    if original!=Decimal(item["requested_qty"]) or filled>original:
                        raise CleanupUncertain("emergency MARKET quantity mismatch")
                    previous=item.get("order")
                    if previous and (oid(previous)!=oid(row) or filled<Decimal(previous["executedQty"])):
                        raise CleanupUncertain("emergency MARKET evidence regressed")
                    item["order"]=row
                    total+=filled
                save(closes=epoch["closes"])
                return total

            def observe():
                opened=maps(self.client.get_open_orders(intent.symbol))
                positions=self.client.get_positions(intent.symbol)
                containers=entry_baseline(positions,intent.symbol,intent.side)
                if any(pid!=epoch["position_id"] for pid in containers):
                    raise CleanupUncertain("emergency container changed or external same-side exposure")
                actual=Decimal(containers.get(epoch["position_id"],"0"))
                save(remaining_qty=str(actual),active_exit_ids=sorted(owners.keys() & opened.keys()))
                wanted="LONG" if intent.side=="BUY" else "SHORT"
                emergency_ids={oid(x["order"]) for x in epoch["closes"] if x.get("order")}
                for order_id,row in opened.items():
                    if str(row.get("positionSide","")).upper()!=wanted:
                        continue
                    if order_id in emergency_ids:
                        continue
                    if order_id not in owners:
                        raise CleanupUncertain("unowned/manual same-side order; no cancellation or MARKET")
                    eid,k=owners[order_id]
                    validate_order(states[eid],k,row)
                missing=owners.keys()-opened.keys()
                history={}
                if missing:
                    now=int(time.time()*1000)
                    history=maps(self.client.get_order_history(intent.symbol,now-6*86400_000,now,required_ids=set(missing)))
                residuals={}
                for eid,s in states.items():
                    # Recompute native fills; the epoch's MARKET fills are
                    # counted once at container level, then allocated below.
                    check={**s,"protection":s.get("protection") or {},"exit_cleanup":{"status":"watching"}}
                    check.pop("force_close_allocation",None)
                    monitor,_,_=inspect_one(None,check,opened,positions,historical_by_id=history,allow_incomplete=True)
                    if not monitor.get("fills_verified"):
                        raise CleanupUncertain("native exit/trigger history incomplete")
                    residuals[eid]=positive(monitor["remaining_qty"],"native residual")
                return actual,opened,residuals

            if epoch["closes"]:
                market_filled=close_rows()
            else:
                market_filled=Decimal(0)
            actual,opened,residuals=observe()
            if sum(residuals.values())-market_filled!=actual:
                raise CleanupUncertain("exchange quantity and exact fills disagree; possible stale/manual change")
            # Cancel TP/trailing before SL, retaining coverage as long as
            # possible. Every exact DELETE intent is persisted before sending.
            for order_id,(eid,k) in sorted(owners.items(),key=lambda x:x[1][1]=="sl"):
                if order_id not in opened:
                    continue
                count=int(epoch["cancels"].get(order_id,0))
                if count>=2:
                    raise CleanupUncertain("emergency cancel budget exhausted")
                epoch["cancels"][order_id]=count+1
                save(phase="canceling",cancels=epoch["cancels"])
                self.store.assert_trading_owner()
                try:
                    self.client.cancel_order(intent.symbol,order_id=order_id)
                except Exception:
                    # Lost DELETE reply or trigger race: terminal history is
                    # authoritative. Never infer cancellation from the reply.
                    pass
            actual,opened,residuals=observe()
            if owners.keys() & opened.keys():
                raise CleanupUncertain("native exits still active; MARKET withheld")
            if epoch["closes"]:
                market_filled=close_rows()
            if any(str(x["order"]["status"]).upper() in ACTIVE for x in epoch["closes"]):
                raise CleanupUncertain("emergency MARKET active/partial; no duplicate close")
            if sum(residuals.values())-market_filled!=actual:
                raise CleanupUncertain("pre-MARKET position/fill mismatch")
            if actual:
                if len(epoch["closes"])>=3:
                    raise CleanupUncertain("emergency residual close budget exhausted")
                precision=int(self.client.get_contract_rules(intent.symbol)["quantityPrecision"])
                if actual.quantize(Decimal(1).scaleb(-precision))!=actual:
                    raise CleanupUncertain("actual quantity exceeds contract precision")
                revision=len(epoch["closes"])
                cid="fc_"+hashlib.sha256(f"{epoch['epoch']}|{revision}".encode()).hexdigest()[:32]
                item={"client_order_id":cid,"requested_qty":str(actual),"revision":revision}
                epoch["closes"].append(item)
                save(phase="close_prepared",closes=epoch["closes"])
                self.store.assert_trading_owner()
                try:
                    self.client.close_market(intent.symbol,intent.side,actual,50,
                        client_order_id=cid,position_id=epoch["position_id"],quantity_precision=precision)
                except Exception:
                    pass
                market_filled=close_rows()
                actual,opened,residuals=observe()
            if (actual or owners.keys() & opened.keys()
                    or any(str(x["order"]["status"]).upper() in ACTIVE for x in epoch["closes"])
                    or sum(residuals.values())!=market_filled):
                raise CleanupUncertain("emergency closure not proven by zero exposure and attributable fills")
            # Epoch finalization precedes individual states: crash midway is
            # recoverable and still blocks admission until all states commit.
            save(phase="finalizing",allocations={eid:str(q) for eid,q in residuals.items()},error=None)
            for eid,s in states.items():
                s.update(terminal=True,ok=False,stage="force_close_confirmed",force_close_epoch=epoch["epoch"],
                    force_close_allocation={"epoch":epoch["epoch"],"quantity":str(residuals[eid]),
                        "orders":[x["order"] for x in epoch["closes"]],"position_id":epoch["position_id"]},
                    safety_status="FORCE_CLOSED",exit_cleanup={"status":"done","fills_verified":True,"remaining_qty":"0"})
                s["abort"]={**(s.get("abort") or {}),"phase":"closed","failed_kind":kind}
                self.store.save_state(eid,s)
                self.store.mark(eid,{"stage":"force_close_confirmed","epoch":epoch["epoch"]})
            save(phase="closed",closed_at=time.time(),remaining_qty="0",error=None)
            self.service._emit("L3.EXEC.FORCE_CLOSE_CONFIRMED","WARNING",
                "Emergency Force Close confirmed: merged position is zero",intent,
                side=intent.side,affected_combos=epoch["affected"],remaining_exposure="0")
            return result()
        except Exception as exc:
            # A lost owner cannot write a stale verdict or make another API
            # mutation. The next owner resumes the durable epoch.
            self.store.assert_trading_owner()
            save(phase="unresolved",error=f"{type(exc).__name__}: {exc}",next_at=time.time()+60)
            now=time.time()
            if now-float(epoch.get("last_alert_at",0))>=300:
                save(last_alert_at=now)
                closes=[{"cid":x["client_order_id"],"requested_qty":x["requested_qty"],
                    "filled_qty":(x.get("order") or {}).get("executedQty"),
                    "status":(x.get("order") or {}).get("status")} for x in epoch["closes"]]
                self.service._emit("L3.EXEC.FORCE_CLOSE_UNRESOLVED","CRITICAL",
                    "Emergency Force Close unresolved: "+epoch["error"],intent,
                    side=intent.side,affected_combos=epoch["affected"],remaining_exposure=epoch.get("remaining_qty"),
                    active_exit_ids=epoch.get("active_exit_ids"),closes=closes)
            return result()
