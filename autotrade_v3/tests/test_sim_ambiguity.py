"""Hand-calculated unit tests for the 4 intrabar ambiguity situations x 4 policies x 2 sides."""
from __future__ import annotations

import pandas as pd
import pytest

from autotrade_v3.sim.leg import simulate_leg
from autotrade_v3.sim.types import ExitRules, LegSpec


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize(
    "policy,exp_buy_reason,exp_sell_reason",
    [
        ("LEGACY", "SL_INTRABAR_AMBIGUOUS", "SL_INTRABAR_AMBIGUOUS"),
        ("CONSERVATIVE", "SL_INTRABAR_AMBIGUOUS", "SL_INTRABAR_AMBIGUOUS"),
        ("OPTIMISTIC", "TP", "TP"),
        # Bullish bar (O=100, C=100.5 -> O->L->H->C): L first -> BUY hits SL, SELL hits TP
        ("OHLC_PATH", "SL_INTRABAR_AMBIGUOUS", "TP"),
    ],
)
def test_situation_1_tp_and_sl_same_bar(side, policy, exp_buy_reason, exp_sell_reason):
    # entry=100, tp=2%, sl=1% -> BUY: tp=102, sl=99; SELL: tp=98, sl=101
    # Bar 0 (bullish): O=100, H=102.5, L=97.5, C=100.5 (touches both BUY tp/sl and SELL tp/sl)
    df = _bars([(100.0, 102.5, 97.5, 100.5), (100.0, 100.0, 100.0, 100.0)])
    out = simulate_leg(
        df,
        entry_idx=0,
        entry_price=100.0,
        side=side,
        leg_spec=LegSpec("L1", "T1", "SINGLE", 100.0, is_trailing=False),
        exit_rules=ExitRules(tp_pct=0.02, sl_pct=0.01),
        ambiguity_policy=policy,
    )
    exp_reason = exp_buy_reason if side == "BUY" else exp_sell_reason
    assert out.exit_bar_idx == 0
    assert out.exit_reason == exp_reason
    assert out.ambiguity_type == "TP_AND_SL"
    assert out.ambiguity_policy == policy


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize(
    "policy,exp_buy,exp_sell",
    [
        ("LEGACY", (0, "SL_INTRABAR_AMBIGUOUS", 99.0), (0, "SL_INTRABAR_AMBIGUOUS", 101.0)),
        ("CONSERVATIVE", (0, "SL_INTRABAR_AMBIGUOUS", 99.0), (0, "SL_INTRABAR_AMBIGUOUS", 101.0)),
        ("OPTIMISTIC", (1, "TP", 101.0), (1, "TP", 99.0)),  # stays open at bar 0, exits next bar
        # Bullish bar 0 (O->L->H->C): L first -> BUY hits SL; SELL hits act at L then H hits trail_stop
        ("OHLC_PATH", (0, "SL_INTRABAR_AMBIGUOUS", 99.0), (0, "TRAIL", 100.495)),
    ],
)
def test_situation_2_sl_and_trail_activation_same_bar(side, policy, exp_buy, exp_sell):
    # entry=100, sl=1%, act=0.5%, cb=0.5%
    # BUY: sl=99, act=100.5; Bar 0 (bullish): O=100, H=101.0, L=98.5, C=100.8
    # SELL: sl=101, act=99.5; Bar 0 (bullish): O=100, H=101.5, L=99.0, C=100.2 -> wait let's use H=101.5, L=98.5 for both!
    # At L=98.5 for SELL: 98.5 * 1.005 = 98.9925 < sl=101 -> trail_stop = 98.9925
    # Wait: for uniform test numbers, use O=100, H=101.5, L=98.5, C=100.5 -> fav=101.5/98.5
    # For OPTIMISTIC on bar 1: BUY trail_stop = 101.5*(1-0.005)=100.9925; SELL trail_stop = 98.5*(1+0.005)=98.9925
    df = _bars([(100.0, 101.5, 98.5, 100.5), (100.5, 100.5, 100.5, 100.5)])
    # Use MAX_BARS_HELD=1 on bar 1 for clean non-SL exit check in OPTIMISTIC
    out = simulate_leg(
        df,
        entry_idx=0,
        entry_price=100.0,
        side=side,
        leg_spec=LegSpec("L2", "T1", "TRAIL_HALF", 50.0, is_trailing=True),
        exit_rules=ExitRules(tp_pct=0.02, sl_pct=0.01, trailing_act_pct=0.005, trailing_cb_pct=0.005, max_bars_held=1),
        ambiguity_policy=policy,
    )
    if policy == "OPTIMISTIC":
        assert out.exit_bar_idx == 1
        assert out.exit_reason == "MAX_BARS_HELD"
    elif policy == "OHLC_PATH" and side == "SELL":
        assert out.exit_bar_idx == 0
        assert out.exit_reason == "TRAIL"
        assert out.exit_price == pytest.approx(98.5 * 1.005, abs=1e-9)
    else:
        exp_idx, exp_reason, exp_px = exp_buy if side == "BUY" else exp_sell
        assert out.exit_bar_idx == exp_idx
        assert out.exit_reason == exp_reason
        assert out.exit_price == pytest.approx(exp_px, abs=1e-9)


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("policy", ["LEGACY", "CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH"])
def test_situation_3_trail_act_and_callback_same_bar(side, policy):
    # entry=100, sl=2% (BUY 98 / SELL 102), act=0.5%, cb=0.5%
    # Bar 0 (bullish O->L->H->C):
    # BUY: O=100, H=102.0, L=99.5, C=101.8 -> no SL (99.5 > 98), act at 100.5, peak=102, stop=101.49 >= L=99.5
    # SELL: O=100, H=100.5, L=98.0, C=100.2 -> no SL (100.5 < 102), act at 99.5, peak=98, stop=98.49 <= H=100.5
    if side == "BUY":
        df = _bars([(100.0, 102.0, 99.5, 101.8), (101.8, 101.8, 101.8, 101.8)])
        exp_stop = 102.0 * 0.995
    else:
        df = _bars([(100.0, 100.5, 98.0, 100.2), (100.2, 100.2, 100.2, 100.2)])
        exp_stop = 98.0 * 1.005

    out = simulate_leg(
        df,
        entry_idx=0,
        entry_price=100.0,
        side=side,
        leg_spec=LegSpec("L2", "T1", "TRAIL_HALF", 50.0, is_trailing=True),
        exit_rules=ExitRules(tp_pct=0.05, sl_pct=0.02, trailing_act_pct=0.005, trailing_cb_pct=0.005, max_bars_held=1),
        ambiguity_policy=policy,
    )
    if policy in ("LEGACY", "CONSERVATIVE") or (policy == "OHLC_PATH" and side == "SELL"):
        assert out.exit_bar_idx == 0
        assert out.exit_reason == "TRAIL"
        assert out.exit_price == pytest.approx(exp_stop, abs=1e-9)
    else:
        # OPTIMISTIC, or OHLC_PATH BUY (L before H so L passed before activation)
        assert out.exit_bar_idx == 1
        assert out.exit_reason == "MAX_BARS_HELD"
    assert out.ambiguity_type == "TRAIL_ACT_AND_CB"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("policy", ["LEGACY", "CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH"])
def test_situation_4_trail_new_peak_and_stop_same_bar(side, policy):
    # entry=100, sl=2%, act=0.5%, cb=0.5%
    # Bar 0: activates trailing cleanly (no pullback): BUY peak=101, old_stop=100.495; SELL peak=99, old_stop=99.495
    # Bar 1 (bullish):
    #   BUY: O=100.8, H=102.0, L=100.6, C=101.9 -> L=100.6 > old_stop=100.495, but L <= new_stop=101.49
    #   SELL: O=99.2, H=99.4, L=98.0, C=99.3 -> H=99.4 < old_stop=99.495, but H >= new_stop=98.49
    if side == "BUY":
        df = _bars([
            (100.5, 101.0, 100.5, 100.9),
            (100.8, 102.0, 100.6, 101.9),
            (101.9, 101.9, 101.9, 101.9),
        ])
        exp_new_stop = 102.0 * 0.995
    else:
        df = _bars([
            (99.5, 99.4, 99.0, 99.1),
            (99.2, 99.4, 98.0, 99.3),
            (99.3, 99.3, 99.3, 99.3),
        ])
        exp_new_stop = 98.0 * 1.005

    out = simulate_leg(
        df,
        entry_idx=0,
        entry_price=100.0,
        side=side,
        leg_spec=LegSpec("L2", "T1", "TRAIL_HALF", 50.0, is_trailing=True),
        exit_rules=ExitRules(tp_pct=0.05, sl_pct=0.02, trailing_act_pct=0.005, trailing_cb_pct=0.005, max_bars_held=2),
        ambiguity_policy=policy,
    )
    if policy in ("LEGACY", "CONSERVATIVE") or (policy == "OHLC_PATH" and side == "SELL"):
        assert out.exit_bar_idx == 1
        assert out.exit_reason == "TRAIL"
        assert out.exit_price == pytest.approx(exp_new_stop, abs=1e-9)
    else:
        # OPTIMISTIC (L didn't touch old_stop) or OHLC_PATH BUY (L before H, L didn't touch old_stop)
        assert out.exit_bar_idx == 2
        assert out.exit_reason == "MAX_BARS_HELD"
    assert out.ambiguity_type == "TRAIL_PEAK_AND_STOP"


@pytest.mark.parametrize(
    "bar_row,exp_reason",
    [
        ((100.0, 102.5, 98.5, 101.0), "SL_INTRABAR_AMBIGUOUS"),  # bullish: O->L->H->C
        ((100.0, 102.5, 98.5, 100.0), "SL_INTRABAR_AMBIGUOUS"),  # doji (close == open): O->L->H->C
        ((100.0, 102.5, 98.5, 99.5), "TP"),  # bearish: O->H->L->C
    ],
)
def test_ohlc_path_bullish_bearish_doji(bar_row, exp_reason):
    df = _bars([bar_row, (100.0, 100.0, 100.0, 100.0)])
    out = simulate_leg(
        df,
        entry_idx=0,
        entry_price=100.0,
        side="BUY",
        leg_spec=LegSpec("L1", "T1", "SINGLE", 100.0, is_trailing=False),
        exit_rules=ExitRules(tp_pct=0.02, sl_pct=0.01),
        ambiguity_policy="OHLC_PATH",
    )
    assert out.exit_reason == exp_reason
