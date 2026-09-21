from __future__ import annotations

import math
import os
import tempfile
import time

import numpy as np
import pandas as pd

from .config import Settings
from .executor import Executor
from .final_config import FINAL_CASES, DISABLED_CASES, enabled_combos, snapshot as final_snapshot
from .final_strategy import (
    FIFTEEN_MIN_COMBOS,
    ONE_HOUR_COMBOS,
    Signal,
    combo_readiness,
    scan_latest,
)
from .indicators import atr, dmi, ema, rsi
from .state import SignalState
from .timeframes import aggregate_15m


def _synthetic_15m(count: int = 3400) -> pd.DataFrame:
    start = 1_700_000_000_000
    step = 15 * 60_000
    twelve_h = 12 * 60 * 60_000
    start = (start // twelve_h) * twelve_h
    rows = []
    prev_close = 30_000.0
    for i in range(count):
        t = start + i * step
        drift = i * 1.75
        wave = 180.0 * math.sin(i / 17.0) + 65.0 * math.sin(i / 5.0)
        close = 30_000.0 + drift + wave
        open_ = prev_close
        high = max(open_, close) + 35.0 + (i % 7)
        low = min(open_, close) - 32.0 - (i % 5)
        volume = 100.0 + (i % 23) * 3.0 + abs(math.sin(i / 8.0)) * 40.0
        rows.append({
            "open_time": t, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume, "close_time": t + step - 1,
        })
        prev_close = close
    return pd.DataFrame(rows)


def _check(name: str, fn) -> dict:
    started = time.monotonic()
    try:
        details = fn() or {}
        return {"ok": True, "name": name, "elapsed_ms": round((time.monotonic()-started)*1000,2), "details": details}
    except Exception as exc:
        return {"ok": False, "name": name, "elapsed_ms": round((time.monotonic()-started)*1000,2), "error": f"{type(exc).__name__}: {exc}"}


def run_startup_self_test(settings: Settings, state=None) -> dict:
    checks=[]

    def config_light():
        assert sum(len(v) for v in FINAL_CASES.values()) == 18
        assert sum(len(v) for v in DISABLED_CASES.values()) == 4
        assert enabled_combos("BTC-USDT") == (1,2,3,4,6,7,9,10,11)
        assert enabled_combos("ETH-USDT") == (1,2,4,5,6,7,8,10,11)
        return final_snapshot()
    checks.append(_check("final18_config", config_light))

    def aggregation_light():
        sample=_synthetic_15m(96)
        h1=aggregate_15m(sample,60); h4=aggregate_15m(sample,240); h6=aggregate_15m(sample,360)
        assert (len(h1),len(h4),len(h6)) == (24,6,4)
        return {"15m":96,"1h":len(h1),"4h":len(h4),"6h":len(h6)}
    checks.append(_check("aggregation_light", aggregation_light))

    def readiness_light():
        frames=[
            pd.DataFrame(index=range(3400)),
            pd.DataFrame(index=range(850)),
            pd.DataFrame(index=range(212)),
            pd.DataFrame(index=range(141)),
        ]
        details={}
        for symbol in ("BTC-USDT","ETH-USDT"):
            status=combo_readiness(*frames,smc_swing_len=settings.smc_swing_len,symbol=symbol)
            ready={c for c,x in status.items() if x["ready"]}
            assert ready == set(enabled_combos(symbol))
            details[symbol]=sorted(ready)
        return details
    checks.append(_check("final18_readiness_light", readiness_light))

    if state is not None:
        def state_read_light():
            counts={symbol:state.candle_count(symbol,"15m") for symbol in settings.symbols}
            return {"backend":state.backend,"persistent":state.backend=="postgres","cached_15m":counts}
        checks.append(_check("state_read_light", state_read_light))

    return {"ok":all(x["ok"] for x in checks),"offline":True,"mode":"startup_light","network_calls":0,"order_calls":0,"checks":checks}


def run_self_test(settings: Settings) -> dict:
    m15=_synthetic_15m(3400)
    h1=aggregate_15m(m15,60); h4=aggregate_15m(m15,240); h6=aggregate_15m(m15,360)
    checks=[]

    def aggregation_check():
        assert len(h1)==len(m15)//4
        assert len(h4)==len(m15)//16
        assert len(h6)==len(m15)//24
        return {"15m":len(m15),"1h":len(h1),"4h":len(h4),"6h":len(h6)}
    checks.append(_check("aggregation",aggregation_check))

    def indicators_check():
        vals={"ema200":ema(m15["close"],200).iloc[-1],"atr21":atr(m15,21).iloc[-1],"rsi14":rsi(m15["close"],14).iloc[-1],"adx14":dmi(m15,14,14)[2].iloc[-1]}
        assert all(np.isfinite(float(v)) for v in vals.values())
        return {k:round(float(v),6) for k,v in vals.items()}
    checks.append(_check("indicators",indicators_check))

    def strategy_check():
        details={}
        for symbol in ("BTC-USDT","ETH-USDT"):
            status=combo_readiness(m15,h1,h4,h6,smc_swing_len=settings.smc_swing_len,symbol=symbol)
            ready={c for c,x in status.items() if x["ready"]}
            assert ready == set(enabled_combos(symbol))
            sigs=scan_latest(
                symbol=symbol,m15=m15,h1=h1,h4=h4,h6=h6,
                smc_swing_len=settings.smc_swing_len,
                smc_confluence=settings.smc_confluence,
                include_1h=True,
            )
            assert all(s.combo in enabled_combos(symbol) for s in sigs)
            assert all(s.side in {"BUY","SELL"} and s.entry>0 and s.sl>0 for s in sigs)
            details[symbol]={"ready":sorted(ready),"latest_signal_count":len(sigs)}
        return details
    checks.append(_check("final18_strategy_pipeline",strategy_check))

    def payload_check():
        ex=Executor(settings)
        buy=Signal(symbol="BTC-USDT",combo=1,side="BUY",timeframe="1h",close_time=1_700_000_899_999,entry=30123.45,tp=31629.62,sl=29852.34,smc_dir=1)
        p=ex.build_payload(buy,100.0)
        assert p["combo"]=="C1"
        assert p["exit_mode"]=="two_tier_trailing_50_50_no_fixed_tp"
        assert p["final_case"]["stage"]=="L3"
        assert "tp" not in p
        assert p["symbol"]=="BTC-USDT"
        return {"combo":p["combo"],"exit_mode":p["exit_mode"],"stage":p["final_case"]["stage"]}
    checks.append(_check("final18_execution_payload",payload_check))

    def split_check():
        a,b=Executor._split_exit_quantities(0.004,3)
        assert np.isclose(a+b,0.004)
        assert a>0 and b>0
        return {"leg1":a,"leg2":b}
    checks.append(_check("two_leg_split",split_check))

    def state_check():
        fd,path=tempfile.mkstemp(prefix="final18-selftest-",suffix=".db"); os.close(fd)
        try:
            st=SignalState(path)
            st.set_runtime_value("final18_test","ok")
            assert st.get_runtime_value("final18_test")=="ok"
            sample=m15.iloc[:12].copy()
            assert st.upsert_candles("BTC-USDT","15m",sample)==12
            assert st.candle_count("BTC-USDT","15m")==12
            return {"runtime_state":True,"candle_upsert":True}
        finally:
            try: os.unlink(path)
            except FileNotFoundError: pass
    checks.append(_check("state_db",state_check))

    return {"ok":all(x["ok"] for x in checks),"offline":True,"network_calls":0,"order_calls":0,"checks":checks}
