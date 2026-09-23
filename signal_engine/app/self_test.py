from __future__ import annotations

import json
import math
import os
import tempfile
import time

import pandas as pd

from .config import Settings
from .executor import execution_prices
from .final14_executor import Final14Executor
from .final14_config import FINAL14_CASES, case_name, enabled_combos
from .final14_exact_strategy import combo_readiness, hard_tp_sl, scan_latest
from .final14_positions import register_execution, snapshot as final14_position_snapshot
from .state import SignalState
from .strategy import Signal


def _synthetic_15m(count: int = 3400) -> pd.DataFrame:
    """Deterministic closed 15m candles; never touches network."""
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
    """Offline FINAL14 validation. Never calls BingX, Discord, or order endpoints."""
    m15 = _synthetic_15m(3400)
    checks: list[dict] = []

    def readiness_check():
        details = {}
        for symbol in ("BTC-USDT", "ETH-USDT"):
            readiness = combo_readiness(m15, symbol=symbol)
            expected = list(enabled_combos(symbol))
            ready = sorted(k for k, v in readiness.items() if v.get("ready"))
            skipped = sorted(k for k, v in readiness.items() if not v.get("ready"))
            assert ready == expected, f"{symbol} readiness mismatch: {ready} != {expected}"
            assert skipped == [], f"{symbol} unexpectedly skipped {skipped}"
            details[symbol] = {
                "ready": [case_name(x) for x in ready],
                "skipped": [],
                "bars_15m": len(m15),
            }
        return details

    checks.append(_check("final14_readiness", readiness_check))

    def strategy_check():
        result = {}
        for symbol in ("BTC-USDT", "ETH-USDT"):
            sigs = scan_latest(symbol=symbol, m15=m15)
            allowed = set(enabled_combos(symbol))
            for sig in sigs:
                assert sig.symbol == symbol
                assert sig.combo in allowed
                assert sig.side in {"BUY", "SELL"}
                assert sig.timeframe in {"15m", "1h"}
                assert sig.entry > 0 and sig.tp > 0 and sig.sl > 0
                if sig.side == "BUY":
                    assert sig.sl < sig.entry < sig.tp
                else:
                    assert sig.tp < sig.entry < sig.sl
            result[symbol] = {
                "enabled": [case_name(x) for x in enabled_combos(symbol)],
                "latest_signal_count": len(sigs),
                "signal_ids": [s.event_id for s in sigs],
            }
        return result

    checks.append(_check("final14_strategy_pipeline", strategy_check))

    def tp_sl_check():
        entry = 10_000.0
        checked = 0
        for symbol, cases in FINAL14_CASES.items():
            for combo, cfg in cases.items():
                long_tp, long_sl = hard_tp_sl(entry, 1, cfg)
                short_tp, short_sl = hard_tp_sl(entry, -1, cfg)
                assert long_sl < entry < long_tp
                assert short_tp < entry < short_sl
                assert math.isclose(long_tp, entry * (1.0 + float(cfg["tp_pct"])), rel_tol=0, abs_tol=1e-9)
                assert math.isclose(long_sl, entry * (1.0 - float(cfg["sl_pct"])), rel_tol=0, abs_tol=1e-9)
                checked += 1
        return {
            "cases_checked": checked,
            "exit_mode": "100% hard TP + 100% hard SL; no trailing; no partial exit",
        }

    checks.append(_check("final14_hard_tp_sl", tp_sl_check))

    cfg = FINAL14_CASES["BTC-USDT"][1]
    entry = 30_000.0
    tp, sl = hard_tp_sl(entry, 1, cfg)
    buy = Signal(
        symbol="BTC-USDT",
        combo=1,
        side="BUY",
        timeframe="1h",
        close_time=1_700_003_599_999,
        entry=entry,
        tp=tp,
        sl=sl,
        smc_dir=1,
    )

    def payload_check():
        ex = Final14Executor(settings)
        payload = ex.build_payload(buy, 100.0)
        e, p, s = execution_prices(buy, settings)
        assert e == buy.entry and p == buy.tp and s == buy.sl
        assert payload["entry"] == buy.entry
        assert payload["tp"] == buy.tp
        assert payload["sl"] == buy.sl
        assert payload["source"] == "bingx-final14-hardtp"
        assert payload["exit_mode"] == "100pct_hard_tp_sl"
        assert payload["usdt_amount"] == 100.0
        ex.validate_order_payload(payload)
        return {
            "combo": case_name(buy.combo),
            "entry": payload["entry"],
            "tp": payload["tp"],
            "sl": payload["sl"],
            "target_notional_usdt": settings.order_margin_usdt * settings.leverage,
            "leverage": settings.leverage,
            "exit_mode": payload["exit_mode"],
        }

    checks.append(_check("final14_execution_payload", payload_check))

    def execution_contract_check():
        class FakeFinal14Executor(Final14Executor):
            def __init__(self, cfg):
                super().__init__(cfg)
                self.order_params = None

            def _assert_hedge_mode(self):
                return None

            def _assert_separate_isolated(self, symbol):
                return None

            def _set_leverage(self, symbol, side, leverage):
                assert leverage == 50
                return {"code": 0}

            def _contract_rules(self, symbol):
                return {
                    "quantity_precision": 4,
                    "price_precision": 2,
                    "min_qty": 0.0001,
                    "min_usdt": 1.0,
                    "max_long_leverage": 125,
                    "max_short_leverage": 125,
                    "status": 1,
                    "api_state_open": "true",
                    "api_state_close": "true",
                }

            def _positions(self, symbol):
                return []

            def _signed_trade_request(self, method, request_path, params):
                if request_path.endswith("/order") and method == "POST":
                    self.order_params = dict(params)
                    return {"code": 0, "data": {"orderID": "o1", "status": "FILLED"}}
                raise AssertionError((method, request_path, params))

            def _order_detail(self, symbol, order_id):
                return {
                    "code": 0,
                    "data": {
                        "orderID": "o1",
                        "status": "FILLED",
                        "executedQty": "0.0033",
                        "avgPrice": "30000.0",
                        "positionId": "p1",
                    },
                }

        ex = FakeFinal14Executor(settings)
        result = ex._execute_direct_bingx(buy)
        assert result["ok"] is True
        assert result["processed"] is True
        assert result["entry_accepted"] is True
        assert result["entry_filled"] is True
        assert result["position_id"] == "p1"
        assert result["target_notional"] == 100.0
        assert result["execution_leverage"] == 50
        assert result["protection_mode"] == "attached_hard_tp_sl"

        params = ex.order_params
        assert params is not None
        assert params["type"] == "MARKET"
        assert "activationPrice" not in params
        assert "priceRate" not in params

        sl_order = json.loads(params["stopLoss"])
        tp_order = json.loads(params["takeProfit"])
        assert sl_order["type"] == "STOP_MARKET"
        assert tp_order["type"] == "TAKE_PROFIT_MARKET"
        assert sl_order["workingType"] == "CONTRACT_PRICE"
        assert tp_order["workingType"] == "CONTRACT_PRICE"
        assert float(sl_order["stopPrice"]) == round(buy.sl, 2)
        assert float(tp_order["stopPrice"]) == round(buy.tp, 2)

        return {
            "notional_usdt": result["target_notional"],
            "leverage": result["execution_leverage"],
            "position_id": result["position_id"],
            "protection_mode": result["protection_mode"],
            "partial_exit": False,
            "trailing": False,
        }

    checks.append(_check("final14_execution_contract", execution_contract_check))

    def state_check():
        fd, db_path = tempfile.mkstemp(prefix="signal-selftest-", suffix=".db")
        os.close(fd)
        try:
            st = SignalState(db_path)
            target = "bingx_account:selftest"
            assert not st.seen(buy.event_id, target)
            st.mark(buy.event_id, target, '{"ok":true}')
            assert st.seen(buy.event_id, target)

            sample = m15.iloc[:12].copy()
            inserted = st.upsert_candles("BTC-USDT", "15m", sample)
            assert inserted == 12
            assert st.candle_count("BTC-USDT", "15m") == 12
            loaded = st.load_candles("BTC-USDT", "15m")
            assert len(loaded) == 12
            st.trim_candles("BTC-USDT", "15m", 5)
            assert st.candle_count("BTC-USDT", "15m") == 5

            st.set_runtime_value("selftest_key", "ok")
            assert st.get_runtime_value("selftest_key") == "ok"

            register_execution(
                st,
                buy,
                {
                    "order_id": "o1",
                    "position_id": "p1",
                    "avg_price": buy.entry,
                    "executed_qty": 0.0033,
                    "tp": buy.tp,
                    "sl": buy.sl,
                    "rr": cfg["rr"],
                },
            )
            active = final14_position_snapshot(st)
            assert len(active) == 1
            assert active[0]["case_id"] == "BTC-USDT|C1"
            assert active[0]["position_id"] == "p1"

            return {
                "dedupe": True,
                "candle_upsert": True,
                "trim": True,
                "runtime_state": True,
                "one_position_per_combo_state": True,
            }
        finally:
            try:
                os.unlink(db_path)
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
