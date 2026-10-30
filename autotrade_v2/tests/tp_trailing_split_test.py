"""Opt-in 50/50 fixed-TP/trailing protection tests; never calls the exchange."""
from dataclasses import replace
from decimal import Decimal

from app.config import Settings
from app.execution.final14_executor import Final14Executor
from app.execution.executor import LegacyStyleBingXClient
from app.execution.quantity import allocate_exit_qty,confirmed_position_qty,remaining_position_qty,PositionQuantityMismatch

from legacy_execution_test import FakeClient, intent


class FakeTrailingClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_trailing=False
        self.trailing_match=None
        self.position_qty="1"
        self.position_id="p-1"
        self.contract_rules={
            "symbol":"BTC-USDT","quantityPrecision":4,
            "tradeMinQuantity":"0.0001","tradeMinUSDT":"2",
        }

    def get_positions(self,symbol):
        self.calls.append(("positions",symbol))
        return [{"symbol":symbol,"positionSide":"LONG",
                 "positionAmt":self.position_qty,"availableAmt":self.position_qty,
                 "positionId":self.position_id}]

    def get_contract_rules(self,symbol):
        self.calls.append(("contracts",symbol))
        return self.contract_rules

    def place_take_profit(self,symbol,entry_side,qty,tp,quantity_precision=None,position_id=None):
        # Same fake exchange behavior, while accepting validated quantity precision.
        return super().place_take_profit(symbol,entry_side,qty,tp)

    def place_trailing_stop(self,symbol,entry_side,qty,activation_price,callback_rate,quantity_precision=None,position_id=None):
        self.calls.append(("trailing",symbol,entry_side,qty,activation_price,callback_rate))
        if self.fail_trailing:
            raise RuntimeError("simulated trailing post failure")
        payload={"code":0,"data":{"order":{
            "orderId":"trail-1","status":"NEW","executedQty":"0","avgPrice":"0",
        }}}
        self.by_order["trail-1"]=payload
        return payload

    def find_matching_trailing(self,symbol,entry_side,qty,activation_price,callback_rate,position_id=None):
        self.calls.append(("find_trailing",qty,activation_price,callback_rate))
        return self.trailing_match


def main():
    settings=replace(
        Settings(),
        tp_trailing_split_enabled=True,
        tp_trailing_callback_rate=0.005,
        dry_run=False,
        execution_enabled=True,
        bingx_api_key="test",
        bingx_api_secret="test",
    )
    executor=Final14Executor(settings)
    fake=FakeTrailingClient()
    executor.client=fake
    result=executor.execute_safe(intent())
    assert result["ok"] and result["stage"]=="complete",result
    assert result["protection_mode"]=="half_hard_tp_half_trailing_full_sl",result
    assert result["exit_mode"]=="split_trailing",result
    assert Decimal(result["tp_qty"])==Decimal("0.5") and Decimal(result["trailing_qty"])==Decimal("0.5"),result
    assert result["actual_position_qty"]=="1" and result["position_id"]=="p-1",result
    assert ("positions","BTC-USDT") in fake.calls and ("contracts","BTC-USDT") in fake.calls
    assert result["trailing_activation_price"]==100.5,result
    assert result["trailing_activation_pct"]==0.005,result
    assert result["trailing_callback_rate"]==0.005,result
    assert [x for x in fake.calls if x[0]=="trailing"][0][4]==100.5,fake.calls
    assert Final14Executor.trailing_activation_price(100.0,"SELL",0.005)==99.5
    assert Final14Executor.trailing_activation_price(100.0,"BUY",0.005)==100.5
    assert result["tp"]==102.0,result  # hard TP independent of trailing activation
    assert result["protection"]["trailing"]["order_id"]=="trail-1",result
    placements=[x for x in fake.calls if x[0] in {"sl","tp","trailing"}]
    assert [x[0] for x in placements]==["sl","tp","trailing"],placements
    assert placements[0][3]==1.0,placements  # hard SL always spans entry
    assert Decimal(str(placements[1][3]))==Decimal("0.5") and Decimal(str(placements[2][3]))==Decimal("0.5"),placements

    before=len(placements)
    resumed=executor.execute_safe(
        intent(),
        resume_state={"exit_mode":"split_trailing","protection":result["protection"]},
        allow_new_entry=False,
    )
    assert resumed["ok"],resumed
    assert len([x for x in fake.calls if x[0] in {"sl","tp","trailing"}])==before

    lost=FakeTrailingClient()
    lost.fail_trailing=True
    executor.client=lost
    failed=executor.execute_safe(intent())
    assert not failed["ok"] and not failed["processed"],failed
    assert failed["stage"]=="protection_trailing_failed_sl_active",failed
    assert failed["sl_protected"] is True
    assert failed["protection"]["tp"] is not None
    assert failed["protection"]["sl"] is not None

    match={"code":0,"data":{"order":{
        "orderId":"trail-lost","status":"NEW",
    }}}
    lost.by_order["trail-lost"]=match
    lost.trailing_match=match
    recovered=executor.execute_safe(
        intent(),
        resume_state=failed,
        allow_new_entry=False,
    )
    assert recovered["ok"] and recovered["protection"]["trailing"]["order_id"]=="trail-lost",recovered

    rules={"quantityPrecision":4,"tradeMinQuantity":"0.0001","tradeMinUSDT":"0"}
    exact=allocate_exit_qty("0.0015",rules,"100")
    assert exact["position_qty"]=="0.0015",exact
    assert exact["tp_qty"]=="0.0008" and exact["trailing_qty"]=="0.0007",exact
    assert exact["mode"]=="split_trailing",exact
    one_tick=allocate_exit_qty("0.0001",rules,"100")
    assert one_tick["mode"]=="hard_tp" and one_tick["tp_qty"]=="0.0001"
    assert allocate_exit_qty("0.0002",rules,"100")["trailing_qty"]=="0.0001"
    min_notional=allocate_exit_qty("0.0015",{**rules,"tradeMinUSDT":"1"},"100")
    assert min_notional["mode"]=="hard_tp" and min_notional["trailing_qty"]=="0"
    min_qty=allocate_exit_qty("0.0015",{**rules,"tradeMinQuantity":"0.001"},"100")
    assert min_qty["mode"]=="hard_tp"
    precision3=allocate_exit_qty("0.013",{"quantityPrecision":3,"tradeMinQuantity":"0.001","tradeMinUSDT":"0"},"100")
    assert precision3["tp_qty"]=="0.007" and precision3["trailing_qty"]=="0.006"
    try:
        allocate_exit_qty("0.00151",rules,"100")
        assert False,"unrepresentable quantity must fail closed"
    except ValueError:
        pass
    pos,position_id=confirmed_position_qty(
        [{"symbol":"BTC-USDT","positionSide":"LONG","positionAmt":"0.0015","positionId":"p-a"}],
        "BTC-USDT","BUY","0.0015",
    )
    assert pos=="0.0015" and position_id=="p-a"
    try:
        confirmed_position_qty(
            [{"symbol":"BTC-USDT","positionSide":"LONG","positionAmt":"0.003","positionId":"p-a"}],
            "BTC-USDT","BUY","0.0015",
        )
        assert False,"aggregated same-side position must be rejected"
    except PositionQuantityMismatch:
        pass

    # After any partial close SL must use a NEW snapshot, not a local subtraction.
    current=[{"symbol":"BTC-USDT","positionSide":"LONG",
              "positionAmt":"0.0008","positionId":"p-a"}]
    assert remaining_position_qty(current,"BTC-USDT","BUY","p-a")=="0.0008"
    assert remaining_position_qty([],"BTC-USDT","BUY","p-a")=="0"
    try:
        remaining_position_qty(current,"BTC-USDT","BUY","other-id")
        assert False,"cannot confuse another position with tracked SL position"
    except PositionQuantityMismatch:
        pass

    mismatch=FakeTrailingClient()
    mismatch.position_qty="0.9"
    executor.client=mismatch
    mismatch_result=executor.execute_safe(intent())
    assert mismatch_result["stage"]=="position_quantity_mismatch_sl_active",mismatch_result
    assert mismatch_result["sl_protected"] and not mismatch_result["processed"]
    assert not any(x[0] in {"tp","trailing"} for x in mismatch.calls),mismatch.calls

    tiny=FakeTrailingClient()
    tiny.contract_rules={**rules,"tradeMinUSDT":"100"}
    executor.client=tiny
    fallback=executor.execute_safe(intent())
    assert fallback["ok"] and fallback["exit_mode"]=="hard_tp",fallback
    assert Decimal(fallback["tp_qty"])==Decimal("1") and Decimal(fallback["trailing_qty"])==Decimal("0"),fallback
    assert not any(x[0]=="trailing" for x in tiny.calls)

    settings_old=replace(settings,tp_trailing_split_enabled=False)
    legacy=Final14Executor(settings_old)
    old_client=FakeTrailingClient()
    legacy.client=old_client
    old=legacy.execute_safe(intent())
    assert old["protection_mode"]=="separate_hard_tp_sl",old
    assert not any(x[0]=="trailing" for x in old_client.calls)

    # Inspect native BingX order parameters without sending an HTTP request.
    transport=LegacyStyleBingXClient(settings)
    captured=[]
    transport._signed_request=lambda method,path,params: captured.append((method,path,params)) or {"code":0}
    transport.place_trailing_stop("BTC-USDT","BUY",0.5,102,0.005)
    long_params=captured[-1][2]
    assert long_params["side"]=="SELL" and long_params["positionSide"]=="LONG",long_params
    assert long_params["type"]=="TRAILING_STOP_MARKET"
    assert long_params["activationPrice"]=="102"
    assert long_params["priceRate"]=="0.005"
    transport.place_trailing_stop("ETH-USDT","SELL",0.5,98,0.005)
    short_params=captured[-1][2]
    assert short_params["side"]=="BUY" and short_params["positionSide"]=="SHORT",short_params

    print({"ok":True,"split":"50/50","activation":"entry +/- 0.5%","callback":0.005,
           "sl_full":True,"restart_dedupe":True,"lost_response_recovery":True})


if __name__=="__main__":
    main()
