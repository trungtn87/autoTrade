from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "research/v3_lab_11_plus31"


def test_tv31_freeze_and_is_parity() -> None:
    registry = pd.read_csv(LAB / "freeze/TV31_SELECTED_REGISTRY.csv")
    parity = pd.read_csv(LAB / "outputs/IS_2Y_PARITY_PER_COMBO.csv")
    assert len(registry) == registry.display_id.nunique() == registry.run_id.nunique() == 31
    assert registry.symbol.value_counts().to_dict() == {"BTC-USDT": 18, "ETH-USDT": 13}
    assert registry.source_verified.all()
    assert parity.status.eq("PASS").all()
    assert int(parity.adapter_trades.sum()) == 2377
    assert abs(float(parity.adapter_net_base.sum()) - 802.04036058) <= 1e-8


def test_database_reconciles_54_portfolio() -> None:
    conn = sqlite3.connect(LAB / "backtests.sqlite3")
    conn.row_factory = sqlite3.Row
    run = conn.execute(
        "SELECT * FROM research_runs WHERE study_group='PRUNE23_PLUS_TV31' AND profile='BASE' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert run is not None
    metrics = json.loads(run["metrics_json"])
    count, net = conn.execute(
        "SELECT count(*),sum(net_pnl) FROM trade_ledger WHERE run_id=?", (run["run_id"],)
    ).fetchone()
    strategies, null_roi = conn.execute(
        "SELECT count(*),sum(CASE WHEN roi IS NULL THEN 1 ELSE 0 END) FROM strategy_results WHERE run_id=?",
        (run["run_id"],),
    ).fetchone()
    assert strategies == 54
    assert null_roi == 0
    assert count == metrics["executed_trades"]
    assert abs(float(net) - float(metrics["net_pnl"])) <= 1e-8
    assert metrics["accounting_pass"] is True
    conn.close()


def test_plus44_registry_and_database_reconcile_without_overwriting_plus54() -> None:
    registry = pd.read_csv(LAB / "outputs/plus44/TV21_SELECTED_REGISTRY.csv")
    parity = pd.read_csv(LAB / "outputs/plus44/TV21_IS_PARITY.csv")
    expected = {
        "TV03", "TV04", "TV07", "TV08", "TV09", "TV10", "TV12", "TV13", "TV14", "TV15", "TV16",
        "TV17", "TV20", "TV21", "TV22", "TV23", "TV24", "TV27", "TV28", "TV30", "TV31",
    }
    assert set(registry.display_id) == expected
    assert len(registry) == 21
    assert parity.status.eq("PASS").all()

    conn = sqlite3.connect(LAB / "backtests.sqlite3")
    conn.row_factory = sqlite3.Row
    plus44 = conn.execute(
        "SELECT * FROM research_runs WHERE study_group='PRUNE23_PLUS_TV21' AND profile='BASE' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert plus44 is not None
    metrics = json.loads(plus44["metrics_json"])
    count, net = conn.execute(
        "SELECT count(*),sum(net_pnl) FROM trade_ledger WHERE run_id=?", (plus44["run_id"],)
    ).fetchone()
    strategies, null_roi = conn.execute(
        "SELECT count(*),sum(CASE WHEN roi IS NULL THEN 1 ELSE 0 END) FROM strategy_results WHERE run_id=?",
        (plus44["run_id"],),
    ).fetchone()
    assert strategies == 44
    assert null_roi == 0
    assert count == metrics["executed_trades"]
    assert abs(float(net) - float(metrics["net_pnl"])) <= 1e-8
    assert metrics["accounting_pass"] is True
    assert metrics["peak_concurrent_legs"] == 15
    assert conn.execute(
        "SELECT count(*) FROM research_runs WHERE study_group='PRUNE23_PLUS_TV31'"
    ).fetchone()[0] == 2
    conn.close()

    attribution = pd.read_csv(LAB / "outputs/plus44/TV21_STANDALONE_VS_PORTFOLIO.csv")
    assert len(attribution) == 21
    assert attribution.label.eq("STANDALONE_INDEPENDENT vs SHARED_PORTFOLIO_ALLOCATION").all()
    assert attribution.allocation_minus_standalone.abs().gt(1e-9).all()
