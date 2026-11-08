"""Offline regression: OBSERVE cannot modify real TradeIntents or execution."""
import unittest
from dataclasses import asdict
from unittest.mock import Mock

import pandas as pd

from app.ai.observer import (
    GeminiObserver, _candles_at_signal, _resample_complete,
    _validated_output, build_pre_context,
)
from app.contracts import TradeIntent, MarketSnapshot, ExecutionResult
from app.orchestrator import Orchestrator


class AiObserverTests(unittest.TestCase):
    def setUp(self):
        self.intent = TradeIntent(
            event_id="BTC-USDT|1h|C4|BUY|3599999",
            symbol="BTC-USDT",combo=4,side="BUY",timeframe="1h",
            close_time=3599999,entry=100,tp=102,sl=99,smc_dir=0,
        )
        self.frame = pd.DataFrame([{
            "open_time": i*900000, "close_time":(i+1)*900000-1,
            "open":99.0+i/1000,"high":101.0+i/1000,
            "low":98.0+i/1000,"close":100.0+i/1000,"volume":100+i
        } for i in range(260)])

    def test_future_candles_not_visible(self):
        ctx1=build_pre_context(self.intent,self.frame)
        mutated=self.frame.copy()
        mutated.loc[mutated["open_time"]>=3600000,"high"] = 1e9
        ctx2=build_pre_context(self.intent,mutated)
        self.assertEqual(ctx1,ctx2)
        self.assertEqual(len(ctx1["candles"]["15m"]),4)
        self.assertEqual(len(ctx1["candles"]["1h"]),1)
        self.assertEqual(len(ctx1["candles"]["4h"]),0)

    def test_resampling_excludes_incomplete(self):
        d=self.frame.iloc[:5]
        h=_resample_complete(d,1)
        self.assertEqual(len(h),1)
        self.assertEqual(h.iloc[0]["close_time"],3599999)
        missing=self.frame.iloc[[0,1,3,4]]
        self.assertEqual(len(_resample_complete(missing,1)),0)

    def test_ai_evidence_must_exist(self):
        context={"features":[{"id":"15m-SMC"}]}
        _validated_output({"decision":"BLOCK","risk_flags":[{"evidence_ids":["15m-SMC"]}]},context)
        with self.assertRaisesRegex(ValueError,"unverified_evidence"):
            _validated_output({"decision":"BLOCK","risk_flags":[{"evidence_ids":["made-up"]}]},context)

    def test_observer_retries_503_once_and_not_other_errors(self):
        from app.ai.observer import GeminiClient
        first = Mock(status_code=503)
        last = Mock(status_code=200)
        last.json.return_value = {"candidates": [], "usageMetadata": {}}
        transport = Mock(side_effect=[first, last])
        with patch("app.ai.observer.sleep") as delay:
            response, usage, ms = GeminiClient("secret", "gemini-3.6-flash", transport=transport).ask("test")
        self.assertEqual(transport.call_count, 2)
        delay.assert_called_once()
        self.assertIsInstance(ms, int)
        self.assertEqual(response["usageMetadata"], {})
        auth = Mock(status_code=401)
        auth.raise_for_status.side_effect = __import__("requests").HTTPError("401")
        transport2 = Mock(return_value=auth)
        with self.assertRaises(__import__("requests").HTTPError):
            GeminiClient("secret", "gemini-3.6-flash", transport=transport2).ask("test")
        transport2.assert_called_once()

    def test_no_key_no_background_work(self):
        observer=GeminiObserver("",enabled=True,key="")
        observer.start()
        snap=MarketSnapshot("BTC-USDT","15m",self.frame,0,0,{})
        self.assertFalse(observer.submit([self.intent],snap))

    def test_observe_only_submit_does_not_change_execution(self):
        # The observer cannot return an ALLOW/BLOCK policy to the caller.
        observer=GeminiObserver("",enabled=True,client=Mock(),key="")
        observer._thread=Mock()
        observer._thread.is_alive.return_value=True
        snap=MarketSnapshot("BTC-USDT","15m",self.frame,0,0,{})
        intent_copy=asdict(self.intent)
        self.assertTrue(observer.submit([self.intent],snap))
        self.assertEqual(asdict(self.intent),intent_copy)
        intents,frame=observer._queue.get_nowait()
        self.assertEqual(intents,(self.intent,))
        frame.iloc[0,frame.columns.get_loc("close")]=12345
        self.assertNotEqual(frame.iloc[0]["close"],self.frame.iloc[0]["close"])

    def test_ai_hypothetical_block_never_reaches_executor(self):
        snapshot=MarketSnapshot("BTC-USDT","15m",self.frame,0,3599999,{})
        data=Mock()
        data.ingest_closed_candle.return_value=snapshot
        strategy=Mock()
        strategy.calculate.return_value=[self.intent]
        execution=Mock()
        execution.execute.return_value=ExecutionResult(self.intent.event_id,True,"accepted")
        observer=Mock()
        observer.submit.return_value="BLOCK"
        orch=Orchestrator(Mock(execution_enabled=True),data,strategy,execution,
                          observer=observer)
        result=orch.on_closed_candle("BTC-USDT",self.frame.iloc[-1:])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["execution"]),1)
        observer.submit.assert_called_once()
        execution.execute.assert_called_once_with(self.intent)


if __name__=="__main__":
    unittest.main()
