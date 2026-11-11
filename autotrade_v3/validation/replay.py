"""Independent exit replay used by gate C3.

Written from the exit specification (design doc section 9) and NOT derived from, nor importing,
the execution simulator. Rules implemented, per M1 bar ``i`` starting at the entry bar:

Fixed TP/SL leg (``FIXED``)
  1. ``max_bars_held`` reached (``i - entry >= max_bars``)  -> MAX_BARS_HELD at open[i]
  2. gap at open (only for i > entry unless ``gap_on_entry``):
       BUY : open <= SL -> SL at open ; open >= TP -> TP at open
       SELL: open >= SL -> SL at open ; open <= TP -> TP at open
  3. intrabar: TP and SL both touched -> SL_INTRABAR_AMBIGUOUS at SL (conservative);
     only SL -> SL at SL ; only TP -> TP at TP
  4. data ends -> FORCE_CLOSE at the last bar close

Trailing leg (``TRAIL``): stop starts at SL; trailing activates when the favourable extreme reaches
entry*(1 +/- act). On the activation bar peak = favourable extreme, stop = max/min(SL, peak*(1 -/+ cb))
and the bar may stop out immediately (trailing-ambiguous). Afterwards each bar: gap through the previous
stop -> TRAIL at open ; adverse extreme through the previous stop -> TRAIL at the previous stop ;
otherwise peak/stop are ratcheted and the adverse extreme is tested against the ratcheted stop
(trailing-ambiguous). Before activation a bar touching both SL and the activation level is
SL_INTRABAR_AMBIGUOUS at SL.

``force_final`` models the portfolio mode, where the last bar force-closes every open leg at its close
before any price check.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SL = "SL"
TP = "TP"
TRAIL = "TRAIL"
AMBIGUOUS = "SL_INTRABAR_AMBIGUOUS"
MAX_BARS = "MAX_BARS_HELD"
FORCE = "FORCE_CLOSE"


@dataclass(frozen=True)
class LegSpec:
    is_buy: bool
    kind: str  # "FIXED" or "TRAIL"
    entry_idx: int
    entry_px: float
    tp_pct: float
    sl_pct: float
    act_pct: float
    cb_pct: float
    max_bars: int | None = None
    gap_on_entry: bool = False
    force_final: bool = False


@dataclass(frozen=True)
class ExitResult:
    idx: int
    reason: str
    raw_price: float
    amb_tpsl: bool = False
    amb_trail: bool = False


def levels(spec: LegSpec) -> tuple[float, float, float]:
    """(tp, sl, activation) prices for a leg from its fill price."""
    e = spec.entry_px
    if spec.is_buy:
        return e * (1.0 + spec.tp_pct), e * (1.0 - spec.sl_pct), e * (1.0 + spec.act_pct)
    return e * (1.0 - spec.tp_pct), e * (1.0 + spec.sl_pct), e * (1.0 - spec.act_pct)


def apply_slippage(price: float, is_buy: bool, is_entry: bool, rate: float) -> float:
    """Slippage always against the trader: buy entry/sell exit pay more, sell entry/buy exit get less."""
    if rate <= 0:
        return price
    pays_more = is_buy if is_entry else (not is_buy)
    return price * (1.0 + rate) if pays_more else price * (1.0 - rate)


def _first_true(mask: np.ndarray) -> int:
    if not mask.size:
        return -1
    k = int(np.argmax(mask))
    return k if bool(mask[k]) else -1


def replay_leg(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, spec: LegSpec) -> ExitResult:  # noqa: E741
    n = len(o)
    last = n - 1
    eval_last = last - 1 if spec.force_final else last
    if spec.entry_idx > eval_last:
        return ExitResult(last, FORCE, float(c[last]))
    cap = None
    if spec.max_bars is not None:
        cap = spec.entry_idx + spec.max_bars
        if cap > eval_last:
            cap = None
    if spec.kind == "FIXED":
        res = _replay_fixed(o, h, l, spec, eval_last, cap)
    else:
        res = _replay_trail(o, h, l, spec, eval_last, cap)
    if res is None:
        return ExitResult(last, FORCE, float(c[last]))
    return res


def _blocks(start: int, stop_incl: int):
    block = 1024
    i = start
    while i <= stop_incl:
        j = min(i + block, stop_incl + 1)
        yield i, j
        i = j
        block = min(block * 2, 1 << 18)


def _cap_hit(cap: int | None, i: int, j: int, k: int) -> bool:
    """True when the max-bars bar lies in [i, j) and comes no later than trigger offset k (ties go to cap)."""
    return cap is not None and i <= cap < j and (k < 0 or cap - i <= k)


def _replay_fixed(o, h, l, spec: LegSpec, eval_last: int, cap: int | None) -> ExitResult | None:  # noqa: E741
    tp, sl, _ = levels(spec)
    buy = spec.is_buy
    for i, j in _blocks(spec.entry_idx, eval_last):
        oo, hh, ll = o[i:j], h[i:j], l[i:j]
        if buy:
            gap_sl, gap_tp = oo <= sl, oo >= tp
            hit_tp, hit_sl = hh >= tp, ll <= sl
        else:
            gap_sl, gap_tp = oo >= sl, oo <= tp
            hit_tp, hit_sl = ll <= tp, hh >= sl
        if i == spec.entry_idx and not spec.gap_on_entry:
            gap_sl = gap_sl.copy()
            gap_tp = gap_tp.copy()
            gap_sl[0] = False
            gap_tp[0] = False
        k = _first_true(gap_sl | gap_tp | hit_tp | hit_sl)
        if _cap_hit(cap, i, j, k):
            assert cap is not None
            return ExitResult(cap, MAX_BARS, float(o[cap]))
        if k < 0:
            continue
        g = i + k
        if gap_sl[k]:
            return ExitResult(g, SL, float(o[g]))
        if gap_tp[k]:
            return ExitResult(g, TP, float(o[g]))
        if hit_tp[k] and hit_sl[k]:
            return ExitResult(g, AMBIGUOUS, sl, amb_tpsl=True)
        if hit_sl[k]:
            return ExitResult(g, SL, sl)
        return ExitResult(g, TP, tp)
    return None


def _replay_trail(o, h, l, spec: LegSpec, eval_last: int, cap: int | None) -> ExitResult | None:  # noqa: E741
    _, sl, act = levels(spec)
    buy = spec.is_buy
    s = 1.0 if buy else -1.0
    mult = 1.0 - s * spec.cb_pct
    fav = h if buy else l
    adv = l if buy else h

    def best(a, b):
        return np.maximum(a, b) if buy else np.minimum(a, b)

    active = False
    peak = 0.0
    stop = sl
    start = spec.entry_idx
    # ---- phase 1: before activation ----
    for i, j in _blocks(start, eval_last):
        oo = o[i:j]
        gap_sl = (s * (oo - sl)) <= 0
        if i == spec.entry_idx and not spec.gap_on_entry:
            gap_sl = gap_sl.copy()
            gap_sl[0] = False
        act_hit = (s * (fav[i:j] - act)) >= 0
        sl_hit = (s * (adv[i:j] - sl)) <= 0
        k = _first_true(gap_sl | act_hit | sl_hit)
        if _cap_hit(cap, i, j, k):
            assert cap is not None
            return ExitResult(cap, MAX_BARS, float(o[cap]))
        if k < 0:
            continue
        g = i + k
        if gap_sl[k]:
            return ExitResult(g, SL, float(o[g]))
        if act_hit[k] and sl_hit[k]:
            return ExitResult(g, AMBIGUOUS, sl, amb_tpsl=True)
        if sl_hit[k]:
            return ExitResult(g, SL, sl)
        # activation bar
        peak = float(fav[g])
        stop = float(best(sl, peak * mult))
        if (s * (float(adv[g]) - stop)) <= 0:
            return ExitResult(g, TRAIL, stop, amb_trail=True)
        active = True
        start = g + 1
        break
    if not active:
        return None
    # ---- phase 2: trailing active ----
    for i, j in _blocks(start, eval_last):
        favs = fav[i:j]
        peak_arr = best(np.maximum.accumulate(favs) if buy else np.minimum.accumulate(favs), peak)
        stop_arr = best(stop, peak_arr * mult)
        prev_stop = np.concatenate(([stop], stop_arr[:-1]))
        gap = (s * (o[i:j] - prev_stop)) <= 0
        hit_prev = (s * (adv[i:j] - prev_stop)) <= 0
        hit_new = (s * (adv[i:j] - stop_arr)) <= 0
        k = _first_true(gap | hit_new)
        if _cap_hit(cap, i, j, k):
            assert cap is not None
            return ExitResult(cap, MAX_BARS, float(o[cap]))
        if k >= 0:
            g = i + k
            if gap[k]:
                return ExitResult(g, TRAIL, float(o[g]))
            if hit_prev[k]:
                return ExitResult(g, TRAIL, float(prev_stop[k]))
            return ExitResult(g, TRAIL, float(stop_arr[k]), amb_trail=True)
        peak = float(peak_arr[-1])
        stop = float(stop_arr[-1])
    return None
