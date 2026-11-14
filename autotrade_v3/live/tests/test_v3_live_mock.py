"""AutoTrade V3 live layers — MOCK tests (fake BingX, fake Discord, in-memory DB).

These tests prove code behaviour against fixtures only. They are NOT live
evidence that BingX or Discord behave this way in production.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.live.l2_strategy import L2Strategy, signal_row
from autotrade_v3.live.l3_execution import client_order_id
from autotrade_v3.live.l4_control import project_results
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.tests.fakes import FakeBingX, RecordingSender, build, sig
from autotrade_v3.live.transport import BingXApiError, SpacingGuard

ROOT = Path(__file__).resolve().parents[3]
LIVE = ROOT / "autotrade_v3" / "live"


def errors(store):
    return [r for r in store.control_logs if r["layer"] in {"L1", "L2", "L3", "L4", "L5"}]


# ---------------------------------------------------------------- L3 independence
def test_mock_two_same_direction_btc_combos_are_independent():
    _, store, client, sender, *_rest, executor = build()
    a = executor.execute(sig(combo=1, side="BUY"))
    b = executor.execute(sig(combo=7, side="BUY"))
    assert a["stage"] == b["stage"] == "protected"
    assert len(client.entry_posts) == 2
    assert a["client_order_id"] != b["client_order_id"]
    assert store.control_logs == []
    assert sender.channels().count("btc") == 2


def test_mock_old_order_with_missing_history_does_not_block_new_entry():
    _, store, client, *_rest, executor = build()
    # An old combo's execution whose BingX order cannot be found any more.
    store.executions["old|C4"] = {"id": 99, "event_id": "old|C4", "client_order_id": "v3e-old", "entry_order_id": "1",
                                  "stage": "entry_unknown", "executed_qty": None, "l4_evidence": []}
    r = executor.execute(sig(combo=2, side="BUY"))
    assert r["stage"] == "protected"
    # L3/L4 never looked at the old order.
    assert all(g["orderId"] != "1" and g["clientOrderId"] != "v3e-old" for g in client.gets)


def test_mock_old_combo_sl_failure_does_not_lose_new_signal():
    _, store, client, *_rest, executor = build()
    client.leg_mode["STOP_MARKET"] = "reject"
    first = executor.execute(sig(combo=1, side="BUY"))
    assert first["stage"] == "sl_failed"
    client.leg_mode.clear()
    second = executor.execute(sig(combo=7, side="BUY"))
    assert second["stage"] == "protected"
    assert len(client.entry_posts) == 2


# ---------------------------------------------------------------- L4 mechanism 2
def test_mock_post_ok_l4_reads_exact_entry_order():
    _, store, client, *_rest, executor = build()
    r = executor.execute(sig())
    entry_gets = [g for g in client.gets if g["orderId"] == r["entry_order_id"]]
    assert entry_gets, "L4 must GET the orderId returned by the POST"
    ev = store.executions[r["event_id"]]["l4_evidence"]
    assert any(e["mechanism"] == 1 for e in ev) and any(e["mechanism"] == 2 for e in ev)
    assert client.position_gets == ["BTC-USDT"]   # mechanism 1 evidence only


def test_mock_post_response_lost_but_get_finds_it_no_second_entry():
    _, store, client, *_rest, executor = build()
    client.entry_mode = "lost"
    r = executor.execute(sig())
    assert len(client.entry_posts) == 1
    assert r["stage"] == "protected"
    assert client.gets[0]["clientOrderId"] == client_order_id(r["event_id"], "entry")


@pytest.mark.parametrize("get_mode", ["ok", "error"])
def test_mock_get_unknown_never_assumes_absent_and_never_resends(get_mode):
    _, store, client, sender, *_rest, executor = build()
    client.entry_mode = "lost_absent"
    client.get_mode = get_mode
    r = executor.execute(sig())
    assert r["stage"] == "entry_unknown"
    assert len(client.entry_posts) == 1 and client.order_posts == []
    log = [x for x in store.control_logs if x["step"] == "entry_confirm"]
    assert len(log) == 1 and "CHƯA XÁC ĐỊNH" in log[0]["message"] and "Không gửi lại" in log[0]["message"]


def test_mock_entry_never_fills_is_unknown_not_rejected():
    _, store, client, *_rest, executor = build()
    client.entry_mode = "never_fills"
    r = executor.execute(sig())
    assert r["stage"] == "entry_unknown" and r["entry_status"] == "NEW"


def test_mock_entry_rejected_reports_bingx_code():
    _, store, client, sender, *_rest, executor = build()
    client.entry_mode = "reject"
    r = executor.execute(sig())
    assert r["stage"] == "entry_rejected"
    rec = store.control_logs[-1]
    assert rec["step"] == "entry_post" and rec["error_code"] == "101204" and "Insufficient margin" in rec["message"]
    assert sender.channels()[-1] == "error"


# ---------------------------------------------------------------- protection steps
def test_mock_entry_filled_but_sl_rejected_reports_exact_step_and_force_closes():
    _, store, client, sender, *_rest, executor = build()
    client.leg_mode["STOP_MARKET"] = "reject"
    r = executor.execute(sig())
    steps = [x["step"] for x in store.control_logs]
    assert steps == ["sl_place", "force_close"]
    assert "Entry ĐÃ KHỚP" in store.control_logs[0]["message"] and "SL THẤT BẠI" in store.control_logs[0]["message"]
    assert store.control_logs[1]["error_code"] == "FORCE_CLOSE_CONFIRMED"
    close = [p for p in client.order_posts if p["type"] == "MARKET"]
    assert len(close) == 1 and close[0]["quantity"] == r["executed_qty"]
    assert "btc" not in sender.channels()[1:]   # no success message


def test_mock_entry_filled_but_trailing_rejected_reports_trailing_step():
    _, store, client, sender, *_rest, executor = build()
    client.leg_mode["TRAILING_STOP_MARKET"] = "reject"
    r = executor.execute(sig())
    assert r["stage"] == "protection_incomplete"
    assert [x["step"] for x in store.control_logs] == ["trailing_place"]
    assert "Trailing" in store.control_logs[0]["message"]
    assert sender.channels() == ["error"]


def test_mock_sl_response_lost_is_verified_by_client_order_id_not_resent():
    _, store, client, *_rest, executor = build()
    client.leg_mode["STOP_MARKET"] = "lost"
    r = executor.execute(sig())
    assert r["stage"] == "protected"
    assert sum(1 for p in client.order_posts if p["type"] == "STOP_MARKET") == 1


def test_mock_tp_sl_are_derived_from_the_real_fill():
    _, store, client, *_rest, executor = build()
    s = sig()
    r = executor.execute(s)
    assert r["tp_price"] == pytest.approx(r["avg_price"] * 1.02)          # BTC C1 tp_pct 2.0%
    assert r["sl_price"] == pytest.approx(r["avg_price"] * (1 - 0.008))    # BTC C1 sl_pct 0.8%
    trail = [p for p in client.order_posts if p["type"] == "TRAILING_STOP_MARKET"][0]
    assert float(trail["activationPrice"]) == pytest.approx(r["avg_price"] * 1.005, abs=0.01)


def test_mock_same_event_never_gets_two_entry_posts():
    _, store, client, *_rest, executor = build()
    s = sig()
    executor.execute(s)
    again = executor.execute(s)
    assert again["stage"] == "duplicate_skipped" and len(client.entry_posts) == 1
    # A fresh process (new executor, same DB) is also stopped by the DB claim.
    *_x, executor2 = build(store=store, client=client)
    assert executor2.execute(s)["stage"] == "duplicate_skipped" and len(client.entry_posts) == 1


# ---------------------------------------------------------------- L5 routing
def test_mock_l2_signal_routing_and_l3_success_routing():
    _, store, client, sender, alert, control, _reader, executor = build()
    l2 = L2Strategy(store, control, alert, executor=None)
    l2.dispatch([sig("BTC-USDT"), sig("ETH-USDT", combo=1, side="SELL")])
    assert sender.channels() == ["btc", "eth"]
    assert sender.calls[0][1].startswith("📡 **BTC BUY · C1 | TÍN HIỆU**")
    executor.execute(sig("ETH-USDT", combo=3, side="BUY"))
    assert sender.channels()[-1] == "eth" and sender.calls[-1][1].startswith("✅ **ETH BUY · C3 | ĐẶT LỆNH THÀNH CÔNG**")
    assert set(store.signals) == {sig("BTC-USDT").event_id, sig("ETH-USDT", combo=1, side="SELL").event_id}


@pytest.mark.parametrize("layer", ["L1", "L2", "L3", "L4", "L5"])
def test_mock_any_layer_error_goes_to_control_logs_and_error_webhook(layer):
    _, store, client, sender, alert, control, *_ = build()
    control.report(layer, "step_x", "lỗi thử", symbol="BTC-USDT", event_id="E1", order_id="42", error_code="X1")
    assert len(store.control_logs) == 1 and store.control_logs[0]["layer"] == layer
    assert store.control_logs[0]["discord_status"] == "sent"
    assert sender.channels() == ["error"]


def test_mock_same_error_twice_gives_two_logs_and_two_alerts():
    _, store, client, sender, alert, control, *_ = build()
    for _ in range(2):
        control.report("L3", "sl_place", "cùng một lỗi", symbol="BTC-USDT", event_id="E1")
    assert len(store.control_logs) == 2 and sender.channels() == ["error", "error"]


def test_mock_discord_and_db_log_failure_never_block_independent_entry():
    store = MemoryStore()
    store.fail = {"insert_control_log", "claim_execution", "update_execution", "insert_signal", "append_evidence",
                  "set_discord_status"}
    sender = RecordingSender(fail=True)
    _, store, client, sender, alert, control, _reader, executor = build(store=store, sender=sender)
    client.leg_mode["TRAILING_STOP_MARKET"] = "reject"   # cause errors on the first signal
    executor.execute(sig(combo=1))
    r = executor.execute(sig(combo=7))
    assert len(client.entry_posts) == 2
    assert all(p["type"] != "MARKET" for p in client.order_posts)
    assert r["entry_order_id"] and r["sl_order_id"]
    assert control.db_failures > 0 and alert.failures > 0


def test_mock_discord_failure_is_logged_once_without_loop():
    store = MemoryStore()
    _, store, client, sender, alert, control, *_ = build(store=store, sender=RecordingSender(fail=True))
    control.report("L3", "x", "lỗi")
    assert [r["layer"] for r in store.control_logs] == ["L3", "L5"]
    assert store.control_logs[0]["discord_status"] == "failed"
    assert len(sender.calls) == 1   # the Discord failure itself is not re-sent


def test_mock_secrets_are_not_stored():
    _, store, client, sender, alert, control, *_ = build()
    control.report("L3", "x", "fail https://discord.com/api/webhooks/123/abc signature=deadbeef",
                   details={"apiKey": "k", "signature": "s", "url": "https://discord.com/api/webhooks/1/2"})
    rec = store.control_logs[0]
    text = str(rec)
    assert "webhooks/123" not in text and "deadbeef" not in text and "'k'" not in text


# ---------------------------------------------------------------- legacy gates gone
FORBIDDEN = ["exposure_check", "allow_new_entry", "entry_admission", "trading_lock", "ProtectionJournal",
             "EventReporter", "discord_batch", "outbox", "system_events", "processed_targets", "CIRCUIT_BREAKER",
             "_blocked_until_ms", "reconcile_required", "exit_cleanup", "emergency_lock", "symbol_side_lock"]


def test_mock_no_code_path_for_old_gates_in_v3_live():
    import io
    import tokenize

    def code_only(path):
        """Identifiers and non-docstring literals; prose in docstrings/comments is ignored."""
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if tok.type == tokenize.NAME or (tok.type == tokenize.STRING and not tok.string.lstrip("rbuf").startswith(('"""', "'''"))):
                out.append(tok.string)
        return "\n".join(out)
    sources = {p: code_only(p) for p in LIVE.glob("*.py")}
    for path, text in sources.items():
        for word in FORBIDDEN:
            assert word not in text, f"{word} found in {path.name}"
    unified = (ROOT / "unified_app.py").read_text(encoding="utf-8")
    assert "autotrade_v2.app.main" not in unified.replace("(autotrade_v2.app.main)", "")
    assert "from autotrade_v3.live.app import app as engine_app" in unified


def test_mock_spacing_guard_has_no_circuit_breaker():
    guard = SpacingGuard(min_interval_sec=0)
    err = guard.api_error("x", "HTTP_429", "rate limit", "/p", {})
    assert isinstance(err, BingXApiError)
    with guard.request_slot():   # a previous 429 never blocks the next request locally
        pass
    assert guard.status()["blocked"] is False


# ---------------------------------------------------------------- L2 / 20FINAL
STRATEGY_FILES = {
    "autotrade_v2/app/strategy/final14_exact_strategy.py": None,
    "autotrade_v2/app/strategy/final14_config.py": None,
    "autotrade_v2/app/strategy/engine.py": None,
    "autotrade_v2/app/strategy/strategy.py": None,
}
BASELINE = ROOT / "autotrade_v3" / "live" / "tests" / "strategy_baseline_sha256.txt"


def test_mock_20final_source_unchanged_vs_baseline():
    pinned = dict(line.split("  ", 1)[::-1] for line in BASELINE.read_text().split("\n") if line.strip())
    assert pinned, "baseline missing"
    for rel, digest in pinned.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, rel


def _candles(seed: int, n: int) -> pd.DataFrame:
    """Synthetic fat-tailed trending fixture (MOCK market data, not BingX history)."""
    rng = np.random.default_rng(seed)
    start = 1_700_000_100_000 // 900_000 * 900_000
    drift = np.repeat(rng.normal(0, 0.002, n // 40 + 1), 40)[:n]
    close = 30000 * np.exp(np.cumsum(rng.standard_t(3, n) * 0.004 + drift))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.random(n) * 0.004)
    low = np.minimum(open_, close) * (1 - rng.random(n) * 0.004)
    t = start + np.arange(n) * 900_000
    return pd.DataFrame({"open_time": t, "open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.lognormal(5, 1, n), "close_time": t + 899_999})


def test_mock_l2_output_identical_to_20final_scan_latest():
    from autotrade_v2.app.contracts import MarketSnapshot
    from autotrade_v2.app.strategy.final14_config import case_name
    from autotrade_v2.app.strategy.final14_exact_strategy import scan_latest
    l2 = L2Strategy(MemoryStore(), control=None)
    total = 0
    # windows chosen so that the fixture produces real 20FINAL signals, plus quiet windows
    for seed, ends in ((0, (3400, 3415)), (1, (3403, 3427, 3430))):
        full = _candles(seed, 3500)
        for end in ends:
            m15 = full.iloc[end - 3400:end].reset_index(drop=True)
            for symbol in ("BTC-USDT", "ETH-USDT"):
                snap = MarketSnapshot(symbol, "15m", m15, int(m15.iloc[-1].open_time), int(m15.iloc[-1].close_time), {})
                got = [(s.event_id, s.side, s.entry, s.tp, s.sl, s.combo_name) for s in l2.calculate(snap)]
                want = [(x.event_id, x.side, x.entry, x.tp, x.sl, case_name(x.combo)) for x in scan_latest(symbol, m15)]
                assert got == want
                total += len(want)
    assert total >= 4, "fixture must exercise real signals"


def test_mock_strategy_signals_has_exactly_nine_fields():
    row = signal_row(sig())
    assert set(row) == {"event_id", "symbol", "combo", "side", "signal_time", "entry", "tp", "sl"}
    sql = (ROOT / "supabase" / "migrations" / "20261114000000_v3_five_layer_tables.sql").read_text()
    body = re.search(r"CREATE TABLE IF NOT EXISTS autotrade_v3\.strategy_signals \((.*?)\n\);", sql, re.S).group(1)
    cols = [ln.strip().split()[0] for ln in body.strip().split("\n") if ln.strip()]
    assert cols == ["event_id", "symbol", "combo", "side", "signal_time", "entry", "tp", "sl", "created_at"]


def test_mock_l2_dispatch_persists_alerts_and_hands_to_l3_even_when_db_fails():
    store = MemoryStore()
    store.fail = {"insert_signal"}
    _, store, client, sender, alert, control, _reader, executor = build(store=store)
    l2 = L2Strategy(store, control, alert, executor)
    threads = l2.dispatch([sig(combo=1), sig(combo=7)])
    for t in threads:
        t.join(timeout=10)
    assert len(client.entry_posts) == 2
    assert [r["step"] for r in store.control_logs].count("signal_store") == 2


# ---------------------------------------------------------------- L4 mechanism 3
def test_mock_hourly_projection_builds_results_and_finances():
    _, store, client, sender, alert, control, _reader, executor = build()
    r = executor.execute(sig())
    ex = store.executions[r["event_id"]]
    store.archive_orders = [
        {"order_id": ex["tp_order_id"], "status": "FILLED", "filled_qty": 0.0005, "avg_price": 102000.0,
         "updated_ms": 1763003600000, "raw": {"profit": "1.0"}},
        {"order_id": ex["sl_order_id"], "status": "NEW", "filled_qty": 0, "avg_price": 0, "raw": {}},
    ]
    store.archive_fills = [
        {"order_id": ex["entry_order_id"], "fee": -0.05, "realized_pnl": 0},
        {"order_id": ex["tp_order_id"], "fee": -0.03, "realized_pnl": 1.0},
    ]
    counts = project_results(store, control)
    assert counts == {"executions": 1, "results": 1, "finances": 1}
    res = store.results[ex["tp_order_id"]]
    assert res["close_type"] == "TP" and res["closed_qty"] == 0.0005
    fin = store.finances[ex["id"]]
    assert fin["leverage"] == 50 and fin["gross_pnl"] == 1.0
    assert fin["net_pnl"] == pytest.approx(1.0 - 0.05 - 0.03)
    assert fin["is_fully_closed"] is False


def test_mock_unified_app_runs_v3_engine_offline():
    import os
    if any(os.getenv(k) for k in ("DATABASE_URL", "BINGX_API_KEY")):
        pytest.skip("offline only")
    from fastapi.testclient import TestClient
    from autotrade_v3.live.app import create_app, Runtime
    rt = Runtime(settings=__import__("autotrade_v3.live.tests.fakes", fromlist=["settings"]).settings(
        bingx_api_key="", bingx_api_secret=""), client=FakeBingX())
    with TestClient(create_app(rt)) as c:
        st = c.get("/status").json()
        assert st["engine"] == "autotrade-v3" and st["runtime"]["execution_enabled"] is False
        assert st["legacy_v2_trading"] is False
        assert c.get("/health").status_code == 200


# ---------------------------------------------------------------- review follow-ups
def test_mock_canceled_entry_with_real_fill_is_still_protected():
    _, store, client, *_rest, executor = build()
    orig = client.get_order_detail

    def canceled_but_filled(symbol, order_id=None, client_order_id=None):
        payload = orig(symbol, order_id=order_id, client_order_id=client_order_id)
        if payload["data"]["order"]["type"] == "MARKET":
            payload["data"]["order"]["status"] = "CANCELED"
        return payload
    client.get_order_detail = canceled_but_filled
    r = executor.execute(sig())
    assert r["stage"] == "protected"
    assert [x["error_code"] for x in store.control_logs] == ["PARTIAL_FILL"]


def test_mock_unknown_entry_late_fill_is_protected_without_resend():
    _, store, client, *_rest, executor = build(late_verify_attempts=3)
    client.entry_mode = "lost_absent"
    r = executor.execute(sig())
    assert r["stage"] == "entry_unknown"
    # BingX shows the order later (MOCK): the late GET-only check protects it.
    order = client._create({"symbol": "BTC-USDT", "side": "BUY", "positionSide": "LONG", "type": "MARKET",
                            "quantity": "0.001", "clientOrderId": r["client_order_id"]}, "FILLED")
    import threading
    import time
    deadline = time.time() + 10
    while time.time() < deadline and not any(x.get("stage") == "protected" for x in executor.results):
        time.sleep(0.05)
    assert any(x.get("stage") == "protected" for x in executor.results)
    assert len(client.entry_posts) == 1
    assert "LATE_FILL" in [x["error_code"] for x in store.control_logs]
    assert order["orderId"]
    assert not [t for t in threading.enumerate() if t.name.startswith("v3-l3-late") and t.is_alive()]


def test_mock_db_claim_failure_row_is_created_on_first_save():
    store = MemoryStore()
    calls = {"n": 0}
    real = store.claim_execution

    def flaky(row):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down (mock)")
        return real(row)
    store.claim_execution = flaky
    _, store, client, *_rest, executor = build(store=store)
    r = executor.execute(sig())
    assert r["stage"] == "protected"
    assert store.executions[r["event_id"]]["client_order_id"] == r["client_order_id"]


def test_mock_hourly_projection_attributes_triggered_child_orders():
    _, store, client, sender, alert, control, _reader, executor = build()
    r = executor.execute(sig())
    ex = store.executions[r["event_id"]]
    store.archive_orders = [
        {"order_id": ex["sl_order_id"], "status": "FILLED", "filled_qty": 0.001, "avg_price": 99200.0, "raw": {}},
        {"order_id": "777", "status": "FILLED", "filled_qty": 0.001, "avg_price": 99200.0, "updated_ms": 1,
         "raw": {"triggerOrderId": ex["sl_order_id"], "profit": "-0.8"}},
    ]
    store.archive_fills = [{"order_id": "777", "fee": -0.04, "realized_pnl": -0.8}]
    counts = project_results(store, control)
    assert counts["results"] == 1 and store.results["777"]["close_type"] == "SL"
    fin = store.finances[ex["id"]]
    assert fin["is_fully_closed"] is True and fin["gross_pnl"] == -0.8


def test_success_message_uses_confirmed_bingx_prices_without_ids():
    from autotrade_v3.live.l5_alert import success_message
    row = {"event_id": "BTC-USDT|1h|C4|SELL|1794625199999", "symbol": "BTC-USDT", "combo": "C4",
           "side": "SELL", "avg_price": 67666.5, "tp_price": 12345.0, "sl_price": 98765.0,
           "entry_order_id": "2121438081244680192", "client_order_id": "v3e-abc",
           "confirmed_at_ms": 1794625249000,
           "protection": {
               "tp": {"status": "confirmed", "evidence": {
                   "confirm": {"order": {"stopPrice": "66448.503"}},
                   "params": {"stopPrice": "66448.5"}}},
               "sl": {"status": "confirmed", "evidence": {
                   "confirm": {"order": {"stopPrice": "68275.4985"}},
                   "params": {"stopPrice": "68275.4"}}}}}
    message = success_message(row)
    assert message == ("✅ **BTC SELL · C4 | ĐẶT LỆNH THÀNH CÔNG**\n"
                       "Entry **67666.5** · TP **66448.5** · SL **68275.5**\n"
                       "🕒 10:00:49 · 14/11/2026")
    assert "12345" not in message and "98765" not in message
    assert "2121438081244680192" not in message and "v3e-abc" not in message
