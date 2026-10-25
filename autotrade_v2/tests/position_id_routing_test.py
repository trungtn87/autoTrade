"""Offline positionId binding/regression: no BingX HTTP requests."""
from dataclasses import replace
import os
import tempfile

from app.config import Settings
from app.execution.executor import LegacyStyleBingXClient
from app.execution.identity import entry_baseline,identify_entry_position
from app.execution.quantity import PositionQuantityMismatch
from app.execution.final14_executor import Final14Executor
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore
from tp_trailing_split_test import FakeTrailingClient
from legacy_execution_test import intent


def pos(pid,amount):
    return {"symbol":"BTC-USDT","positionSide":"LONG",
            "positionId":str(pid),"positionAmt":str(amount)}


def raises_mismatch(callback):
    try:
        callback()
    except PositionQuantityMismatch:
        return
    raise AssertionError("expected ambiguous position identity to fail closed")


def main():
    baseline=entry_baseline([pos(101,"1"),pos(102,"2")],"BTC-USDT","BUY")
    assert baseline=={"101":"1","102":"2"}
    # One NEW Separate Isolated position, existing combos untouched.
    new=[pos(101,"1"),pos(102,"2"),pos(103,"0.5")]
    assert identify_entry_position(new,"BTC-USDT","BUY","0.5",baseline)=="103"
    # Existing regular Hedge position increases by precisely the trade's fill.
    assert identify_entry_position([pos(101,"1.5")],"BTC-USDT","BUY","0.5",{"101":"1"})=="101"
    raises_mismatch(lambda:identify_entry_position(
        [pos(101,"1"),pos(102,"2")],"BTC-USDT","BUY","0.5",baseline
    ))
    raises_mismatch(lambda:identify_entry_position(
        [pos(101,"1"),pos(102,"2"),pos(103,"0.5"),pos(104,"0.5")],
        "BTC-USDT","BUY","0.5",baseline,
    ))
    raises_mismatch(lambda:identify_entry_position(
        [pos(101,"1"),pos(102,"2")],"BTC-USDT","BUY","0.5",
        baseline,entry_position_id="999",
    ))
    raises_mismatch(lambda:identify_entry_position(
        [pos(101,"1"),pos(102,"2")],"BTC-USDT","BUY","0.5",
    ))

    settings=replace(Settings(),bingx_api_key="test",bingx_api_secret="test")
    client=LegacyStyleBingXClient(settings)
    sent=[]
    client._signed_request=lambda method,path,params: sent.append((method,path,params)) or {"code":0}
    client.place_stop_loss("BTC-USDT","BUY",0.5,99,position_id="103")
    client.place_take_profit("BTC-USDT","BUY",0.25,102,quantity_precision=4,position_id="103")
    client.place_trailing_stop("BTC-USDT","BUY",0.25,102,0.005,quantity_precision=4,position_id="103")
    client.close_market("BTC-USDT","BUY",0.5,50,client_order_id="atv2close",position_id="103")
    assert len(sent)==4
    assert [x[2]["type"] for x in sent]==[
        "STOP_MARKET","TAKE_PROFIT_MARKET","TRAILING_STOP_MARKET","MARKET"
    ]
    assert all(x[2].get("positionId")=="103" for x in sent)
    assert sent[-1][2]["positionSide"]=="LONG" and sent[-1][2]["side"]=="SELL"
    for invalid in ("",):  # Empty ID only allowed for regular Hedge compatibility.
        client.place_stop_loss("BTC-USDT","BUY",0.5,99,position_id=invalid)
        assert "positionId" not in sent[-1][2]
    before=len(sent)
    for invalid in ("0","abc","1&side=BUY","-1","1.0","\\n2"):
        try:
            client.place_stop_loss("BTC-USDT","BUY",0.5,99,position_id=invalid)
            raise AssertionError("invalid positionId was signed")
        except ValueError:
            pass
    assert len(sent)==before
    # BingX openOrders responses may omit positionId. Only an exact and unique
    # order match is safe to recover from an ambiguous POST response.
    rows=[{"orderId":"sl-103","symbol":"BTC-USDT","side":"SELL",
           "positionSide":"LONG","type":"STOP_MARKET","status":"NEW",
           "origQty":"0.5","stopPrice":"99"}]
    client._signed_request=lambda method,path,params: {"code":0,"data":list(rows)}
    recovered=client.find_matching_protection(
        "BTC-USDT","BUY",0.5,99,"STOP_MARKET",position_id="103"
    )
    assert recovered["data"]["order"]["orderId"]=="sl-103"
    rows.append({**rows[0],"orderId":"sl-another"})
    try:
        client.find_matching_protection(
            "BTC-USDT","BUY",0.5,99,"STOP_MARKET",position_id="103"
        )
        raise AssertionError("duplicate matching SL should fail closed")
    except RuntimeError:
        pass
    rows[0]["positionId"]="999"
    rows.pop()
    assert client.find_matching_protection(
        "BTC-USDT","BUY",0.5,99,"STOP_MARKET",position_id="103"
    ) is None
    client.http.close()

    # Simulate the live 50/50 path, including durable pre-entry snapshot.
    live=replace(settings,tp_trailing_split_enabled=True,execution_enabled=True,dry_run=False)
    execution=Final14Executor(live)
    fake=FakeTrailingClient()
    fake.position_id="103"
    execution.client=fake
    result=execution.execute_safe(
        intent(),resume_state={"pre_entry_positions":{},"exit_mode":"split_trailing"}
    )
    assert result["ok"],result
    assert result["position_id"]=="103",result

    # Ambiguous identity: never place an unbound SL, TP or trailing.
    uncertain=FakeTrailingClient()
    uncertain.position_id="103"
    uncertain.position_qty="1"
    def two_positions(symbol):
        return [pos("201","1"),pos("202","1")]
    uncertain.get_positions=two_positions
    execution.client=uncertain
    blocked=execution.execute_safe(
        intent(),resume_state={"pre_entry_positions":{},"exit_mode":"split_trailing"}
    )
    assert blocked["stage"]=="position_identification_pending",blocked
    assert not any(x[0] in {"sl","tp","trailing","close"} for x in uncertain.calls)

    # Emergency close with a confirmed positionId sends the unique identifier.
    closing=FakeTrailingClient()
    closing.position_id="303"
    closing.position_qty="1"
    def close_with_id(*args,**kwargs):
        assert kwargs.get("position_id")=="303",kwargs
        return FakeTrailingClient.close_market(closing,*args,**kwargs)
    closing.close_market=close_with_id
    execution.client=closing
    risk=execution._confirm_market_close(
        intent(),1.0,"test",resume_state={"pre_entry_positions":{}}
    )
    assert risk["confirmed"],risk

    # Crash-safe DB state: ambiguous post-entry ID must retain its BEFORE snapshot
    # and must not submit another MARKET entry during recovery.
    class RecoveryFake(FakeTrailingClient):
        def __init__(self):
            super().__init__()
            self.position_qty="0"
            self.position_id="301"
            self.ambiguous=True

        def get_positions(self,symbol):
            if self.position_qty=="0":
                return []
            return [pos("301","1"),pos("302","1")] if self.ambiguous else [pos("301","1")]

        def place_market_entry(self,*args,**kwargs):
            result=super().place_market_entry(*args,**kwargs)
            self.position_qty="1"
            return result

    fd,db_path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store=ExecutionStore(db_path)
        fake_db=RecoveryFake()
        service=ExecutionService(
            replace(live,position_lifecycle_enabled=True),store,
        )
        service.credentials_verified=True
        service.executor.client=fake_db
        first=service.execute(intent())
        assert first.status=="position_identification_pending",first
        state=store.get_state(intent().event_id)
        assert state["pre_entry_positions"]=={},state
        assert state["terminal"] is False
        assert len([c for c in fake_db.calls if c[0]=="entry"])==1
        fake_db.ambiguous=False
        restarted=ExecutionService(
            replace(live,position_lifecycle_enabled=True),store,
        )
        restarted.credentials_verified=True
        restarted.executor.client=fake_db
        replay=restarted.recover_pending()
        assert replay and replay[0]["status"]=="complete",replay
        state=store.get_state(intent().event_id)
        assert state["position_id"]=="301" and state["pre_entry_positions"]=={}
        assert len([c for c in fake_db.calls if c[0]=="entry"])==1
    finally:
        os.unlink(db_path)

    print({"ok":True,"separate_isolated_id_proof":True,"aggregated_hedge_proof":True,
           "ambiguous_fail_closed":True,"all_exit_types_bound":True,
           "emergency_close_bound":True,"restart_recovery":True})


if __name__=="__main__":
    main()
