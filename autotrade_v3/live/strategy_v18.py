"""Signal engine for registry V3_LIVE_18_H1_V2 (pure: no network, DB or logging).

Every rule below reuses the frozen 20FINAL indicator code in
``autotrade_v2.app.strategy`` (identical to the lab ``strategy_core``):

* FINAL14 combos: the same ``precompute``/``entry_variants``/``approved`` path
  as ``final14_exact_strategy.scan_latest``.
* NEW6 combos: same raw indicator signals + SMC/OB approval. With
  ``new6_signal_mode == "every_bar"`` a signal is emitted on every confirmed 1H
  bar where the approved condition holds (lab ``Final20`` batch semantics);
  ``"virtual_position"`` keeps the 20FINAL live single-virtual-position rule.
* ETH-USDT/C1 = H1_BOTH_1: port of ``P3C1ResearchStrategy(variant_id="H1_BOTH_1",
  buy_max_slope_4h_pct=1.5, sell_min_dist_ema150_4h_pct=-6.0, pre_edge_h1=True)``
  — the path ``ExperimentRunner`` really calls (``generate_signals``): BUY =
  20FINAL C1 BUY + 4H EMA150 10-bar slope <= 1.5 %, SELL = corrected C1 SELL
  (CCI < -80) + distance to 4H EMA150 >= -6 %, both filtered before the edge.

Only closed candles are used. A 1H signal exists only on the 15m bar closing at
HH:59:59.999 (the last 15m of that hour); 4H values are aligned to bars that
closed at or before the 1H bar open (``align_confirmed``), so no forming H1/H4
bar is read.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autotrade_v2.app.strategy.final14_exact_strategy import (
    MIN_LIVE_15M_BARS, _new6_latest_action, _new6_raw_signals, _research_frame, hard_tp_sl)
from autotrade_v2.app.strategy.final14_research.indicators import (
    atr, cci, dmi, edge_event, ema, mfi, rsi, sma, stochastic)
from autotrade_v2.app.strategy.final14_research.layer3_long import entry_variants, precompute
from autotrade_v2.app.strategy.final14_research.signals import align_confirmed, resample_ohlcv
from autotrade_v2.app.strategy.final14_research.smc_ob import OBConfig, ob_context
from autotrade_v2.app.strategy.final14_research.smc_structure import smc_direction
from autotrade_v2.app.strategy.final14_research.two_trail_layer12 import approved

from .strategy_registry import ComboSpec, StrategyRegistry

M15_MS = 15 * 60_000
OB_CFG = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)


@dataclass(frozen=True)
class LiveSignal:
    symbol: str
    combo: int
    combo_name: str
    side: str
    timeframe: str
    close_time: int
    entry: float
    tp: float
    sl: float
    tp_pct: float
    sl_pct: float
    smc_dir: int
    registry_version: str
    spec_hash: str
    signal_variant: str

    @property
    def event_id(self) -> str:
        # Identical format to 20FINAL so restart/registry switch dedupe on the same bar.
        return f"{self.symbol}|{self.timeframe}|{self.combo_name}|{self.side}|{self.close_time}"


def h1_both_1_events(d15: pd.DataFrame, params: dict) -> tuple[pd.Series, pd.Series, pd.Series, pd.DataFrame]:
    """ETH-USDT/C1 H1_BOTH_1 BUY/SELL edge events on 1H bars (+ smc1, ob1 used for approval)."""
    d1 = resample_ohlcv(d15, "1h")
    d4 = resample_ohlcv(d15, "4h")
    a21 = atr(d1, 21)
    ema150_4h_raw = ema(d4.close, 150)
    slope_raw = (ema150_4h_raw - ema150_4h_raw.shift(10)) / ema150_4h_raw.shift(10) * 100.0
    ema150_4h = align_confirmed(ema150_4h_raw, d1.index, "4h")
    slope_10bar = align_confirmed(slope_raw, d1.index, "4h")
    dist = (d1.close - ema150_4h) / ema150_4h * 100.0
    r14 = rsi(d1.close, 14)
    m14 = mfi(d1, 14)
    _, _, adx14 = dmi(d1, 14, 14)
    volma20 = sma(d1.volume, 20)
    k11 = stochastic(d1.close, d1.high, d1.low, 11)
    d3 = sma(k11, 3)
    c9 = cci(d1.close, d1.high, d1.low, 9)
    macd = ema(d1.close, 8) - ema(d1.close, 21)
    hist = macd - ema(macd, 9)
    volsp = d1.volume > volma20 * 1.5
    adxr = adx14 > adx14.shift(1)
    mfi_long = float(params.get("mfi_long", 50.0))
    mfi_short = float(params.get("mfi_short", 50.0))

    sb08 = (d1.close > d1.open) & ((d1.close - d1.open) > a21 * 0.8)
    longc = (k11 > 30) & (k11 > d3) & (c9 > 80) & (c9 > c9.shift(1))
    l_base = longc & (r14 > 55) & (hist > 0) & (m14 > mfi_long) & adxr & sb08 & (d1.close > ema150_4h) & volsp

    ss08 = (d1.close < d1.open) & ((d1.open - d1.close) > a21 * 0.8)
    shortc = (k11 < 70) & (k11 < d3) & (c9 < -80) & (c9 < c9.shift(1))
    s_base = shortc & (r14 < 45) & (hist < 0) & (m14 < mfi_short) & adxr & ss08 & (d1.close < ema150_4h) & volsp

    buy_cond = slope_10bar.notna() & (slope_10bar <= float(params["buy_max_slope_4h_pct"]))
    sell_cond = dist.notna() & (dist >= float(params["sell_min_dist_ema150_4h_pct"]))
    if params.get("pre_edge_h1", True):
        ev_l = edge_event(l_base & buy_cond)
        ev_s = edge_event(s_base & sell_cond)
    else:
        ev_l = edge_event(l_base) & buy_cond
        ev_s = edge_event(s_base) & sell_cond
    smc1 = smc_direction(d1, 50, False)
    ob1 = ob_context(d1, OB_CFG)
    return ev_l.fillna(False).astype(bool), ev_s.fillna(False).astype(bool), smc1, ob1


class V18Engine:
    """Computes the approved signals of one registry for a closed-15m frame."""

    def __init__(self, registry: StrategyRegistry):
        self.registry = registry

    # ------------------------------------------------------------------
    def frame_signals(self, symbol: str, m15: pd.DataFrame, *, latest_only: bool = True) -> list[LiveSignal]:
        """All approved signals in ``m15`` (``latest_only`` = only those closing on the last bar).

        ``latest_only=False`` is the batch form used by the parity test; live uses
        ``latest_only=True`` on the newest 3400 closed bars.
        """
        sym = symbol.upper()
        combos = self.registry.for_symbol(sym)
        if not combos:
            return []
        d = _research_frame(m15)
        latest = d.index[-1]
        f14 = [c for c in combos if not c.is_new6 and c.signal_variant == "BASELINE"]
        wanted = {c.token: {c.entry_variant} for c in f14}
        pc = precompute(d, wanted if wanted else {"C1": {"MFI_L50_S50"}})
        d1 = pc["d1"]
        smc15 = smc_direction(d, 50, False)
        smc1 = smc_direction(d1, 50, False)
        ob15 = ob_context(d, OB_CFG)
        ob1 = ob_context(d1, OB_CFG)
        posmap = {ts: i for i, ts in enumerate(d.index)}
        closes = d.close.to_numpy(float)
        out: list[LiveSignal] = []

        def emit(c: ComboSpec, side_code: str, pos: int, sd: int) -> None:
            direction = 1 if side_code == "L" else -1
            entry = float(closes[pos])
            tp, sl = hard_tp_sl(entry, direction, c.as_case())
            close_ms = int(d.index[pos].value // 1_000_000 + M15_MS - 1)
            out.append(LiveSignal(symbol=sym, combo=c.combo_id, combo_name=c.token, side="BUY" if direction == 1 else "SELL",
                                  timeframe=c.native_timeframe, close_time=close_ms, entry=entry, tp=tp, sl=sl,
                                  tp_pct=c.tp_pct, sl_pct=c.sl_pct, smc_dir=int(sd),
                                  registry_version=self.registry.version, spec_hash=c.spec_hash,
                                  signal_variant=c.signal_variant))

        def bar_ok(last_ts) -> int | None:
            pos = posmap.get(last_ts)
            if pos is None:
                return None
            if latest_only and last_ts != latest:
                return None
            return pos

        # FINAL14 (unchanged 20FINAL logic) -------------------------------------
        if f14:
            selected = entry_variants(pc, wanted)
            for c in f14:
                matches = [x for x in selected[c.token] if x[0] == c.entry_variant]
                if len(matches) != 1:
                    raise RuntimeError(f"registry variant mismatch {c.key} {c.entry_variant}: {len(matches)}")
                _, L, S = matches[0]
                one_h = c.native_timeframe == "1h"
                for side_code, series in (("L", L), ("S", S)):
                    hits = series.index[series.fillna(False).astype(bool)]
                    if latest_only:
                        want = latest - pd.Timedelta(minutes=45) if one_h else latest
                        hits = hits[hits == want]
                    for ot in hits:
                        pos = bar_ok(ot + (pd.Timedelta(minutes=45) if one_h else pd.Timedelta(0)))
                        if pos is None:
                            continue
                        sd = int((smc1 if one_h else smc15).loc[ot])
                        ob = (ob1 if one_h else ob15).loc[ot]
                        blocked = bool(ob["buy_blocked" if side_code == "L" else "sell_blocked"])
                        if approved(side_code, sd, blocked, c.layer2):
                            emit(c, side_code, pos, sd)

        # ETH-USDT/C1 H1_BOTH_1 ---------------------------------------------------
        for c in (x for x in combos if x.signal_variant == "H1_BOTH_1"):
            params = {**self.registry.h1_both_1, **c.entry}
            ev_l, ev_s, smc_h, ob_h = h1_both_1_events(d, params)
            for side_code, series in (("L", ev_l), ("S", ev_s)):
                hits = series.index[series]
                if latest_only:
                    hits = hits[hits == latest - pd.Timedelta(minutes=45)]
                for ot in hits:
                    pos = bar_ok(ot + pd.Timedelta(minutes=45))
                    if pos is None:
                        continue
                    sd = int(smc_h.loc[ot])
                    blocked = bool(ob_h.loc[ot]["buy_blocked" if side_code == "L" else "sell_blocked"])
                    if approved(side_code, sd, blocked, c.layer2):
                        emit(c, side_code, pos, sd)

        # NEW6 -------------------------------------------------------------------
        for c in (x for x in combos if x.is_new6):
            if self.registry.new6_signal_mode == "virtual_position":
                if not latest_only:
                    raise NotImplementedError("virtual_position NEW6 is defined only for the latest bar")
                direction, sd = _new6_latest_action(d, d1, c.combo_id, c.as_case(), smc1, ob1)
                if direction:
                    emit(c, "L" if direction == 1 else "S", len(d) - 1, sd)
                continue
            raw_l, raw_s = _new6_raw_signals(d1, c.combo_id)
            lv = raw_l.to_numpy(bool)
            sv = raw_s.to_numpy(bool)
            idx = [int(i) for i in np.flatnonzero(lv != sv)]
            if latest_only:
                want = latest - pd.Timedelta(minutes=45)
                idx = [i for i in idx if d1.index[i] == want]
            for i in idx:
                ot = d1.index[i]
                pos = bar_ok(ot + pd.Timedelta(minutes=45))
                if pos is None:
                    continue
                side_code = "L" if lv[i] else "S"
                sd = int(smc1.loc[ot])
                blocked = bool(ob1.loc[ot, "buy_blocked" if side_code == "L" else "sell_blocked"])
                if approved(side_code, sd, blocked, c.layer2):
                    emit(c, side_code, pos, sd)

        out.sort(key=lambda s: (s.close_time, s.combo, s.side))
        return out

    def calculate(self, symbol: str, m15: pd.DataFrame) -> list[LiveSignal]:
        if len(m15) < MIN_LIVE_15M_BARS:
            from autotrade_v2.app.control.errors import StrategyInsufficientCandlesError
            raise StrategyInsufficientCandlesError(symbol, available=int(len(m15)), required=MIN_LIVE_15M_BARS)
        sigs = self.frame_signals(symbol, m15, latest_only=True)
        allowed = self.registry.allowed()
        bad = [s for s in sigs if (s.symbol, s.combo) not in allowed]
        if bad:  # defensive: never hand a combo outside the registry to L3
            raise RuntimeError(f"tín hiệu ngoài registry {self.registry.version}: {[s.event_id for s in bad]}")
        return sigs
