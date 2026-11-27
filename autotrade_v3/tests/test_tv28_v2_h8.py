"""Unit & integration tests for TV28 v2 (BTC-USDT / H8 / E1_FIXED / $100)."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LAB31 = ROOT / "research" / "v3_lab_11_plus31"
if str(LAB31) not in sys.path:
    sys.path.insert(0, str(LAB31))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.reconcile_v3.keys import (
    build_combo_table,
    event_id_to_signal_id,
    signal_id_to_event_id,
    verify_live_signals_against_combos,
)


def test_tv28_v2_registry_and_untouched_freeze() -> None:
    freeze_df = pd.read_csv(LAB31 / "freeze" / "TV31_SELECTED_REGISTRY.csv")
    v2_df = pd.read_csv(LAB31 / "registry_v2" / "TV20_PLUS43_V2_REGISTRY.csv")
    manifest = json.loads((LAB31 / "registry_v2" / "TV28_V2_H8_MANIFEST.json").read_text(encoding="utf-8"))

    # Historical freeze remains D1
    old_tv28 = freeze_df.loc[freeze_df["display_id"] == "TV28"].iloc[0]
    assert old_tv28["timeframe"] == "D1"
    assert int(old_tv28["timeframe_minutes"]) == 1440

    # V2 registry has 20 kept TV combos with TV28 upgraded to H8
    assert len(v2_df) == 20 and v2_df["display_id"].nunique() == 20
    new_tv28 = v2_df.loc[v2_df["display_id"] == "TV28"].iloc[0]
    assert new_tv28["new_combo_id"] == "TV28_V2_H8"
    assert new_tv28["symbol"] == "BTC-USDT"
    assert new_tv28["timeframe"] == "H8"
    assert int(new_tv28["timeframe_minutes"]) == 480
    assert float(new_tv28["tp_pct"]) == 0.03
    assert float(new_tv28["sl_pct"]) == 0.012
    assert int(new_tv28["warmup_bars"]) == 120
    assert manifest["version_tag"] == "TV28_V2_H8"
    assert manifest["notional_usd"] == 100.0


def test_tv28_v2_keys_and_peer_isolation() -> None:
    eid = "BTC-USDT|H8|TV28_V2_H8|BUY|1795593599999"
    sid = event_id_to_signal_id(eid)
    assert sid == "SIG_BTCUSDT_TV28_V2_H8_H8_BUY_1795593599999"
    assert signal_id_to_event_id(sid) == eid

    table = build_combo_table()
    # Both historical D1 TV28 and v2 H8 TV28_V2_H8 coexist without collision
    assert ("BTC-USDT", "TV28") in table
    assert ("BTC-USDT", "TV28_V2_H8") in table
    assert ("BTC-USDT", "TV10") in table
    assert ("BTC-USDT", "TV12") in table

    assert table[("BTC-USDT", "TV28")].native_timeframe == "D1"
    v2_spec = table[("BTC-USDT", "TV28_V2_H8")]
    assert v2_spec.native_timeframe == "H8"
    assert float(v2_spec.exit_policy.tp_pct) == 0.03
    assert float(v2_spec.exit_policy.sl_pct) == 0.012

    # Distinct combo_ids across TV10, TV12, TV28_V2_H8
    cids = {
        table[("BTC-USDT", "TV10")].combo_id,
        table[("BTC-USDT", "TV12")].combo_id,
        v2_spec.combo_id,
    }
    assert len(cids) == 3

    resolved = verify_live_signals_against_combos(
        [
            {
                "event_id": eid,
                "symbol": "BTC-USDT",
                "combo": "TV28_V2_H8",
                "side": "BUY",
                "entry": "90000.0",
                "tp": str(Decimal("90000.0") * Decimal("1.03")),
                "sl": str(Decimal("90000.0") * Decimal("0.988")),
            }
        ],
        table,
    )
    assert eid in resolved


def test_tv28_v2_h8_alignment_and_causality() -> None:
    import run_backtest as core
    import run_plus43_forward as p43_fwd

    core.configure_tv_lab()
    m1_btc = DataLayer().query_m1("BTC-USDT", "2024-01-01", "2025-06-01")
    bars_h8 = core.tvlab.resample_m1_closed(m1_btc, 480)
    assert sorted({int(ts.hour) for ts in bars_h8.index}) == [0, 8, 16]

    # Partial trailing H8 bar must be trimmed by _resample_closed_safe
    partial_m1 = m1_btc.iloc[:-100]
    safe_h8 = p43_fwd._resample_closed_safe(partial_m1, 480)
    last_open_ms = int(safe_h8.index[-1].value // 10**6)
    last_m1_ms = int(partial_m1.index[-1].value // 10**6)
    assert last_open_ms + 480 * 60_000 - 1 <= last_m1_ms

    # Causality: future perturbation after cut does not alter signals <= cut
    cut = int(len(bars_h8) * 0.70)
    pert = bars_h8.copy()
    pert.iloc[cut:, pert.columns.get_loc("close")] *= 1.29
    pert.iloc[cut:, pert.columns.get_loc("high")] = np.maximum(pert.iloc[cut:]["high"], pert.iloc[cut:]["close"])
    pert.iloc[cut:, pert.columns.get_loc("low")] = np.minimum(pert.iloc[cut:]["low"], pert.iloc[cut:]["close"])

    s1 = core.tvlab.PORT_CLASSES["L2_S1"]().evaluate(bars_h8, "B0")["signal"].to_numpy()
    s2 = core.tvlab.PORT_CLASSES["L2_S1"]().evaluate(bars_h8.iloc[:cut], "B0")["signal"].to_numpy()
    s3 = core.tvlab.PORT_CLASSES["L2_S1"]().evaluate(pert, "B0")["signal"].to_numpy()
    assert np.array_equal(s1[:cut], s2)
    assert np.array_equal(s1[:cut], s3[:cut])
