"""Opt-in 50/50 fixed-TP/trailing protection tests; never calls the exchange."""
from dataclasses import replace

from app.config import Settings
from app.execution.final14_executor import Final14Executor
from app.execution.executor import LegacyStyleBingXClient

from legacy_execution_test import FakeClient, intent


class FakeTrailingClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_trailing=False
        self.trailing_match=None

    def place_trailing_stop(self,symbol,entry_side,qty,activation_price,callback_rate):
        self.calls.append(("trailing",symbol,entry_side,qty,activation_price,callback_rate))
        if self.fail_trailing:
            raise RuntimeError("simulated trailing post failure")
        payload={"code":0,"data":{"order":{
            "orderId":"trail-1","status":"NEW","executedQty":"0","avgPrice":"0",
        }}}
        self.by_order["trail-1"]=payload
        return payload

    def find_matching_trailing(self,symbol,entry_side,qty,activation_price,callback_rate):
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
    assert result["tp_qty"]==0.5 and result["trailing_qty"]==0.5,result
    assert result["trailing_activation_price"]==102.0,result
    assert result["trailing_callback_rate"]==0.005,result
    assert result["protection"]["trailing"]["order_id"]=="trail-1",result
    placements=[x for x in fake.calls if x[0] in {"sl","tp","trailing"}]
    assert [x[0] for x in placements]==["sl","tp","trailing"],placements
    assert placements[0][3]==1.0,placements  # hard SL always spans entry
    assert placements[1][3]==0.5 and placements[2][3]==0.5,placements

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

    assert executor._split_exit_qty(1.2345)==(0.6173,0.6172)
    assert executor._split_exit_qty(0.0001) is None
    assert executor._split_exit_qty(0.0002)==(0.0001,0.0001)

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

    print({"ok":True,"split":"50/50","activation":"old TP","callback":0.005,
           "sl_full":True,"restart_dedupe":True,"lost_response_recovery":True})


if __name__=="__main__":
    main()
