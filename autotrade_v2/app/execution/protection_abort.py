"""Durable protection-failure abort, without guessing a merged combo's size.

An automatic MARKET close requires exclusive ownership of the selected exchange
container and no competing exit targeting it. A shared partial container cannot
be fenced with a REST snapshot: retain protection and require intervention.
"""
import time

from .identity import entry_baseline, position_id, verified_entry_order
from .exit_cleanup import inspect_one, oid, CleanupUncertain, positive, abort_close_quantity, ACTIVE


def abort_setup(client,intent,state,persist,close_cid,kind,leverage,gate=None):
    def save(**fields):
        value={**state,"abort":{**(state.get("abort") or {}),**fields}}
        persist(value)
        state.clear();state.update(value)

    def result(stage,error=None,closed=False):
        return {"processed":closed,"ok":False,"stage":stage,
            "entry_accepted":True,"entry_filled":True,"client_order_id":state.get("client_order_id"),
            "order_id":state.get("order_id"),"executed_qty":state.get("executed_qty"),
            "position_id":state.get("position_id"),"avg_price":state.get("avg_price"),
            "protection":state.get("protection") or {},"emergency_closed":closed,
            "close_result":({"confirmed":closed,"order_id":oid(state["abort"]["close_order"]),
                "client_order_id":state.get("emergency_close",{}).get("client_order_id"),
                "status":state["abort"]["close_order"].get("status"),
                "executed_qty":state["abort"]["close_order"].get("executedQty")}
                if state.get("abort",{}).get("close_order") else None),"error":error}

    def order_map(rows):
        if not isinstance(rows,list):
            raise CleanupUncertain("abort order snapshot missing")
        found={}
        for row in rows:
            key=oid(row)
            if not key or key in found or row.get("symbol")!=intent.symbol:
                raise CleanupUncertain("abort order snapshot ambiguous")
            found[key]=row
        return found

    def observe():
        opened=order_map(client.get_open_orders(intent.symbol))
        positions=client.get_positions(intent.symbol)
        own_ids={str(v["order_id"]) for v in (state.get("protection") or {}).values() if v}
        missing=own_ids-set(opened)
        history={}
        if missing:
            now=int(time.time()*1000)
            history=order_map(client.get_order_history(intent.symbol,now-6*86400_000,now,required_ids=missing))
        check={**state,"protection":state.get("protection") or {},
            "exit_cleanup":{**(state.get("exit_cleanup") or {}),"status":"watching"}}
        monitor,_,_=inspect_one(None,check,opened,positions,historical_by_id=history,allow_incomplete=True)
        if not monitor.get("fills_verified"):
            raise CleanupUncertain("owned exit history incomplete; no abort close")
        return monitor,opened,entry_baseline(positions,intent.symbol,intent.side),own_ids

    save(phase=(state.get("abort") or {}).get("phase") or "required",failed_kind=kind)
    try:
        verified_entry_order(state["verified_entry"],intent.symbol,intent.side,
                             state["client_order_id"],state["order_id"])
        for item in (state.get("protection_journal") or {}).values():
            if item.get("phase")!="rejected":
                raise CleanupUncertain("uncertain accepted protection POST; no abort close")
        pid=str(state["position_id"])
        journal=state.get("emergency_close") or {}
        # Query an already prepared CID first. Never resubmit if it is absent:
        # absence is not evidence that a previous MARKET was rejected.
        raw=client.find_order_by_client_id(intent.symbol,close_cid)
        if raw is not None:
            row=client.extract_order(raw)
            save(close_order=row,phase="close_pending")
            abort_close_quantity(state)  # strict validation before using fills
        elif journal:
            save(phase="close_pending",error="prepared close outcome unknown; not resent")
            return result("protection_abort_reconcile_required",state["abort"]["error"])
        monitor,opened,positions,own=observe()
        remaining=positive(monitor["remaining_qty"],"owned residual")
        if remaining and raw is None:
            if gate is not None:
                blockers=gate(state)
                if blockers:
                    raise CleanupUncertain("abort exposure mismatch: "+"; ".join(blockers))
            if pid not in positions or positive(positions[pid],"container quantity")!=remaining:
                raise CleanupUncertain("shared/changed container: cannot safely close just this combo")
            for key,row in opened.items():
                same_side=str(row.get("positionSide") or "").upper()==("LONG" if intent.side=="BUY" else "SHORT")
                target=position_id(row)
                if same_side and target in ("",pid) and key not in own:
                    raise CleanupUncertain("competing unowned exit targets abort container")
            precision=client.get_contract_rules(intent.symbol)["quantityPrecision"]
            journal={"client_order_id":close_cid,"position_id":pid,"requested_qty":str(remaining),
                     "phase":"prepared","reason":"required_protection_exhausted","revision":0}
            prepared={**state,"emergency_close":journal}
            persist(prepared);state.clear();state.update(prepared)
            save(phase="close_prepared")
            try:
                client.close_market(intent.symbol,intent.side,remaining,leverage,
                    client_order_id=close_cid,position_id=pid,quantity_precision=precision)
            except Exception:
                pass  # read by exact CID below; never repeat an uncertain POST
            raw=client.find_order_by_client_id(intent.symbol,close_cid)
            if raw is None:
                save(phase="close_pending",error="close POST not confirmed; not resent")
                return result("protection_abort_reconcile_required",state["abort"]["error"])
            save(close_order=client.extract_order(raw),phase="close_pending")
            abort_close_quantity(state)
            monitor,opened,positions,own=observe()
            remaining=positive(monitor["remaining_qty"],"owned residual")
        close_row=(state.get("abort") or {}).get("close_order")
        if remaining or (close_row and str(close_row.get("status") or "").upper() in ACTIVE):
            save(phase="close_pending",error="abort close is partial/active; no duplicate MARKET")
            return result("protection_abort_reconcile_required",state["abort"]["error"])
        # Own verified fills prove closure; do not use a missing ID as proof.
        save(phase="cleanup")
        for key in own & set(opened):
            attempts=dict((state.get("abort") or {}).get("cancel_attempts") or {})
            if int(attempts.get(key,0))>=2:
                raise CleanupUncertain("abort cleanup cancel attempts exhausted")
            attempts[key]=int(attempts.get(key,0))+1
            save(cancel_attempts=attempts)  # WAL before exact-ID DELETE
            client.cancel_order(intent.symbol,order_id=key)
        monitor,opened,positions,own=observe()
        if own & set(opened):
            raise CleanupUncertain("owned protection cancel not yet verified")
        state["exit_cleanup"]=monitor
        save(phase="closed",error=None)
        return result("protection_setup_aborted_closed",closed=True)
    except Exception as exc:
        save(phase="manual",error=f"{type(exc).__name__}: {exc}")
        return result("protection_abort_reconcile_required",state["abort"]["error"])
