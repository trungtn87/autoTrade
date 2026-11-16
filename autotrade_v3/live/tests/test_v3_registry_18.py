"""Registry V3_LIVE_18_H1_V2 (18 combo-symbols) — MOCK/fixture tests, no network.

Real-data signal parity vs the lab baseline is evidence produced by
``scripts/v3_live18_parity.py`` (needs the Owner's local 3-year M1 dataset) and
recorded under ``evidence/v3-live18-parity/``; these tests guard the code paths.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade_v2.app.contracts import MarketSnapshot
from autotrade_v2.app.strategy.final14_config import FINAL14_CASES, NEW6_CASES, enabled_combos, enabled_new6_combos
from autotrade_v2.app.strategy.final14_exact_strategy import scan_latest
from autotrade_v3.live import strategy_v18
from autotrade_v3.live.config import V3Settings
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.l3_execution import V3Signal
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.strategy_registry import (REGISTRY_20FINAL, REGISTRY_V18, REGISTRY_V20, case_for, load_registry)
from autotrade_v3.live.strategy_v18 import V18Engine
from autotrade_v3.live.tests.fakes import PRICE, build

ROOT = Path(__file__).resolve().parents[3]
REG_JSON = ROOT / "autotrade_v3" / "live" / "registries" / "V3_LIVE_18_H1_V2.json"

BTC_18 = {1, 2, 4, 6, 7, 10, 11, 101, 106}
ETH_18 = {1, 3, 4, 7, 10, 101, 102, 103, 105}
# Frozen identity of the deployed registry. Changing any combo/threshold/TP/SL changes it.
V18_SPEC_HASH = "c09830666c3d50a31baed1312f9474203a5c8f1c3792b26e8a0f1f9319ddddcd"


def _candles(seed: int, n: int, start: int = 1_700_000_100_000 // 900_000 * 900_000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 30000 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.random(n) * 0.004)
    low = np.minimum(open_, close) * (1 - rng.random(n) * 0.004)
    t = start + np.arange(n) * 900_000
    return pd.DataFrame({"open_time": t, "open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.lognormal(5, 1, n), "close_time": t + 899_999})


def _snap(symbol, m15):
    return MarketSnapshot(symbol, "15m", m15, int(m15.iloc[-1].open_time), int(m15.iloc[-1].close_time), {})


# ---------------------------------------------------------------- A. registry
def test_registry_has_exactly_the_18_owner_combo_symbols():
    reg = load_registry(REGISTRY_V18)
    assert len(reg.combos) == 18
    assert {c.combo_id for c in reg.for_symbol("BTC-USDT")} == BTC_18
    assert {c.combo_id for c in reg.for_symbol("ETH-USDT")} == ETH_18
    assert len({c.key for c in reg.combos}) == 18          # no merged / duplicated combo-symbol


def test_registry_source_is_the_v2_baseline_not_v1():
    raw = json.loads(REG_JSON.read_text())
    assert raw["baseline_id"] == "V3_18_COMBO_H1_BASELINE_V1"
    assert "v3_18_baseline_v2" in raw["baseline_source"]
    assert raw["baseline_registry_sha256"] == "5f47aec3dc1a712574767c2f54dd06925e9f0a9352abaec282df20e9629c5857"
    assert raw["baseline_signals_sha256"] == "e8fc3e0914b36a8a5c7766e84ad7b959e9e4d0b069605aa0e0e3e2ad8d2a19f0"
    assert raw["live_exit_policy"] == "FIXED_TP_SL"


def test_c1_eth_uses_h1_both_1_and_only_c1_eth_does():
    reg = load_registry(REGISTRY_V18)
    variants = {c.key: c.signal_variant for c in reg.combos if c.signal_variant != "BASELINE"}
    assert variants == {"ETH-USDT/1": "H1_BOTH_1"}
    assert reg.h1_both_1 == {"buy_max_slope_4h_pct": 1.5, "sell_min_dist_ema150_4h_pct": -6.0,
                             "pre_edge_h1": True, "sell_cci_corrected": True}
    assert reg.get("BTC-USDT", 1).signal_variant == "BASELINE"


def test_17_other_combos_keep_their_baseline_spec_exactly():
    reg = load_registry(REGISTRY_V18)
    checked = 0
    for c in reg.combos:
        if c.symbol == "ETH-USDT" and c.combo_id == 101:
            src = NEW6_CASES["BTC-USDT"][101]          # ETH-C101 = BTC C11 DeMarker clone (lab cross-symbol)
        elif c.is_new6:
            src = NEW6_CASES[c.symbol][c.combo_id]
        else:
            src = FINAL14_CASES[c.symbol][c.combo_id]
        assert c.entry_variant == src["entry_variant"], c.key
        assert c.layer2 == src["layer2"], c.key
        assert c.tp_pct == pytest.approx(src["tp_pct"]) and c.sl_pct == pytest.approx(src["sl_pct"]), c.key
        if not c.is_new6:
            assert c.entry == src["entry"], c.key
        checked += 1
    assert checked == 18


def test_same_combo_id_on_btc_and_eth_are_distinct_identities():
    reg = load_registry(REGISTRY_V18)
    for cid in BTC_18 & ETH_18:
        b, e = reg.get("BTC-USDT", cid), reg.get("ETH-USDT", cid)
        assert b.key != e.key and b.spec_hash != e.spec_hash
        assert case_for("ETH-USDT", cid, reg) == e.as_case()


def test_registry_spec_hash_is_frozen():
    assert load_registry(REGISTRY_V18).spec_hash == V18_SPEC_HASH


def test_rollback_registry_is_the_untouched_20final():
    reg = load_registry(REGISTRY_20FINAL)
    assert len(reg.combos) == 20
    for sym in ("BTC-USDT", "ETH-USDT"):
        assert {c.combo_id for c in reg.for_symbol(sym)} == set(enabled_combos(sym)) | set(enabled_new6_combos(sym))


def test_unknown_registry_blocks_new_entries_only():
    l2 = L2Strategy(MemoryStore(), control=None, registry="V9_TYPO")
    assert l2.registry is None and l2.registry_view()["new_entries"] == "blocked_invalid_registry"
    with pytest.raises(RuntimeError):
        l2.calculate(_snap("BTC-USDT", _candles(0, 3400)))


def test_settings_default_registry_and_env_override(monkeypatch):
    monkeypatch.delenv("V3_STRATEGY_REGISTRY", raising=False)
    assert V3Settings().strategy_registry == REGISTRY_V20
    monkeypatch.setenv("V3_STRATEGY_REGISTRY", "20final")
    assert V3Settings().strategy_registry == REGISTRY_20FINAL


# ---------------------------------------------------------------- B. live path == batch path
@pytest.mark.parametrize("seed", [0, 1])
def test_latest_bar_path_equals_batch_path_on_the_same_window(seed):
    """Live calls ``latest_only=True``; parity evidence uses the batch form. They must agree bar by bar."""
    eng = V18Engine(load_registry(REGISTRY_V18))
    full = _candles(seed, 4400)
    seen = 0
    for symbol in ("BTC-USDT", "ETH-USDT"):
        hits = sorted({s.close_time for s in eng.frame_signals(symbol, full, latest_only=False)
                       if s.close_time > int(full.iloc[3399].close_time)})
        ends = [int(np.searchsorted(full.close_time.to_numpy(), t)) + 1 for t in hits[:6]] + [3400, 4123]
        for end in ends:
            m15 = full.iloc[end - 3400:end].reset_index(drop=True)
            last_close = int(m15.iloc[-1].close_time)
            live = eng.calculate(symbol, m15)
            batch = [s for s in eng.frame_signals(symbol, m15, latest_only=False) if s.close_time == last_close]
            assert live == batch
            seen += len(live)
    assert seen > 0, "fixture must produce real signals"


def test_only_registry_combos_can_emit_and_no_duplicates():
    eng = V18Engine(load_registry(REGISTRY_V18))
    full = _candles(3, 6000)
    for symbol, allowed in (("BTC-USDT", BTC_18), ("ETH-USDT", ETH_18)):
        sigs = eng.frame_signals(symbol, full, latest_only=False)
        assert sigs and {s.combo for s in sigs} <= allowed
        ids = [s.event_id for s in sigs]
        assert len(ids) == len(set(ids))
        assert all(s.registry_version == REGISTRY_V18 and len(s.spec_hash) == 64 for s in sigs)


def test_l2_eth_c1_goes_through_h1_both_1_on_the_real_call_path(monkeypatch):
    """V1 bug class: the class had the new rule but the runner called another method.
    Here the LIVE entry point (L2Strategy.calculate) must reach h1_both_1_events for ETH only."""
    calls = []
    real = strategy_v18.h1_both_1_events

    def spy(d15, params):
        calls.append(params)
        return real(d15, params)

    monkeypatch.setattr(strategy_v18, "h1_both_1_events", spy)
    l2 = L2Strategy(MemoryStore(), control=None, registry=REGISTRY_V18)
    m15 = _candles(5, 3400)
    l2.calculate(_snap("BTC-USDT", m15))
    assert calls == []
    l2.calculate(_snap("ETH-USDT", m15))
    assert len(calls) == 1 and calls[0]["buy_max_slope_4h_pct"] == 1.5 and calls[0]["sell_min_dist_ema150_4h_pct"] == -6.0


def test_h1_both_1_sell_uses_corrected_cci_and_filters_before_edge():
    import inspect
    src = inspect.getsource(strategy_v18.h1_both_1_events)
    assert "(c9 < -80)" in src and "(c9 > -80)" not in src
    assert "edge_event(l_base & buy_cond)" in src and "edge_event(s_base & sell_cond)" in src


def test_no_signal_unless_bar_is_closed_hour_for_1h_combos():
    """1H combos only fire on the 15m bar closing at HH:59:59.999 (the H1 bar is then closed)."""
    eng = V18Engine(load_registry(REGISTRY_V18))
    full = _candles(1, 9000)
    for symbol in ("BTC-USDT", "ETH-USDT"):
        for s in eng.frame_signals(symbol, full, latest_only=False):
            minute = pd.Timestamp(s.close_time + 1, unit="ms", tz="UTC").minute
            if s.timeframe == "1h":
                assert minute == 0, s.event_id
            else:
                assert minute % 15 == 0


def test_no_lookahead_appending_future_bars_never_changes_past_signals():
    eng = V18Engine(load_registry(REGISTRY_V18))
    full = _candles(2, 5200)
    a = eng.frame_signals("ETH-USDT", full.iloc[:5000], latest_only=False)
    b = [s for s in eng.frame_signals("ETH-USDT", full, latest_only=False) if s.close_time <= int(full.iloc[4999].close_time)]
    assert a == b


def test_missing_candles_raise_and_emit_nothing():
    from autotrade_v2.app.control.errors import StrategyInsufficientCandlesError
    eng = V18Engine(load_registry(REGISTRY_V18))
    with pytest.raises(StrategyInsufficientCandlesError):
        eng.calculate("BTC-USDT", _candles(0, 3399))


def test_rollback_l2_is_byte_identical_to_20final_scan_latest():
    l2 = L2Strategy(MemoryStore(), control=None, registry=REGISTRY_20FINAL)
    full = _candles(0, 3420)
    for end in (3400, 3415):
        m15 = full.iloc[end - 3400:end].reset_index(drop=True)
        for symbol in ("BTC-USDT", "ETH-USDT"):
            got = [(s.event_id, s.entry, s.tp, s.sl) for s in l2.calculate(_snap(symbol, m15))]
            want = [(x.event_id, x.entry, x.tp, x.sl) for x in scan_latest(symbol, m15)]
            assert got == want


# ---------------------------------------------------------------- C. L2 -> L3 -> L4 (MOCK)
def _sig18(symbol, combo, side, close_time=1763003699999):
    reg = load_registry(REGISTRY_V18)
    spec = reg.get(symbol, combo)
    entry = PRICE[symbol]
    d = 1 if side == "BUY" else -1
    return V3Signal(event_id=f"{symbol}|{spec.native_timeframe}|{spec.token}|{side}|{close_time}", symbol=symbol,
                    combo=combo, combo_name=spec.token, side=side, timeframe=spec.native_timeframe,
                    close_time=close_time, entry=entry, tp=entry * (1 + d * spec.tp_pct), sl=entry * (1 - d * spec.sl_pct),
                    tp_pct=spec.tp_pct, sl_pct=spec.sl_pct, registry_version=reg.version, spec_hash=spec.spec_hash,
                    signal_variant=spec.signal_variant)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_new_eth_c101_entry_is_protected_with_fixed_tp_sl_from_registry(side):
    _, store, client, sender, *_r, executor = build()
    r = executor.execute(_sig18("ETH-USDT", 101, side))      # not in 20FINAL get_case: must come from registry
    assert r["stage"] == "protected" and r["exit_mode"] == "fixed_tp_sl"
    d = 1 if side == "BUY" else -1
    assert r["tp_price"] == pytest.approx(r["avg_price"] * (1 + d * 0.018))
    assert r["sl_price"] == pytest.approx(r["avg_price"] * (1 - d * 0.009))
    assert [p["type"] for p in client.order_posts] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    ident = store.executions[r["event_id"]]["raw_entry"]["strategy"]
    assert ident["registry_version"] == REGISTRY_V18 and ident["symbol"] == "ETH-USDT" and ident["combo_id"] == 101


def test_btc_and_eth_same_combo_name_do_not_collide():
    _, store, client, *_r, executor = build()
    a = executor.execute(_sig18("BTC-USDT", 101, "BUY"))
    b = executor.execute(_sig18("ETH-USDT", 101, "BUY"))
    assert a["stage"] == b["stage"] == "protected"
    assert a["client_order_id"] != b["client_order_id"] and len(client.entry_posts) == 2
    assert store.executions[a["event_id"]]["symbol"] == "BTC-USDT" and store.executions[b["event_id"]]["symbol"] == "ETH-USDT"


def test_sl_failure_on_registry_signal_force_closes_exact_qty():
    _, store, client, *_r, executor = build()
    client.leg_mode["STOP_MARKET"] = "reject"
    r = executor.execute(_sig18("ETH-USDT", 1, "SELL"))
    assert r["stage"] == "sl_failed"
    close = [p for p in client.order_posts if p["type"] == "MARKET"]
    assert len(close) == 1 and close[0]["quantity"] == r["executed_qty"]


def test_api_timeout_on_entry_never_resends():
    _, store, client, *_r, executor = build()
    client.entry_mode = "lost_absent"
    r = executor.execute(_sig18("BTC-USDT", 106, "SELL"))
    assert r["stage"] in {"entry_unknown", "entry_not_filled"} and len(client.entry_posts) == 1


def test_restart_and_registry_switch_never_duplicate_an_entry():
    """Same bar, same combo: 20FINAL (rollback) and V18 share event_id -> DB claim blocks a 2nd POST."""
    _, store, client, *_r, executor = build()
    s18 = _sig18("BTC-USDT", 4, "BUY")
    executor.execute(s18)
    *_x, executor2 = build(store=store, client=client)            # restarted process, same DB
    legacy = V3Signal(event_id=s18.event_id, symbol=s18.symbol, combo=4, combo_name="C4", side="BUY", timeframe="1h",
                      close_time=s18.close_time, entry=s18.entry, tp=s18.tp, sl=s18.sl)   # rollback registry signal
    assert executor2.execute(legacy)["stage"] == "duplicate_skipped"
    assert executor2.execute(s18)["stage"] == "duplicate_skipped"
    assert len(client.entry_posts) == 1


def test_switching_registry_does_not_touch_open_positions():
    """Registry only gates NEW signals: no cancel/close/amend call is made by building L2 or switching."""
    _, store, client, *_r, executor = build()
    executor.execute(_sig18("BTC-USDT", 2, "BUY"))
    posts_before = list(client.order_posts)
    for version in (REGISTRY_20FINAL, REGISTRY_V18, REGISTRY_20FINAL):
        L2Strategy(store, control=None, executor=executor, registry=version)
    assert client.order_posts == posts_before
    assert getattr(client, "cancels", []) == [] and getattr(client, "amends", []) == []


def test_l2_dispatch_with_registry_signal_runs_l3():
    _, store, client, sender, alert, control, _reader, executor = build()
    l2 = L2Strategy(store, control, alert, executor, registry=REGISTRY_V18)
    threads = l2.dispatch([_sig18("ETH-USDT", 105, "SELL")])
    for t in threads:
        t.join(5)
    assert len(client.entry_posts) == 1 and list(store.signals.values())[0]["combo"] == "C15"


def test_status_exposes_active_registry():
    l2 = L2Strategy(MemoryStore(), control=None, registry=REGISTRY_V18)
    view = l2.registry_view()
    assert view["version"] == REGISTRY_V18 and view["count"] == 18 and view["exit_policy"] == "FIXED_TP_SL"
    assert view["provenance"]["baseline_id"] == "V3_18_COMBO_H1_BASELINE_V1"


def test_registry_json_is_not_edited_by_hand():
    digest = hashlib.sha256(REG_JSON.read_bytes()).hexdigest()
    assert digest == "01b8d187928e28d0c584d0d26f0f9b3791cd4d9f3ddfb108828eba89510e2753"


def test_new6_keeps_20final_virtual_position_rule():
    """Owner 2026-11-16: NEW6 keeps the live single-virtual-position rule (not the lab every-bar rule)."""
    reg = load_registry(REGISTRY_V18)
    assert reg.new6_signal_mode == "virtual_position"
    eng = V18Engine(reg)
    full = _candles(4, 3600)
    for end in range(3400, 3600, 4):
        m15 = full.iloc[end - 3400:end].reset_index(drop=True)
        for symbol in ("BTC-USDT", "ETH-USDT"):
            mine = {(s.event_id, s.entry, s.tp, s.sl) for s in eng.calculate(symbol, m15) if s.combo > 100}
            legacy = {(x.event_id, x.entry, x.tp, x.sl) for x in scan_latest(symbol, m15)
                      if x.combo > 100 and reg.get(symbol, x.combo) is not None}
            assert legacy <= mine            # every shared NEW6 combo fires exactly as in 20FINAL
            assert {e for e in mine if e not in legacy} <= {e for e in mine if e[0].startswith("ETH-USDT|1h|C11|")}
