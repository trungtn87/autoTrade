"""Tests for V3-16 MT5-style report metrics, Vietnamese labels, and verification (Issue #102)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from autotrade_v3.analysis.metrics import compute_mt5_extended_metrics
from autotrade_v3.reports.labels_vi import find_english_label_leaks
from autotrade_v3.reports.mt5_report import generate_mt5_report
from autotrade_v3.tools.verify_report import verify_mt5_report


def _hand_calc_ledger() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic Mode C ledger with hand-calculated answers.

    7 entries ordered by exit_time_ms:
      E1 (BUY):  own=+10, vwap=+12  (2 legs: +4, +8)
      E2 (BUY):  own=+20, vwap=+18
      E3 (SELL): own=-5,  vwap=-6
      E4 (SELL): own=-10, vwap=-9
      E5 (BUY):  own=-5,  vwap=-5
      E6 (SELL): own=+15, vwap=+14
      E7 (SELL): own=-2,  vwap=-1
    Total own = +23.0, Total vwap = +23.0 (basis_shift sum = 0.0).
    Win streaks on own: [2 (+30), 1 (+15)] -> max_w=2 ($30), max_prof=$30 (2), avg_w=1.5
    Loss streaks on own: [3 (-20), 1 (-2)] -> max_l=3 (-$20), max_loss=-$20 (3), avg_l=2.0
    """
    base_ms = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC
    day_ms = 86_400_000
    rows = [
        ("E1", "E1#L1", "BTC-USDT", "C1", "1h", "BUY", base_ms, base_ms + 1 * day_ms, 5.0, 0.5, 4.0, 5.0, 100.0),
        ("E1", "E1#L2", "BTC-USDT", "C1", "1h", "BUY", base_ms, base_ms + 1 * day_ms, 9.0, 0.5, 8.0, 5.0, 100.0),
        ("E2", "E2#L1", "BTC-USDT", "C1", "1h", "BUY", base_ms + day_ms, base_ms + 2 * day_ms, 21.0, 1.0, 18.0, 20.0, 100.0),
        ("E3", "E3#L1", "ETH-USDT", "C3", "1h", "SELL", base_ms + 2 * day_ms, base_ms + 3 * day_ms, -4.0, 1.0, -6.0, -5.0, 100.0),
        ("E4", "E4#L1", "ETH-USDT", "C3", "1h", "SELL", base_ms + 3 * day_ms, base_ms + 4 * day_ms, -9.0, 1.0, -9.0, -10.0, 100.0),
        ("E5", "E5#L1", "BTC-USDT", "C1", "1h", "BUY", base_ms + 4 * day_ms, base_ms + 5 * day_ms, -4.0, 1.0, -5.0, -5.0, 100.0),
        ("E6", "E6#L1", "ETH-USDT", "C3", "1h", "SELL", base_ms + 5 * day_ms, base_ms + 6 * day_ms, 16.0, 1.0, 14.0, 15.0, 100.0),
        ("E7", "E7#L1", "ETH-USDT", "C3", "1h", "SELL", base_ms + 6 * day_ms, base_ms + 7 * day_ms, -1.0, 1.0, -1.0, -2.0, 100.0),
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "trade_id", "leg_id", "symbol", "combo_name", "timeframe", "side",
            "entry_time_ms", "exit_time_ms", "gross_pnl", "fee_total", "net_pnl",
            "pnl_own_entry", "notional",
        ],
    )
    snaps = pd.DataFrame(
        [
            {"timestamp_ms": base_ms, "balance": 1000.0, "equity": 1000.0, "used_margin": 0.0, "free_margin": 1000.0, "drawdown_dollars": 0.0, "drawdown_pct": 0.0},
            {"timestamp_ms": base_ms + 1 * day_ms, "balance": 1012.0, "equity": 1012.0, "used_margin": 2.0, "free_margin": 1010.0, "drawdown_dollars": 0.0, "drawdown_pct": 0.0},
            {"timestamp_ms": base_ms + 2 * day_ms, "balance": 1030.0, "equity": 1030.0, "used_margin": 2.0, "free_margin": 1028.0, "drawdown_dollars": 0.0, "drawdown_pct": 0.0},
            {"timestamp_ms": base_ms + 5 * day_ms, "balance": 1010.0, "equity": 1005.0, "used_margin": 5.0, "free_margin": 1000.0, "drawdown_dollars": 25.0, "drawdown_pct": 25.0 / 1030.0 * 100.0},
            {"timestamp_ms": base_ms + 7 * day_ms, "balance": 1023.0, "equity": 1023.0, "used_margin": 1.0, "free_margin": 1022.0, "drawdown_dollars": 7.0, "drawdown_pct": 7.0 / 1030.0 * 100.0},
        ]
    )
    return df, snaps


def test_mt5_extended_metrics_hand_calculated() -> None:
    df, snaps = _hand_calc_ledger()
    m = compute_mt5_extended_metrics(
        df,
        initial_equity=1000.0,
        snapshots_df=snaps,
        total_signals=10,
        execution_mode="BINGX_PORTFOLIO",
    )

    assert m["entry_basis"] == "pnl_own_entry"
    assert m["volume"]["accepted_entries"] == 7
    assert m["volume"]["total_legs"] == 8
    assert m["volume"]["entry_fill_rate_pct"] == pytest.approx(70.0)
    assert m["mt5_results"]["total_net_profit"] == pytest.approx(23.0)
    assert m["mt5_results"]["winning_entries"] == 3
    assert m["mt5_results"]["losing_entries"] == 4
    assert m["mt5_results"]["gross_profit_entry"] == pytest.approx(45.0)
    assert m["mt5_results"]["gross_loss_entry"] == pytest.approx(-22.0)
    assert m["mt5_results"]["largest_profit_entry"] == pytest.approx(20.0)
    assert m["mt5_results"]["largest_loss_entry"] == pytest.approx(-10.0)

    # Long vs Short on pnl_own_entry: BUY=(10, 20, -5) sum=25; SELL=(-5,-10,15,-2) sum=-2
    assert m["long_short"]["long"]["entries"] == 3
    assert m["long_short"]["long"]["won"] == 2
    assert m["long_short"]["long"]["net_pnl"] == pytest.approx(25.0)
    assert m["long_short"]["short"]["entries"] == 4
    assert m["long_short"]["short"]["won"] == 1
    assert m["long_short"]["short"]["net_pnl"] == pytest.approx(-2.0)

    # 6 MT5 streak metrics
    stk = m["streaks"]
    assert stk["max_consecutive_wins_count"] == 2
    assert stk["max_consecutive_wins_dollars"] == pytest.approx(30.0)
    assert stk["max_consecutive_losses_count"] == 3
    assert stk["max_consecutive_losses_dollars"] == pytest.approx(-20.0)
    assert stk["maximal_consecutive_profit_dollars"] == pytest.approx(30.0)
    assert stk["maximal_consecutive_profit_count"] == 2
    assert stk["maximal_consecutive_loss_dollars"] == pytest.approx(-20.0)
    assert stk["maximal_consecutive_loss_count"] == 3
    assert stk["avg_consecutive_wins"] == pytest.approx(1.5)
    assert stk["avg_consecutive_losses"] == pytest.approx(2.0)

    # Closed DD = 20.0 (1030 -> 1010), M2M DD = 25.0
    assert m["risk"]["closed_max_dd_dollars"] == pytest.approx(20.0)
    assert m["risk"]["m2m_max_dd_dollars"] == pytest.approx(25.0)
    assert m["risk"]["recovery_factor_closed"] == pytest.approx(23.0 / 20.0)
    assert m["risk"]["recovery_factor_m2m"] == pytest.approx(23.0 / 25.0)
    assert m["account"]["min_margin_level_pct"] == pytest.approx(1005.0 / 5.0 * 100.0)
    assert m["risk"]["sharpe_daily_365"] is not None and math.isfinite(m["risk"]["sharpe_daily_365"])


def test_mt5_metrics_fail_closed_on_missing_required_column() -> None:
    df, _ = _hand_calc_ledger()
    df_no_own = df.drop(columns=["pnl_own_entry"])
    with pytest.raises(ValueError, match="pnl_own_entry"):
        compute_mt5_extended_metrics(df_no_own, execution_mode="BINGX_PORTFOLIO")

    df_no_net = df.drop(columns=["net_pnl"])
    with pytest.raises(ValueError, match="net_pnl"):
        compute_mt5_extended_metrics(df_no_net, execution_mode="ALL_SIGNALS")


def test_mt5_report_mutation_and_vietnamese_label_verification(tmp_path: Path) -> None:
    df, snaps = _hand_calc_ledger()
    run_dir = tmp_path / "run"
    out_dir = tmp_path / "out"
    run_dir.mkdir()
    df.to_parquet(run_dir / "trades.parquet", index=False)
    snaps.to_parquet(run_dir / "snapshots.parquet", index=False)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "run_id": "SYNTH_MODE_C",
                "execution_mode": "BINGX_PORTFOLIO",
                "initial_equity": 1000.0,
                "symbols": ["BTC-USDT", "ETH-USDT"],
                "time_window": {"start": "2024-01-01", "end": "2024-01-10"},
                "cost_profile": "IDEAL",
                "cost_profile_hash": "abc12345",
                "ambiguity_policy": "CONSERVATIVE",
                "git_sha": "deadbeef",
                "dataset_hash": "cafebabe",
                "config_hash": "12345678",
                "validation_status": "VERIFIED",
                "total_signals": 10,
            }
        ),
        encoding="utf-8",
    )

    json_p, html_p = generate_mt5_report(run_dir, out_dir)
    assert verify_mt5_report(run_dir, json_p, html_p) is True
    assert find_english_label_leaks(html_p.read_text(encoding="utf-8")) == []

    # Mutation 1: tamper JSON metric -> FAIL
    tampered_json = json.loads(json_p.read_text(encoding="utf-8"))
    tampered_json["kpi"]["mt5_results"]["total_net_profit"]["value"] += 1.0
    bad_json_p = out_dir / "bad_report_v1.json"
    bad_json_p.write_text(json.dumps(tampered_json), encoding="utf-8")
    assert verify_mt5_report(run_dir, bad_json_p, html_p) is False

    # Mutation 2: tamper HTML rendered number -> FAIL
    orig_html = html_p.read_text(encoding="utf-8")
    bad_html_num = out_dir / "bad_num.html"
    bad_html_num.write_text(orig_html.replace("+$23.00", "+$99.99"), encoding="utf-8")
    assert verify_mt5_report(run_dir, json_p, bad_html_num) is False

    # Mutation 3: leak English label into HTML -> FAIL
    bad_html_eng = out_dir / "bad_eng.html"
    bad_html_eng.write_text(orig_html.replace("Tổng lãi ròng", "Total Net Profit"), encoding="utf-8")
    assert verify_mt5_report(run_dir, json_p, bad_html_eng) is False


@pytest.mark.parametrize(
    ("mode", "expected_net", "expected_fees", "expected_entries", "expect_mtm"),
    [
        ("ALL_SIGNALS", 410.7007, 598.3866, 5984, False),
        ("ONE_TRADE_PER_COMBO", 282.1935, 414.9453, 4149, False),
        ("BINGX_PORTFOLIO", 282.1935, 414.9453, 4149, True),
    ],
)
def test_golden_v3_13_mt5_report_all_three_modes(
    tmp_path: Path,
    mode: str,
    expected_net: float,
    expected_fees: float,
    expected_entries: int,
    expect_mtm: bool,
) -> None:
    golden_root = Path("runs/golden/v3-13")
    run_dir = golden_root / mode
    if not (run_dir / "trades.parquet").exists():
        pytest.skip(f"Golden V3-13 artifacts not present at {run_dir}")

    out_dir = tmp_path / mode
    json_p, html_p = generate_mt5_report(run_dir, out_dir)
    assert verify_mt5_report(run_dir, json_p, html_p) is True

    data = json.loads(json_p.read_text(encoding="utf-8"))
    assert data["kpi"]["mt5_results"]["total_net_profit"]["value"] == pytest.approx(expected_net, abs=1e-3)
    assert data["kpi"]["cost_reality"]["total_fees"]["value"] == pytest.approx(expected_fees, abs=1e-3)
    assert data["kpi"]["volume"]["accepted_entries"]["value"] == expected_entries
    if expect_mtm:
        assert data["kpi"]["account"]["final_equity_mtm"]["status"] == "AVAILABLE"
    else:
        assert data["kpi"]["account"]["final_equity_mtm"]["status"] == "N/A"
        assert "không có snapshot M2M" in data["kpi"]["account"]["final_equity_mtm"]["reason"]
