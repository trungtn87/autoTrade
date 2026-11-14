"""Specification unit tests for M1 leg execution rules (V3-12).

Hand-crafted M1 candles with hand-calculated expected outcomes.
At commit (1) these run against the legacy ExecutionSimulator._simulate_single_leg
and _simulate_trailing_leg; once autotrade_v3.sim.leg is implemented, they run
against simulate_leg (and verify parity with the legacy implementation while present).
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd
import pytest

from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.engine.signal_engine import Signal
from autotrade_v3.strategy.base import ExitPolicy

try:
    from autotrade_v3.sim.leg import simulate_leg
    from autotrade_v3.sim.types import ExitRules, LegSpec

    HAS_SIM_LEG = True
except ImportError:
    HAS_SIM_LEG = False

try:
    from autotrade_v3.engine.execution_simulator import ExecutionSimulator

    HAS_LEGACY_SIM = True
except ImportError:
    HAS_LEGACY_SIM = False


@dataclass
class _LegCheckOutcome:
    exit_bar_idx: int
    exit_price: float
    exit_reason: str
    bars_held: int
    intrabar_ambiguous: bool
    intrabar_trailing_ambiguous: bool


def _make_bars(rows: list[tuple[float, float, float, float]], start_ms: int = 1_700_000_040_000) -> pd.DataFrame:
    """Create deterministic M1 DataFrame from (open, high, low, close) tuples."""
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


def _run_leg(
    df_bars: pd.DataFrame,
    entry_idx: int,
    side: str,
    *,
    is_trailing: bool = False,
    tp_pct: float = 0.02,
    sl_pct: float = 0.01,
    trailing_act_pct: float = 0.01,
    trailing_cb_pct: float = 0.005,
    max_bars_held: int | None = None,
) -> _LegCheckOutcome:
    """Execute one leg using sim.leg.simulate_leg if available, else legacy ExecutionSimulator."""
    entry_price = float(df_bars["open"].iloc[entry_idx])

    if HAS_SIM_LEG:
        leg_spec = LegSpec(
            leg_id="SPEC#L1",
            trade_id="SPEC",
            leg_type="TRAIL_HALF" if is_trailing else "SINGLE",
            notional=100.0,
            is_trailing=is_trailing,
        )
        exit_rules = ExitRules(
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            trailing_act_pct=trailing_act_pct,
            trailing_cb_pct=trailing_cb_pct,
            max_bars_held=max_bars_held,
        )
        out = simulate_leg(
            bars_m1=df_bars,
            entry_idx=entry_idx,
            entry_price=entry_price,
            side=side,
            leg_spec=leg_spec,
            exit_rules=exit_rules,
        )
        return _LegCheckOutcome(
            exit_bar_idx=out.exit_bar_idx,
            exit_price=out.exit_price,
            exit_reason=out.exit_reason,
            bars_held=out.bars_held,
            intrabar_ambiguous=out.intrabar_ambiguous,
            intrabar_trailing_ambiguous=out.intrabar_trailing_ambiguous,
        )

    assert HAS_LEGACY_SIM, "Neither autotrade_v3.sim.leg nor legacy ExecutionSimulator is available"
    sim = ExecutionSimulator(risk_config=RiskConfig(slippage_rate=0.0))
    t_ms = (df_bars.index.asi8 // 1_000_000).astype(np.int64)
    policy = ExitPolicy(
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        split_legs=is_trailing,
        trailing_act_pct=trailing_act_pct,
        trailing_cb_pct=trailing_cb_pct,
        max_bars_held=max_bars_held,
    )
    sig_close_ms = int(t_ms[entry_idx]) - 1
    sig = Signal(
        signal_id="SPEC_SIG",
        symbol="BTC-USDT",
        combo_id=1,
        combo_name="SPEC",
        timeframe="15m",
        side=side,  # type: ignore[arg-type]
        signal_close_ms=sig_close_ms,
        signal_close_dt=pd.Timestamp(sig_close_ms, unit="ms", tz="UTC"),
        bar_position=0,
        bar_open_ms=sig_close_ms - 899_999,
        approved=True,
        warmup_ready=True,
        smc_dir=1 if side == "BUY" else -1,
        ob_blocked=False,
        provider="BINANCE",
        entry_price=entry_price,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        tp_price=entry_price * (1.0 + tp_pct) if side == "BUY" else entry_price * (1.0 - tp_pct),
        sl_price=entry_price * (1.0 - sl_pct) if side == "BUY" else entry_price * (1.0 + sl_pct),
        is_new6=False,
        exit_policy=policy,
    )
    opens = df_bars["open"].to_numpy(dtype=float)
    highs = df_bars["high"].to_numpy(dtype=float)
    lows = df_bars["low"].to_numpy(dtype=float)
    closes = df_bars["close"].to_numpy(dtype=float)
    is_buy = side == "BUY"
    if not is_trailing:
        rec = sim._simulate_single_leg(
            trade_id="SPEC",
            leg_id="SPEC_L1",
            leg_type="SINGLE",
            sig=sig,
            entry_idx=entry_idx,
            entry_price=entry_price,
            leg_notional=100.0,
            is_buy=is_buy,
            tp_price=sig.tp_price,
            sl_price=sig.sl_price,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            n_bars=len(df_bars),
            t_ms_array=t_ms,
            df_bars=df_bars,
            policy=policy,
        )
    else:
        rec = sim._simulate_trailing_leg(
            trade_id="SPEC",
            leg_id="SPEC_L2",
            leg_type="TRAIL_HALF",
            sig=sig,
            entry_idx=entry_idx,
            entry_price=entry_price,
            leg_notional=100.0,
            is_buy=is_buy,
            sl_price=sig.sl_price,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            n_bars=len(df_bars),
            t_ms_array=t_ms,
            df_bars=df_bars,
            policy=policy,
        )
    return _LegCheckOutcome(
        exit_bar_idx=entry_idx + rec.bars_held,
        exit_price=rec.exit_price,
        exit_reason=rec.exit_reason,
        bars_held=rec.bars_held,
        intrabar_ambiguous=rec.intrabar_ambiguous,
        intrabar_trailing_ambiguous=rec.intrabar_trailing_ambiguous,
    )


# ---------------------------------------------------------------------------
# 1. Fixed TP / SL: BUY and SELL
# ---------------------------------------------------------------------------


def test_buy_tp_only() -> None:
    # entry=100.0, TP=102.0 (+2%), SL=99.0 (-1%)
    df = _make_bars([
        (100.0, 101.0, 99.5, 100.5),  # bar 0 (entry): no hit
        (100.5, 102.5, 100.0, 102.0),  # bar 1: high >= 102.0 -> TP
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "TP"
    assert res.exit_price == pytest.approx(102.0)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1
    assert res.intrabar_ambiguous is False


def test_sell_tp_only() -> None:
    # entry=100.0, TP=98.0 (-2%), SL=101.0 (+1%)
    df = _make_bars([
        (100.0, 100.5, 99.0, 99.5),   # bar 0 (entry): no hit
        (99.5, 100.0, 97.5, 98.0),    # bar 1: low <= 98.0 -> TP
    ])
    res = _run_leg(df, 0, "SELL", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "TP"
    assert res.exit_price == pytest.approx(98.0)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1
    assert res.intrabar_ambiguous is False


def test_buy_sl_only() -> None:
    # entry=100.0, TP=102.0, SL=99.0
    df = _make_bars([
        (100.0, 101.0, 99.2, 99.8),
        (99.8, 100.2, 98.5, 98.9),    # bar 1: low <= 99.0 -> SL
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL"
    assert res.exit_price == pytest.approx(99.0)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1
    assert res.intrabar_ambiguous is False


def test_sell_sl_only() -> None:
    # entry=100.0, TP=98.0, SL=101.0
    df = _make_bars([
        (100.0, 100.8, 99.0, 100.2),
        (100.2, 101.5, 99.9, 101.1),  # bar 1: high >= 101.0 -> SL
    ])
    res = _run_leg(df, 0, "SELL", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL"
    assert res.exit_price == pytest.approx(101.0)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1
    assert res.intrabar_ambiguous is False


def test_buy_both_tp_and_sl_same_bar_prioritizes_sl() -> None:
    # entry=100.0, TP=102.0, SL=99.0
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 103.0, 98.0, 101.0),  # bar 1: both TP & SL hit -> SL_INTRABAR_AMBIGUOUS
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL_INTRABAR_AMBIGUOUS"
    assert res.exit_price == pytest.approx(99.0)
    assert res.exit_bar_idx == 1
    assert res.intrabar_ambiguous is True


def test_sell_both_tp_and_sl_same_bar_prioritizes_sl() -> None:
    # entry=100.0, TP=98.0, SL=101.0
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 102.0, 97.0, 99.0),   # bar 1: both TP & SL hit -> SL_INTRABAR_AMBIGUOUS
    ])
    res = _run_leg(df, 0, "SELL", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL_INTRABAR_AMBIGUOUS"
    assert res.exit_price == pytest.approx(101.0)
    assert res.exit_bar_idx == 1
    assert res.intrabar_ambiguous is True


def test_buy_gap_open_past_sl() -> None:
    # entry=100.0, TP=102.0, SL=99.0
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.0),
        (97.5, 98.0, 97.0, 97.8),     # bar 1: open 97.5 <= 99.0 -> gap SL at 97.5
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL"
    assert res.exit_price == pytest.approx(97.5)
    assert res.exit_bar_idx == 1


def test_sell_gap_open_past_sl() -> None:
    # entry=100.0, TP=98.0, SL=101.0
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.0),
        (102.5, 103.0, 102.0, 102.8), # bar 1: open 102.5 >= 101.0 -> gap SL at 102.5
    ])
    res = _run_leg(df, 0, "SELL", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL"
    assert res.exit_price == pytest.approx(102.5)
    assert res.exit_bar_idx == 1


def test_buy_gap_open_past_tp() -> None:
    # entry=100.0, TP=102.0, SL=99.0
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.0),
        (103.0, 104.0, 102.5, 103.5), # bar 1: open 103.0 >= 102.0 -> gap TP at 103.0
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "TP"
    assert res.exit_price == pytest.approx(103.0)
    assert res.exit_bar_idx == 1


def test_sell_gap_open_past_tp() -> None:
    # entry=100.0, TP=98.0, SL=101.0
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.0),
        (97.0, 97.5, 96.5, 96.8),     # bar 1: open 97.0 <= 98.0 -> gap TP at 97.0
    ])
    res = _run_leg(df, 0, "SELL", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "TP"
    assert res.exit_price == pytest.approx(97.0)
    assert res.exit_bar_idx == 1


def test_buy_hit_on_entry_bar() -> None:
    # entry=100.0, TP=102.0, SL=99.0; bar 0 hits TP immediately
    df = _make_bars([
        (100.0, 102.2, 99.5, 101.8),
        (101.8, 102.5, 101.0, 102.0),
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "TP"
    assert res.exit_price == pytest.approx(102.0)
    assert res.exit_bar_idx == 0
    assert res.bars_held == 0


def test_sell_hit_on_entry_bar() -> None:
    # entry=100.0, TP=98.0, SL=101.0; bar 0 hits SL immediately
    df = _make_bars([
        (100.0, 101.2, 99.5, 100.8),
        (100.8, 101.0, 97.0, 98.0),
    ])
    res = _run_leg(df, 0, "SELL", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "SL"
    assert res.exit_price == pytest.approx(101.0)
    assert res.exit_bar_idx == 0
    assert res.bars_held == 0


# ---------------------------------------------------------------------------
# 2. Trailing Stop: BUY and SELL
# ---------------------------------------------------------------------------


def test_buy_trailing_activate_then_callback_next_bar() -> None:
    # entry=100.0, SL=99.0 (-1%), act=101.0 (+1%), cb=0.5%
    # bar 0: high=102.0 >= 101.0 -> active, peak=102.0, trail_stop = max(99.0, 102.0 * 0.995 = 101.49)
    #        low=101.6 > 101.49 -> stays open
    # bar 1: low=101.4 <= 101.49 -> TRAIL at 101.49
    df = _make_bars([
        (100.0, 102.0, 101.6, 101.8),
        (101.8, 101.9, 101.4, 101.5),
    ])
    res = _run_leg(df, 0, "BUY", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(102.0 * 0.995)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1
    assert res.intrabar_trailing_ambiguous is False


def test_sell_trailing_activate_then_callback_next_bar() -> None:
    # entry=100.0, SL=101.0 (+1%), act=99.0 (-1%), cb=0.5%
    # bar 0: no activation (low=99.2 > 99.0), closes at 98.3? No, low=99.2, close=99.2
    # bar 1: open=98.3 (below 98.49), high=98.4, low=98.0 -> active, peak=98.0, trail_stop=98.49
    # bar 2: high=98.6 >= 98.49 -> TRAIL at 98.49
    df = _make_bars([
        (100.0, 100.2, 99.2, 99.2),
        (98.3, 98.4, 98.0, 98.2),
        (98.2, 98.6, 98.1, 98.5),
    ])
    res = _run_leg(df, 0, "SELL", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(98.0 * 1.005)
    assert res.exit_bar_idx == 2
    assert res.bars_held == 2
    assert res.intrabar_trailing_ambiguous is False


def test_buy_trailing_activate_and_callback_same_bar() -> None:
    # entry=100.0, SL=99.0, act=101.0, cb=0.5%
    # bar 0: high=102.0 >= 101.0 -> trail_stop=101.49; low=99.5 (> SL, <= 101.49) -> TRAIL intrabar
    df = _make_bars([
        (100.0, 102.0, 99.5, 100.0),
    ])
    res = _run_leg(df, 0, "BUY", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(102.0 * 0.995)
    assert res.exit_bar_idx == 0
    assert res.intrabar_trailing_ambiguous is True


def test_sell_trailing_activate_and_callback_same_bar() -> None:
    # entry=100.0, SL=101.0, act=99.0, cb=0.5%
    # bar 0: low=98.0 <= 99.0 -> trail_stop=98.49; high=100.5 (< SL, >= 98.49) -> TRAIL intrabar
    df = _make_bars([
        (100.0, 100.5, 98.0, 100.0),
    ])
    res = _run_leg(df, 0, "SELL", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(98.0 * 1.005)
    assert res.exit_bar_idx == 0
    assert res.intrabar_trailing_ambiguous is True


def test_buy_trailing_sl_and_activation_same_bar() -> None:
    # entry=100.0, SL=99.0, act=101.0
    # bar 0: low=98.5 <= 99.0 AND high=101.5 >= 101.0 -> SL_INTRABAR_AMBIGUOUS at 99.0
    df = _make_bars([
        (100.0, 101.5, 98.5, 100.0),
    ])
    res = _run_leg(df, 0, "BUY", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "SL_INTRABAR_AMBIGUOUS"
    assert res.exit_price == pytest.approx(99.0)
    assert res.intrabar_ambiguous is True


def test_sell_trailing_sl_and_activation_same_bar() -> None:
    # entry=100.0, SL=101.0, act=99.0
    # bar 0: high=101.5 >= 101.0 AND low=98.5 <= 99.0 -> SL_INTRABAR_AMBIGUOUS at 101.0
    df = _make_bars([
        (100.0, 101.5, 98.5, 100.0),
    ])
    res = _run_leg(df, 0, "SELL", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "SL_INTRABAR_AMBIGUOUS"
    assert res.exit_price == pytest.approx(101.0)
    assert res.intrabar_ambiguous is True


def test_buy_trailing_new_peak_and_hit_new_stop_same_bar() -> None:
    # entry=100.0, SL=99.0, act=101.0, cb=0.5%
    # bar 0: high=102.0 -> active, peak=102.0, trail_stop=101.49, low=101.6
    # bar 1: low=101.6 (> 101.49), high=104.0 (> peak 102.0) -> new_stop=104.0*0.995=103.48
    #        Since low=101.6 <= 103.48 -> TRAIL at 103.48 with intrabar_trailing_ambiguous=True
    df = _make_bars([
        (100.0, 102.0, 101.6, 101.8),
        (101.8, 104.0, 101.6, 102.5),
    ])
    res = _run_leg(df, 0, "BUY", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(104.0 * 0.995)
    assert res.exit_bar_idx == 1
    assert res.intrabar_trailing_ambiguous is True


def test_sell_trailing_new_peak_and_hit_new_stop_same_bar() -> None:
    # entry=100.0, SL=101.0, act=99.0, cb=0.5%
    # bar 0: low=98.0 -> active, peak=98.0, trail_stop=98.49, high=98.4
    # bar 1: high=98.4 (< 98.49), low=96.0 (< peak 98.0) -> new_stop=96.0*1.005=96.48
    #        Since high=98.4 >= 96.48 -> TRAIL at 96.48 with intrabar_trailing_ambiguous=True
    df = _make_bars([
        (100.0, 98.4, 98.0, 98.2),
        (98.2, 98.4, 96.0, 97.5),
    ])
    res = _run_leg(df, 0, "SELL", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(96.0 * 1.005)
    assert res.exit_bar_idx == 1
    assert res.intrabar_trailing_ambiguous is True


def test_buy_trailing_gap_open_past_trail_stop() -> None:
    # bar 0: high=102.0 -> active, trail_stop=101.49, low=101.6
    # bar 1: open=100.5 <= 101.49 -> gap TRAIL at open 100.5
    df = _make_bars([
        (100.0, 102.0, 101.6, 101.8),
        (100.5, 101.0, 100.2, 100.8),
    ])
    res = _run_leg(df, 0, "BUY", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(100.5)
    assert res.exit_bar_idx == 1


def test_sell_trailing_gap_open_past_trail_stop() -> None:
    # bar 0: low=98.0 -> active, trail_stop=98.49, high=98.4
    # bar 1: open=99.5 >= 98.49 -> gap TRAIL at open 99.5
    df = _make_bars([
        (100.0, 98.4, 98.0, 98.2),
        (99.5, 99.8, 99.1, 99.2),
    ])
    res = _run_leg(df, 0, "SELL", is_trailing=True, sl_pct=0.01, trailing_act_pct=0.01, trailing_cb_pct=0.005)
    assert res.exit_reason == "TRAIL"
    assert res.exit_price == pytest.approx(99.5)
    assert res.exit_bar_idx == 1


# ---------------------------------------------------------------------------
# 3. MAX_BARS_HELD and End-of-Data
# ---------------------------------------------------------------------------


def test_max_bars_held_zero() -> None:
    df = _make_bars([
        (100.0, 105.0, 95.0, 101.0),
        (101.0, 102.0, 100.0, 101.5),
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01, max_bars_held=0)
    assert res.exit_reason == "MAX_BARS_HELD"
    assert res.exit_price == pytest.approx(100.0)
    assert res.exit_bar_idx == 0
    assert res.bars_held == 0


def test_max_bars_held_one() -> None:
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.2),  # bar 0: no TP/SL hit
        (100.3, 105.0, 95.0, 101.0),  # bar 1: MAX_BARS_HELD checked before TP/SL -> exit at open 100.3
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01, max_bars_held=1)
    assert res.exit_reason == "MAX_BARS_HELD"
    assert res.exit_price == pytest.approx(100.3)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1


def test_max_bars_held_n() -> None:
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.1),  # bar 0
        (100.1, 100.6, 99.6, 100.2),  # bar 1
        (100.2, 100.7, 99.7, 100.3),  # bar 2
        (100.4, 101.0, 100.0, 100.8), # bar 3: bars_held == 3 -> MAX_BARS_HELD at open 100.4
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01, max_bars_held=3)
    assert res.exit_reason == "MAX_BARS_HELD"
    assert res.exit_price == pytest.approx(100.4)
    assert res.exit_bar_idx == 3
    assert res.bars_held == 3


def test_end_of_data_force_close() -> None:
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.1),
        (100.1, 100.8, 99.6, 100.4),
        (100.4, 101.0, 99.8, 100.7),  # last bar, neither TP nor SL hit -> FORCE_CLOSE at close 100.7
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "FORCE_CLOSE"
    assert res.exit_price == pytest.approx(100.7)
    assert res.exit_bar_idx == 2
    assert res.bars_held == 2


def test_final_bar_hits_tp() -> None:
    df = _make_bars([
        (100.0, 100.5, 99.5, 100.1),
        (100.1, 102.5, 100.0, 101.9), # last bar hits TP=102.0 -> TP (not FORCE_CLOSE)
    ])
    res = _run_leg(df, 0, "BUY", tp_pct=0.02, sl_pct=0.01)
    assert res.exit_reason == "TP"
    assert res.exit_price == pytest.approx(102.0)
    assert res.exit_bar_idx == 1
    assert res.bars_held == 1
