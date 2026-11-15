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


def test_all_signals_portfolio_m2m_snapshots_and_parity() -> None:
    """ALL_SIGNALS_PORTFOLIO produces M2M snapshots, end equity == balance, and matches Mode A PnL."""
    from autotrade_v3.engine.risk_manager import RiskConfig
    from autotrade_v3.engine.signal_engine import Signal
    from autotrade_v3.sim.costs import CostModel
    from autotrade_v3.sim.engine import UnifiedSimulator

    base_ms = 1_704_067_200_000
    rows = []
    # 10 M1 bars: starts at 100.0, rises to 105.0 so +2% TP hits cleanly
    for i in range(10):
        t = base_ms + i * 60_000
        o = 100.0 + i * 0.6
        rows.append(
            {
                "timestamp": pd.Timestamp(t, unit="ms", tz="UTC"),
                "t_ms": t,
                "open": o,
                "high": o + 1.0,
                "low": o - 0.2,
                "close": o + 0.5,
                "volume": 10.0,
            }
        )
    m1_df = pd.DataFrame(rows).set_index("timestamp")
    candle_dict = {"BTC-USDT": m1_df}

    signals = [
        Signal(
            symbol="BTC-USDT",
            combo_id=11,
            combo_name="C11",
            timeframe="1h",
            side="BUY",
            signal_close_ms=base_ms + 30_000,
            signal_close_dt=pd.Timestamp(base_ms + 30_000, unit="ms", tz="UTC"),
            bar_position=0,
            bar_open_ms=base_ms,
            entry_price=100.6,
            tp_pct=0.02,
            sl_pct=0.05,
            tp_price=102.612,
            sl_price=95.57,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="test",
            is_new6=True,
        ),
        Signal(
            symbol="BTC-USDT",
            combo_id=11,
            combo_name="C11",
            timeframe="1h",
            side="BUY",
            signal_close_ms=base_ms + 90_000,
            signal_close_dt=pd.Timestamp(base_ms + 90_000, unit="ms", tz="UTC"),
            bar_position=1,
            bar_open_ms=base_ms + 60_000,
            entry_price=101.2,
            tp_pct=0.02,
            sl_pct=0.05,
            tp_price=103.224,
            sl_price=96.14,
            approved=True,
            warmup_ready=True,
            smc_dir=1,
            ob_blocked=False,
            provider="test",
            is_new6=True,
        ),
    ]

    risk_cfg = RiskConfig(
        initial_equity=1000.0,
        notional_per_trade=100.0,
        leverage_btc=100.0,
        leverage_eth=50.0,
        taker_fee_rate=0.0005,
        slippage_rate=0.0002,
        cost_profile="BASE",
    )
    cost_model = CostModel.from_profile_name("BASE", is_real_data=False)
    sim = UnifiedSimulator(risk_config=risk_cfg, cost_model=cost_model, ambiguity_policy="CONSERVATIVE")

    res_a = sim.run(mode="ALL_SIGNALS", signals=signals, candle_dict=candle_dict)
    res_p0 = sim.run(mode="ALL_SIGNALS_PORTFOLIO", signals=signals, candle_dict=candle_dict)

    assert len(res_a.trades) == 2
    assert len(res_p0.trades) == 2
    assert sum(t.net_pnl for t in res_p0.trades) == pytest.approx(
        sum(t.net_pnl for t in res_a.trades), abs=1e-6
    )
    assert len(res_p0.snapshots) > 0
    last_snap = res_p0.snapshots.iloc[-1]
    assert float(last_snap["used_margin"]) == pytest.approx(0.0, abs=1e-9)
    assert float(last_snap["equity"]) == pytest.approx(float(last_snap["balance"]), abs=1e-9)


def test_step3_concentration_and_sensitivity_verification_mutations(tmp_path: Path) -> None:
    """Verify concentration, sensitivity grid, Vietnamese BASE banner, and tamper detection."""
    df, snaps = _hand_calc_ledger()
    df["combo_name"] = ["C11", "C11", "C11", "C3", "C3", "C1", "C3", "C3"]
    df["notional"] = 100.0

    runs: dict[float, Path] = {}
    for slip_bps, mult in [(0.0, 1.2), (2.0, 1.0), (5.0, 0.7)]:
        rdir = tmp_path / f"run_{slip_bps}"
        rdir.mkdir()
        df_s = df.copy()
        df_s["pnl_own_entry"] = df["pnl_own_entry"] * mult
        df_s["net_pnl"] = df["net_pnl"] * mult
        df_s["gross_pnl"] = df_s["net_pnl"] + df_s["fee_total"]
        df_s.to_parquet(rdir / "trades.parquet", index=False)
        snaps.to_parquet(rdir / "snapshots.parquet", index=False)
        profile_name = "IDEAL" if slip_bps == 0.0 else ("BASE" if slip_bps == 2.0 else "STRESS")
        (rdir / "run_manifest.json").write_text(
            json.dumps(
                {
                    "mode": "ALL_SIGNALS_PORTFOLIO",
                    "symbols": ["BTC-USDT", "ETH-USDT"],
                    "time_window": {"start": "2024-01-01", "end": "2024-01-10"},
                    "cost_profile": profile_name,
                    "cost_profile_hash": f"hash_{profile_name}",
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
        runs[slip_bps] = rdir

    out_dir = tmp_path / "out_base"
    json_p, html_p = generate_mt5_report(runs[2.0], out_dir, sensitivity_runs=runs)
    assert verify_mt5_report(runs[2.0], json_p, html_p, sensitivity_runs=runs) is True

    html_text = html_p.read_text(encoding="utf-8")
    assert find_english_label_leaks(html_text) == []
    assert "CHẾ ĐỘ CHI PHÍ: BASE (giả định 2 bps)" in html_text
    assert "ngoài phạm vi báo cáo chính từ V3-16 Bước 3" in html_text

    # Mutation A: tamper concentration max_open_entries_any_sym_side -> FAIL
    bad_conc = json.loads(json_p.read_text(encoding="utf-8"))
    bad_conc["kpi"]["concentration"]["max_open_entries_any_sym_side"]["value"] += 1
    p_a = out_dir / "bad_conc.json"
    p_a.write_text(json.dumps(bad_conc), encoding="utf-8")
    assert verify_mt5_report(runs[2.0], p_a, html_p, sensitivity_runs=runs) is False

    # Mutation B: tamper BTC C11 own-entry PnL -> FAIL
    bad_c11 = json.loads(json_p.read_text(encoding="utf-8"))
    bad_c11["kpi"]["concentration"]["btc_c11_own_entry_pnl"]["value"] += 1.0
    p_b = out_dir / "bad_c11.json"
    p_b.write_text(json.dumps(bad_c11), encoding="utf-8")
    assert verify_mt5_report(runs[2.0], p_b, html_p, sensitivity_runs=runs) is False

    # Mutation C: tamper sensitivity grid net_pnl -> FAIL
    bad_sens = json.loads(json_p.read_text(encoding="utf-8"))
    bad_sens["breakdowns"]["cost_sensitivity"][0]["net_pnl"]["value"] += 5.0
    p_c = out_dir / "bad_sens.json"
    p_c.write_text(json.dumps(bad_sens), encoding="utf-8")
    assert verify_mt5_report(runs[2.0], p_c, html_p, sensitivity_runs=runs) is False


def test_v3_16_all_signals_base_real_run_smoke(tmp_path: Path) -> None:
    """Smoke check on real 3Y ALL_SIGNALS_PORTFOLIO BASE + sensitivity runs if present."""
    root = Path("runs/v3_16_all_signals")
    base_dir = root / "BASE"
    if not (base_dir / "trades.parquet").exists():
        pytest.skip(f"Real ALL_SIGNALS_PORTFOLIO runs not present at {root}")

    sens = {
        0.0: root / "IDEAL",
        1.0: root / "SLIP_1BPS",
        2.0: root / "BASE",
        3.4: root / "SLIP_3_4BPS",
        5.0: root / "STRESS",
    }
    out_dir = tmp_path / "ALL_SIGNALS_BASE"
    json_p, html_p = generate_mt5_report(base_dir, out_dir, sensitivity_runs=sens)
    assert verify_mt5_report(base_dir, json_p, html_p, sensitivity_runs=sens) is True
    assert find_english_label_leaks(html_p.read_text(encoding="utf-8")) == []

    data = json.loads(json_p.read_text(encoding="utf-8"))
    assert data["kpi"]["mt5_results"]["total_net_profit"]["value"] == pytest.approx(168.5995, abs=0.05)
    assert data["kpi"]["risk"]["m2m_max_dd_dollars"]["value"] == pytest.approx(105.0274, abs=0.05)
    assert data["kpi"]["concentration"]["max_open_entries_any_sym_side"]["value"] == 26

