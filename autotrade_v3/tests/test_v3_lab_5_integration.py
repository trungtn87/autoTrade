"""Comprehensive integration, parity, candle-only, and no-lookahead tests for V3_LAB_5@5.0.0."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Literal

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.filters.c15_v5_sell_atr140 import (
    C15_V5_ATR_RATIO_MAX,
    H1_MS,
    C15V5SellAtr140Filter,
    build_c15_1h_atr_ratio_lookup,
)
from autotrade_v3.lab import (
    V3_LAB_5_ACTIVE_VERSION,
    V3_LAB_5_STRATEGY_ID,
    generate_v3_lab_5_signals,
    load_v3_lab_4_config,
    load_v3_lab_4_strategy,
    load_v3_lab_5_config,
    load_v3_lab_5_strategy,
)
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256

FIXTURE_PATH = ROOT / "autotrade_v3" / "tests" / "fixtures" / "v3_lab_5_l2_parity_fixture.json"


def _sig_record(s: Signal) -> dict[str, Any]:
    return {
        "signal_id": s.signal_id,
        "symbol": s.symbol,
        "combo_id": int(s.combo_id),
        "combo_name": s.combo_name,
        "timeframe": s.timeframe,
        "side": s.side,
        "bar_open_ms": int(s.bar_open_ms),
        "signal_close_ms": int(s.signal_close_ms),
        "entry_price": round(float(s.entry_price), 8),
        "tp_pct": round(float(s.tp_pct), 10),
        "sl_pct": round(float(s.sl_pct), 10),
        "approved": bool(s.approved),
        "warmup_ready": bool(s.warmup_ready),
        "exit_policy_hash": str(s.exit_policy_hash),
    }


def _unpack_fixture_data(fixture: dict[str, Any]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for sym, cols in fixture["ohlcv_m15"].items():
        idx = pd.to_datetime(cols["timestamps_open_ms"], unit="ms", utc=True)
        out[sym] = pd.DataFrame(
            {
                "open": np.asarray(cols["open"], dtype=float),
                "high": np.asarray(cols["high"], dtype=float),
                "low": np.asarray(cols["low"], dtype=float),
                "close": np.asarray(cols["close"], dtype=float),
                "volume": np.asarray(cols["volume"], dtype=float),
                "provider": "SYNTH_FIXTURE",
            },
            index=idx,
        )
    return out


def _dummy_signal(symbol: str, combo_id: int, side: str, close_ms: int) -> Signal:
    side_lit: Literal["BUY", "SELL"] = "BUY" if side == "BUY" else "SELL"
    return Signal(
        symbol=symbol,
        combo_id=combo_id,
        combo_name=f"C{combo_id if combo_id < 100 else combo_id - 90}",
        timeframe="1h",
        side=side_lit,
        signal_close_ms=close_ms,
        signal_close_dt=pd.to_datetime(close_ms, unit="ms", utc=True),
        bar_position=100,
        bar_open_ms=close_ms - 900_000 + 1,
        entry_price=3000.0,
        tp_pct=0.02,
        sl_pct=0.008,
        tp_price=3060.0 if side_lit == "BUY" else 2940.0,
        sl_price=2976.0 if side_lit == "BUY" else 3024.0,
        approved=True,
        warmup_ready=True,
        smc_dir=-1,
        ob_blocked=False,
        provider="TEST",
        is_new6=combo_id >= 101,
    )


def test_v3_lab_5_registry_config_and_29_combo_inheritance() -> None:
    assert V3_LAB_5_ACTIVE_VERSION == "5.0.0"
    assert V3_LAB_5_STRATEGY_ID == "V3_LAB_5@5.0.0"

    cfg5 = load_v3_lab_5_config()
    cfg411 = load_v3_lab_4_config(version="4.1.1")
    assert cfg5["config_id"] == "V3_LAB_5"
    assert cfg5["version"] == "5.0.0"
    assert cfg5["inherits_from"] == "V3_LAB_4@4.1.1"
    assert cfg5["live_eligible"] is False
    assert cfg5["top20_live_modified"] is False
    assert cfg5["total_combos"] == 30
    assert len(cfg5["combos"]) == 30
    assert cfg5["live_governance"]["live_combo_count"] == 20
    assert cfg5["live_governance"]["live_combo_count_modified_by_v5"] is False

    keys5 = {(c["symbol"], int(c["combo_id"])) for c in cfg5["combos"]}
    keys411 = {(c["symbol"], int(c["combo_id"])) for c in cfg411["combos"]}
    assert keys5 == keys411

    for sym, cid in keys5 - {("ETH-USDT", 105)}:
        c5 = next(c for c in cfg5["combos"] if (c["symbol"], int(c["combo_id"])) == (sym, cid))
        c4 = next(c for c in cfg411["combos"] if (c["symbol"], int(c["combo_id"])) == (sym, cid))
        assert c5 == c4

    c15 = next(c for c in cfg5["combos"] if c["symbol"] == "ETH-USDT" and int(c["combo_id"]) == 105)
    assert c15["execution_version"] == "V5_SELL_ONLY_ATR140"
    assert c15["tp_pct"] == pytest.approx(0.02)
    assert c15["sl_pct"] == pytest.approx(0.008)
    assert c15["trailing_enabled"] is False
    ov15 = cfg5["overrides"]["ETH-USDT/C15"]
    assert ov15["buy"]["enabled"] is False
    assert ov15["sell"]["atr_ratio_50_lte"] == pytest.approx(C15_V5_ATR_RATIO_MAX)
    assert ov15["sell"]["post_tp_cooldown_minutes"] == 0
    assert ov15["candle_only"] is True
    assert ov15["stateful_controller_removed"] is True

    strat5 = load_v3_lab_5_strategy()
    strat411 = load_v3_lab_4_strategy(version="4.1.1")
    assert isinstance(strat5, V3Lab5Strategy)
    assert StrategyRegistry.get("V3_LAB_5", "5.0.0") is strat5
    for ver in ("4.0.0", "4.0.1", "4.1.0", "4.1.1"):
        assert StrategyRegistry.get("V3_LAB_4", ver).version == ver

    for sym, cid in keys5:
        assert strat5.get_exit_policy(sym, cid).get_policy_hash() == strat411.get_exit_policy(sym, cid).get_policy_hash()


def test_c15_v5_filter_unit_boundaries() -> None:
    close_ms_keep = 1_700_003_599_999
    close_ms_drop = 1_700_007_199_999
    close_ms_nan = 1_700_010_799_999
    close_ms_miss = 1_700_014_399_999

    flt = C15V5SellAtr140Filter(
        {
            close_ms_keep: 1.40,
            close_ms_drop: 1.400001,
            close_ms_nan: float("nan"),
        }
    )

    # Non-C15 passthrough
    assert flt.evaluate(_dummy_signal("BTC-USDT", 105, "BUY", close_ms_miss)).action == "KEEP"
    assert flt.evaluate(_dummy_signal("ETH-USDT", 14, "BUY", close_ms_miss)).action == "KEEP"

    # C15 BUY always dropped even when ratio <= 1.40
    dec_buy = flt.evaluate(_dummy_signal("ETH-USDT", 105, "BUY", close_ms_keep))
    assert dec_buy.action == "DROP"
    assert dec_buy.reason == "BLOCKED_V5_BUY_DISABLED"

    # C15 SELL boundary <= 1.40
    assert flt.evaluate(_dummy_signal("ETH-USDT", 105, "SELL", close_ms_keep)).action == "KEEP"
    dec_gt = flt.evaluate(_dummy_signal("ETH-USDT", 105, "SELL", close_ms_drop))
    assert dec_gt.action == "DROP"
    assert dec_gt.reason == "BLOCKED_V5_SELL_ATR_RATIO_GT_140"

    # Fail closed on NaN or missing closed 1H bar
    dec_nan = flt.evaluate(_dummy_signal("ETH-USDT", 105, "SELL", close_ms_nan))
    assert dec_nan.action == "DROP"
    assert dec_nan.reason == "BLOCKED_V5_NONFINITE_ATR_RATIO"

    dec_miss = flt.evaluate(_dummy_signal("ETH-USDT", 105, "SELL", close_ms_miss))
    assert dec_miss.action == "DROP"
    assert dec_miss.reason == "BLOCKED_V5_MISSING_1H_BAR"

    # Verify warmup of build_c15_1h_atr_ratio_lookup (14 + 50 bars)
    idx_1h = pd.date_range("2025-01-01", periods=70, freq="1h", tz="UTC")
    df_1h = pd.DataFrame(
        {
            "open": np.full(70, 3000.0),
            "high": np.full(70, 3010.0),
            "low": np.full(70, 2990.0),
            "close": np.full(70, 3000.0),
            "volume": np.full(70, 100.0),
        },
        index=idx_1h,
    )
    lookup = build_c15_1h_atr_ratio_lookup(df_1h)
    assert len(lookup) == 70
    sorted_keys = sorted(lookup)
    assert sorted_keys[0] % H1_MS == H1_MS - 1
    assert np.isnan(lookup[sorted_keys[0]])
    assert lookup[sorted_keys[-1]] == pytest.approx(1.0)


def test_v3_lab_5_candle_only_ast_and_no_controller() -> None:
    forbidden_roots = (
        "scripts",
        "autotrade_v3.sim",
        "autotrade_v3.live",
        "autotrade_v2",
        "duckdb",
        "sqlite3",
        "requests",
        "httpx",
        "urllib",
        "pybit",
        "ccxt",
    )
    checked_paths = (
        ROOT / "autotrade_v3" / "filters" / "c15_v5_sell_atr140.py",
        ROOT / "autotrade_v3" / "strategy" / "v3_lab_5.py",
        ROOT / "autotrade_v3" / "lab" / "v3_lab_5_loader.py",
    )
    for path in checked_paths:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        assert "build_c15_h3_controller" not in src
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_roots), f"Forbidden import {alias.name} in {path}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_roots), f"Forbidden import-from {node.module} in {path}"
            elif isinstance(node, ast.Name):
                assert node.id != "H3AsymmetricController"
            elif isinstance(node, ast.Attribute):
                assert node.attr != "H3AsymmetricController"


def test_v3_lab_5_simulator_independence_subprocess() -> None:
    snippet = (
        "import sys, json; from pathlib import Path; import numpy as np, pandas as pd\n"
        "BLOCKED = [\n"
        "    'autotrade_v3.sim.engine', 'autotrade_v3.sim.equity', 'autotrade_v3.sim.portfolio',\n"
        "    'autotrade_v3.sim.costs', 'autotrade_v3.engine.execution_simulator',\n"
        "    'autotrade_v3.live', 'autotrade_v2', 'scripts.run_v3_lab_2_eth_c15_h3_asymmetric',\n"
        "]\n"
        "class Blocker:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if any(fullname == b or fullname.startswith(b + '.') for b in BLOCKED):\n"
        "            raise ImportError(f'Blocked {fullname}')\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy\n"
        "BLOCKED.append('scripts')\n"
        "assert 'scripts.run_v3_lab_2_eth_c15_h3_asymmetric' not in sys.modules\n"
        "if 'duckdb' in sys.modules:\n"
        "    sys.modules['duckdb'].connect = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('DB forbidden'))\n"
        "if 'autotrade_v3.sim.leg' in sys.modules:\n"
        "    sys.modules['autotrade_v3.sim.leg'].simulate_leg = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('Simulator forbidden'))\n"
        f"fix = json.loads(Path(r'{FIXTURE_PATH}').read_text(encoding='utf-8'))\n"
        "data = {}\n"
        "for sym, cols in fix['ohlcv_m15'].items():\n"
        "    idx = pd.to_datetime(cols['timestamps_open_ms'], unit='ms', utc=True)\n"
        "    data[sym] = pd.DataFrame({k: cols[k] for k in ('open','high','low','close','volume')}, index=idx)\n"
        "sigs = V3Lab5Strategy(version='5.0.0').generate_signals(data)\n"
        "assert len(sigs) == fix['expected']['approved_signal_count']\n"
        "print('SUBPROCESS_SIM_INDEPENDENT_OK', len(sigs))\n"
    )
    proc = subprocess.run([sys.executable, "-c", snippet], capture_output=True, text=True, cwd=str(ROOT), check=False)
    assert proc.returncode == 0, f"stderr={proc.stderr}\nstdout={proc.stdout}"
    assert "SUBPROCESS_SIM_INDEPENDENT_OK" in proc.stdout


def test_v3_lab_5_parity_fixture_no_lookahead_determinism_and_subsets() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    data = _unpack_fixture_data(fixture)

    cfg5 = load_v3_lab_5_config()
    strat5 = load_v3_lab_5_strategy()
    strat411 = load_v3_lab_4_strategy(version="4.1.1")
    assert fixture["config_hash"] == cfg5["config_hash"]
    assert fixture["spec_hash"] == strat5.get_spec_hash()

    # Determinism across repeated runs + full fixture parity
    app_a = strat5.generate_signals(data, only_approved=True, only_warmup_ready=True)
    app_b = strat5.generate_signals(data, only_approved=True, only_warmup_ready=True)
    recs_a = [_sig_record(s) for s in app_a]
    recs_b = [_sig_record(s) for s in app_b]
    assert recs_a == recs_b == fixture["expected"]["approved_signals"]
    assert len(recs_a) == fixture["expected"]["approved_signal_count"]
    assert canonical_json_sha256(recs_a) == fixture["expected"]["approved_signals_sha256"]

    # Raw signal mode marks dropped C15 BUY/high-ATR signals with approved=False
    raw5 = strat5.generate_signals(data, only_approved=False, only_warmup_ready=True)
    raw_recs = [_sig_record(s) for s in raw5]
    assert len(raw_recs) == fixture["expected"]["raw_signal_count"]
    assert canonical_json_sha256(raw_recs) == fixture["expected"]["raw_signals_sha256"]
    c15_raw_buys = [s for s in raw5 if s.symbol == "ETH-USDT" and int(s.combo_id) == 105 and s.side == "BUY"]
    assert len(c15_raw_buys) > 0
    assert all(not s.approved for s in c15_raw_buys)

    # 29-combo parity vs V3_LAB_4@4.1.1 + C15 V5 decision parity
    app411 = strat411.generate_signals(data, only_approved=True, only_warmup_ready=True)
    non15_5 = [_sig_record(s) for s in app_a if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 105)]
    non15_4 = [_sig_record(s) for s in app411 if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 105)]
    assert non15_5 == non15_4

    c15_ob4 = [s for s in app411 if s.symbol == "ETH-USDT" and int(s.combo_id) == 105]
    kept_c15, dropped_c15 = strat5.evaluate_c15_v5_decisions(c15_ob4, data["ETH-USDT"])
    assert len(kept_c15) == fixture["expected"]["c15_v5_kept_count"]
    reason_counts: dict[str, int] = {}
    for _, reason in dropped_c15:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    assert reason_counts == fixture["expected"]["c15_v5_dropped_reason_counts"]

    # Strict no-lookahead test: corrupt bars strictly after cutoff bar 3800
    cutoff_pos = 3800
    cutoff_ts = pd.DatetimeIndex([data["ETH-USDT"].index[cutoff_pos]]).tz_convert("UTC").as_unit("ms")
    cutoff_close_ms = int(cutoff_ts.asi8[0]) + 900_000 - 1
    perturbed: dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        df_mod = df.copy()
        df_mod.iloc[cutoff_pos + 1 :, df_mod.columns.get_loc("open")] *= 1.75
        df_mod.iloc[cutoff_pos + 1 :, df_mod.columns.get_loc("high")] *= 1.90
        df_mod.iloc[cutoff_pos + 1 :, df_mod.columns.get_loc("low")] *= 0.65
        df_mod.iloc[cutoff_pos + 1 :, df_mod.columns.get_loc("close")] *= 1.70
        df_mod.iloc[cutoff_pos + 1 :, df_mod.columns.get_loc("volume")] *= 4.0
        perturbed[sym] = df_mod
    app_pert = strat5.generate_signals(perturbed, only_approved=True, only_warmup_ready=True)
    before_orig = [_sig_record(s) for s in app_a if int(s.signal_close_ms) <= cutoff_close_ms]
    before_pert = [_sig_record(s) for s in app_pert if int(s.signal_close_ms) <= cutoff_close_ms]
    assert len(before_orig) > 0
    assert before_orig == before_pert

    # Symbols and allowed_combos subset tests
    btc_only = strat5.generate_signals(data, symbols=["BTC-USDT"])
    assert btc_only and all(s.symbol == "BTC-USDT" for s in btc_only)
    assert btc_only == [s for s in app_a if s.symbol == "BTC-USDT"]

    subset = {("ETH-USDT", 105), ("BTC-USDT", 14)}
    sub_sigs = generate_v3_lab_5_signals(data, allowed_combos=subset)
    assert sub_sigs == [s for s in app_a if (s.symbol, int(s.combo_id)) in subset]
    assert generate_v3_lab_5_signals(data, allowed_combos=set()) == []
