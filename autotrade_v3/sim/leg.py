"""Pure M1 leg execution engine — single source of truth for exit rules."""
from __future__ import annotations

import numpy as np
import pandas as pd

from autotrade_v3.engine.candle_loader import datetime_index_to_ms
from autotrade_v3.sim.ambiguity import (
    AmbiguityPolicy,
    normalize_ambiguity_policy,
    resolve_sl_and_trail_act,
    resolve_tp_and_sl,
    resolve_trail_act_and_cb,
    resolve_trail_peak_and_stop,
)
from autotrade_v3.sim.types import ExitRules, LegOutcome, LegSpec, M1BarArrays


def extract_m1_arrays(bars_m1: pd.DataFrame) -> M1BarArrays:
    """Extract contiguous numpy arrays from an M1 DataFrame once per symbol."""
    t_ms = datetime_index_to_ms(bars_m1.index)
    opens = bars_m1["open"].to_numpy(dtype=float) if "open" in bars_m1 else None
    highs = bars_m1["high"].to_numpy(dtype=float)
    lows = bars_m1["low"].to_numpy(dtype=float)
    closes = bars_m1["close"].to_numpy(dtype=float)
    providers = (
        bars_m1["provider"].to_numpy()
        if "provider" in bars_m1
        else np.array(["BINANCE"] * len(bars_m1))
    )
    return M1BarArrays(
        t_ms=t_ms,
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        index=bars_m1.index,
        providers=providers,
        n_bars=len(bars_m1),
    )


def _simulate_fixed_leg(
    arrays: M1BarArrays,
    entry_idx: int,
    entry_price: float,
    is_buy: bool,
    leg_spec: LegSpec,
    exit_rules: ExitRules,
    ambiguity_policy: AmbiguityPolicy = "LEGACY",
) -> LegOutcome:
    tp_price = entry_price * (1.0 + exit_rules.tp_pct) if is_buy else entry_price * (1.0 - exit_rules.tp_pct)
    sl_price = entry_price * (1.0 - exit_rules.sl_pct) if is_buy else entry_price * (1.0 + exit_rules.sl_pct)

    opens = arrays.opens
    highs = arrays.highs
    lows = arrays.lows
    closes = arrays.closes
    n_bars = arrays.n_bars

    ambiguous = False
    amb_type = "NONE"
    exit_price = 0.0
    exit_reason = ""
    exit_bar_idx = n_bars - 1

    for i in range(entry_idx, n_bars):
        if exit_rules.max_bars_held is not None and (i - entry_idx) >= exit_rules.max_bars_held:
            exit_price = float(opens[i]) if opens is not None else float(closes[i])
            exit_reason = "MAX_BARS_HELD"
            exit_bar_idx = i
            break

        if i > entry_idx and opens is not None:
            op = float(opens[i])
            if is_buy:
                if op <= sl_price:
                    exit_price = op
                    exit_reason = "SL"
                    exit_bar_idx = i
                    break
                elif op >= tp_price:
                    exit_price = op
                    exit_reason = "TP"
                    exit_bar_idx = i
                    break
            else:
                if op >= sl_price:
                    exit_price = op
                    exit_reason = "SL"
                    exit_bar_idx = i
                    break
                elif op <= tp_price:
                    exit_price = op
                    exit_reason = "TP"
                    exit_bar_idx = i
                    break

        h = float(highs[i])
        l = float(lows[i])

        if is_buy:
            hit_tp = h >= tp_price
            hit_sl = l <= sl_price
        else:
            hit_tp = l <= tp_price
            hit_sl = h >= sl_price

        if hit_tp and hit_sl:
            bar_op = float(opens[i]) if opens is not None else float(closes[i])
            bar_cl = float(closes[i])
            res = resolve_tp_and_sl(
                policy=ambiguity_policy,
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=sl_price,
                bar_open=bar_op,
                bar_close=bar_cl,
            )
            ambiguous = res.intrabar_ambiguous
            amb_type = res.ambiguity_type
            exit_price = res.exit_price
            exit_reason = res.exit_reason
            exit_bar_idx = i
            break
        elif hit_sl:
            exit_price = sl_price
            exit_reason = "SL"
            exit_bar_idx = i
            break
        elif hit_tp:
            exit_price = tp_price
            exit_reason = "TP"
            exit_bar_idx = i
            break
    else:
        exit_bar_idx = n_bars - 1
        exit_price = float(closes[exit_bar_idx])
        exit_reason = "FORCE_CLOSE"

    return LegOutcome(
        leg_id=leg_spec.leg_id,
        trade_id=leg_spec.trade_id,
        leg_type=leg_spec.leg_type,
        entry_idx=entry_idx,
        exit_bar_idx=exit_bar_idx,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        bars_held=exit_bar_idx - entry_idx,
        intrabar_ambiguous=ambiguous,
        intrabar_trailing_ambiguous=False,
        ambiguity_type=amb_type,
        ambiguity_policy=ambiguity_policy,
    )


def _simulate_trailing_leg(
    arrays: M1BarArrays,
    entry_idx: int,
    entry_price: float,
    is_buy: bool,
    leg_spec: LegSpec,
    exit_rules: ExitRules,
    ambiguity_policy: AmbiguityPolicy = "LEGACY",
) -> LegOutcome:
    sl_price = entry_price * (1.0 - exit_rules.sl_pct) if is_buy else entry_price * (1.0 + exit_rules.sl_pct)
    act_price = (
        entry_price * (1.0 + exit_rules.trailing_act_pct)
        if is_buy
        else entry_price * (1.0 - exit_rules.trailing_act_pct)
    )
    cb_pct = exit_rules.trailing_cb_pct

    opens = arrays.opens
    highs = arrays.highs
    lows = arrays.lows
    closes = arrays.closes
    n_bars = arrays.n_bars

    trail_active = False
    trail_stop = sl_price
    peak_price = entry_price

    exit_bar_idx = n_bars - 1
    exit_price = 0.0
    exit_reason = ""
    ambiguous = False
    trailing_ambiguous = False
    amb_type = "NONE"

    for i in range(entry_idx, n_bars):
        if exit_rules.max_bars_held is not None and (i - entry_idx) >= exit_rules.max_bars_held:
            exit_price = float(opens[i]) if opens is not None else float(closes[i])
            exit_reason = "MAX_BARS_HELD"
            exit_bar_idx = i
            break

        effective_stop = trail_stop if trail_active else sl_price

        if i > entry_idx and opens is not None:
            op = float(opens[i])
            if is_buy and op <= effective_stop:
                exit_price = op
                exit_reason = "TRAIL" if trail_active else "SL"
                exit_bar_idx = i
                break
            elif not is_buy and op >= effective_stop:
                exit_price = op
                exit_reason = "TRAIL" if trail_active else "SL"
                exit_bar_idx = i
                break

        h = float(highs[i])
        l = float(lows[i])
        bar_op = float(opens[i]) if opens is not None else float(closes[i])
        bar_cl = float(closes[i])
        trailing_ambiguous = False

        if is_buy:
            if not trail_active:
                if l <= sl_price and h >= act_price:
                    res = resolve_sl_and_trail_act(
                        policy=ambiguity_policy,
                        is_buy=True,
                        sl_price=sl_price,
                        fav_extreme=h,
                        cb_pct=cb_pct,
                        bar_open=bar_op,
                        bar_close=bar_cl,
                    )
                    ambiguous = res.intrabar_ambiguous
                    amb_type = res.ambiguity_type
                    if res.should_exit:
                        exit_price = res.exit_price
                        exit_reason = res.exit_reason
                        exit_bar_idx = i
                        break
                    trail_active = True
                    peak_price = res.new_peak_price
                    trail_stop = res.new_trail_stop
                elif h >= act_price:
                    trail_active = True
                    cand_peak = h
                    cand_stop = max(sl_price, cand_peak * (1.0 - cb_pct))
                    if l <= cand_stop:
                        res = resolve_trail_act_and_cb(
                            policy=ambiguity_policy,
                            is_buy=True,
                            fav_extreme=cand_peak,
                            new_stop=cand_stop,
                            bar_open=bar_op,
                            bar_close=bar_cl,
                        )
                        trailing_ambiguous = res.intrabar_trailing_ambiguous
                        amb_type = res.ambiguity_type
                        peak_price = res.new_peak_price
                        trail_stop = res.new_trail_stop
                        if res.should_exit:
                            exit_price = res.exit_price
                            exit_reason = res.exit_reason
                            exit_bar_idx = i
                            break
                    else:
                        peak_price = cand_peak
                        trail_stop = cand_stop
                elif l <= sl_price:
                    exit_price = sl_price
                    exit_reason = "SL"
                    exit_bar_idx = i
                    break
            else:
                if h > peak_price:
                    cand_stop = max(trail_stop, h * (1.0 - cb_pct))
                    if l <= cand_stop:
                        res = resolve_trail_peak_and_stop(
                            policy=ambiguity_policy,
                            is_buy=True,
                            old_stop=trail_stop,
                            new_stop=cand_stop,
                            fav_extreme=h,
                            hit_old_stop=(l <= trail_stop),
                            bar_open=bar_op,
                            bar_close=bar_cl,
                        )
                        trailing_ambiguous = res.intrabar_trailing_ambiguous
                        amb_type = res.ambiguity_type
                        peak_price = res.new_peak_price
                        trail_stop = res.new_trail_stop
                        if res.should_exit:
                            exit_price = res.exit_price
                            exit_reason = res.exit_reason
                            exit_bar_idx = i
                            break
                    else:
                        peak_price = h
                        trail_stop = cand_stop
                elif l <= trail_stop:
                    exit_price = trail_stop
                    exit_reason = "TRAIL"
                    exit_bar_idx = i
                    break
        else:
            if not trail_active:
                if h >= sl_price and l <= act_price:
                    res = resolve_sl_and_trail_act(
                        policy=ambiguity_policy,
                        is_buy=False,
                        sl_price=sl_price,
                        fav_extreme=l,
                        cb_pct=cb_pct,
                        bar_open=bar_op,
                        bar_close=bar_cl,
                    )
                    ambiguous = res.intrabar_ambiguous
                    amb_type = res.ambiguity_type
                    if res.should_exit:
                        exit_price = res.exit_price
                        exit_reason = res.exit_reason
                        exit_bar_idx = i
                        break
                    trail_active = True
                    peak_price = res.new_peak_price
                    trail_stop = res.new_trail_stop
                elif l <= act_price:
                    trail_active = True
                    cand_peak = l
                    cand_stop = min(sl_price, cand_peak * (1.0 + cb_pct))
                    if h >= cand_stop:
                        res = resolve_trail_act_and_cb(
                            policy=ambiguity_policy,
                            is_buy=False,
                            fav_extreme=cand_peak,
                            new_stop=cand_stop,
                            bar_open=bar_op,
                            bar_close=bar_cl,
                        )
                        trailing_ambiguous = res.intrabar_trailing_ambiguous
                        amb_type = res.ambiguity_type
                        peak_price = res.new_peak_price
                        trail_stop = res.new_trail_stop
                        if res.should_exit:
                            exit_price = res.exit_price
                            exit_reason = res.exit_reason
                            exit_bar_idx = i
                            break
                    else:
                        peak_price = cand_peak
                        trail_stop = cand_stop
                elif h >= sl_price:
                    exit_price = sl_price
                    exit_reason = "SL"
                    exit_bar_idx = i
                    break
            else:
                if l < peak_price:
                    cand_stop = min(trail_stop, l * (1.0 + cb_pct))
                    if h >= cand_stop:
                        res = resolve_trail_peak_and_stop(
                            policy=ambiguity_policy,
                            is_buy=False,
                            old_stop=trail_stop,
                            new_stop=cand_stop,
                            fav_extreme=l,
                            hit_old_stop=(h >= trail_stop),
                            bar_open=bar_op,
                            bar_close=bar_cl,
                        )
                        trailing_ambiguous = res.intrabar_trailing_ambiguous
                        amb_type = res.ambiguity_type
                        peak_price = res.new_peak_price
                        trail_stop = res.new_trail_stop
                        if res.should_exit:
                            exit_price = res.exit_price
                            exit_reason = res.exit_reason
                            exit_bar_idx = i
                            break
                    else:
                        peak_price = l
                        trail_stop = cand_stop
                elif h >= trail_stop:
                    exit_price = trail_stop
                    exit_reason = "TRAIL"
                    exit_bar_idx = i
                    break
    else:
        exit_bar_idx = n_bars - 1
        exit_price = float(closes[exit_bar_idx])
        exit_reason = "FORCE_CLOSE"

    return LegOutcome(
        leg_id=leg_spec.leg_id,
        trade_id=leg_spec.trade_id,
        leg_type=leg_spec.leg_type,
        entry_idx=entry_idx,
        exit_bar_idx=exit_bar_idx,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        bars_held=exit_bar_idx - entry_idx,
        intrabar_ambiguous=ambiguous,
        intrabar_trailing_ambiguous=trailing_ambiguous,
        ambiguity_type=amb_type,
        ambiguity_policy=ambiguity_policy,
    )


def _simulate_breakeven_lock_leg(
    arrays: M1BarArrays,
    entry_idx: int,
    entry_price: float,
    is_buy: bool,
    leg_spec: LegSpec,
    exit_rules: ExitRules,
    ambiguity_policy: AmbiguityPolicy = "CONSERVATIVE",
) -> LegOutcome:
    """Simulate a fixed TP/SL leg with MFE-triggered BREAKEVEN_LOCK."""
    tp_price = entry_price * (1.0 + exit_rules.tp_pct) if is_buy else entry_price * (1.0 - exit_rules.tp_pct)
    sl_price = entry_price * (1.0 - exit_rules.sl_pct) if is_buy else entry_price * (1.0 + exit_rules.sl_pct)
    be_trigger_pct = float(exit_rules.be_trigger_pct or 0.0)
    be_offset_pct = float(exit_rules.be_offset_pct or 0.0)
    trigger_px = entry_price * (1.0 + be_trigger_pct) if is_buy else entry_price * (1.0 - be_trigger_pct)
    be_stop_px = entry_price * (1.0 + be_offset_pct)

    opens = arrays.opens
    highs = arrays.highs
    lows = arrays.lows
    closes = arrays.closes
    n_bars = arrays.n_bars

    be_armed = False
    ambiguous = False
    amb_type = "NONE"
    exit_price = 0.0
    exit_reason = ""
    exit_bar_idx = n_bars - 1

    for i in range(entry_idx, n_bars):
        if exit_rules.max_bars_held is not None and (i - entry_idx) >= exit_rules.max_bars_held:
            exit_price = float(opens[i]) if opens is not None else float(closes[i])
            exit_reason = "MAX_BARS_HELD"
            exit_bar_idx = i
            break

        cur_sl = be_stop_px if be_armed else sl_price
        cur_sl_reason = "BREAKEVEN_STOP" if be_armed else "SL"

        if i > entry_idx and opens is not None:
            op = float(opens[i])
            if is_buy:
                if op <= cur_sl:
                    exit_price, exit_reason, exit_bar_idx = op, cur_sl_reason, i
                    break
                elif op >= tp_price:
                    exit_price, exit_reason, exit_bar_idx = op, "TP", i
                    break
            else:
                if op >= cur_sl:
                    exit_price, exit_reason, exit_bar_idx = op, cur_sl_reason, i
                    break
                elif op <= tp_price:
                    exit_price, exit_reason, exit_bar_idx = op, "TP", i
                    break

        h = float(highs[i])
        l = float(lows[i])
        hit_tp = (h >= tp_price) if is_buy else (l <= tp_price)
        hit_sl = (l <= cur_sl) if is_buy else (h >= cur_sl)

        if hit_tp and hit_sl:
            bar_op = float(opens[i]) if opens is not None else float(closes[i])
            bar_cl = float(closes[i])
            res = resolve_tp_and_sl(
                policy=ambiguity_policy,
                is_buy=is_buy,
                tp_price=tp_price,
                sl_price=cur_sl,
                bar_open=bar_op,
                bar_close=bar_cl,
            )
            ambiguous = res.intrabar_ambiguous
            amb_type = res.ambiguity_type
            exit_price = res.exit_price
            exit_reason = cur_sl_reason if res.exit_reason in ("SL", "SL_INTRABAR_AMBIGUOUS") else res.exit_reason
            exit_bar_idx = i
            break
        elif hit_sl:
            exit_price, exit_reason, exit_bar_idx = cur_sl, cur_sl_reason, i
            break
        elif hit_tp:
            exit_price, exit_reason, exit_bar_idx = tp_price, "TP", i
            break

        if not be_armed:
            hit_trig = (h >= trigger_px) if is_buy else (l <= trigger_px)
            if hit_trig:
                hit_be_same_bar = (l <= be_stop_px) if is_buy else (h >= be_stop_px)
                if hit_be_same_bar:
                    ambiguous = True
                    amb_type = "BE_TRIGGER_AND_STOP_SAME_BAR"
                    exit_price = be_stop_px
                    exit_reason = "BREAKEVEN_STOP"
                    exit_bar_idx = i
                    break
                be_armed = True
    else:
        exit_bar_idx = n_bars - 1
        exit_price = float(closes[exit_bar_idx])
        exit_reason = "FORCE_CLOSE"

    return LegOutcome(
        leg_id=leg_spec.leg_id,
        trade_id=leg_spec.trade_id,
        leg_type=leg_spec.leg_type,
        entry_idx=entry_idx,
        exit_bar_idx=exit_bar_idx,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        bars_held=exit_bar_idx - entry_idx,
        intrabar_ambiguous=ambiguous,
        intrabar_trailing_ambiguous=False,
        ambiguity_type=amb_type,
        ambiguity_policy=ambiguity_policy,
    )


def simulate_leg(
    bars_m1: pd.DataFrame | M1BarArrays,
    entry_idx: int,
    entry_price: float,
    side: str,
    leg_spec: LegSpec,
    exit_rules: ExitRules,
    ambiguity_policy: str = "LEGACY",
) -> LegOutcome:
    """Simulate a single leg over M1 bars starting at ``entry_idx``.

    Pure function: contains the sole implementation of M1 exit rules
    (fixed TP/SL, trailing activation/peak/callback, gap open, intrabar
    ambiguity policy, MAX_BARS_HELD, and FORCE_CLOSE).
    """
    policy = normalize_ambiguity_policy(ambiguity_policy)
    arrays = bars_m1 if isinstance(bars_m1, M1BarArrays) else extract_m1_arrays(bars_m1)
    if entry_idx < 0 or entry_idx >= arrays.n_bars:
        raise IndexError(f"entry_idx {entry_idx} out of range for {arrays.n_bars} M1 bars")

    is_buy = side.upper() == "BUY"
    if leg_spec.is_trailing:
        return _simulate_trailing_leg(
            arrays=arrays,
            entry_idx=entry_idx,
            entry_price=entry_price,
            is_buy=is_buy,
            leg_spec=leg_spec,
            exit_rules=exit_rules,
            ambiguity_policy=policy,
        )
    if exit_rules.be_trigger_pct is not None:
        return _simulate_breakeven_lock_leg(
            arrays=arrays,
            entry_idx=entry_idx,
            entry_price=entry_price,
            is_buy=is_buy,
            leg_spec=leg_spec,
            exit_rules=exit_rules,
            ambiguity_policy=policy,
        )
    return _simulate_fixed_leg(
        arrays=arrays,
        entry_idx=entry_idx,
        entry_price=entry_price,
        is_buy=is_buy,
        leg_spec=leg_spec,
        exit_rules=exit_rules,
        ambiguity_policy=policy,
    )
