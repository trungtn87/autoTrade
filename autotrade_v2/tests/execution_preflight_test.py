from __future__ import annotations

import os
import tempfile
from dataclasses import replace

from app.config import Settings
from app.contracts import TradeIntent
from app.execution.service import ExecutionService
from app.execution.store import ExecutionStore


class GoodClient:
    def query_balance(self):
        return {"code":0,"data":[{"asset":"USDT"}]}


class BadClient:
    def query_balance(self):
        raise RuntimeError("bad credentials")


def make_intent():
    return TradeIntent(
        event_id="BTC-USDT|15m|C1|BUY|999",
        symbol="BTC-USDT",
        combo=1,
        side="BUY",
        timeframe="15m",
        close_time=999,
        entry=100.0,
        tp=102.0,
        sl=99.0,
        smc_dir=1,
    )


def main():
    fd,path=tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        settings=replace(
            Settings(),
            execution_enabled=True,
            dry_run=False,
            bingx_api_key="key",
            bingx_api_secret="secret",
        )
        service=ExecutionService(settings,ExecutionStore(path))

        bad=BadClient()
        service.executor.client=bad
        p1=service.preflight()
        assert p1["ok"] is False
        r1=service.execute(make_intent())
        assert r1.status=="credential_preflight_required"
        assert r1.details["network_called"] is False

        good=GoodClient()
        service.executor.client=good
        p2=service.preflight()
        assert p2["ok"] is True
        assert service.credentials_verified is True
        print({"ok":True,"fail_closed":True,"read_only_preflight":True})
    finally:
        os.unlink(path)


if __name__=="__main__":
    main()
