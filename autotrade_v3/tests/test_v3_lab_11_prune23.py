"""Unit & signal-parity tests for V3_LAB_11_PRUNE23@11.0.0 (23 kept combos)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.lab import (
    V3_LAB_11_PRUNE23_ACTIVE_VERSION,
    V3_LAB_11_PRUNE23_STRATEGY_ID,
    load_v3_lab_11_prune23_config,
    load_v3_lab_11_prune23_strategy,
    load_v3_lab_11_strategy,
)
from autotrade_v3.strategy import StrategyRegistry, V3Lab11Prune23Strategy
from autotrade_v3.strategy.v3_lab_11_prune23 import PRUNE23_EXCLUDED_COMBOS


def _make_synthetic_m15(n_bars: int = 3000) -> dict[str, pd.DataFrame]:
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="15min", tz="UTC")
    rng = np.random.default_rng(123)
    out: dict[str, pd.DataFrame] = {}
    for sym, base in (("BTC-USDT", 45000.0), ("ETH-USDT", 2500.0)):
        steps = rng.normal(0.0, base * 0.003, size=n_bars)
        closes = base + np.cumsum(steps)
        opens = np.roll(closes, 1)
        opens[0] = base
        highs = np.maximum(opens, closes) + np.abs(rng.normal(0.0, base * 0.002, size=n_bars))
        lows = np.minimum(opens, closes) - np.abs(rng.normal(0.0, base * 0.002, size=n_bars))
        vols = rng.uniform(50.0, 500.0, size=n_bars)
        out[sym] = pd.DataFrame(
            {
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": vols,
            },
            index=idx,
        )
    return out


def test_v3_lab_11_prune23_registry_and_config_parity() -> None:
    strat = load_v3_lab_11_prune23_strategy()
    assert isinstance(strat, V3Lab11Prune23Strategy)
    assert strat.strategy_id == V3_LAB_11_PRUNE23_STRATEGY_ID == "V3_LAB_11_PRUNE23"
    assert strat.version == V3_LAB_11_PRUNE23_ACTIVE_VERSION == "11.0.0"

    reg = StrategyRegistry.get("V3_LAB_11_PRUNE23", "11.0.0")
    assert isinstance(reg, V3Lab11Prune23Strategy)

    s11 = load_v3_lab_11_strategy()
    cfg23 = load_v3_lab_11_prune23_config()
    assert len(cfg23["combos"]) == 23
    assert len(strat.spec().combos) == 23
    assert strat.get_spec_hash() != s11.get_spec_hash()

    kept_keys = {(str(c["symbol"]), int(c["combo_id"])) for c in cfg23["combos"]}
    assert kept_keys.isdisjoint(PRUNE23_EXCLUDED_COMBOS)
    assert ("ETH-USDT", 1) in kept_keys
    assert ("BTC-USDT", 1) in kept_keys
    assert sum(1 for sym, _ in kept_keys if sym == "BTC-USDT") == 12
    assert sum(1 for sym, _ in kept_keys if sym == "ETH-USDT") == 11

    for sym, cid in kept_keys:
        assert strat.get_combo_config(sym, cid) == s11.get_combo_config(sym, cid)
        assert strat.get_exit_policy(sym, cid) == s11.get_exit_policy(sym, cid)


def test_v3_lab_11_prune23_signal_parity_with_v3_lab_11() -> None:
    m15 = _make_synthetic_m15(3500)
    s11 = load_v3_lab_11_strategy()
    s23 = load_v3_lab_11_prune23_strategy()

    sigs_11 = s11.generate_signals(m15, symbols=["BTC-USDT", "ETH-USDT"], only_approved=True, only_warmup_ready=True)
    sigs_23 = s23.generate_signals(m15, symbols=["BTC-USDT", "ETH-USDT"], only_approved=True, only_warmup_ready=True)

    # Zero signals from excluded 7 combos
    assert all((s.symbol, int(s.combo_id)) not in PRUNE23_EXCLUDED_COMBOS for s in sigs_23)

    # 100% exact parity on all 23 kept combos
    expected_23 = [s for s in sigs_11 if (s.symbol, int(s.combo_id)) not in PRUNE23_EXCLUDED_COMBOS]
    assert len(sigs_23) == len(expected_23)
    for a, b in zip(sigs_23, expected_23):
        assert a.symbol == b.symbol
        assert a.combo_id == b.combo_id
        assert a.side == b.side
        assert a.signal_close_ms == b.signal_close_ms
        assert a.tp_pct == b.tp_pct
        assert a.sl_pct == b.sl_pct
        assert a.smc_dir == b.smc_dir
        assert a.ob_blocked == b.ob_blocked
        assert a.exit_policy_hash == b.exit_policy_hash
