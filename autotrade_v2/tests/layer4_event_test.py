from __future__ import annotations

import os
import tempfile
import time
from dataclasses import replace

from app.config import Settings
from app.contracts import TradeIntent
from app.control.events import EventReporter
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore


class FakeExecutor:
    @staticmethod
    def client_order_id(event_id,purpose="entry"):
        return f"fake-{purpose}-{event_id}"[:40]

    def execute_safe(self, intent, resume_state=None, allow_new_entry=True):
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
                "sl":{"order_id":"sl-1","status":"NEW"},
                "tp":{"order_id":"tp-1","status":"NEW"},
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

    print({"ok":True,"layer4_queue":True,"execution_event_keys":expected})


if __name__=="__main__":
    main()
