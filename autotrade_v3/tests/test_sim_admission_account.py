"""Unit and synthetic integration tests for AdmissionPolicy, AccountLedger, UNFILLABLE, and artifacts."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from autotrade_v3.engine.candle_loader import generate_synthetic_m1, resample_m1_to_timeframe
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager
from autotrade_v3.sim.account import AccountLedger
from autotrade_v3.sim.admission import AllSignals, OneTradePerCombo, Portfolio
from autotrade_v3.sim.engine import UnifiedSimulator
from autotrade_v3.strategy.base import ExitPolicy
from autotrade_v3.validation.gates_c import gate_c5
from autotrade_v3.validation.context import RunBundle, VerifyContext


def _make_bars(rows: list[tuple[float, float, float, float]], start_ms: int = 1_700_000_040_000) -> pd.DataFrame:
    n = len(rows)
    ts = [start_ms + i * 60_000 for i in range(n)]
    idx = pd.to_datetime(ts, unit="ms", utc=True)
    return pd.DataFrame(
        {
            "open": [float(r[0]) for r in rows],
            "high": [float(r[1]) for r in rows],
            "low": [float(r[2]) for r in rows],
            "close": [float(r[3]) for r in rows],
            "volume": [100.0] * n,
            "provider": ["BINANCE"] * n,
        },
        index=idx,
    )


def _make_sig(
    sig_id: str,
    symbol: str,
    combo_id: int,
    signal_close_ms: int,
    side: str = "BUY",
) -> Signal:
    return Signal(
        signal_id=sig_id,
        symbol=symbol,
        combo_id=combo_id,
        combo_name=f"C{combo_id}",
        timeframe="15m",
        side=side,  # type: ignore[arg-type]
        signal_close_ms=signal_close_ms,
        signal_close_dt=pd.Timestamp(signal_close_ms, unit="ms", tz="UTC"),
        bar_position=0,
        bar_open_ms=signal_close_ms - 899_999,
        approved=True,
        warmup_ready=True,
        smc_dir=1 if side == "BUY" else -1,
        ob_blocked=False,
        provider="BINANCE",
        entry_price=100.0,
        tp_pct=0.02,
        sl_pct=0.01,
        tp_price=102.0,
        sl_price=99.0,
        is_new6=False,
        exit_policy=ExitPolicy(tp_pct=0.02, sl_pct=0.01, split_legs=False, leg_weights=(1.0,)),
    )


def test_admission_mode_a_never_skips() -> None:
    pol = AllSignals()
    d1 = pol.evaluate(
        signal_id="S1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=1000, signal_close_dt="t1",
        entry_idx=0, entry_time_ms=1001, req_margin=1.0,
    )
    d2 = pol.evaluate(
        signal_id="S2", symbol="BTC-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=2000, signal_close_dt="t2",
        entry_idx=0, entry_time_ms=1001, req_margin=1.0,
    )
    assert d1.status == "ACCEPTED"
    assert d2.status == "ACCEPTED"


def test_admission_mode_b_skips_open_combo_and_same_bar() -> None:
    pol = OneTradePerCombo()
    d1 = pol.evaluate(
        signal_id="S1", symbol="BTC-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=1000, signal_close_dt="t1",
        entry_idx=0, entry_time_ms=1001, req_margin=1.0,
    )
    assert d1.status == "ACCEPTED"
    pol.record_open("BTC-USDT", 1, "S1", free_bar_idx=5)

    # Same bar as exit (entry_idx == 5) -> SKIPPED when strict_same_bar=True
    d_same = pol.evaluate(
        signal_id="S2", symbol="BTC-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=2000, signal_close_dt="t2",
        entry_idx=5, entry_time_ms=2001, req_margin=1.0,
    )
    assert d_same.status == "SKIPPED"
    assert d_same.reason == "SKIPPED_BY_CONCURRENCY"
    assert d_same.blocking_trade_id == "S1"

    # Different combo on same symbol -> ACCEPTED
    d_other_combo = pol.evaluate(
        signal_id="S3", symbol="BTC-USDT", combo_id=2, combo_name="C2",
        timeframe="15m", side="BUY", signal_close_ms=2000, signal_close_dt="t2",
        entry_idx=2, entry_time_ms=2001, req_margin=1.0,
    )
    assert d_other_combo.status == "ACCEPTED"

    # Same combo_id on different symbol -> ACCEPTED (independent)
    d_other_sym = pol.evaluate(
        signal_id="S4", symbol="ETH-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=2000, signal_close_dt="t2",
        entry_idx=2, entry_time_ms=2001, req_margin=1.0,
    )
    assert d_other_sym.status == "ACCEPTED"

    # Bar after exit (entry_idx == 6) -> ACCEPTED
    d_after = pol.evaluate(
        signal_id="S5", symbol="BTC-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=3000, signal_close_dt="t3",
        entry_idx=6, entry_time_ms=3001, req_margin=1.0,
    )
    assert d_after.status == "ACCEPTED"


def test_unfillable_signal_at_end_of_data() -> None:
    df = _make_bars([(100.0, 101.0, 99.5, 100.5), (100.5, 101.0, 99.5, 100.5)])
    last_open_ms = int((df.index.asi8[-1] // 1_000_000))
    # Signal closes at or after last bar open -> no M1 bar strictly after signal_close_ms
    sig_late = _make_sig("S_LATE", "BTC-USDT", 1, last_open_ms)
    sim = UnifiedSimulator(
        risk_config=RiskConfig(slippage_rate=0.0),
    )
    for mode in ("ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"):
        res = sim.run(mode=mode, signals=[sig_late], candle_dict={"BTC-USDT": df})
        assert len(res.trades) == 0
        assert len(res.signal_decisions) == 1
        assert res.signal_decisions[0].status == "UNFILLABLE"
        assert res.signal_decisions[0].reason == "NO_M1_BAR_AFTER_SIGNAL"


def test_account_ledger_vwap_partial_full_close_and_margin_rejection() -> None:
    opens = {"BTC-USDT": np.array([100.0, 120.0, 120.0])}
    closes = {"BTC-USDT": np.array([100.0, 120.0, 120.0])}
    acct = AccountLedger(
        initial_equity=10.0,
        symbols=["BTC-USDT"],
        opens_by_symbol=opens,
        closes_by_symbol=closes,
    )

    # Entry 1: notional 100 at price 100 -> qty 1.0, avg_price 100.0
    q1 = acct.open_position(
        symbol="BTC-USDT", side="BUY", notional=100.0, entry_price=100.0, req_margin=2.0, entry_fee=0.05
    )
    assert q1 == pytest.approx(1.0)
    assert acct.positions[("BTC-USDT", "BUY")].avg_price == pytest.approx(100.0)

    # Entry 2: notional 100 at price 120 -> qty 0.833333..., total_notional 200, total_qty 1.833333...
    # VWAP = 200 / (1 + 100/120) = 109.090909...
    q2 = acct.open_position(
        symbol="BTC-USDT", side="BUY", notional=100.0, entry_price=120.0, req_margin=2.0, entry_fee=0.05
    )
    expected_vwap = 200.0 / (1.0 + 100.0 / 120.0)
    assert acct.positions[("BTC-USDT", "BUY")].avg_price == pytest.approx(expected_vwap)

    # Partial close of leg 1 (qty=q1) -> VWAP remains unchanged
    acct.close_leg(
        symbol="BTC-USDT", side="BUY", leg_qty=q1, leg_margin=2.0,
        leg_entry_price=100.0, exec_exit_price=120.0, entry_fee=0.05, exit_fee=0.06,
    )
    assert acct.positions[("BTC-USDT", "BUY")].qty == pytest.approx(q2)
    assert acct.positions[("BTC-USDT", "BUY")].avg_price == pytest.approx(expected_vwap)

    # Full close of leg 2 -> position resets to 0
    acct.close_leg(
        symbol="BTC-USDT", side="BUY", leg_qty=q2, leg_margin=2.0,
        leg_entry_price=120.0, exec_exit_price=120.0, entry_fee=0.05, exit_fee=0.05,
    )
    assert acct.positions[("BTC-USDT", "BUY")].qty == 0.0
    assert acct.positions[("BTC-USDT", "BUY")].avg_price == 0.0
    assert acct.positions[("BTC-USDT", "BUY")].used_margin == 0.0

    # Margin rejection via Portfolio policy when req_margin > free_margin
    pol = Portfolio(acct)
    dec = pol.evaluate(
        signal_id="S_BIG", symbol="BTC-USDT", combo_id=1, combo_name="C1",
        timeframe="15m", side="BUY", signal_close_ms=1000, signal_close_dt="t",
        entry_idx=0, entry_time_ms=1001, req_margin=999.0,
    )
    assert dec.status == "REJECTED"
    assert dec.reason == "INSUFFICIENT_MARGIN"


def test_synthetic_integration_all_three_modes(tmp_path: Path) -> None:
    """Section 7: full pipeline on synthetic data for all 3 modes with signal_decisions & C5."""
    runner = ExperimentRunner(run_manager=RunManager(runs_dir=tmp_path))
    for mode in ("ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"):
        res = runner.run_experiment(
            strategy_id="20FINAL_BASELINE",
            mode=mode,
            symbols=["BTC-USDT"],
            start="2024-01-01",
            end="2024-02-01",
            smoke_test=True,
            is_golden=False,
        )
        dec_path = res.run_dir / "signal_decisions.parquet"
        sig_path = res.run_dir / "signals.parquet"
        trd_path = res.run_dir / "trades.parquet"
        assert dec_path.exists()
        df_dec = pd.read_parquet(dec_path)
        df_sig = pd.read_parquet(sig_path)
        df_trd = pd.read_parquet(trd_path)
        assert len(df_dec) == len(df_sig)
        assert df_dec["signal_id"].nunique() == len(df_sig)
        accepted_ids = set(df_dec.loc[df_dec["status"] == "ACCEPTED", "signal_id"])
        leg_sig_ids = set(df_trd["signal_id"]) if len(df_trd) else set()
        assert accepted_ids == leg_sig_ids

        # Run C5 directly on the persisted run directory
        c5_res = gate_c5(VerifyContext(run_dir=res.run_dir, dataset_root=tmp_path, repo_root=Path(".")))
        assert c5_res.status == "PASS", c5_res.violations
