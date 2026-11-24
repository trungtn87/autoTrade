"""Kiểm thử đơn vị, không nhìn tương lai, đột biến và xác minh cho nghiên cứu V3-18."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.research_v3_18.combo_regime_analyzer import summarize_trade_slice
from autotrade_v3.research_v3_18.combos_30 import (
    CROSS30_SOURCE_COMMIT,
    CrossSymbol30FixedStrategy,
    build_30_combo_catalog,
    get_live_23_pairs,
    get_non_live_7_pairs,
)
from autotrade_v3.research_v3_18.labels_vi import find_english_label_leaks
from autotrade_v3.research_v3_18.portfolio_experiments import evaluate_portfolio_candidate_flag
from autotrade_v3.research_v3_18.regime_features import (
    ALL_REGIME_DIMENSIONS,
    compute_causal_hourly_regimes,
    load_research_config,
    map_timestamp_to_regime,
    validate_m15_continuity,
)
from autotrade_v3.research_v3_18.verify import DEFAULT_EVIDENCE_DIR, verify_evidence_dir


def _make_synthetic_m15(n_bars: int = 3600, seed: int = 20261118) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01T00:00:00Z", periods=n_bars, freq="15min", tz="UTC")
    rets = rng.normal(loc=0.0001, scale=0.004, size=n_bars)
    close = 40000.0 * np.exp(np.cumsum(rets))
    open_px = np.concatenate(([40000.0], close[:-1]))
    spread = np.abs(rng.normal(loc=0.0015, scale=0.001, size=n_bars)) * close
    high = np.maximum(open_px, close) + spread
    low = np.minimum(open_px, close) - spread
    vol = rng.uniform(50.0, 500.0, size=n_bars)
    return pd.DataFrame(
        {
            "open": open_px,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "provider": ["SYNTH"] * n_bars,
        },
        index=idx,
    )


def _df_sha256(df: pd.DataFrame) -> str:
    payload = df.to_json(orient="split", date_unit="ms")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_catalog_30_combos_exact_23_live_and_7_non_live() -> None:
    catalog = build_30_combo_catalog()
    assert len(catalog) == 30
    live = [(r.symbol, r.combo_id) for r in catalog if r.is_live_23]
    non_live = [(r.symbol, r.combo_id) for r in catalog if not r.is_live_23]
    assert len(live) == 23
    assert len(non_live) == 7
    assert set(live) == get_live_23_pairs()
    assert set(non_live) == {(s, cid) for s, cid, _ in get_non_live_7_pairs()}
    assert sum(1 for r in catalog if r.audit_20_status == "DƯƠNG_20") == 20
    assert sum(1 for r in catalog if r.audit_20_status == "ÂM_10") == 10
    for r in catalog:
        assert len(r.source_commit_cross30) >= 7
        assert CROSS30_SOURCE_COMMIT.startswith(r.source_commit_cross30)
        assert r.tp_pct > 0.0
        assert r.sl_pct > 0.0
        assert json.loads(r.entry_params_json)


def test_strict_no_lookahead_hourly_regimes_and_timestamp_mapping() -> None:
    cfg = load_research_config()
    df_m15 = _make_synthetic_m15(n_bars=3600)
    full_hourly = compute_causal_hourly_regimes(df_m15, config=cfg, enforce_continuity=True)

    for cut_hours in (750, 810, 860):
        cut_m15 = cut_hours * 4
        t_close_ms = int(full_hourly.iloc[cut_hours - 1]["close_ms"])

        trunc_hourly = compute_causal_hourly_regimes(df_m15.iloc[:cut_m15].copy(), config=cfg, enforce_continuity=True)
        assert len(trunc_hourly) == cut_hours

        mutated_m15 = df_m15.copy()
        mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("open")] *= 3.5
        mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("high")] *= 5.0
        mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("low")] *= 0.2
        mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("close")] *= 4.0
        mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("volume")] *= 100.0
        mut_hourly = compute_causal_hourly_regimes(mutated_m15, config=cfg, enforce_continuity=True)

        full_prefix = full_hourly.loc[full_hourly["close_ms"] <= t_close_ms]
        mut_prefix = mut_hourly.loc[mut_hourly["close_ms"] <= t_close_ms]
        assert len(full_prefix) == len(trunc_hourly) == len(mut_prefix) == cut_hours

        for col in ALL_REGIME_DIMENSIONS + ("open_ms", "close_ms"):
            assert list(full_prefix[col]) == list(trunc_hourly[col]) == list(mut_prefix[col])

        for col in ("ema50_1h", "ema150_1h", "ema150_4h", "slope_4h_pct", "atr14_1h", "atr_ratio_50", "atr_pct_rank_720", "vol_ratio_20"):
            np.testing.assert_allclose(full_prefix[col].to_numpy(), trunc_hourly[col].to_numpy(), equal_nan=True, rtol=0, atol=0)
            np.testing.assert_allclose(full_prefix[col].to_numpy(), mut_prefix[col].to_numpy(), equal_nan=True, rtol=0, atol=0)

        for probe_ms in (t_close_ms, t_close_ms + 900_000):
            m_full = map_timestamp_to_regime(full_hourly, probe_ms)
            m_mut = map_timestamp_to_regime(mut_hourly, probe_ms)
            assert m_full == m_mut


def test_mutation_deleted_candle_fails_continuity_and_changes_output() -> None:
    cfg = load_research_config()
    df_m15 = _make_synthetic_m15(n_bars=1200)
    base_hourly = compute_causal_hourly_regimes(df_m15, config=cfg, enforce_continuity=True)

    gapped_m15 = pd.concat([df_m15.iloc[:600], df_m15.iloc[601:]])
    with pytest.raises(ValueError, match="thiếu nến"):
        validate_m15_continuity(gapped_m15)
    with pytest.raises(ValueError, match="thiếu nến"):
        compute_causal_hourly_regimes(gapped_m15, config=cfg, enforce_continuity=True)

    deleted_tail_m15 = df_m15.iloc[:-4].copy()
    tail_hourly = compute_causal_hourly_regimes(deleted_tail_m15, config=cfg, enforce_continuity=True)
    assert len(tail_hourly) == len(base_hourly) - 1
    assert _df_sha256(tail_hourly) != _df_sha256(base_hourly)


def test_min_sample_size_threshold_and_candidate_selection_rules() -> None:
    cfg = load_research_config()
    min_n = int(cfg["statistics"]["min_sample_size"])
    insuff_label = str(cfg["statistics"]["insufficient_sample_label"])

    def _make_trades(n: int) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "trade_id": [f"T{i}" for i in range(n)],
                "entry_time_ms": [1700000000000 + i * 60_000 for i in range(n)],
                "exit_time_ms": [1700000000000 + (i + 1) * 60_000 for i in range(n)],
                "gross_pnl": [1.5] * n,
                "fee_total": [0.1] * n,
                "net_pnl": [1.4] * n,
            }
        )

    under = summarize_trade_slice(
        _make_trades(min_n - 1),
        min_sample_size=min_n,
        insufficient_label=insuff_label,
        n_boot=200,
        ci_level=0.95,
        seed=42,
    )
    assert under["status"] == "CHƯA ĐỦ MẪU"
    assert under["conclusive"] is False

    at_min = summarize_trade_slice(
        _make_trades(min_n),
        min_sample_size=min_n,
        insufficient_label=insuff_label,
        n_boot=200,
        ci_level=0.95,
        seed=42,
    )
    assert at_min["status"] == "ĐỦ_MẪU"
    assert at_min["conclusive"] is True

    assert evaluate_portfolio_candidate_flag(
        val_delta=10.0, hold_delta=-1.0, val_dd=20.0, hold_dd=20.0, base_val_dd=20.0, base_hold_dd=20.0, dd_multiplier=1.25
    ) is False
    assert evaluate_portfolio_candidate_flag(
        val_delta=-1.0, hold_delta=10.0, val_dd=20.0, hold_dd=20.0, base_val_dd=20.0, base_hold_dd=20.0, dd_multiplier=1.25
    ) is False
    assert evaluate_portfolio_candidate_flag(
        val_delta=10.0, hold_delta=10.0, val_dd=30.0, hold_dd=20.0, base_val_dd=20.0, base_hold_dd=20.0, dd_multiplier=1.25
    ) is False
    assert evaluate_portfolio_candidate_flag(
        val_delta=10.0, hold_delta=10.0, val_dd=22.0, hold_dd=22.0, base_val_dd=20.0, base_hold_dd=20.0, dd_multiplier=1.25
    ) is True


def test_evidence_verify_and_regime_label_mutations(tmp_path: Path) -> None:
    for fname in ("manifest.json", "regime_report.json", "regime_report.html"):
        assert (DEFAULT_EVIDENCE_DIR / fname).exists(), f"Thiếu tệp minh chứng {fname}"

    clean_res = verify_evidence_dir(DEFAULT_EVIDENCE_DIR, verify_data_hash=True, write_result=False)
    assert clean_res["status"] == "PASS", clean_res["failures"]

    html_text = (DEFAULT_EVIDENCE_DIR / "regime_report.html").read_text(encoding="utf-8")
    assert find_english_label_leaks(html_text) == []

    report = json.loads((DEFAULT_EVIDENCE_DIR / "regime_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((DEFAULT_EVIDENCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    per_combo = report["combo_regime_analysis"]["per_combo"]
    assert len(per_combo) == 30
    assert sum(int(r["overall_all"]["so_lenh"]) for r in per_combo) == report["combo_regime_analysis"]["total_trades_all_signals"] > 0
    assert all(int(r["overall_all"]["so_lenh"]) > 0 for r in per_combo)
    assert manifest["live_23_count"] == 23
    assert manifest["non_live_7_count"] == 7
    assert manifest["combos_spec_hash"] == CrossSymbol30FixedStrategy().get_spec_hash()

    mut_dir = tmp_path / "mutated_evidence"
    shutil.copytree(DEFAULT_EVIDENCE_DIR, mut_dir)
    assert verify_evidence_dir(mut_dir, verify_data_hash=False, write_result=False)["status"] == "PASS"

    mut_report_path = mut_dir / "regime_report.json"
    mut_rep_orig = mut_report_path.read_text(encoding="utf-8")
    mut_rep = json.loads(mut_rep_orig)
    first_reg = next(iter(mut_rep["combo_regime_analysis"]["per_combo"][0]["by_regime"]))
    mut_rep["combo_regime_analysis"]["per_combo"][0]["by_regime"][first_reg]["so_lenh"] += 1
    mut_report_path.write_text(json.dumps(mut_rep, ensure_ascii=False), encoding="utf-8")

    fail_res = verify_evidence_dir(mut_dir, verify_data_hash=False, write_result=False)
    assert fail_res["status"] == "FAIL"
    assert any("report_and_html_hashes" in f or "combo_regime_accounting" in f for f in fail_res["failures"])

    mut_rep_lbl = json.loads(mut_rep_orig)
    cell_val = mut_rep_lbl["combo_regime_analysis"]["per_combo"][0]["by_regime"].pop(first_reg)
    mut_rep_lbl["combo_regime_analysis"]["per_combo"][0]["by_regime"]["NHÃN_CHẾ_ĐỘ_ĐỘT_BIẾN"] = cell_val
    mut_report_path.write_text(json.dumps(mut_rep_lbl, ensure_ascii=False), encoding="utf-8")

    fail_lbl_res = verify_evidence_dir(mut_dir, verify_data_hash=False, write_result=False)
    assert fail_lbl_res["status"] == "FAIL"
    assert any("report_and_html_hashes" in f for f in fail_lbl_res["failures"])
