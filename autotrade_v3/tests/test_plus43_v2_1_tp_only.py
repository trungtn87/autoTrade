"""Unit & integration tests for PLUS43 V2.1 TP-Only (PORT_FINAL_TP_ONLY · Zero-Drop · BingX $100)."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
LAB31 = ROOT / "research" / "v3_lab_11_plus31"
if str(LAB31) not in sys.path:
    sys.path.insert(0, str(LAB31))

from autotrade_v3.reconcile_v3.keys import (
    ComboVerificationError,
    build_combo_table,
    event_id_to_signal_id,
    signal_id_to_event_id,
    verify_live_signals_against_combos,
)
from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy
from autotrade_v3.reconcile_v3.era_engines import ensure_fixed_v1_registered

EXPECTED_UPGRADES = {
    "TV03": {"symbol": "BTC-USDT", "tf": "H4", "orig_tp": 0.03, "new_tp": 0.024, "sl": 0.015},
    "TV07": {"symbol": "ETH-USDT", "tf": "H4", "orig_tp": 0.036, "new_tp": 0.045, "sl": 0.015},
    "TV08": {"symbol": "ETH-USDT", "tf": "H4", "orig_tp": 0.036, "new_tp": 0.045, "sl": 0.015},
    "TV09": {"symbol": "ETH-USDT", "tf": "H4", "orig_tp": 0.03, "new_tp": 0.0375, "sl": 0.015},
    "TV12": {"symbol": "BTC-USDT", "tf": "H8", "orig_tp": 0.03, "new_tp": 0.0375, "sl": 0.0125},
    "TV20": {"symbol": "BTC-USDT", "tf": "H12", "orig_tp": 0.03, "new_tp": 0.0375, "sl": 0.0125},
    "TV24": {"symbol": "ETH-USDT", "tf": "H12", "orig_tp": 0.036, "new_tp": 0.045, "sl": 0.015},
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_plus43_v2_1_tp_registry_and_manifest_invariants() -> None:
    reg_dir = LAB31 / "registry_v2"
    v2_df = pd.read_csv(reg_dir / "TV20_PLUS43_V2_REGISTRY.csv").sort_values("display_id").reset_index(drop=True)
    v2_1_df = pd.read_csv(reg_dir / "TV20_PLUS43_V2_1_TP_REGISTRY.csv").sort_values("display_id").reset_index(drop=True)
    manifest = json.loads((reg_dir / "PLUS43_V2_1_TP_MANIFEST.json").read_text(encoding="utf-8"))

    # 20 TV20 + 23 PRUNE23 = 43 combos
    ensure_fixed_v1_registered()
    prune_combos = list(V3Lab11Prune23Strategy().spec().combos)
    assert len(prune_combos) == 23
    assert len(v2_1_df) == 20 and v2_1_df["display_id"].nunique() == 20
    for c in prune_combos:
        assert float(c.exit_policy.sl_pct) <= 0.015 + 1e-12

    # Manifest metadata and rollback SHA-256 integrity
    assert manifest["version_tag"] == "PLUS43_V2_1_TP_ONLY"
    assert manifest["upgraded_combos_count"] == 7
    assert manifest["invariants"]["notional_usd"] == 100.0
    assert manifest["invariants"]["max_sl_pct"] == 0.015
    assert manifest["invariants"]["tv21_unchanged"] is True
    assert manifest["invariants"]["tv21_breakeven"] is False
    assert manifest["registry_v2_1_sha256"] == _sha256(reg_dir / "TV20_PLUS43_V2_1_TP_REGISTRY.csv")
    assert manifest["rollback"]["v2_tv28_h8"]["registry_sha256"] == _sha256(reg_dir / "TV20_PLUS43_V2_REGISTRY.csv")
    assert manifest["rollback"]["v1_freeze"]["registry_sha256"] == _sha256(
        LAB31 / "freeze" / "TV31_SELECTED_REGISTRY.csv"
    )

    # Exact 7 TP changes; other 13 rows untouched
    for r2, r21 in zip(v2_df.to_dict("records"), v2_1_df.to_dict("records")):
        did = str(r21["display_id"])
        assert float(r21["sl_pct"]) <= 0.015 + 1e-12
        if did in EXPECTED_UPGRADES:
            sp = EXPECTED_UPGRADES[did]
            assert r21["new_combo_id"] == f"{did}_V2_1_TP"
            assert r21["symbol"] == sp["symbol"]
            assert r21["timeframe"] == sp["tf"]
            assert float(r21["tp_pct"]) == pytest.approx(sp["new_tp"], abs=1e-12)
            assert float(r21["sl_pct"]) == pytest.approx(sp["sl"], abs=1e-12)
            assert float(r2["tp_pct"]) == pytest.approx(sp["orig_tp"], abs=1e-12)
            for col in ("strategy_id", "variant", "variant_id", "side_scope", "filter", "warmup_bars"):
                assert r21[col] == r2[col]
        else:
            assert r21 == r2

    tv21 = v2_1_df.loc[v2_1_df["display_id"] == "TV21"].iloc[0]
    assert float(tv21["tp_pct"]) == pytest.approx(0.036, abs=1e-12)
    assert float(tv21["sl_pct"]) == pytest.approx(0.015, abs=1e-12)

    tv28 = v2_1_df.loc[v2_1_df["display_id"] == "TV28"].iloc[0]
    assert tv28["new_combo_id"] == "TV28_V2_H8"
    assert tv28["symbol"] == "BTC-USDT"
    assert tv28["timeframe"] == "H8"
    assert float(tv28["tp_pct"]) == pytest.approx(0.03, abs=1e-12)
    assert float(tv28["sl_pct"]) == pytest.approx(0.012, abs=1e-12)
    assert int(tv28["warmup_bars"]) == 120


def test_plus43_v2_1_tp_keys_zero_collision_and_historical_preservation() -> None:
    table = build_combo_table()

    # Historical keys preserved
    assert table[("BTC-USDT", "TV28")].native_timeframe == "D1"
    assert table[("BTC-USDT", "TV28_V2_H8")].native_timeframe == "H8"
    for did, sp in EXPECTED_UPGRADES.items():
        assert (sp["symbol"], did) in table
        assert float(table[(sp["symbol"], did)].exit_policy.tp_pct) == pytest.approx(sp["orig_tp"], abs=1e-12)

    # All 7 V2.1 TP keys present with new TP% and distinct combo_ids
    v21_cids: list[int] = []
    hist_cids = {table[(sp["symbol"], did)].combo_id for did, sp in EXPECTED_UPGRADES.items()}
    hist_cids.add(table[("BTC-USDT", "TV28_V2_H8")].combo_id)
    for did, sp in EXPECTED_UPGRADES.items():
        version_id = f"{did}_V2_1_TP"
        spec = table[(sp["symbol"], version_id)]
        assert spec.native_timeframe == sp["tf"]
        assert float(spec.exit_policy.tp_pct) == pytest.approx(sp["new_tp"], abs=1e-12)
        assert float(spec.exit_policy.sl_pct) == pytest.approx(sp["sl"], abs=1e-12)
        v21_cids.append(spec.combo_id)
        assert spec.combo_id not in hist_cids

        eid = f"{sp['symbol']}|{sp['tf']}|{version_id}|BUY|1795593599999"
        sid = event_id_to_signal_id(eid)
        assert signal_id_to_event_id(sid) == eid

        entry = Decimal("50000.0")
        tp = entry * (Decimal("1") + Decimal(str(sp["new_tp"])))
        sl = entry * (Decimal("1") - Decimal(str(sp["sl"])))
        resolved = verify_live_signals_against_combos(
            [{"event_id": eid, "symbol": sp["symbol"], "combo": version_id, "side": "BUY", "entry": str(entry), "tp": str(tp), "sl": str(sl)}],
            table,
        )
        assert eid in resolved

        # Old TP% must fail against V2_1_TP key
        old_tp = entry * (Decimal("1") + Decimal(str(sp["orig_tp"])))
        with pytest.raises(ComboVerificationError):
            verify_live_signals_against_combos(
                [{"event_id": eid, "symbol": sp["symbol"], "combo": version_id, "side": "BUY", "entry": str(entry), "tp": str(old_tp), "sl": str(sl)}],
                table,
            )

    assert len(set(v21_cids)) == 7


def test_plus43_v2_1_tp_dry_run_signals_and_no_unclosed_leakage() -> None:
    import run_backtest as core
    import run_plus43_forward as p43_fwd

    core.configure_tv_lab()
    reg_v2_1 = pd.read_csv(LAB31 / "registry_v2" / "TV20_PLUS43_V2_1_TP_REGISTRY.csv")
    m1_fwd_dict, max_m1_ms = p43_fwd._load_combined_m1(ROOT / "data" / "bingx")
    table = build_combo_table()

    bars_cache: dict[tuple[str, int], pd.DataFrame] = {}
    all_event_ids: list[str] = []
    all_signal_ids: list[str] = []

    for row in reg_v2_1.to_dict("records"):
        did = str(row["display_id"])
        if did not in EXPECTED_UPGRADES:
            continue
        sp = EXPECTED_UPGRADES[did]
        sym = str(row["symbol"])
        tf = str(row["timeframe"])
        tf_min = int(row["timeframe_minutes"])
        version_id = str(row["new_combo_id"])

        if (sym, tf_min) not in bars_cache:
            bars = p43_fwd._resample_closed_safe(m1_fwd_dict[sym], tf_min)
            last_open_ms = int(bars.index[-1].value // 10**6)
            assert last_open_ms + tf_min * 60_000 - 1 <= max_m1_ms + 59_999
            bars_cache[(sym, tf_min)] = bars
        bars = bars_cache[(sym, tf_min)]

        ev = core.tvlab.PORT_CLASSES[str(row["strategy_id"])]().evaluate(bars, str(row["variant"]))
        warmup = int(row["warmup_bars"])
        if warmup and len(ev) > warmup:
            ev.loc[ev.index[:warmup], "signal"] = 0
        ev = ev.join(bars[["open", "high", "low", "close", "volume", "provider"]])
        tr = core.tvlab.apply_variant(ev, str(row["variant_id"]))
        sigs = core.tvlab.make_signals(tr, row, float(row["tp_pct"]), float(row["sl_pct"]), str(row["variant_id"]))
        assert len(sigs) > 0, f"Expected closed-bar dry-run signals for {did}"

        batch = []
        for s in sigs[-5:]:
            entry = float(s.entry_price)
            tp = float(s.tp_price)
            sl = float(s.sl_price)
            assert abs(abs(tp - entry) / entry - sp["new_tp"]) < 1e-9
            assert abs(abs(sl - entry) / entry - sp["sl"]) < 1e-9
            eid = f"{sym}|{tf}|{version_id}|{s.side}|{s.signal_close_ms}"
            sid = event_id_to_signal_id(eid)
            assert signal_id_to_event_id(sid) == eid
            all_event_ids.append(eid)
            all_signal_ids.append(sid)
            batch.append({"event_id": eid, "symbol": sym, "combo": version_id, "side": s.side, "entry": str(entry), "tp": str(tp), "sl": str(sl)})
        resolved = verify_live_signals_against_combos(batch, table)
        assert len(resolved) == len(batch)

    assert len(set(all_event_ids)) == len(all_event_ids)
    assert len(set(all_signal_ids)) == len(all_signal_ids)
