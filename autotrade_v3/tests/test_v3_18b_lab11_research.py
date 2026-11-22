"""Kiểm thử đơn vị, không nhìn tương lai, đột biến và xác minh cho nghiên cứu V3-18b."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.research_v3_18.combo_regime_analyzer import summarize_trade_slice
from autotrade_v3.research_v3_18.combos_30 import get_live_23_pairs, get_non_live_7_pairs
from autotrade_v3.research_v3_18.portfolio_experiments import evaluate_portfolio_candidate_flag
from autotrade_v3.research_v3_18.regime_features import (
    compute_causal_hourly_regimes,
    load_research_config,
)
from autotrade_v3.research_v3_18b import DEFAULT_CONFIG_PATH
from autotrade_v3.research_v3_18b.experiments_v3_18b import filter_signals_by_causal_regime
from autotrade_v3.research_v3_18b.lab11_baseline import build_lab11_combo_catalog
from autotrade_v3.research_v3_18b.labels_vi import find_english_label_leaks_v3_18b
from autotrade_v3.research_v3_18b.verify import DEFAULT_EVIDENCE_DIR, verify_v3_18b_evidence
from autotrade_v3.strategy.base import ExitPolicy, Signal
from autotrade_v3.strategy.v3_lab_11_prune23 import PRUNE23_EXCLUDED_COMBOS
from autotrade_v3.strategy_core.hashing import canonical_json_sha256


def _make_synthetic_m15(n_bars: int = 3600, seed: int = 20261122) -> pd.DataFrame:
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


def test_lab11_catalog_23_live_and_7_excluded_exact() -> None:
    catalog = build_lab11_combo_catalog()
    assert len(catalog) == 30
    live = [(c.symbol, c.combo_id) for c in catalog if c.is_live_23]
    non_live = [(c.symbol, c.combo_id) for c in catalog if not c.is_live_23]
    assert len(live) == 23
    assert len(non_live) == 7
    assert set(live) == get_live_23_pairs()
    assert set(non_live) == set(PRUNE23_EXCLUDED_COMBOS)
    assert set(non_live) == {(s, cid) for s, cid, _ in get_non_live_7_pairs()}


def test_causal_filter_no_lookahead() -> None:
    cfg = load_research_config(DEFAULT_CONFIG_PATH)
    df_m15 = _make_synthetic_m15(n_bars=3600)
    full_hourly = compute_causal_hourly_regimes(df_m15, config=cfg, enforce_continuity=True)

    cut_hours = 840
    cut_m15 = cut_hours * 4
    t_cut_close_ms = int(full_hourly.iloc[cut_hours - 1]["close_ms"])

    mutated_m15 = df_m15.copy()
    mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("open")] *= 3.0
    mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("high")] *= 4.0
    mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("low")] *= 0.2
    mutated_m15.iloc[cut_m15:, mutated_m15.columns.get_loc("close")] *= 3.5
    mut_hourly = compute_causal_hourly_regimes(mutated_m15, config=cfg, enforce_continuity=True)

    ep = ExitPolicy(tp_pct=0.02, sl_pct=0.008, split_legs=False)
    sample_signals: list[Signal] = []
    for idx_h in range(740, cut_hours):
        row = full_hourly.iloc[idx_h]
        ts = int(row["close_ms"])
        side: Literal["BUY", "SELL"] = "BUY" if idx_h % 2 == 0 else "SELL"
        sample_signals.append(
            Signal(
                symbol="BTC-USDT",
                combo_id=1,
                combo_name="C1",
                timeframe="1h",
                side=side,
                signal_close_ms=ts,
                signal_close_dt=pd.to_datetime(ts, unit="ms", utc=True),
                bar_position=idx_h,
                bar_open_ms=int(row["open_ms"]),
                entry_price=float(row["close"]),
                tp_pct=0.02,
                sl_pct=0.008,
                tp_price=float(row["close"]) * 1.02,
                sl_price=float(row["close"]) * 0.992,
                approved=True,
                warmup_ready=True,
                smc_dir=0,
                ob_blocked=False,
                provider="SYNTH",
                is_new6=False,
                exit_policy=ep,
                exit_policy_hash=ep.get_policy_hash(),
                signal_id=f"TEST_{idx_h}_{side}_{ts}",
            )
        )
    assert all(int(s.signal_close_ms) <= t_cut_close_ms for s in sample_signals)

    sess_cfg = dict(cfg["regime_params"]["sessions_utc"])
    for flt in cfg["pre_registered_filters"]:
        kept_full = filter_signals_by_causal_regime(
            sample_signals,
            {"BTC-USDT": full_hourly},
            sessions_cfg=sess_cfg,
            exclude_sessions=set(flt["exclude_sessions_utc"]),
            block_buy_zones=set(flt["block_buy_zones"]),
            block_sell_zones=set(flt["block_sell_zones"]),
        )
        kept_mut = filter_signals_by_causal_regime(
            sample_signals,
            {"BTC-USDT": mut_hourly},
            sessions_cfg=sess_cfg,
            exclude_sessions=set(flt["exclude_sessions_utc"]),
            block_buy_zones=set(flt["block_buy_zones"]),
            block_sell_zones=set(flt["block_sell_zones"]),
        )
        assert [s.signal_id for s in kept_full] == [s.signal_id for s in kept_mut]


def test_mutation_parameter_swap_and_deleted_candle() -> None:
    cfg = load_research_config(DEFAULT_CONFIG_PATH)
    df_m15 = _make_synthetic_m15(n_bars=3200)
    base_hourly = compute_causal_hourly_regimes(df_m15, config=cfg, enforce_continuity=True)

    gapped_m15 = pd.concat([df_m15.iloc[:600], df_m15.iloc[601:]])
    with pytest.raises(ValueError, match="thiếu nến"):
        compute_causal_hourly_regimes(gapped_m15, config=cfg, enforce_continuity=True)

    trimmed = compute_causal_hourly_regimes(df_m15.iloc[:-4].copy(), config=cfg, enforce_continuity=True)
    assert len(trimmed) == len(base_hourly) - 1

    mut_cfg = json.loads(json.dumps(cfg))
    mut_cfg["regime_params"]["ema_fast_1h"] = 20
    mut_hourly = compute_causal_hourly_regimes(df_m15, config=mut_cfg, enforce_continuity=True)
    assert not np.allclose(mut_hourly["ema50_1h"].to_numpy(), base_hourly["ema50_1h"].to_numpy(), equal_nan=True)
    assert canonical_json_sha256(mut_cfg) != canonical_json_sha256(cfg)


def test_min_sample_size_and_gating_rules_v3_18b() -> None:
    cfg = load_research_config(DEFAULT_CONFIG_PATH)
    min_n = int(cfg["statistics"]["min_sample_size"])
    insuff_lbl = str(cfg["statistics"]["insufficient_sample_label"])

    df29 = pd.DataFrame(
        {
            "trade_id": [f"T{i}" for i in range(min_n - 1)],
            "entry_time_ms": [1700000000000 + i * 60_000 for i in range(min_n - 1)],
            "exit_time_ms": [1700000000000 + (i + 1) * 60_000 for i in range(min_n - 1)],
            "gross_pnl": [1.0] * (min_n - 1),
            "fee_total": [0.1] * (min_n - 1),
            "net_pnl": [0.9] * (min_n - 1),
        }
    )
    res29 = summarize_trade_slice(df29, min_sample_size=min_n, insufficient_label=insuff_lbl, n_boot=100, ci_level=0.95, seed=1)
    assert res29["status"] == "CHƯA ĐỦ MẪU"
    assert res29["conclusive"] is False

    assert not evaluate_portfolio_candidate_flag(val_delta=5.0, hold_delta=-1.0, val_dd=20.0, hold_dd=20.0, base_val_dd=20.0, base_hold_dd=20.0, dd_multiplier=1.25)
    assert evaluate_portfolio_candidate_flag(val_delta=5.0, hold_delta=5.0, val_dd=22.0, hold_dd=22.0, base_val_dd=20.0, base_hold_dd=20.0, dd_multiplier=1.25)


def test_generated_v3_18b_evidence_parity_and_mutations(tmp_path: Path) -> None:
    for fname in ("manifest.json", "lab11_report.json", "lab11_report.html"):
        assert (DEFAULT_EVIDENCE_DIR / fname).exists(), f"Thiếu {fname}"

    clean = verify_v3_18b_evidence(DEFAULT_EVIDENCE_DIR, verify_data_hash=True, write_result=False)
    assert clean["status"] == "PASS", clean["failures"]

    html_text = (DEFAULT_EVIDENCE_DIR / "lab11_report.html").read_text(encoding="utf-8")
    assert find_english_label_leaks_v3_18b(html_text) == []

    report = json.loads((DEFAULT_EVIDENCE_DIR / "lab11_report.json").read_text(encoding="utf-8"))
    parity = report["parity_v3_16"]
    assert parity["parity_ok"] is True
    assert parity["reproduced_full_bingx"]["total_signals"] == 4364
    assert parity["reproduced_full_bingx"]["accepted_entries"] == 3138
    assert float(parity["reproduced_full_bingx"]["net_pnl_diff_usd"]) < 0.01

    exps = report["experiments"]
    assert exps["total_trials_m"] == 33
    assert exps["counts_by_group"] == {"add_7": 7, "drop_23": 23, "filter_3": 3}

    mut_dir = tmp_path / "mut_v3_18b"
    shutil.copytree(DEFAULT_EVIDENCE_DIR, mut_dir)
    assert verify_v3_18b_evidence(mut_dir, verify_data_hash=False, write_result=False)["status"] == "PASS"

    rep_p = mut_dir / "lab11_report.json"
    mut_rep = json.loads(rep_p.read_text(encoding="utf-8"))
    mut_rep["combo_regime_analysis_23"]["per_combo"][0]["catalog"]["tp_pct"] = 0.0999
    first_reg = next(iter(mut_rep["combo_regime_analysis_23"]["per_combo"][0]["by_regime"]))
    mut_rep["combo_regime_analysis_23"]["per_combo"][0]["by_regime"][first_reg]["so_lenh"] += 1
    rep_p.write_text(json.dumps(mut_rep, ensure_ascii=False), encoding="utf-8")

    fail_res = verify_v3_18b_evidence(mut_dir, verify_data_hash=False, write_result=False)
    assert fail_res["status"] == "FAIL"
    assert fail_res["failures"]
