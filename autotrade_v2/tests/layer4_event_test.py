from __future__ import annotations

import os
import tempfile
import time
from dataclasses import replace
from types import SimpleNamespace

from app.config import Settings
from app.contracts import TradeIntent
import app.control.events as events_module
from app.control.events import EventReporter, SystemEvent
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore


class FakeExecutor:
    def __init__(self):
        self.client=SimpleNamespace(get_positions=lambda symbol:[],get_open_orders=lambda symbol:[],
                                    find_order_by_client_id=lambda symbol,cid:None)

    @staticmethod
    def client_order_id(event_id,purpose="entry"):
        return f"fake-{purpose}-{event_id}"[:40]

    def execute_safe(self, intent, resume_state=None, allow_new_entry=True, protection_persist=None, exposure_check=None, force_close=None):
        return {
            "ok": True,
            "processed": True,
            "entry_accepted": True,
            "entry_filled": True,
            "stage": "complete",
            "client_order_id":self.client_order_id(intent.event_id),
            "order_id": "order-123",
            "avg_price": 100.1,
            "executed_qty": 1.0,
            "tp": 102.0,
            "sl": 99.0,
            "protection":{
                "sl":{"order_id":"2001","status":"NEW"},
                "tp":{"order_id":"2002","status":"NEW"},
            },
        }


def intent():
    return TradeIntent(
        event_id="BTC-USDT|15m|C1|BUY|123",
        symbol="BTC-USDT",
        combo=1,
        side="BUY",
        timeframe="15m",
        close_time=123,
        entry=100.0,
        tp=102.0,
        sl=99.0,
        smc_dir=1,
    )


def main():
    reporter=EventReporter("",discord_enabled=False)
    reporter.emit(
        "L4.SYSTEM.TEST","INFO","queue test",
        event_id="evt",details={"api_secret":"must-redact"},
    )
    deadline=time.time()+2
    while reporter.status()["processed"]<1 and time.time()<deadline:
        time.sleep(0.02)
    assert reporter.status()["processed"]>=1
    reporter.stop()

    # Trading-critical data WARNINGs must reach the error webhook once per
    # 15m candle cycle. A second recovery attempt in the same cycle is muted,
    # while the next expected candle alerts again even if it occurs soon.
    posts=[]
    original_post=events_module.requests.post

    class FakeResponse:
        status_code=204
        text=""

    def fake_post(url,json,timeout,headers=None):
        posts.append((url,json,timeout))
        return FakeResponse()

    events_module.requests.post=fake_post
    warning_reporter=EventReporter(
        "",
        discord_enabled=True,
        webhook_error="https://discord.invalid/error",
    )
    try:
        base=dict(
            event_key="L1.DATA.CANDLE_STALE",
            layer="L1",
            severity="WARNING",
            message="latest closed 15m candle missing or invalid",
            symbol="BTC-USDT",
        )
        warning_reporter._notify(SystemEvent(
            **base,details={"expected_open_time":100},
        ).normalized())
        warning_reporter._notify(SystemEvent(
            **base,details={"expected_open_time":100},
        ).normalized())
        warning_reporter._notify(SystemEvent(
            **base,details={"expected_open_time":200},
        ).normalized())
        assert len(posts)==2,posts

        # Cleanup polling failures are logged for diagnosis, not presented as
        # failed trades in Discord. Actual unsafe cleanup remains critical.
        warning_reporter._notify(SystemEvent(
            event_key="L3.EXEC.EXIT_CLEANUP_SCAN_FAIL",
            layer="L3",severity="WARNING",
            message="read-only BingX cleanup scan unavailable; no orders changed",
            symbol="BTC-USDT",
            details={"error":"BingX error 1094000: timestamp is invalid"},
        ).normalized())
        assert len(posts)==2,posts
        warning_reporter._notify(SystemEvent(
            event_key="L3.EXEC.EXIT_CLEANUP_RISK",
            layer="L3",severity="CRITICAL",
            message="exit cleanup verification failed; no orders mutated",
            symbol="BTC-USDT",
        ).normalized())
        assert len(posts)==3,posts
        # Whole-container emergency reports must use the system/error channel,
        # with all affected combos and quantities, not a single failed-trade card.
        for key,severity in (("FORCE_CLOSE_UNRESOLVED","CRITICAL"),("FORCE_CLOSE_CONFIRMED","WARNING")):
            warning_reporter._notify(SystemEvent(
                event_key="L3.EXEC."+key,layer="L3",severity=severity,
                message="merged container emergency",symbol="BTC-USDT",
                details={"affected_combos":["C7","C16"],"remaining_exposure":"0.0013",
                         "closes":[{"requested_qty":"0.0026","filled_qty":"0.0013"}]},
            ).normalized())
            assert posts[-1][0]=="https://discord.invalid/error"
            assert all(value in posts[-1][1]['content'] for value in ('C7','C16','0.0026','0.0013'))
        assert len(posts)==5,posts
    finally:
        warning_reporter.stop()
        events_module.requests.post=original_post

    events=[]
    def capture(key,severity,message,**kwargs):
        events.append((key,severity,message,kwargs))

    fd,path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings=replace(
            Settings(),
            execution_enabled=True,
            dry_run=False,
            bingx_api_key="test",
            bingx_api_secret="test",
        )
        service=ExecutionService(settings,ExecutionStore(path),event_cb=capture)
        service.credentials_verified=True
        service.executor=FakeExecutor()
        result=service.execute(intent())
        assert result.accepted is True
        keys=[x[0] for x in events]
        expected=[
            "L3.EXEC.ORDER_REQUEST",
            "L3.EXEC.ORDER_SENT",
            "L3.EXEC.ORDER_FILLED",
            "L3.EXEC.SL_PLACED",
            "L3.EXEC.TP_PLACED",
            "L3.EXEC.ORDER_COMPLETE",
        ]
        assert keys==expected,(keys,expected)
        assert all(x[3].get("event_id")==intent().event_id for x in events)
        assert all(x[3].get("symbol")=="BTC-USDT" for x in events)
        state=service.store.get_state(intent().event_id)
        assert state and state["terminal"] is True
    finally:
        os.unlink(path)

    print({
        "ok":True,
        "layer4_queue":True,
        "stale_warning_per_cycle":True,
        "execution_event_keys":expected,
    })


if __name__=="__main__":
    main()
