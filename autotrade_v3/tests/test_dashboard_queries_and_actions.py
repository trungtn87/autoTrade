"""Unit tests for V3-15 dashboard queries.py, notes.py, actions.py, and offline/security invariants."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import duckdb
import pandas as pd
import pytest

from autotrade_v3.dashboard import _cli_ops, actions, notes, queries


def _build_synthetic_store(tmp_path: Path) -> tuple[Path, str]:
    """Create a small [SYNTHETIC] RunStore with hand-computed expected values."""
    store_dir = tmp_path / "store"
    run_id = "R_SYNTHETIC_001"
    rdir = store_dir / run_id
    rdir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "strategy_id": "20FINAL",
        "strategy_version": "1",
        "execution_mode": "ALL_SIGNALS",
        "cost_profile": "IDEAL",
        "ambiguity_policy": "CONSERVATIVE",
        "validation_status": "PARTIALLY_VERIFIED",
        "smoke_test": True,
        "synthetic": True,
        "initial_equity": 1000.0,
        "dataset_hash": "syn_ds_hash",
        "spec_hash": "syn_spec_hash",
        "time_window": {"start": "2024-01-01", "end": "2026-06-01"},
        "symbols": ["BTC-USDT", "ETH-USDT"],
        "created_at_utc": "2026-11-14T00:00:00+00:00",
    }
    (rdir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (rdir / "validation.json").write_text(
        json.dumps({"run_id": run_id, "overall_status": "PARTIALLY_VERIFIED", "is_real_data": False, "gates": []}),
        encoding="utf-8",
    )

    # Note: both symbols share combo_id=1 and combo_name='C1' to verify (symbol, combo_name) keying
    signals_df = pd.DataFrame(
        [
            {
                "signal_id": "SIG_1",
                "symbol": "BTC-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "LONG",
                "signal_close_ms": 1704067200000,  # 2024-01-01
                "signal_close_dt": "2024-01-01 00:00:00+00:00",
                "entry_price": 100.0,
                "tp_pct": 0.10,
                "sl_pct": 0.05,
            },
            {
                "signal_id": "SIG_2",
                "symbol": "ETH-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "SHORT",
                "signal_close_ms": 1764547200000,  # 2025-12-01
                "signal_close_dt": "2025-12-01 00:00:00+00:00",
                "entry_price": 200.0,
                "tp_pct": 0.10,
                "sl_pct": 0.05,
            },
            {
                "signal_id": "SIG_3",
                "symbol": "ETH-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "LONG",
                "signal_close_ms": 1764633600000,  # 2025-12-02
                "signal_close_dt": "2025-12-02 00:00:00+00:00",
                "entry_price": 210.0,
                "tp_pct": 0.10,
                "sl_pct": 0.05,
            },
        ]
    )
    signals_df.to_parquet(rdir / "signals.parquet", index=False)

    decisions_df = pd.DataFrame(
        [
            {"signal_id": "SIG_1", "symbol": "BTC-USDT", "combo_id": 1, "combo_name": "C1", "status": "ACCEPTED", "reason": "OK"},
            {"signal_id": "SIG_2", "symbol": "ETH-USDT", "combo_id": 1, "combo_name": "C1", "status": "ACCEPTED", "reason": "OK"},
            {"signal_id": "SIG_3", "symbol": "ETH-USDT", "combo_id": 1, "combo_name": "C1", "status": "SKIPPED", "reason": "SKIPPED_BY_CONCURRENCY"},
        ]
    )
    decisions_df.to_parquet(rdir / "signal_decisions.parquet", index=False)

    # Hand-computed legs:
    # T1 (BTC-USDT/C1): L1 net=+9.0 (gross=10.0, fee=1.0), L2 net=+4.0 (gross=5.0, fee=1.0) => trade net=+13.0
    # T2 (ETH-USDT/C1): L3 net=-6.0 (gross=-5.0, fee=1.0) => trade net=-6.0
    # Total net=7.0, gross=10.0, fees=3.0. Trade WR = 1/2 = 50%. Leg WR = 2/3 = 66.67%.
    # Net PF trade = 13/6 = 2.17, Net PF leg = 13/6 = 2.17. Gross PF trade = 15/5 = 3.0.
    legs_df = pd.DataFrame(
        [
            {
                "trade_id": "T1",
                "leg_id": "L1",
                "signal_id": "SIG_1",
                "symbol": "BTC-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "LONG",
                "leg_type": "TP",
                "entry_time_ms": 1704067260000,
                "entry_time_dt": "2024-01-01 00:01:00+00:00",
                "entry_price": 100.0,
                "entry_px_raw": 100.0,
                "exit_time_ms": 1704070800000,
                "exit_time_dt": "2024-01-01 01:00:00+00:00",
                "exit_price": 110.0,
                "exit_px_fill": 110.0,
                "exit_reason": "TP",
                "gross_pnl": 10.0,
                "gross_pnl_raw": 10.0,
                "fee_total": 1.0,
                "fee_entry": 0.5,
                "fee_exit": 0.5,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "funding_cost": 0.0,
                "net_pnl": 9.0,
                "tp_pct": 0.10,
                "sl_pct": 0.05,
                "trailing_act_pct": 0.0,
                "trailing_cb_pct": 0.0,
                "intrabar_ambiguous": False,
                "intrabar_trailing_ambiguous": False,
                "ambiguity_type": "NONE",
            },
            {
                "trade_id": "T1",
                "leg_id": "L2",
                "signal_id": "SIG_1",
                "symbol": "BTC-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "LONG",
                "leg_type": "TRAIL",
                "entry_time_ms": 1704067260000,
                "entry_time_dt": "2024-01-01 00:01:00+00:00",
                "entry_price": 100.0,
                "entry_px_raw": 100.0,
                "exit_time_ms": 1704074400000,
                "exit_time_dt": "2024-01-01 02:00:00+00:00",
                "exit_price": 105.0,
                "exit_px_fill": 105.0,
                "exit_reason": "TRAIL",
                "gross_pnl": 5.0,
                "gross_pnl_raw": 5.0,
                "fee_total": 1.0,
                "fee_entry": 0.5,
                "fee_exit": 0.5,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "funding_cost": 0.0,
                "net_pnl": 4.0,
                "tp_pct": 0.10,
                "sl_pct": 0.05,
                "trailing_act_pct": 0.02,
                "trailing_cb_pct": 0.01,
                "intrabar_ambiguous": False,
                "intrabar_trailing_ambiguous": True,
                "ambiguity_type": "TRAIL_PEAK_AND_STOP",
            },
            {
                "trade_id": "T2",
                "leg_id": "L3",
                "signal_id": "SIG_2",
                "symbol": "ETH-USDT",
                "combo_id": 1,
                "combo_name": "C1",
                "timeframe": "1h",
                "side": "SHORT",
                "leg_type": "SL",
                "entry_time_ms": 1764547260000,
                "entry_time_dt": "2025-12-01 00:01:00+00:00",
                "entry_price": 200.0,
                "entry_px_raw": 200.0,
                "exit_time_ms": 1764550800000,
                "exit_time_dt": "2025-12-01 01:00:00+00:00",
                "exit_price": 210.0,
                "exit_px_fill": 210.0,
                "exit_reason": "SL",
                "gross_pnl": -5.0,
                "gross_pnl_raw": -5.0,
                "fee_total": 1.0,
                "fee_entry": 0.5,
                "fee_exit": 0.5,
                "spread_cost": 0.0,
                "slippage_cost": 0.0,
                "funding_cost": 0.0,
                "net_pnl": -6.0,
                "tp_pct": 0.10,
                "sl_pct": 0.05,
                "trailing_act_pct": 0.0,
                "trailing_cb_pct": 0.0,
                "intrabar_ambiguous": False,
                "intrabar_trailing_ambiguous": False,
                "ambiguity_type": "NONE",
            },
        ]
    )
    legs_df.to_parquet(rdir / "legs.parquet", index=False)

    events_df = pd.DataFrame(
        [
            {"event_id": "E1", "timestamp_ms": 1704070800000, "signal_id": "SIG_1", "leg_id": "L1", "event_type": "EXIT", "price": 110.0, "exit_reason": "TP"},
            {"event_id": "E2", "timestamp_ms": 1704074400000, "signal_id": "SIG_1", "leg_id": "L2", "event_type": "EXIT", "price": 105.0, "exit_reason": "TRAIL"},
            {"event_id": "E3", "timestamp_ms": 1764550800000, "signal_id": "SIG_2", "leg_id": "L3", "event_type": "EXIT", "price": 210.0, "exit_reason": "SL"},
        ]
    )
    events_df.to_parquet(rdir / "events.parquet", index=False)

    equity_df = pd.DataFrame(
        [
            {"time_ms": 1704070800000, "leg_id": "L1", "net_pnl": 9.0, "balance": 1009.0, "equity": 1009.0, "equity_kind": "CLOSED_TRADE"},
            {"time_ms": 1704074400000, "leg_id": "L2", "net_pnl": 4.0, "balance": 1013.0, "equity": 1013.0, "equity_kind": "CLOSED_TRADE"},
            {"time_ms": 1764550800000, "leg_id": "L3", "net_pnl": -6.0, "balance": 1007.0, "equity": 1007.0, "equity_kind": "CLOSED_TRADE"},
        ]
    )
    equity_df.to_parquet(rdir / "equity.parquet", index=False)

    return store_dir, run_id


def test_queries_synthetic_hand_computed_and_drilldown(tmp_path: Path) -> None:
    store_dir, run_id = _build_synthetic_store(tmp_path)

    s = queries.get_run_summary(run_id, store_dir=store_dir)
    assert s["is_synthetic"] is True
    assert s["validation_status"] == "PARTIALLY_VERIFIED"
    assert "tham chiếu trên vốn danh nghĩa" in s["roi_label"]
    assert abs(s["net_pnl"] - 7.0) < 1e-9
    assert abs(s["gross_pnl"] - 10.0) < 1e-9
    assert abs(s["total_fees"] - 3.0) < 1e-9
    assert s["accepted_entries"] == 2
    assert s["total_legs"] == 3
    assert abs(s["trade_win_rate_pct"] - 50.0) < 1e-9
    assert abs(s["leg_win_rate_pct"] - (200.0 / 3.0)) < 1e-9
    assert s["net_pf_trade"] == 2.17
    assert s["net_pf_leg"] == 2.17
    assert s["gross_pf_trade"] == 3.0
    assert s["m2m_status"] == "NOT_APPLICABLE"
    assert s["decision_counts"] == {"ACCEPTED": 2, "SKIPPED": 1}

    # Drill-down sum invariant (1e-9) for overall, symbol, combo, month, exit_reason
    all_dd = queries.get_run_legs_drilldown(run_id, store_dir=store_dir)
    assert abs(all_dd["sum_net_pnl"] - s["net_pnl"]) < 1e-9

    sym_df = queries.get_run_symbol_breakdown(run_id, store_dir=store_dir)
    assert len(sym_df) == 2
    for _, row in sym_df.iterrows():
        dd = queries.get_run_legs_drilldown(run_id, store_dir=store_dir, symbol=row["symbol"])
        assert abs(dd["sum_net_pnl"] - row["net_pnl"]) < 1e-9

    combo_df = queries.get_run_combo_breakdown(run_id, store_dir=store_dir)
    assert set(combo_df["combo_key"]) == {"BTC-USDT/C1", "ETH-USDT/C1"}
    for _, row in combo_df.iterrows():
        dd = queries.get_run_legs_drilldown(run_id, store_dir=store_dir, combo_key=row["combo_key"])
        assert abs(dd["sum_net_pnl"] - row["net_pnl"]) < 1e-9

    month_df = queries.get_run_monthly_breakdown(run_id, store_dir=store_dir)
    for _, row in month_df.iterrows():
        dd = queries.get_run_legs_drilldown(run_id, store_dir=store_dir, year_month=row["year_month"])
        assert abs(dd["sum_net_pnl"] - row["net_pnl"]) < 1e-9

    exit_df = queries.get_run_exit_reasons(run_id, store_dir=store_dir)
    for _, row in exit_df.iterrows():
        dd = queries.get_run_legs_drilldown(run_id, store_dir=store_dir, exit_reason=row["exit_reason"])
        assert abs(dd["sum_net_pnl"] - row["net_pnl"]) < 1e-9

    # Optional table missing -> None
    assert queries.get_run_uncertainty(run_id, store_dir=store_dir) is None


def test_queries_empty_store_and_missing_artifact(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_store"
    empty_dir.mkdir()
    assert queries.list_store_runs(empty_dir) == []
    assert queries.list_research_log(empty_dir, notes_db_path=tmp_path / "notes.duckdb") == []

    store_dir, run_id = _build_synthetic_store(tmp_path)
    (store_dir / run_id / "legs.parquet").unlink()
    with pytest.raises(queries.MissingArtifactError):
        queries.get_run_summary(run_id, store_dir=store_dir)


def test_duckdb_readonly_enforced(tmp_path: Path) -> None:
    db_p = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_p))
    con.execute("CREATE TABLE t (id INT)")
    con.close()

    ro_con = queries.connect_duckdb_readonly(db_p)
    try:
        with pytest.raises(duckdb.Error):
            ro_con.execute("CREATE TABLE evil (id INT)")
    finally:
        ro_con.close()


def test_notes_db_isolated_from_store(tmp_path: Path) -> None:
    notes_db = tmp_path / "notes.duckdb"
    notes.set_run_note("R_1", tags="baseline,v1", note="Initial check", notes_db_path=notes_db)
    notes.set_pinned_baseline("R_1", pinned=True, notes_db_path=notes_db)
    assert notes_db.exists()

    store_dir, run_id = _build_synthetic_store(tmp_path)
    notes.set_pinned_baseline(run_id, pinned=True, notes_db_path=notes_db)
    log_rows = queries.list_research_log(store_dir=store_dir, notes_db_path=notes_db)
    assert len(log_rows) == 1
    assert log_rows[0]["is_pinned_baseline"] is True


def test_actions_whitelist_and_injection_rejection(tmp_path: Path) -> None:
    with pytest.raises(actions.ActionNotAllowedError):
        actions.dispatch_action("arbitrary_shell", {}, log_path=tmp_path / "a.log")

    with pytest.raises(actions.InvalidActionInputError):
        actions.run_strategy_clone(from_ref="20FINAL@1; rm -rf /", to_ref="EXP@1", log_path=tmp_path / "a.log")

    with pytest.raises(actions.InvalidActionInputError):
        actions.run_edit_clone_params(
            strategy_ref="20FINAL@1",
            combo_key="BTC-USDT/C1",
            updates={"tp_pct": 0.05},
            log_path=tmp_path / "a.log",
        )

    with pytest.raises(PermissionError):
        _cli_ops.edit_clone_params(
            strategy_ref="20FINAL@1",
            combo_key="BTC-USDT/C1",
            updates={"tp_pct": 0.05},
        )

    with pytest.raises(actions.InvalidActionInputError):
        actions.run_backtest(
            strategy="20FINAL@1",
            mode="ALL_SIGNALS",
            symbols="BTC-USDT",
            start="2024-01-01",
            end="2024-02-01",
            cost_profile="BASE",
            smoke_test=False,
            log_path=tmp_path / "a.log",
        )


def test_dashboard_forbidden_imports_and_strings() -> None:
    dash_dir = Path(__file__).resolve().parents[1] / "dashboard"
    forbidden_modules = {
        "autotrade_v2",
        "requests",
        "httpx",
        "websocket",
        "websockets",
        "aiohttp",
        "supabase",
        "bingx",
        "dotenv",
    }
    for py_file in sorted(dash_dir.rglob("*.py")):
        src = py_file.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden_modules, f"Forbidden import {alias.name} in {py_file}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in forbidden_modules, f"Forbidden import {node.module} in {py_file}"
        assert not re.search(r"""['"/\\]\.env\b""", src), f"Forbidden .env reference in {py_file}"
        assert "http://" not in src and "https://" not in src, f"Forbidden external URL in {py_file}"
        assert "lợi nhuận thực tế" not in src.lower(), f"Forbidden profit claim in {py_file}"
