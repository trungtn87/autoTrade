"""Registry V3_LIVE_20_POSITIVE_FIXED_V1 (20 combo-symbols) — MOCK/fixture tests, no network.

Owner 2026-11-16: 20 positive combo-symbols of audit AUDIT_20_POSITIVE_FIXED_TPSL_20261116
(Fixed TP/SL, lab v3_lab 5c471903) = V3_LIVE_18_H1_V2 + BTC-USDT/C9 + ETH-USDT/C6.
Real-data signal parity vs the audit run is produced by ``scripts/v3_live18_parity.py``
and recorded under ``evidence/v3-live20-parity/``; these tests guard the code paths.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from autotrade_v2.app.strategy.final14_config import FINAL14_CASES
from autotrade_v2.app.strategy.final14_exact_strategy import scan_latest
from autotrade_v3.live.config import V3Settings
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.l3_execution import V3Signal
from autotrade_v3.live.l4_control import project_results
from autotrade_v3.live.store import MemoryStore
from autotrade_v3.live.strategy_registry import (DEFAULT_REGISTRY, REGISTRY_V18, REGISTRY_V20,
                                                 load_registry)
from autotrade_v3.live.strategy_v18 import V18Engine
from autotrade_v3.live.tests.fakes import PRICE, FakeBingX, build, settings
from autotrade_v3.live.tests.test_v3_registry_18 import _candles, _snap

ROOT = Path(__file__).resolve().parents[3]
LIVE = ROOT / "autotrade_v3" / "live"
REG_JSON = LIVE / "registries" / "V3_LIVE_20_POSITIVE_FIXED_V1.json"

BTC_20 = {1, 2, 4, 6, 7, 9, 10, 11, 101, 106}
ETH_20 = {1, 3, 4, 6, 7, 10, 101, 102, 103, 105}
ADDED = {("BTC-USDT", 9), ("ETH-USDT", 6)}
# Frozen identity of the registry. Changing any combo/threshold/TP/SL changes it.
V20_SPEC_HASH = "d26a9bd62b9ef59ee8b9358851ba9884b08d2bcd9388dfc1742e9d7002bd1049"
V20_JSON_SHA256 = "65997bb1c030b5bdf017b85352b66e291ae82aca2944b44b105ceae531ae93c3"


# ---------------------------------------------------------------- A. registry
def test_default_registry_is_lab8_23():
    assert DEFAULT_REGISTRY == "V3_LIVE_23_LAB_8_V1"
    assert REGISTRY_V20 == "V3_LIVE_20_POSITIVE_FIXED_V1"


def test_settings_default_and_selectable_versions(monkeypatch):
    monkeypatch.delenv("V3_STRATEGY_REGISTRY", raising=False)
    assert V3Settings().strategy_registry == DEFAULT_REGISTRY
    for v in ("v3_live_18_h1_v2", "V3_LIVE_20_POSITIVE_FIXED_V1", "V3_LIVE_23_LAB_8_V1"):
        monkeypatch.setenv("V3_STRATEGY_REGISTRY", v)
        assert load_registry(V3Settings().strategy_registry).version.startswith(v.upper())


def test_registry_has_exactly_the_20_owner_combo_symbols():
    reg = load_registry(REGISTRY_V20)
    assert len(reg.combos) == 20
    assert {c.combo_id for c in reg.for_symbol("BTC-USDT")} == BTC_20
    assert {c.combo_id for c in reg.for_symbol("ETH-USDT")} == ETH_20
    assert len(reg.for_symbol("BTC-USDT")) == len(reg.for_symbol("ETH-USDT")) == 10
    assert len(reg.allowed()) == 20                        # no duplicated (symbol, combo_id)


def test_eth_c1_is_h1_both_1_and_btc_c1_is_baseline():
    reg = load_registry(REGISTRY_V20)
    assert {c.key: c.signal_variant for c in reg.combos if c.signal_variant != "BASELINE"} == {"ETH-USDT/1": "H1_BOTH_1"}
    assert reg.get("BTC-USDT", 1).signal_variant == "BASELINE"
    assert reg.h1_both_1 == {"buy_max_slope_4h_pct": 1.5, "sell_min_dist_ema150_4h_pct": -6.0,
                             "pre_edge_h1": True, "sell_cci_corrected": True}
    assert reg.summary()["signal_variants"] == {"ETH-USDT/1": "H1_BOTH_1"}


def test_id_11_is_tier_and_id_101_is_new6_c11():
    reg = load_registry(REGISTRY_V20)
    tier = reg.get("BTC-USDT", 11)
    assert tier.token == "TIER" and not tier.is_new6 and tier.entry_variant == FINAL14_CASES["BTC-USDT"][11]["entry_variant"]
    for sym in ("BTC-USDT", "ETH-USDT"):
        c = reg.get(sym, 101)
        assert c.token == "C11" and c.is_new6 and c.native_timeframe == "1h"
    assert reg.get("ETH-USDT", 11) is None                 # ETH TIER is NOT in the list


def test_registry_spec_hash_and_json_are_frozen():
    assert load_registry(REGISTRY_V20).spec_hash == V20_SPEC_HASH
    assert hashlib.sha256(REG_JSON.read_bytes()).hexdigest() == V20_JSON_SHA256


def test_registry_provenance_is_the_fixed_tpsl_audit():
    raw = json.loads(REG_JSON.read_text())
    assert raw["audit_id"] == "AUDIT_20_POSITIVE_FIXED_TPSL_20261116"
    assert raw["lab_git_sha"] == "5c471903090f174c349f062c0de8b555cadd2895"
    assert raw["audit_repro_run"].startswith("RUN_20261116_132654_20FINAL_CROSS_SYMBOL_30_BINGX_PORTFOLIO")
    assert raw["audit_sha256"]["repro_run/signals.csv"] == \
        "c876551c0ab664977993d32b31d6f563ab7b0f5baea6a7b633bb5c2800a37ae3"
    assert raw["live_exit_policy"] == "FIXED_TP_SL" and raw["new6_signal_mode"] == "virtual_position"
    assert all("baseline_exit_policy" not in c for c in raw["combos"])     # no 50/50 trailing leftovers


# ---------------------------------------------------------------- D. 18 -> 20 diff
def test_18_shared_combos_are_byte_identical_and_only_c9_btc_c6_eth_are_new():
    r18, r20 = load_registry(REGISTRY_V18), load_registry(REGISTRY_V20)
    assert r20.allowed() - r18.allowed() == ADDED and r18.allowed() <= r20.allowed()
    for c in r18.combos:
        assert r20.get(c.symbol, c.combo_id).signal_spec() == c.signal_spec(), c.key
    assert r20.h1_both_1 == r18.h1_both_1 and r20.new6_signal_mode == r18.new6_signal_mode


def test_added_combos_equal_the_frozen_20final_spec():
    from autotrade_v2.app.strategy.final14_config import get_case  # test-only oracle: frozen V2 table
    r20 = load_registry(REGISTRY_V20)
    for sym, cid in ADDED:
        cfg = get_case(sym, cid)
        spec = r20.get(sym, cid)
        assert (spec.entry_variant, spec.layer2, spec.tp_pct, spec.sl_pct) == (
            cfg["entry_variant"], cfg["layer2"], float(cfg["tp_pct"]), float(cfg["sl_pct"]))
    assert (r20.get("BTC-USDT", 9).tp_pct, r20.get("BTC-USDT", 9).sl_pct) == (0.01, 0.004)
    assert (r20.get("ETH-USDT", 6).tp_pct, r20.get("ETH-USDT", 6).sl_pct) == (0.02, 0.00666667)


@pytest.mark.parametrize("seed", [0, 1])
def test_v20_signals_equal_v18_for_shared_combos_and_20final_for_added(seed):
    """Same window: the 18 shared combos emit exactly what V18 emits; C9-BTC / C6-ETH exactly what 20FINAL emits."""
    e18, e20 = V18Engine(load_registry(REGISTRY_V18)), V18Engine(load_registry(REGISTRY_V20))
    full = _candles(seed, 3400 + 200)
    for symbol in ("BTC-USDT", "ETH-USDT"):
        b18 = {(s.combo, s.side, s.close_time): (s.entry, s.tp, s.sl, s.spec_hash)
               for s in e18.frame_signals(symbol, full, latest_only=False)}
        b20 = {(s.combo, s.side, s.close_time): (s.entry, s.tp, s.sl, s.spec_hash)
               for s in e20.frame_signals(symbol, full, latest_only=False)}
        added = {k: v for k, v in b20.items() if (symbol, k[0]) in ADDED}
        assert {k: v for k, v in b20.items() if (symbol, k[0]) not in ADDED} == b18
        for end in range(3400, len(full) + 1, 20):
            w = full.iloc[end - 3400:end].reset_index(drop=True)
            got = [(s.event_id, s.entry, s.tp, s.sl) for s in e20.calculate(symbol, w) if (symbol, s.combo) in ADDED]
            want = [(x.event_id, x.entry, x.tp, x.sl) for x in scan_latest(symbol, w) if (symbol, int(x.combo)) in ADDED]
            assert got == want
        assert all(cid in {9, 6} for cid, *_ in added)


def test_l2_v20_emits_only_registry_combos():
    l2 = L2Strategy(MemoryStore(), control=None, registry=REGISTRY_V20)
    allowed = load_registry(REGISTRY_V20).allowed()
    full = _candles(3, 3400 + 100)
    for symbol in ("BTC-USDT", "ETH-USDT"):
        for end in range(3400, len(full) + 1, 20):
            for s in l2.calculate(_snap(symbol, full.iloc[end - 3400:end].reset_index(drop=True))):
                assert (s.symbol, s.combo) in allowed and s.registry_version == REGISTRY_V20


# ---------------------------------------------------------------- C. L2 -> L3 -> L4 regression (MOCK)
def _sig20(symbol, combo, side, close_time=1763003699999):
    reg = load_registry(REGISTRY_V20)
    spec = reg.get(symbol, combo)
    entry = PRICE[symbol]
    d = 1 if side == "BUY" else -1
    return V3Signal(event_id=f"{symbol}|{spec.native_timeframe}|{spec.token}|{side}|{close_time}", symbol=symbol,
                    combo=combo, combo_name=spec.token, side=side, timeframe=spec.native_timeframe,
                    close_time=close_time, entry=entry, tp=entry * (1 + d * spec.tp_pct), sl=entry * (1 - d * spec.sl_pct),
                    tp_pct=spec.tp_pct, sl_pct=spec.sl_pct, registry_version=reg.version, spec_hash=spec.spec_hash,
                    signal_variant=spec.signal_variant)


@pytest.mark.parametrize("symbol,combo,tp,sl", [("BTC-USDT", 9, 0.01, 0.004), ("ETH-USDT", 6, 0.02, 0.00666667),
                                                 ("ETH-USDT", 1, 0.02, 0.00666667), ("ETH-USDT", 101, 0.018, 0.009)])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_entry_gets_fixed_tp_sl_and_no_trailing(symbol, combo, tp, sl, side):
    _, store, client, *_r, executor = build()
    r = executor.execute(_sig20(symbol, combo, side))
    assert r["stage"] == "protected" and r["exit_mode"] == "fixed_tp_sl"
    d = 1 if side == "BUY" else -1
    assert r["tp_price"] == pytest.approx(r["avg_price"] * (1 + d * tp))
    assert r["sl_price"] == pytest.approx(r["avg_price"] * (1 - d * sl))
    assert [p["type"] for p in client.order_posts] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]   # no TRAILING_*
    ident = store.executions[r["event_id"]]["raw_entry"]["strategy"]
    assert ident["registry_version"] == REGISTRY_V20 and (ident["symbol"], ident["combo_id"]) == (symbol, combo)


def test_sl_failure_on_new_combo_force_closes():
    _, store, client, *_r, executor = build()
    client.leg_mode["STOP_MARKET"] = "reject"
    r = executor.execute(_sig20("BTC-USDT", 9, "BUY"))
    assert r["stage"] == "sl_failed"
    close = [p for p in client.order_posts if p["type"] == "MARKET"]
    assert len(close) == 1 and close[0]["quantity"] == r["executed_qty"]


def test_restart_and_switch_between_all_registries_never_duplicate():
    _, store, client, *_r, executor = build()
    s20 = _sig20("BTC-USDT", 9, "SELL")
    executor.execute(s20)
    *_x, executor2 = build(store=store, client=client)            # restarted process, same DB
    legacy = V3Signal(event_id=s20.event_id, symbol="BTC-USDT", combo=9, combo_name="C9", side="SELL", timeframe="15m",
                      close_time=s20.close_time, entry=s20.entry, tp=s20.tp, sl=s20.sl)   # 20FINAL rollback signal
    assert executor2.execute(legacy)["stage"] == "duplicate_skipped"
    assert executor2.execute(s20)["stage"] == "duplicate_skipped"
    assert len(client.entry_posts) == 1


def test_switching_registry_never_touches_open_positions():
    _, store, client, *_r, executor = build()
    executor.execute(_sig20("ETH-USDT", 6, "BUY"))
    posts_before = list(client.order_posts)
    for version in (REGISTRY_V20, REGISTRY_V18, REGISTRY_V20, "BROKEN"):
        L2Strategy(store, control=None, executor=executor, registry=version)
    assert client.order_posts == posts_before
    assert getattr(client, "cancels", []) == [] and getattr(client, "amends", []) == []


def test_l4_has_no_registry_dependency():
    """L4 manages positions from DB rows + BingX only, so no registry choice can orphan a position."""
    for f in sorted(LIVE.glob("l4_*.py")):
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"strategy_registry|l2_strategy|strategy_v18|enabled_combos|get_case", src), f.name


@pytest.mark.parametrize("opened_under,now_active", [(REGISTRY_V18, REGISTRY_V20), (REGISTRY_V20, REGISTRY_V18)])
def test_l4_keeps_managing_a_position_opened_under_a_previous_registry(opened_under, now_active):
    """e.g. legacy BTC C14 (combo 104, NOT in V20) opened under the V18 path; after switching, L4 still projects its close."""
    _, store, client, sender, alert, control, _reader, executor = build()
    if opened_under == REGISTRY_V18:
        entry = PRICE["BTC-USDT"]
        s = V3Signal(event_id="BTC-USDT|1h|C14|BUY|1763003699999", symbol="BTC-USDT", combo=104, combo_name="C14",
                     side="BUY", timeframe="1h", close_time=1763003699999, entry=entry, tp=entry * 1.02, sl=entry * 0.99333,
                     tp_pct=0.020, sl_pct=0.006666666666666667)
    else:
        s = _sig20("BTC-USDT", 9, "BUY")
    r = executor.execute(s)
    assert r["stage"] == "protected"
    L2Strategy(store, control, alert, executor, registry=now_active)          # restart with another registry
    ex = store.executions[r["event_id"]]
    client.orders[ex["tp_order_id"]].update({"status": "FILLED", "executedQty": str(ex["executed_qty"]),
                                             "avgPrice": str(ex["tp_price"]), "updateTime": 1763003600000,
                                             "profit": "1.0", "commission": "-0.03"})
    counts = project_results(store, control, client)
    assert counts["results"] == 1 and store.results[ex["tp_order_id"]]["close_type"] == "TP"


# ---------------------------------------------------------------- health / status
def test_health_and_status_expose_the_active_registry():
    import os
    if any(os.getenv(k) for k in ("DATABASE_URL", "BINGX_API_KEY")):
        pytest.skip("offline only")
    from fastapi.testclient import TestClient

    from autotrade_v3.live.app import Runtime, create_app
    rt = Runtime(settings=settings(bingx_api_key="", bingx_api_secret="", strategy_registry=REGISTRY_V20),
                 client=FakeBingX())
    with TestClient(create_app(rt)) as c:
        h = c.get("/health").json()["strategy_registry"]
        assert h["version"] == REGISTRY_V20 and h["count"] == 20 and h["spec_hash"] == V20_SPEC_HASH
        assert h["exit_policy"] == "FIXED_TP_SL" and h["signal_variants"] == {"ETH-USDT/1": "H1_BOTH_1"}
        assert len(h["combos"]["BTC-USDT"]) == len(h["combos"]["ETH-USDT"]) == 10
        st = c.get("/status").json()
        reg = st.get("strategy_registry") or st["runtime"]["strategy_registry"]
        assert reg["version"] == REGISTRY_V20 and reg["provenance"]["audit_id"] == "AUDIT_20_POSITIVE_FIXED_TPSL_20261116"
