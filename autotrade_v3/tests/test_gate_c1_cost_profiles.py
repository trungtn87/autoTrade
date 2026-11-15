"""Unit tests for Gate C1 entry causality across cost profiles (0/2/5 bps), sides, and error cases."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.sim.costs import CostModel
from autotrade_v3.validation.context import M1Series, VerifyContext
from autotrade_v3.validation.gates_c import gate_c1

REPO_ROOT = Path(__file__).resolve().parents[2]


def _build_ctx(
    tmp_path: Path,
    *,
    mode: str,
    cost_profile: str,
    side: str,
    entry_price: float,
    qty: float,
    notional: float = 100.0,
    entry_time_ms: int | None = None,
    open_px: float = 100.0,
) -> VerifyContext:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Bar 0 at t=1700000040000, Bar 1 at t=1700000100000, Bar 2 at t=1700000160000
    t_arr = np.array([1_700_000_040_000, 1_700_000_100_000, 1_700_000_160_000], dtype=np.int64)
    o_arr = np.array([open_px, open_px, open_px], dtype=float)
    h_arr = np.array([open_px * 1.01] * 3, dtype=float)
    l_arr = np.array([open_px * 0.99] * 3, dtype=float)
    c_arr = np.array([open_px] * 3, dtype=float)
    v_arr = np.array([10.0] * 3, dtype=float)
    p_arr = np.array(["BINANCE"] * 3, dtype=object)

    sig_close_ms = int(t_arr[0] - 1)  # earliest exec = t_arr[0]
    actual_entry_ms = int(t_arr[0]) if entry_time_ms is None else entry_time_ms

    cm = CostModel.from_profile_name(cost_profile, is_real_data=False)
    manifest = {
        "run_id": "TEST_RUN",
        "strategy_id": "20FINAL_FIXED_V1",
        "execution_mode": mode,
        "time_window": {"start": "2023-11-14", "end": "2023-11-15"},
        "symbols": ["BTC-USDT"],
        "slippage_rate": 0.0,
        "fee_rate_taker": 0.0005,
        "cost_profile": cm.profile.name,
        "cost_profile_hash": cm.profile.profile_hash,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "dataset_manifest.json").write_text(json.dumps({"partitions": []}), encoding="utf-8")

    sig_df = pd.DataFrame(
        [
            {
                "signal_id": "SIG_1",
                "symbol": "BTC-USDT",
                "combo_id": 1,
                "side": side,
                "signal_close_ms": sig_close_ms,
            }
        ]
    )
    sig_df.to_parquet(run_dir / "signals.parquet", index=False)

    trades_df = pd.DataFrame(
        [
            {
                "signal_id": "SIG_1",
                "trade_id": "SIG_1",
                "leg_id": "SIG_1#L1",
                "symbol": "BTC-USDT",
                "combo_id": 1,
                "side": side,
                "leg_type": "SINGLE",
                "entry_time_ms": actual_entry_ms,
                "entry_price": entry_price,
                "exit_time_ms": int(t_arr[2]),
                "exit_price": open_px,
                "exit_reason": "TP",
                "qty": qty,
                "notional": notional,
                "gross_pnl": 0.0,
                "fee_total": 0.1,
                "net_pnl": -0.1,
                "return_pct": -0.001,
            }
        ]
    )
    trades_df.to_parquet(run_dir / "trades.parquet", index=False)

    ctx = VerifyContext(run_dir=run_dir, dataset_root=tmp_path, repo_root=REPO_ROOT)
    ctx.cache["win:BTCUSDT"] = M1Series(t_arr, o_arr, h_arr, l_arr, c_arr, v_arr, p_arr)
    return ctx


@pytest.mark.parametrize("mode", ["ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"])
@pytest.mark.parametrize("cost_profile,bps", [("IDEAL", 0.0), ("BASE", 2.0), ("STRESS", 5.0)])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_gate_c1_pass_with_correct_slippage_for_buy_and_sell(
    tmp_path: Path, mode: str, cost_profile: str, bps: float, side: str
):
    open_px = 100.0
    rate = bps / 10000.0
    fill_px = open_px * (1.0 + rate) if side == "BUY" else open_px * (1.0 - rate)
    qty = round(100.0 / fill_px, 6)
    ctx = _build_ctx(
        tmp_path,
        mode=mode,
        cost_profile=cost_profile,
        side=side,
        entry_price=fill_px,
        qty=qty,
        open_px=open_px,
    )
    res = gate_c1(ctx)
    assert res.status == "PASS", res.violations
    assert res.violation_count == 0


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_gate_c1_fail_when_entry_price_is_wrong(tmp_path: Path, side: str):
    # BASE expects 2 bps adverse slippage, but we supply raw open (0 bps)
    open_px = 100.0
    ctx = _build_ctx(
        tmp_path,
        mode="ONE_TRADE_PER_COMBO",
        cost_profile="BASE",
        side=side,
        entry_price=open_px,
        qty=1.0,
        open_px=open_px,
    )
    res = gate_c1(ctx)
    assert res.status == "FAIL"
    assert res.violation_count == 1
    assert res.violations[0]["kind"] == "ENTRY_PRICE"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_gate_c1_fail_when_slippage_direction_is_reversed(tmp_path: Path, side: str):
    open_px = 100.0
    rate = 5.0 / 10000.0
    # Reversed direction: favorable instead of adverse
    wrong_px = open_px * (1.0 - rate) if side == "BUY" else open_px * (1.0 + rate)
    qty = round(100.0 / wrong_px, 6)
    ctx = _build_ctx(
        tmp_path,
        mode="BINGX_PORTFOLIO",
        cost_profile="STRESS",
        side=side,
        entry_price=wrong_px,
        qty=qty,
        open_px=open_px,
    )
    res = gate_c1(ctx)
    assert res.status == "FAIL"
    assert any(v["kind"] in ("FILL_QTY_NOTIONAL", "VWAP_ENTRY_PRICE") for v in res.violations)


def test_gate_c1_fail_on_lookahead_entry_time(tmp_path: Path):
    open_px = 100.0
    # Entry timestamp is 1 minute BEFORE bar 0 (look-ahead)
    lookahead_ms = 1_700_000_040_000 - 60_000
    ctx = _build_ctx(
        tmp_path,
        mode="ONE_TRADE_PER_COMBO",
        cost_profile="IDEAL",
        side="BUY",
        entry_price=open_px,
        qty=1.0,
        entry_time_ms=lookahead_ms,
        open_px=open_px,
    )
    res = gate_c1(ctx)
    assert res.status == "FAIL"
    assert any(v["kind"] == "ENTRY_TIME" for v in res.violations)
