"""P2: 14 unit tests for Historical Funding Rate integration in UnifiedSimulator (Mode C)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import UnifiedSimulator
from autotrade_v3.sim.funding import FundingTable
from autotrade_v3.strategy.base import ExitPolicy


def _make_m1(start_utc: str, n_bars: int, base_px: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start_utc, periods=n_bars, freq="1min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.full(n_bars, base_px, dtype=float),
            "high": np.full(n_bars, base_px * 1.001, dtype=float),
            "low": np.full(n_bars, base_px * 0.999, dtype=float),
            "close": np.full(n_bars, base_px, dtype=float),
            "volume": np.full(n_bars, 10.0, dtype=float),
        },
        index=idx,
    )
    return df


def _make_sig(
    *,
    symbol: str = "BTC-USDT",
    combo_id: int = 1,
    combo_name: str = "C1",
    side: str = "BUY",
    signal_close_dt: str,
    tp_pct: float = 0.05,
    sl_pct: float = 0.05,
    split_legs: bool = False,
    trailing_act_pct: float = 0.0,
    trailing_cb_pct: float = 0.0,
) -> Signal:
    ts = pd.Timestamp(signal_close_dt)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    close_ms = int(ts.timestamp() * 1000)
    ep = ExitPolicy(
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        split_legs=split_legs,
        trailing_act_pct=trailing_act_pct,
        trailing_cb_pct=trailing_cb_pct,
        leg_weights=(0.5, 0.5) if split_legs else (1.0,),
    )
    return Signal(
        signal_id=f"SIG_{symbol}_{combo_id}_{side}_{close_ms}",
        symbol=symbol,
        combo_id=combo_id,
        combo_name=combo_name,
        timeframe="1h",
        side=side,
        bar_position=0,
        bar_open_ms=close_ms - 3_599_999,
        signal_close_ms=close_ms,
        signal_close_dt=ts,
        entry_price=100.0,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=100.0 * (1 + tp_pct) if side == "BUY" else 100.0 * (1 - tp_pct),
        sl_price=100.0 * (1 - sl_pct) if side == "BUY" else 100.0 * (1 + sl_pct),
        smc_dir=0,
        ob_blocked=False,
        approved=True,
        provider="test",
        is_new6=combo_id >= 100,
        warmup_ready=True,
        exit_policy=ep,
    )


def _sim(funding_table: FundingTable | None, initial_equity: float = 1000.0, notional: float = 100.0) -> UnifiedSimulator:
    rc = RiskConfig(initial_equity=initial_equity, notional_per_trade=notional, cost_profile="IDEAL")
    cm = CostModel.from_uniform_params(name="IDEAL", fee_taker=0.0, fee_maker=0.0, slippage_bps=0.0)
    return UnifiedSimulator(risk_config=rc, cost_model=cm, funding_table=funding_table)


def _ft(rows: list[dict]) -> FundingTable:
    by_sym: dict[str, list[dict]] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    return FundingTable.from_dataframes({s: pd.DataFrame(v) for s, v in by_sym.items()})


# 1. LONG pays funding (positive rate)
def test_01_long_pays_positive_funding() -> None:
    m1 = {"BTC-USDT": _make_m1("2025-01-01 07:50:00", 30, 100.0)}
    settle_ms = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": settle_ms, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], m1)
    assert len(res.trades) == 1
    assert res.trades[0].funding_cost == pytest.approx(0.10, abs=1e-9)
    assert res.trades[0].net_pnl == pytest.approx(-0.10, abs=1e-9)


# 2. SHORT receives funding (positive rate)
def test_02_short_receives_positive_funding() -> None:
    m1 = {"BTC-USDT": _make_m1("2025-01-01 07:50:00", 30, 100.0)}
    settle_ms = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": settle_ms, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="SELL", signal_close_dt="2025-01-01 07:54:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], m1)
    assert res.trades[0].funding_cost == pytest.approx(-0.10, abs=1e-9)
    assert res.trades[0].net_pnl == pytest.approx(0.10, abs=1e-9)


# 3. Negative funding rate (LONG receives, SHORT pays)
def test_03_negative_funding_rate() -> None:
    m1 = {"BTC-USDT": _make_m1("2025-01-01 07:50:00", 30, 100.0)}
    settle_ms = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": settle_ms, "funding_rate": -0.002, "mark_price": 100.0, "source": "bingx"}])
    s_buy = _make_sig(combo_id=1, side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    s_sell = _make_sig(combo_id=2, side="SELL", signal_close_dt="2025-01-01 07:54:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [s_buy, s_sell], m1)
    by_side = {t.side: t for t in res.trades}
    assert by_side["BUY"].funding_cost == pytest.approx(-0.20, abs=1e-9)
    assert by_side["SELL"].funding_cost == pytest.approx(0.20, abs=1e-9)


# 4. Position closed before settlement => 0 funding
def test_04_closed_before_settlement() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    # Trigger TP at 07:58 (+5% -> 105)
    df.loc[df.index[8], "high"] = 106.0
    m1 = {"BTC-USDT": df}
    settle_ms = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": settle_ms, "funding_rate": 0.005, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999", tp_pct=0.05)
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], m1)
    assert res.trades[0].exit_time_ms < settle_ms
    assert res.trades[0].funding_cost == 0.0
    assert len(res.funding_events) == 0


# 5. Position open across multiple settlements
def test_05_multiple_settlements() -> None:
    m1 = _make_m1("2025-01-01 07:50:00", 16 * 60 + 20, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    t2 = int(pd.Timestamp("2025-01-01 16:00:00", tz="UTC").timestamp() * 1000)
    t3 = int(pd.Timestamp("2025-01-02 00:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([
        {"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"},
        {"symbol": "BTC-USDT", "funding_time_ms": t2, "funding_rate": 0.002, "mark_price": 100.0, "source": "bingx"},
        {"symbol": "BTC-USDT", "funding_time_ms": t3, "funding_rate": -0.0005, "mark_price": 100.0, "source": "bingx"},
    ])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": m1})
    assert len(res.funding_events) == 3
    assert res.trades[0].funding_cost == pytest.approx(0.10 + 0.20 - 0.05, abs=1e-9)


# 6. Partial close (one leg closes early, other stays open)
def test_06_partial_close_two_legs_different_lifetimes() -> None:
    df = _make_m1("2025-01-01 07:50:00", 60, 100.0)
    # At 07:58, high=102.5 -> hits 2% TP for L1, does not hit L2 trailing
    df.loc[df.index[8], "high"] = 102.5
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999", tp_pct=0.02, sl_pct=0.05, split_legs=True, trailing_act_pct=0.03, trailing_cb_pct=0.005)
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": df})
    by_leg = {t.leg_type: t for t in res.trades}
    assert by_leg["TP_HALF"].funding_cost == 0.0
    assert by_leg["TRAIL_HALF"].funding_cost == pytest.approx(0.05, abs=1e-9)


# 7. L1/L2 split legs both crossing settlement
def test_07_l1_l2_both_cross_settlement() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999", tp_pct=0.05, sl_pct=0.05, split_legs=True, trailing_act_pct=0.03, trailing_cb_pct=0.005)
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": df})
    assert len(res.trades) == 2
    assert sum(t.funding_cost for t in res.trades) == pytest.approx(0.10, abs=1e-9)


# 8. Hedge LONG + SHORT concurrent on same symbol
def test_08_hedge_long_and_short_concurrent() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    s1 = _make_sig(combo_id=1, side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    s2 = _make_sig(combo_id=2, side="SELL", signal_close_dt="2025-01-01 07:54:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [s1, s2], {"BTC-USDT": df})
    assert res.summary_dict["funding_net_cashflow"] == pytest.approx(0.0, abs=1e-9)
    assert res.summary_dict["funding_paid"] == pytest.approx(0.10, abs=1e-9)
    assert res.summary_dict["funding_received"] == pytest.approx(0.10, abs=1e-9)


# 9. VWAP multiple combos same side (no double counting)
def test_09_vwap_multiple_combos_same_side() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    s1 = _make_sig(combo_id=1, combo_name="C1", side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    s2 = _make_sig(combo_id=2, combo_name="C2", side="BUY", signal_close_dt="2025-01-01 07:55:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [s1, s2], {"BTC-USDT": df})
    assert len(res.trades) == 2
    assert sum(t.funding_cost for t in res.trades) == pytest.approx(0.20, abs=1e-9)
    assert res.summary_dict["accounting_invariant"]["passed"] is True


# 10. Settlement timestamp == entry or == exit
def test_10_boundary_entry_and_exit_at_settlement() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"}])
    # Signal closes at 07:59:59.999 => entry_time_ms == 08:00:00.000 == t1 => 0 funding
    s_at_entry = _make_sig(combo_id=1, side="BUY", signal_close_dt="2025-01-01 07:59:59.999")
    res1 = _sim(ft).run("BINGX_PORTFOLIO", [s_at_entry], {"BTC-USDT": df})
    assert res1.trades[0].entry_time_ms == t1
    assert res1.trades[0].funding_cost == 0.0

    # Exit at 08:00:00.000 (bar_idx=10) => held across 07:59->08:00 boundary => 1 funding
    df2 = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    df2.loc[df2.index[10], "high"] = 106.0
    s_at_exit = _make_sig(combo_id=2, side="BUY", signal_close_dt="2025-01-01 07:54:59.999", tp_pct=0.05)
    res2 = _sim(ft).run("BINGX_PORTFOLIO", [s_at_exit], {"BTC-USDT": df2})
    assert res2.trades[0].exit_time_ms == t1
    assert res2.trades[0].funding_cost == pytest.approx(0.10, abs=1e-9)


# 11. No position (signal rejected due to margin) => 0 funding
def test_11_rejected_signal_zero_funding() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.005, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    # initial_equity=0.5, notional=100, lev=100 => req_margin=1.0 => REJECTED
    res = _sim(ft, initial_equity=0.5, notional=100.0).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": df})
    assert len(res.trades) == 0
    assert len(res.funding_events) == 0
    assert res.summary_dict["funding_net_cashflow"] == 0.0


# 12. Funding accumulation and per-combo attribution equals portfolio total
def test_12_combo_attribution_equals_portfolio_total() -> None:
    m1 = {
        "BTC-USDT": _make_m1("2025-01-01 07:50:00", 30, 100.0),
        "ETH-USDT": _make_m1("2025-01-01 07:50:00", 30, 50.0),
    }
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([
        {"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"},
        {"symbol": "ETH-USDT", "funding_time_ms": t1, "funding_rate": -0.002, "mark_price": 50.0, "source": "bingx"},
    ])
    sigs = [
        _make_sig(symbol="BTC-USDT", combo_id=1, side="BUY", signal_close_dt="2025-01-01 07:54:59.999"),
        _make_sig(symbol="ETH-USDT", combo_id=4, side="SELL", signal_close_dt="2025-01-01 07:54:59.999"),
    ]
    res = _sim(ft).run("BINGX_PORTFOLIO", sigs, m1)
    combo_sum = sum(-t.funding_cost for t in res.trades)
    event_sum = sum(ev.funding_cashflow for ev in res.funding_events)
    assert combo_sum == pytest.approx(event_sum, abs=1e-9)
    assert combo_sum == pytest.approx(res.summary_dict["funding_net_cashflow"], abs=1e-9)


# 13. Missing or duplicate funding timestamps handled cleanly
def test_13_missing_and_duplicate_funding_timestamps() -> None:
    df = _make_m1("2025-01-01 07:50:00", 16 * 60 + 20, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    # t2 missing, t1 duplicated
    ft = _ft([
        {"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"},
        {"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.001, "mark_price": 100.0, "source": "bingx"},
    ])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    res = _sim(ft).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": df})
    assert len(res.funding_events) == 1
    assert res.trades[0].funding_cost == pytest.approx(0.10, abs=1e-9)


# 14. Reproducibility with fixed dataset & funding table
def test_14_reproducibility() -> None:
    df = _make_m1("2025-01-01 07:50:00", 30, 100.0)
    t1 = int(pd.Timestamp("2025-01-01 08:00:00", tz="UTC").timestamp() * 1000)
    ft = _ft([{"symbol": "BTC-USDT", "funding_time_ms": t1, "funding_rate": 0.00123, "mark_price": 100.0, "source": "bingx"}])
    sig = _make_sig(side="BUY", signal_close_dt="2025-01-01 07:54:59.999")
    r1 = _sim(ft).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": df})
    r2 = _sim(ft).run("BINGX_PORTFOLIO", [sig], {"BTC-USDT": df})
    assert r1.trades[0].net_pnl == r2.trades[0].net_pnl
    assert r1.funding_events == r2.funding_events
