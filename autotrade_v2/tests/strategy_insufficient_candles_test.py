from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from app.contracts import MarketSnapshot
from app.control.errors import StrategyInsufficientCandlesError
from app.orchestrator import Orchestrator
from app.strategy.engine import StrategyEngine


def main() -> None:
    snapshot=MarketSnapshot(
        symbol="BTC-USDT",
        timeframe="15m",
        candles=pd.DataFrame(index=range(3399)),
        latest_open_time=0,
        latest_close_time=0,
        validation={"ok":True},
    )

    try:
        StrategyEngine().calculate(snapshot)
        raise AssertionError("3399 candles must fail Layer 2 readiness")
    except StrategyInsufficientCandlesError as exc:
        assert exc.category=="STRATEGY_INSUFFICIENT_CANDLES"
        assert exc.available==3399
        assert exc.required==3400
        assert exc.missing==1

    class ShortData:
        def ingest_closed_candle(self, symbol, candle):
            raise StrategyInsufficientCandlesError(symbol,available=3399,required=3400)

    class NoExecution:
        def execute(self, intent):
            raise AssertionError("execution must not run")

    events=[]
    def capture(key,severity,message,**kwargs):
        events.append((key,severity,message,kwargs))

    orchestrator=Orchestrator(
        SimpleNamespace(execution_enabled=False),
        ShortData(),
        StrategyEngine(),
        NoExecution(),
        event_cb=capture,
    )
    result=orchestrator.on_closed_candle("BTC-USDT",pd.DataFrame([{"x":1}]))

    assert result["ok"] is False
    assert result["stage"]=="strategy_readiness"
    assert result["category"]=="STRATEGY_INSUFFICIENT_CANDLES"
    assert len(events)==1
    key,severity,message,kwargs=events[0]
    assert key=="L2.FINAL14.INSUFFICIENT_CANDLES"
    assert severity=="ERROR"
    assert kwargs["symbol"]=="BTC-USDT"
    assert kwargs["details"]=={
        "stage":"strategy_readiness",
        "category":"STRATEGY_INSUFFICIENT_CANDLES",
        "available":3399,
        "required":3400,
        "missing":1,
        "timeframe":"15m",
    }

    print({
        "ok":True,
        "event_key":key,
        "category":result["category"],
        "available":3399,
        "required":3400,
    })


if __name__=="__main__":
    main()
