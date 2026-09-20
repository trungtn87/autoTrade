from __future__ import annotations

import math
import os
import tempfile
import time

import numpy as np
import pandas as pd

from .config import Settings
from .executor import Executor, execution_prices
from .indicators import atr, dmi, ema, rsi
from .state import SignalState
from .strategy import (
    FIFTEEN_MIN_COMBOS,
    ONE_HOUR_COMBOS,
    Signal,
    combo15_events,
    combo60_events,
    scan_latest,
    smc_direction,
)
from .timeframes import aggregate_15m


def _synthetic_15m(count: int = 2600) -> pd.DataFrame:
    """Deterministic closed 15m candles; never touches network."""
    start = 1_700_000_000_000
    step = 15 * 60_000
    # Align to a 12H boundary, the LCM of 1H/4H/6H, so every derived
    # timeframe starts on a complete UTC bucket.
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
            "open_time": t,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": t + step - 1,
        })
        prev_close = close
    return pd.DataFrame(rows)


def _check(name: str, fn) -> dict:
    started = time.monotonic()
    try:
        details = fn() or {}
        return {
            "ok": True,
            "name": name,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "details": details,
        }
    except Exception as exc:
        return {
            "ok": False,
            "name": name,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_self_test(settings: Settings) -> dict:
    """Offline validation of processing after market-data acquisition.

    This function does NOT call BingX, Discord, or order webhooks.
    """
    m15 = _synthetic_15m()
    h1 = aggregate_15m(m15, 60)
    h4 = aggregate_15m(m15, 240)
    h6 = aggregate_15m(m15, 360)

    checks: list[dict] = []

    def aggregation_check():
        assert len(h1) == len(m15) // 4
        assert len(h4) == len(m15) // 16
        assert len(h6) == len(m15) // 24

        g = m15.iloc[:4]
        first = h1.iloc[0]
        assert float(first["open"]) == float(g.iloc[0]["open"])
        assert float(first["close"]) == float(g.iloc[-1]["close"])
        assert float(first["high"]) == float(g["high"].max())
        assert float(first["low"]) == float(g["low"].min())
        assert np.isclose(float(first["volume"]), float(g["volume"].sum()))
        return {
            "15m": len(m15),
            "1h": len(h1),
            "4h": len(h4),
            "6h": len(h6),
        }

    checks.append(_check("aggregation", aggregation_check))

    def indicators_check():
        e = ema(m15["close"], 200)
        a = atr(m15, 21)
        r = rsi(m15["close"], 14)
        _, _, adx = dmi(m15, 14, 14)
        for label, series in {
            "ema200": e,
            "atr21": a,
            "rsi14": r,
            "adx14": adx,
        }.items():
            value = float(series.iloc[-1])
            assert np.isfinite(value), f"{label} latest is not finite"
        return {
            "ema200": round(float(e.iloc[-1]), 6),
            "atr21": round(float(a.iloc[-1]), 6),
            "rsi14": round(float(r.iloc[-1]), 6),
            "adx14": round(float(adx.iloc[-1]), 6),
        }

    checks.append(_check("indicators", indicators_check))

    def strategy_check():
        ev15 = combo15_events(m15, h4)
        ev60 = combo60_events(h1, h4, h6)
        assert set(ev15) == set(FIFTEEN_MIN_COMBOS)
        assert set(ev60) == set(ONE_HOUR_COMBOS)
        for pair in list(ev15.values()) + list(ev60.values()):
            assert len(pair) == 2
            assert len(pair[0]) > 0 and len(pair[1]) > 0

        sigs = scan_latest(
            symbol="BTC-USDT",
            m15=m15,
            h1=h1,
            h4=h4,
            h6=h6,
            smc_mode=settings.smc_mode,
            smc_swing_len=settings.smc_swing_len,
            smc_confluence=settings.smc_confluence,
            sl_pct=settings.sl_pct,
            tp_pct=settings.tp_pct,
            include_1h=True,
        )
        for sig in sigs:
            assert sig.symbol == "BTC-USDT"
            assert sig.side in {"BUY", "SELL"}
            assert sig.combo in FIFTEEN_MIN_COMBOS | ONE_HOUR_COMBOS
            assert sig.tp > 0 and sig.sl > 0 and sig.entry > 0
        return {
            "15m_combo_keys": sorted(ev15),
            "1h_combo_keys": sorted(ev60),
            "latest_signal_count": len(sigs),
        }

    checks.append(_check("strategy_pipeline", strategy_check))

    def smc_check():
        smc15 = smc_direction(
            m15,
            swing_len=settings.smc_swing_len,
            confluence=settings.smc_confluence,
        )
        smc60 = smc_direction(
            h1,
            swing_len=settings.smc_swing_len,
            confluence=settings.smc_confluence,
        )
        assert len(smc15) == len(m15)
        assert len(smc60) == len(h1)
        assert set(pd.Series(smc15).dropna().astype(int).unique()).issubset({-1, 0, 1})
        assert set(pd.Series(smc60).dropna().astype(int).unique()).issubset({-1, 0, 1})
        return {
            "15m_latest": int(smc15.iloc[-1]),
            "1h_latest": int(smc60.iloc[-1]),
        }

    checks.append(_check("smc_gate", smc_check))

    buy = Signal(
        symbol="BTC-USDT",
        combo=5,
        side="BUY",
        timeframe="15m",
        close_time=1_700_000_899_999,
        entry=30_123.45,
        tp=30_454.81,
        sl=29_852.34,
        smc_dir=1,
    )
    sell = Signal(
        symbol="ETH-USDT",
        combo=4,
        side="SELL",
        timeframe="1h",
        close_time=1_700_003_599_999,
        entry=2_123.45,
        tp=2_100.10,
        sl=2_142.56,
        smc_dir=-1,
    )

    def payload_check():
        ex = Executor(settings)
        bp = ex.build_payload(buy, settings.order_usdt)
        sp = ex.build_payload(sell, settings.order_usdt)

        assert bp["side"] == "BUY"
        assert sp["side"] == "SELL"
        assert bp["combo"] == "Combo 5"
        assert sp["combo"] == "Combo 4"
        assert bp["order_type"] == "MARKET"
        assert sp["order_type"] == "MARKET"
        assert bp["usdt_amount"] == settings.order_usdt
        assert sp["usdt_amount"] == settings.order_usdt
        assert bp["signal_id"] == buy.event_id
        assert sp["signal_id"] == sell.event_id

        ex.validate_order_payload(bp)
        ex.validate_order_payload(sp)

        invalid = dict(bp)
        invalid["tp"] = invalid["entry"] - 1
        rejected = False
        try:
            ex.validate_order_payload(invalid)
        except ValueError:
            rejected = True
        assert rejected, "invalid BUY payload was not rejected"

        bentry, btp, bsl = execution_prices(buy, settings)
        sentry, stp, ssl = execution_prices(sell, settings)
        assert bp["entry"] == bentry and bp["tp"] == btp and bp["sl"] == bsl
        assert sp["entry"] == sentry and sp["tp"] == stp and sp["sl"] == ssl

        return {
            "buy": {
                "event_id": bp["signal_id"],
                "entry": bp["entry"],
                "tp": bp["tp"],
                "sl": bp["sl"],
            },
            "sell": {
                "event_id": sp["signal_id"],
                "entry": sp["entry"],
                "tp": sp["tp"],
                "sl": sp["sl"],
            },
        }

    checks.append(_check("execution_payload", payload_check))

    def state_check():
        fd, path = tempfile.mkstemp(prefix="signal-selftest-", suffix=".db")
        os.close(fd)
        try:
            st = SignalState(path)
            target = "account_1:selftest"
            assert not st.seen(buy.event_id, target)
            st.mark(buy.event_id, target, '{"ok":true}')
            assert st.seen(buy.event_id, target)

            sample = m15.iloc[:12].copy()
            inserted = st.upsert_candles("BTC-USDT", "15m", sample)
            assert inserted == 12
            assert st.candle_count("BTC-USDT", "15m") == 12
            assert st.latest_open_time("BTC-USDT", "15m") == int(sample.iloc[-1]["open_time"])
            loaded = st.load_candles("BTC-USDT", "15m")
            assert len(loaded) == 12
            st.trim_candles("BTC-USDT", "15m", 5)
            assert st.candle_count("BTC-USDT", "15m") == 5
            return {
                "dedupe": True,
                "candle_upsert": True,
                "trim": True,
            }
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    checks.append(_check("state_db_and_dedupe", state_check))

    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "offline": True,
        "network_calls": 0,
        "order_calls": 0,
        "checks": checks,
    }
