from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import (
    atr, cci, combo15_supertrend_dir, combo3_custom_trend, crossover, crossunder,
    dmi, ema, mfi, nadaraya_mid, parabolic_sar, rsi, sma, stdev, stoch,
    ut_atr_trailing_stop,
)


ATR_RISK_COMBOS = {1, 3, 6}
ONE_HOUR_COMBOS = {1, 2, 3, 4, 6}
FIFTEEN_MIN_COMBOS = {5, 7, 8, 9, 10}


def combo_readiness(
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    h6: pd.DataFrame,
    smc_swing_len: int = 50,
) -> dict[int, dict]:
    """Return per-combo readiness without changing combo signal logic.

    A combo is eligible only when every timeframe it depends on has enough
    fully closed bars for its longest indicator input, and its execution
    timeframe has enough bars for the configured SMC swing gate.
    """
    available = {
        "15m": len(m15),
        "1h": len(h1),
        "4h": len(h4),
        "6h": len(h6),
    }
    smc15 = int(smc_swing_len) + 1
    smc1h = int(smc_swing_len) + 1

    requirements: dict[int, dict[str, int]] = {
        # 15m combos
        5: {"15m": max(200, smc15), "4h": 50},
        7: {"15m": smc15, "4h": 100},
        8: {"15m": smc15, "4h": 50},
        9: {"15m": max(200, smc15)},
        10: {"15m": smc15, "4h": 100},
        # 1h combos
        1: {"1h": max(27, smc1h), "4h": 150},
        2: {"1h": max(27, smc1h)},
        3: {"1h": max(27, smc1h)},
        4: {"1h": max(42, smc1h), "6h": 50},
        6: {"1h": max(27, smc1h)},
    }

    out: dict[int, dict] = {}
    for combo, required in requirements.items():
        missing = {
            tf: {"required": need, "available": available[tf]}
            for tf, need in required.items()
            if available[tf] < need
        }
        out[combo] = {
            "ready": not missing,
            "timeframe": "15m" if combo in FIFTEEN_MIN_COMBOS else "1h",
            "requirements": required,
            "available": {tf: available[tf] for tf in required},
            "missing": missing,
        }
    return out


def strategy_static_snapshot() -> dict:
    """Compact non-secret profile for startup diagnostics."""
    return {
        "fifteen_min_combos": sorted(FIFTEEN_MIN_COMBOS),
        "one_hour_combos": sorted(ONE_HOUR_COMBOS),
        "atr_risk_combos": sorted(ATR_RISK_COMBOS),
        "atr_risk_tp_mult": 1.6,
        "atr_risk_sl_mult": 1.4,
        "15m_htf_inputs": {
            "4h_ema50": True,
            "4h_ema100": True,
        },
        "1h_htf_inputs": {
            "4h_ema150": True,
            "6h_ema50": True,
        },
        "smc": {
            "internal_swing_len": 5,
            "modes": ["Off", "Strict", "Veto Only"],
        },
    }


@dataclass(frozen=True)
class Signal:
    symbol: str
    combo: int
    side: str
    timeframe: str
    close_time: int
    entry: float
    tp: float
    sl: float
    smc_dir: int

    @property
    def event_id(self) -> str:
        return f"{self.symbol}|{self.timeframe}|C{self.combo}|{self.side}|{self.close_time}"


def _map_htf(target: pd.DataFrame, higher: pd.DataFrame, values: pd.Series) -> pd.Series:
    """Map last fully closed HTF value to each target candle by close_time."""
    if target.empty or higher.empty:
        return pd.Series(np.nan, index=target.index)
    ht = higher["close_time"].to_numpy(np.int64)
    vv = values.to_numpy(float)
    tt = target["close_time"].to_numpy(np.int64)
    pos = np.searchsorted(ht, tt, side="right") - 1
    out = np.full(len(target), np.nan)
    good = pos >= 0
    out[good] = vv[pos[good]]
    return pd.Series(out, index=target.index)


def _event(cond: pd.Series) -> pd.Series:
    c = cond.fillna(False).astype(bool)
    return c & ~c.shift(1, fill_value=False)


def _hl2(df):
    return (df["high"] + df["low"]) / 2.0


def smc_direction(df: pd.DataFrame, swing_len: int = 50, confluence: bool = False) -> pd.Series:
    """Simplified SMC gate used by Pine v13."""
    n = len(df)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)

    out = np.zeros(n, dtype=int)

    swing_leg = 0
    internal_leg = 0
    swing_high = np.nan
    swing_low = np.nan
    internal_high = np.nan
    internal_low = np.nan
    swing_high_crossed = False
    swing_low_crossed = False
    internal_high_crossed = False
    internal_low_crossed = False
    swing_bias = 0
    internal_bias = 0

    for t in range(n):
        old_swing_leg = swing_leg
        if t >= swing_len:
            recent_high = np.nanmax(high[t - swing_len + 1:t + 1])
            recent_low = np.nanmin(low[t - swing_len + 1:t + 1])
            if high[t - swing_len] > recent_high:
                swing_leg = 0
            elif low[t - swing_len] < recent_low:
                swing_leg = 1
        swing_change = swing_leg - old_swing_leg

        old_internal_leg = internal_leg
        if t >= 5:
            recent_high5 = np.nanmax(high[t - 4:t + 1])
            recent_low5 = np.nanmin(low[t - 4:t + 1])
            if high[t - 5] > recent_high5:
                internal_leg = 0
            elif low[t - 5] < recent_low5:
                internal_leg = 1
        internal_change = internal_leg - old_internal_leg

        if swing_change == 1:
            swing_low = low[t - swing_len]
            swing_low_crossed = False
        elif swing_change == -1:
            swing_high = high[t - swing_len]
            swing_high_crossed = False

        if internal_change == 1:
            internal_low = low[t - 5]
            internal_low_crossed = False
        elif internal_change == -1:
            internal_high = high[t - 5]
            internal_high_crossed = False

        bullish_bar = True
        bearish_bar = True
        if confluence:
            bullish_bar = (high[t] - max(close[t], open_[t])) > min(close[t], open_[t] - low[t])
            bearish_bar = (high[t] - max(close[t], open_[t])) < min(close[t], open_[t] - low[t])

        prev_close = np.nan if t == 0 else close[t - 1]

        internal_bull_extra = (
            not np.isnan(internal_high) and not np.isnan(swing_high)
            and internal_high != swing_high and bullish_bar
        )
        if (
            not np.isnan(internal_high) and not np.isnan(prev_close)
            and close[t] > internal_high and prev_close <= internal_high
            and not internal_high_crossed and internal_bull_extra
        ):
            internal_high_crossed = True
            internal_bias = 1

        internal_bear_extra = (
            not np.isnan(internal_low) and not np.isnan(swing_low)
            and internal_low != swing_low and bearish_bar
        )
        if (
            not np.isnan(internal_low) and not np.isnan(prev_close)
            and close[t] < internal_low and prev_close >= internal_low
            and not internal_low_crossed and internal_bear_extra
        ):
            internal_low_crossed = True
            internal_bias = -1

        if (
            not np.isnan(swing_high) and not np.isnan(prev_close)
            and close[t] > swing_high and prev_close <= swing_high and not swing_high_crossed
        ):
            swing_high_crossed = True
            swing_bias = 1

        if (
            not np.isnan(swing_low) and not np.isnan(prev_close)
            and close[t] < swing_low and prev_close >= swing_low and not swing_low_crossed
        ):
            swing_low_crossed = True
            swing_bias = -1

        if internal_bias == 1 and swing_bias == 1:
            out[t] = 1
        elif internal_bias == -1 and swing_bias == -1:
            out[t] = -1
        else:
            out[t] = 0

    return pd.Series(out, index=df.index)


def combo15_events(df: pd.DataFrame, h4: pd.DataFrame) -> dict[int, tuple[pd.Series, pd.Series]]:
    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]
    v = df["volume"]

    ema50_h4 = _map_htf(df, h4, ema(h4["close"], 50))
    ema100_h4 = _map_htf(df, h4, ema(h4["close"], 100))
    trend_up = c > ema50_h4
    trend_down = c < ema50_h4

    mfi14 = mfi(df, 14)
    sar = parabolic_sar(df, 0.05, 0.1, 0.2)
    sar_bull = c > sar
    sar_bear = c < sar

    ema150 = ema(c, 150)
    ema200 = ema(c, 200)
    rsi14 = rsi(c, 14)
    atr14 = atr(df, 14)
    _, _, adx = dmi(df, 14, 14)
    adx_strong = (adx > 23) & (adx > adx.shift(1))

    candle_body = (c - o).abs()
    candle_range = h - l
    body_ratio = candle_body / candle_range.replace(0, np.nan)
    valid_candle = body_ratio > 0.75

    vol_sma = sma(v, 20)
    vol_spike = v > vol_sma * 1.5
    st_dir = combo15_supertrend_dir(df, atr14)

    combo5_long = (
        (st_dir == 1) & (ema150 > ema200) & (ema150 > ema150.shift(1)) & vol_spike
        & (rsi14 > 45) & valid_candle & adx_strong & (mfi14 > 55) & sar_bull & trend_up
    )
    combo5_short = (
        (st_dir == -1) & vol_spike & (ema150 < ema200) & (ema150 < ema150.shift(1))
        & (rsi14 < 55) & valid_candle & adx_strong & (mfi14 < 55) & sar_bear & trend_down
    )

    ema_fast = ema(c, 21)
    ema_slow = ema(c, 55)
    strong_bullish = (c > o) & ((c - o) > atr14 * 1.5)
    strong_bearish = (c < o) & ((o - c) > atr14 * 1.5)
    adx_rising = adx > adx.shift(1)
    volume_surge = v > v.shift(1) * 1.8

    combo7_long = (
        volume_surge & (ema_fast > ema_slow) & (c > ema100_h4) & adx_rising & (adx > 25)
        & strong_bullish & vol_spike & valid_candle & (rsi14 > 60) & (rsi14 < 85)
    )
    combo7_short = (
        volume_surge & (ema_fast < ema_slow) & (c < ema100_h4) & adx_rising & (adx > 25)
        & strong_bearish & vol_spike & valid_candle & (rsi14 < 40) & (rsi14 > 15)
    )

    cci10 = cci(c, 10)
    long_cond8 = (cci10 > 100) & (cci10 > cci10.shift(1)) & (v > vol_sma * 0.8) & (v > v.shift(1))
    short_cond8 = (cci10 < -100) & (cci10 < cci10.shift(1)) & (v > vol_sma * 0.8) & (v > v.shift(1))
    macd_line = ema(c, 12) - ema(c, 26)
    signal_line = ema(macd_line, 9)
    macd_hist = macd_line - signal_line
    bull_engulfing = (c > o) & (c > o.shift(1)) & (o < c.shift(1))
    bear_engulfing = (c < o) & (c < o.shift(1)) & (o > c.shift(1))

    combo8_long = long_cond8 & (mfi14 > 70) & (adx > 25) & (adx > adx.shift(1)) & bull_engulfing & trend_up & (macd_hist > 0) & valid_candle
    combo8_short = short_cond8 & (mfi14 < 30) & (adx > 25) & (adx > adx.shift(1)) & bear_engulfing & trend_down & (macd_hist < 0) & valid_candle

    nw_mid = nadaraya_mid(c, 24, 3)
    nw_range = stdev(c, 24) * 2.3
    nw_upper = nw_mid + nw_range
    nw_lower = nw_mid - nw_range
    nwe_buy = c < nw_lower
    nwe_sell = c > nw_upper

    combo9_long = (c > ema200) & (rsi14 < 40) & nwe_buy & (st_dir == 1) & (body_ratio > 0.4)
    combo9_short = (c < ema200) & (rsi14 > 60) & nwe_sell & (st_dir == 1) & (body_ratio > 0.4)

    ao = sma(_hl2(df), 5) - sma(_hl2(df), 34)
    squeeze = ema(c, 20) - ema(c, 50)
    mean20 = sma(c, 20)
    sd20 = stdev(c, 20)
    zscore = (c - mean20) / sd20
    body = (c - o).abs()
    upper_wick = h - pd.concat([c, o], axis=1).max(axis=1)
    lower_wick = pd.concat([c, o], axis=1).min(axis=1) - l
    bullish_pin = (lower_wick > body * 1.5) & (c > o)
    bearish_pin = (upper_wick > body * 1.5) & (c < o)

    combo10_long = (ao > 0) & (squeeze > 0) & (zscore < -1.5) & (c > ema100_h4) & (adx > 20) & bullish_pin
    combo10_short = (ao < 0) & (squeeze < 0) & (zscore > 1.5) & (c < ema100_h4) & (adx > 20) & bearish_pin

    return {
        5: (_event(combo5_long), _event(combo5_short)),
        7: (_event(combo7_long), _event(combo7_short)),
        8: (_event(combo8_long), _event(combo8_short)),
        9: (_event(combo9_long), _event(combo9_short)),
        10: (_event(combo10_long), _event(combo10_short)),
    }


def combo60_events(df: pd.DataFrame, h4: pd.DataFrame, h6: pd.DataFrame) -> dict[int, tuple[pd.Series, pd.Series]]:
    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]
    v = df["volume"]

    atr8 = atr(df, 8)
    atr14 = atr(df, 14)
    atr21 = atr(df, 21)
    ema10 = ema(c, 10)
    ema21 = ema(c, 21)
    ema25 = ema(c, 25)

    vol_sma = sma(v, 20)
    vol_spike = v > vol_sma * 1.5
    rsi14 = rsi(c, 14)
    macd_line = ema(c, 8) - ema(c, 21)
    signal_line = ema(macd_line, 9)
    macd_hist = macd_line - signal_line
    _, _, adx = dmi(df, 14, 14)
    adx_rising = adx > adx.shift(1)
    mfi14 = mfi(df, 14)

    strong_bull_candle = (c > o) & ((c - o) > atr21 * 0.8)
    strong_bear_candle = (c < o) & ((o - c) > atr21 * 0.8)
    ema150_h4 = _map_htf(df, h4, ema(h4["close"], 150))
    trend_up_h4 = c > ema150_h4
    trend_down_h4 = c < ema150_h4

    stoch_k = stoch(c, h, l, 11)
    stoch_d = sma(stoch_k, 3)
    cci9 = cci(c, 9)
    long_condition = (stoch_k > 30) & (stoch_k > stoch_d) & (cci9 > 80) & (cci9 > cci9.shift(1))
    short_condition = (stoch_k < 70) & (stoch_k < stoch_d) & (cci9 > -80) & (cci9 < cci9.shift(1))

    bb_basis = sma(c, 20)
    bb_dev = 1.8 * stdev(c, 20)
    upper_bb = bb_basis + bb_dev
    lower_bb = bb_basis - bb_dev
    breakout_bb_up = c > upper_bb
    breakout_bb_down = c < lower_bb

    trailing = ut_atr_trailing_stop(c, atr14, 1.5)
    above = crossover(c, trailing)
    below = crossover(trailing, c)

    sar = parabolic_sar(df, 0.05, 0.2, 0.3)
    bull_engulfing = (c > o) & (c.shift(1) < o.shift(1)) & (c > o.shift(1)) & (o < c.shift(1))
    bear_engulfing = (c < o) & (c.shift(1) > o.shift(1)) & (c < o.shift(1)) & (o > c.shift(1))

    trend = combo3_custom_trend(df, atr8, 4.0)
    supertrend_dir = pd.Series(np.where(c > trend, 1, -1), index=df.index)
    change_close = c.diff()
    obv = (change_close.fillna(0) * v).cumsum()
    obv_up = obv > obv.shift(1)
    obv_down = obv < obv.shift(1)

    ema50_h6 = _map_htf(df, h6, ema(h6["close"], 50))

    t3_fast = ema(c, 21)
    t3_slow = ema(c, 42)
    bullish_t3 = t3_fast > t3_slow
    bearish_t3 = t3_fast < t3_slow
    bb_upper = sma(c, 20) + 2.0 * stdev(c, 20)
    bb_lower = sma(c, 20) - 2.0 * stdev(c, 20)
    keltner_upper = sma(c, 20) + 1.7 * atr(df, 20)
    keltner_lower = sma(c, 20) - 1.7 * atr(df, 20)
    breakout_bullish = crossover(c, bb_upper) & (c > keltner_upper)
    breakout_bearish = crossunder(c, bb_lower) & (c < keltner_lower)

    combo1_long = long_condition & (rsi14 > 55) & (macd_hist > 0) & (mfi14 > 65) & adx_rising & strong_bull_candle & trend_up_h4 & vol_spike
    combo1_short = short_condition & (rsi14 < 45) & (macd_hist < 0) & (mfi14 < 45) & adx_rising & strong_bear_candle & trend_down_h4 & vol_spike

    combo2_long = (c > trailing) & above & (ema10 > ema25) & (macd_line > signal_line) & (adx > 20) & breakout_bb_up
    combo2_short = (c < trailing) & below & (ema10 < ema25) & (macd_line < signal_line) & (adx > 20) & breakout_bb_down

    combo3_long = (supertrend_dir == 1) & (adx > 30) & (c > sar) & bull_engulfing & obv_up
    combo3_short = (supertrend_dir == -1) & (adx > 30) & (c < sar) & bear_engulfing & obv_down

    combo4_long = bullish_t3 & breakout_bullish & (c > ema50_h6) & (supertrend_dir == 1) & strong_bull_candle & vol_spike & (adx > 23)
    combo4_short = bearish_t3 & breakout_bearish & (c < ema50_h6) & (supertrend_dir == -1) & strong_bear_candle & vol_spike & (adx > 23)

    long_condition6 = (rsi14 > 30) & (stoch_k > 30) & (stoch_k > stoch_d) & (cci9 > 100) & (cci9 > cci9.shift(1))
    short_condition6 = (rsi14 < 70) & (stoch_k < 70) & (stoch_k < stoch_d) & (cci9 > -100) & (cci9 < cci9.shift(1))
    strong_bullish6 = (c > o) & ((c - o) > atr21 * 1.2)
    strong_bearish6 = (c < o) & ((o - c) > atr21 * 1.2)
    adx_strong = (adx > 20) & adx_rising
    combo6_long = strong_bullish6 & breakout_bullish & long_condition6 & adx_strong
    combo6_short = strong_bearish6 & breakout_bearish & short_condition6 & adx_strong

    return {
        1: (_event(combo1_long), _event(combo1_short)),
        2: (_event(combo2_long), _event(combo2_short)),
        3: (_event(combo3_long), _event(combo3_short)),
        4: (_event(combo4_long), _event(combo4_short)),
        6: (_event(combo6_long), _event(combo6_short)),
    }


def _smc_approved(smc_mode: str, smc_dir: int, direction: int) -> bool:
    mode = smc_mode.strip().lower()
    if mode == "off":
        return True
    if mode == "strict":
        return smc_dir == direction
    return smc_dir != -direction


def _make_signal(symbol: str, combo: int, direction: int, timeframe: str, row: pd.Series, atr21_value: float, smc_dir: int, sl_pct: float, tp_pct: float) -> Signal:
    entry = float(row["close"])
    if combo in ATR_RISK_COMBOS:
        if direction == 1:
            tp = entry + atr21_value * 1.6
            sl = entry - atr21_value * 1.4
        else:
            tp = entry - atr21_value * 1.6
            sl = entry + atr21_value * 1.4
    else:
        if direction == 1:
            tp = entry * (1 + tp_pct)
            sl = entry * (1 - sl_pct)
        else:
            tp = entry * (1 - tp_pct)
            sl = entry * (1 + sl_pct)
    return Signal(
        symbol=symbol,
        combo=combo,
        side="BUY" if direction == 1 else "SELL",
        timeframe=timeframe,
        close_time=int(row["close_time"]),
        entry=entry,
        tp=float(tp),
        sl=float(sl),
        smc_dir=int(smc_dir),
    )


def scan_latest(symbol: str, m15: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame, h6: pd.DataFrame, smc_mode: str = "Veto Only", smc_swing_len: int = 50, smc_confluence: bool = False, sl_pct: float = 0.009, tp_pct: float = 0.011, include_1h: bool = True) -> list[Signal]:
    signals: list[Signal] = []
    readiness = combo_readiness(m15, h1, h4, h6, smc_swing_len)

    ev15 = combo15_events(m15, h4)
    smc15 = smc_direction(m15, smc_swing_len, smc_confluence)
    atr21_15 = atr(m15, 21)
    if len(m15):
        i = len(m15) - 1
        for combo in sorted(FIFTEEN_MIN_COMBOS):
            if not readiness[combo]["ready"]:
                continue
            long_e, short_e = ev15[combo]
            direction = 1 if bool(long_e.iloc[i]) else -1 if bool(short_e.iloc[i]) else 0
            if direction and _smc_approved(smc_mode, int(smc15.iloc[i]), direction):
                signals.append(_make_signal(symbol, combo, direction, "15m", m15.iloc[i], float(atr21_15.iloc[i]), int(smc15.iloc[i]), sl_pct, tp_pct))

    if include_1h and len(h1):
        ev60 = combo60_events(h1, h4, h6)
        smc60 = smc_direction(h1, smc_swing_len, smc_confluence)
        atr21_60 = atr(h1, 21)
        i = len(h1) - 1
        for combo in sorted(ONE_HOUR_COMBOS):
            if not readiness[combo]["ready"]:
                continue
            long_e, short_e = ev60[combo]
            direction = 1 if bool(long_e.iloc[i]) else -1 if bool(short_e.iloc[i]) else 0
            if direction and _smc_approved(smc_mode, int(smc60.iloc[i]), direction):
                signals.append(_make_signal(symbol, combo, direction, "1h", h1.iloc[i], float(atr21_60.iloc[i]), int(smc60.iloc[i]), sl_pct, tp_pct))

    return signals
