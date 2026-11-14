from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

from ...api import ComboSpec, ExitPolicy, SignalEvent, Strategy, StrategySpec
from ...hashing import compute_all_hashes, normalize_lf
from ...indicators.indicators import (
    atr,
    cci,
    crossover,
    crossunder,
    edge_event,
    ema,
    obv_variant,
    parabolic_sar,
    pine_custom_supertrend_dir,
    sma,
    stochastic,
    session_vwap,
)
from ...indicators.layer3_long import _nadaraya, precompute, entry_variants, NATIVE
from ...indicators.signals import align_confirmed
from ...indicators.smc_structure import smc_direction
from ...indicators.smc_ob import ob_context, OBConfig
from ...indicators.two_trail_layer12 import approved

MIN_LIVE_15M_BARS = 3400


def _research_frame(m15: pd.DataFrame) -> pd.DataFrame:
    d=m15.copy(deep=True)
    if not isinstance(d.index,pd.DatetimeIndex):
        d.index=pd.to_datetime(d["open_time"],unit="ms",utc=True)
    elif d.index.tz is None:
        d.index=d.index.tz_localize("UTC")
    else:
        d.index=d.index.tz_convert("UTC")
    return d.sort_index()


def _combo_name(combo:int)->str:
    return "TIER" if int(combo)==11 else f"C{int(combo)}"


# -----------------------------------------------------------------------------
# NEW6 exact indicator ports. These intentionally mirror the research scripts
# used to select N1..N6. All operate on confirmed native 1H candles only.
# -----------------------------------------------------------------------------

def _clean_bool(values, index:pd.Index)->pd.Series:
    return pd.Series(values,index=index).fillna(False).astype(bool)


def _cross_up(a:np.ndarray,b:np.ndarray)->np.ndarray:
    return (a>b) & np.r_[False,a[:-1]<=b[:-1]]


def _cross_down(a:np.ndarray,b:np.ndarray)->np.ndarray:
    return (a<b) & np.r_[False,a[:-1]>=b[:-1]]


def _demarker(high:np.ndarray,low:np.ndarray,n:int)->np.ndarray:
    prev_h=np.r_[np.nan,high[:-1]]
    prev_l=np.r_[np.nan,low[:-1]]
    demax=np.maximum(high-prev_h,0)
    demin=np.maximum(prev_l-low,0)
    a=pd.Series(demax).rolling(n).mean().to_numpy()
    b=pd.Series(demin).rolling(n).mean().to_numpy()
    return a/(a+b)


def _aroon(high:np.ndarray,low:np.ndarray,period:int)->tuple[np.ndarray,np.ndarray]:
    n=len(high)
    up=np.full(n,np.nan)
    down=np.full(n,np.nan)
    for i in range(period-1,n):
        hs=high[i-period+1:i+1]
        ls=low[i-period+1:i+1]
        imax=0
        imin=0
        mx=hs[0]
        mn=ls[0]
        for j in range(1,period):
            if hs[j]>=mx:
                mx=hs[j]
                imax=j
            if ls[j]<=mn:
                mn=ls[j]
                imin=j
        since_h=(period-1)-imax
        since_l=(period-1)-imin
        up[i]=100.0*(period-since_h)/period
        down[i]=100.0*(period-since_l)/period
    return up,down


def _cmo(close:np.ndarray,length:int)->np.ndarray:
    s=pd.Series(close)
    delta=s.diff()
    up=delta.clip(lower=0).rolling(length).sum()
    down=(-delta.clip(upper=0)).rolling(length).sum()
    return (100*(up-down)/(up+down)).to_numpy()


def _ichimoku(
    high:np.ndarray,low:np.ndarray,conv:int,base:int,spanb:int
)->tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray]:
    hs=pd.Series(high)
    ls=pd.Series(low)
    tenkan=(hs.rolling(conv).max()+ls.rolling(conv).min())/2
    kijun=(hs.rolling(base).max()+ls.rolling(base).min())/2
    span_a=((tenkan+kijun)/2).shift(base)
    span_b=((hs.rolling(spanb).max()+ls.rolling(spanb).min())/2).shift(base)
    return (
        tenkan.to_numpy(),kijun.to_numpy(),
        span_a.to_numpy(),span_b.to_numpy(),
    )


def _kama_er(
    close:np.ndarray,erlen:int,fastlen:int,slowlen:int
)->tuple[np.ndarray,np.ndarray]:
    n=len(close)
    kama=np.full(n,np.nan)
    er=np.full(n,np.nan)
    fast=2.0/(fastlen+1.0)
    slow=2.0/(slowlen+1.0)
    if n<=erlen:
        return kama,er
    kama[erlen]=close[erlen]
    for i in range(erlen,n):
        change=abs(close[i]-close[i-erlen])
        volatility=0.0
        for j in range(i-erlen+1,i+1):
            volatility+=abs(close[j]-close[j-1])
        efficiency=change/volatility if volatility>1e-12 else 0.0
        er[i]=efficiency
        if i>erlen:
            sc=(efficiency*(fast-slow)+slow)**2
            kama[i]=kama[i-1]+sc*(close[i]-kama[i-1])
    return kama,er


def _vortex(
    high:np.ndarray,low:np.ndarray,close:np.ndarray,length:int
)->tuple[np.ndarray,np.ndarray]:
    prev_c=np.r_[np.nan,close[:-1]]
    prev_h=np.r_[np.nan,high[:-1]]
    prev_l=np.r_[np.nan,low[:-1]]
    tr=np.nanmax(
        np.vstack([high-low,np.abs(high-prev_c),np.abs(low-prev_c)]),
        axis=0,
    )
    vm_plus=np.abs(high-prev_l)
    vm_minus=np.abs(low-prev_h)
    tr_sum=pd.Series(tr).rolling(length).sum().to_numpy()
    vi_plus=pd.Series(vm_plus).rolling(length).sum().to_numpy()/tr_sum
    vi_minus=pd.Series(vm_minus).rolling(length).sum().to_numpy()/tr_sum
    return vi_plus,vi_minus


def _new6_raw_signals(d1:pd.DataFrame,combo:int)->tuple[pd.Series,pd.Series]:
    h=d1.high.to_numpy(float)
    l=d1.low.to_numpy(float)
    c=d1.close.to_numpy(float)
    combo=int(combo)

    if combo==101:
        z=_demarker(h,l,60)
        return _clean_bool(z>.75,d1.index),_clean_bool(z<.25,d1.index)

    if combo==102:
        up,down=_aroon(h,l,40)
        return (
            _clean_bool(_cross_up(up,down)&(up>=70),d1.index),
            _clean_bool(_cross_up(down,up)&(down>=70),d1.index),
        )

    if combo==103:
        z=_cmo(c,40)
        long=(z>40)&np.r_[False,z[:-1]<=40]
        short=(z<-40)&np.r_[False,z[:-1]>=-40]
        return _clean_bool(long,d1.index),_clean_bool(short,d1.index)

    if combo==104:
        tenkan,kijun,span_a,span_b=_ichimoku(h,l,12,30,60)
        top=np.maximum(span_a,span_b)
        bottom=np.minimum(span_a,span_b)
        return (
            _clean_bool(
                _cross_up(tenkan,kijun)&(c>top)&(span_a>span_b),d1.index
            ),
            _clean_bool(
                _cross_down(tenkan,kijun)&(c<bottom)&(span_a<span_b),d1.index
            ),
        )

    if combo==105:
        kama,er=_kama_er(c,40,2,40)
        slope=np.r_[np.nan,np.diff(kama)]
        return (
            _clean_bool((c>kama)&(slope>0)&(er>.4),d1.index),
            _clean_bool((c<kama)&(slope<0)&(er>.4),d1.index),
        )

    if combo==106:
        vi_plus,vi_minus=_vortex(h,l,c,14)
        return (
            _clean_bool(
                _cross_up(vi_plus,vi_minus)&(vi_plus>vi_minus*1.2),d1.index
            ),
            _clean_bool(
                _cross_up(vi_minus,vi_plus)&(vi_minus>vi_plus*1.2),d1.index
            ),
        )

    raise KeyError(f"unknown NEW6 combo {combo}")


def _new6_filtered_signals(
    d1:pd.DataFrame,
    combo:int,
    cfg:dict,
    smc1:pd.Series,
    ob1:pd.DataFrame|None,
)->tuple[pd.Series,pd.Series]:
    raw_l,raw_s=_new6_raw_signals(d1,combo)
    long=pd.Series(False,index=d1.index,dtype=bool)
    short=pd.Series(False,index=d1.index,dtype=bool)
    layer2=str(cfg["layer2"])

    for i,ot in enumerate(d1.index):
        is_long=bool(raw_l.iloc[i])
        is_short=bool(raw_s.iloc[i])
        if is_long==is_short:
            continue
        side_code="L" if is_long else "S"
        sd=int(smc1.loc[ot]) if ot in smc1.index else 0
        blocked=False
        if ob1 is not None and ot in ob1.index:
            col="buy_blocked" if is_long else "sell_blocked"
            blocked=bool(ob1.loc[ot,col])
        if approved(side_code,sd,blocked,layer2):
            if is_long:
                long.loc[ot]=True
            else:
                short.loc[ot]=True
    return long,short


# =============================================================================
# V3-11 Strategy Core Adapter (Batch generate() & Parameter-Driven Evaluation)
# =============================================================================

M15_MS = 900_000
FINAL20_DIR = Path(__file__).resolve().parent
DEFAULT_PARAMS_PATH = FINAL20_DIR / "params" / "20final_v1.json"
DEFAULT_LOCK_PATH = FINAL20_DIR / "params" / "20final_v1.lock"

_BASELINE_ENTRY_DEFAULTS: dict[tuple[int, str], dict[str, Any]] = {
    (1, "MFI_L50_S50"): {"mfi_long": 50.0, "mfi_short": 50.0},
    (2, "ADX_20"): {"adx": 20.0},
    (3, "ADX_34"): {"adx": 34.0},
    (4, "ADX_23"): {"adx": 23.0},
    (6, "BODY_1.4"): {"body_atr": 1.4},
    (6, "BODY_1.8"): {"body_atr": 1.8},
    (7, "BODY1.6_VOL2.0"): {"body_atr": 1.6, "volume_mult": 2.0},
    (7, "BODY1.3_VOL1.5"): {"body_atr": 1.3, "volume_mult": 1.5},
    (9, "RSI45_FIXST"): {"rsi_long_max": 45.0, "rsi_short_min": 55.0, "fix_short_supertrend": True},
    (10, "ADX_22"): {"adx": 22.0},
    (10, "ADX_28"): {"adx": 28.0},
    (11, "ADX30_T27_T35_V1.3"): {"adx": 30.0, "tier2_score": 7.0, "tier3_score": 5.0, "volume_mult": 1.3},
    (101, "trend_n60_0.25_0.75"): {"indicator": "DeMarker", "period": 60, "short_max": 0.25, "long_min": 0.75},
    (102, "cross_n40_t70"): {"indicator": "Aroon", "period": 40, "threshold": 70.0},
    (103, "momcross_n40_t40"): {"indicator": "CMO", "period": 40, "threshold": 40.0},
    (104, "tkcross_12_30_60"): {"indicator": "Ichimoku", "conv": 12, "base": 30, "spanb": 60},
    (105, "state_e40_s40_t0.4"): {"indicator": "KAMA_ER", "er_len": 40, "fast_len": 2, "slow_len": 40, "er_min": 0.4},
    (106, "cross_n14_r1.2"): {"indicator": "Vortex", "period": 14, "ratio": 1.2},
}


class BaselineLockError(RuntimeError):
    """Raised when loading 20FINAL@1 fails baseline lock verification."""


def _is_default_entry(combo: ComboSpec) -> bool:
    expected = _BASELINE_ENTRY_DEFAULTS.get((combo.combo_id, combo.entry_variant))
    return expected is not None and combo.entry == expected


def _custom_final14_signals(pc: dict[str, Any], combo: ComboSpec) -> tuple[pd.Series, pd.Series]:
    """Evaluate FINAL14 entry signals when a clone modifies entry thresholds outside the default grid."""
    cid = combo.combo_id
    ent = combo.entry
    d15 = pc["d15"]
    d1 = pc["d1"]
    d4 = pc["d4"]
    d6 = pc["d6"]
    p15 = pc["15"]
    p1 = pc["1"]

    if cid in (1, 2, 3, 4, 6, 11):
        d = d1
        A21 = p1["A21"]
        R = p1["R"]
        M = p1["M"]
        ADX = p1["ADX"]
        K = p1["K"]
        D = p1["D"]
        C = p1["C"]
        macd = ema(d.close, 8) - ema(d.close, 21)
        sig = ema(macd, 9)
        hist = macd - sig
        volma = p1["volma"]
        volsp = d.volume > volma * 1.5
        adxr = ADX > ADX.shift(1)
        sb08 = (d.close > d.open) & ((d.close - d.open) > A21 * 0.8)
        ss08 = (d.close < d.open) & ((d.open - d.close) > A21 * 0.8)
        tu = d.close > p1["ema1504"]
        td = d.close < p1["ema1504"]
        longc = (K > 30) & (K > D) & (C > 80) & (C > C.shift(1))
        shortc = (K < 70) & (K < D) & (C > -80) & (C < C.shift(1))

        if cid == 1:
            lth = float(ent.get("mfi_long", 50.0))
            sth = float(ent.get("mfi_short", 50.0))
            L = longc & (R > 55) & (hist > 0) & (M > lth) & adxr & sb08 & tu & volsp
            S = shortc & (R < 45) & (hist < 0) & (M < sth) & adxr & ss08 & td & volsp
            return edge_event(L), edge_event(S)

        if cid == 2:
            adxth = float(ent.get("adx", 20.0))
            e10 = ema(d.close, 10)
            e25 = ema(d.close, 25)
            basis = sma(d.close, 20)
            dev = 1.8 * d.close.rolling(20, min_periods=20).std(ddof=0)
            bbup = d.close > (basis + dev)
            bbdn = d.close < (basis - dev)
            a14 = p1.get("A14") if "A14" in p1 else atr(d, 14)
            trstop = 1.5 * a14
            x = d.close.to_numpy(float)
            ts = trstop.to_numpy(float)
            arr = np.full(len(d), np.nan)
            for i in range(len(d)):
                prev = arr[i - 1] if i else np.nan
                nz = 0 if not np.isfinite(prev) else prev
                prevsrc = x[i - 1] if i else np.nan
                if i and x[i] > nz and prevsrc > nz:
                    arr[i] = max(nz, x[i] - ts[i])
                elif i and x[i] < nz and prevsrc < nz:
                    arr[i] = min(nz, x[i] + ts[i])
                else:
                    arr[i] = x[i] - ts[i] if x[i] > nz else x[i] + ts[i]
            ats = pd.Series(arr, index=d.index)
            above = crossover(ema(d.close, 1), ats)
            below = crossover(ats, ema(d.close, 1))
            L = (d.close > ats) & above & (e10 > e25) & (macd > sig) & (ADX > adxth) & bbup
            S = (d.close < ats) & below & (e10 < e25) & (macd < sig) & (ADX > adxth) & bbdn
            return edge_event(L), edge_event(S)

        stdir1 = pine_custom_supertrend_dir(d, 8, 4.0)
        if cid == 3:
            adxth = float(ent.get("adx", 34.0))
            sar = parabolic_sar(d, 0.05, 0.2, 0.3)
            bull_eng = (d.close > d.open) & (d.close.shift(1) < d.open.shift(1)) & (d.close > d.open.shift(1)) & (d.open < d.close.shift(1))
            bear_eng = (d.close < d.open) & (d.close.shift(1) > d.open.shift(1)) & (d.close < d.open.shift(1)) & (d.open > d.close.shift(1))
            O = obv_variant(d)
            oup = O > O.shift(1)
            odn = O < O.shift(1)
            L = (stdir1 == 1) & (ADX > adxth) & (d.close > sar) & bull_eng & oup
            S = (stdir1 == -1) & (ADX > adxth) & (d.close < sar) & bear_eng & odn
            return edge_event(L), edge_event(S)

        e50_6 = align_confirmed(ema(d6.close, 50), d.index, "6h")
        tfast = ema(d.close, 21)
        tslow = ema(d.close, 42)
        bt = tfast > tslow
        br = tfast < tslow
        std20 = d.close.rolling(20, min_periods=20).std(ddof=0)
        bbu = sma(d.close, 20) + 2 * std20
        bbl = sma(d.close, 20) - 2 * std20
        ku = sma(d.close, 20) + 1.7 * atr(d, 20)
        kl = sma(d.close, 20) - 1.7 * atr(d, 20)
        breakout_b = crossover(d.close, bbu) & (d.close > ku)
        breakout_s = crossunder(d.close, bbl) & (d.close < kl)

        if cid == 4:
            adxth = float(ent.get("adx", 23.0))
            L = bt & breakout_b & (d.close > e50_6) & (stdir1 == 1) & sb08 & volsp & (ADX > adxth)
            S = br & breakout_s & (d.close < e50_6) & (stdir1 == -1) & ss08 & volsp & (ADX > adxth)
            return edge_event(L), edge_event(S)

        if cid == 6:
            bm = float(ent.get("body_atr", 1.4))
            lc6 = (R > 30) & (K > 30) & (K > D) & (C > 100) & (C > C.shift(1))
            sc6 = (R < 70) & (K < 70) & (K < D) & (C > -100) & (C < C.shift(1))
            adxs = (ADX > 20) & adxr
            sb = (d.close > d.open) & ((d.close - d.open) > A21 * bm)
            ss = (d.close < d.open) & ((d.open - d.close) > A21 * bm)
            return edge_event(sb & breakout_b & lc6 & adxs), edge_event(ss & breakout_s & sc6 & adxs)

        if cid == 11:
            adxth = float(ent.get("adx", 30.0))
            t2 = float(ent.get("tier2_score", 7.0))
            t3 = float(ent.get("tier3_score", 5.0))
            vm = float(ent.get("volume_mult", 1.3))
            dip = p1["dip"]
            dim = p1["dim"]
            A20 = atr(d, 20)
            e50 = align_confirmed(ema(d4.close, 50), d.index, "4h")
            e150 = align_confirmed(ema(d4.close, 150), d.index, "4h")
            e200 = align_confirmed(ema(d4.close, 200), d.index, "4h")
            V = session_vwap(d)
            macd12 = ema(d.close, 12) - ema(d.close, 26)
            sig12 = ema(macd12, 9)
            hist12 = macd12 - sig12
            C9 = cci(d.close, d.high, d.low, 9)
            K14 = stochastic(d.close, d.high, d.low, 14)
            D14 = sma(K14, 3)
            e9 = ema(d.close, 9)
            e21 = ema(d.close, 21)
            sup = sma(d.close, 8) + 2 * A20
            strong = (d.close - d.open).abs() > A20 * 0.8
            sd = d.close.rolling(20, min_periods=20).std(ddof=0)
            z = (d.close - sma(d.close, 20)) / sd
            ao = ema(d.close, 5) - ema(d.close, 34)
            sq = ema(d.close, 20) - ema(d.close, 50)
            rav = sma(R, 14)
            stdt = pine_custom_supertrend_dir(d, 8, 4.0)
            pcL = d.close > d.close.rolling(20, min_periods=20).max().shift(1)
            pcS = d.close < d.close.rolling(20, min_periods=20).min().shift(1)
            vs = d.volume > sma(d.volume, 20) * vm
            tdL = (dip > dim) & (dip > 22)
            tdS = (dip < dim) & (dim > 22)
            trL = (e50 > e200) & tdL & (d.close > V) & (e50 > e50.shift(1))
            trS = (e50 < e200) & tdS & (d.close < V) & (e50 < e50.shift(1))
            t1L = trL & vs & (ADX > adxth)
            t1S = trS & vs & (ADX > adxth)
            s2L = (hist12 > 0) * 2 + (e150 > e200) * 2 + (C9 > 100) * 1 + (R > 50) * 1 + (d.close > sup) * 1 + crossover(K14, D14) * 0.5 + (d.close > e21) * 1 + (strong & (d.close > d.open)) * 1
            s2S = (hist12 < 0) * 2 + (e150 < e200) * 2 + (C9 < -100) * 1 + (R < 50) * 1 + (d.close < sup) * 1 + crossunder(K14, D14) * 0.5 + (d.close < e21) * 1 + (strong & (d.close < d.open)) * 1
            s2L = s2L + (M > 55) * 1 + (A20 > A20.shift(1)) * 1 + (e9 > e21) * 1 + ((C9 > C9.shift(1)) & (C9 > 100)) * 1 + ((K14 > K14.shift(1)) & (K14 > 50)) * 1
            s2S = s2S + (M < 45) * 1 + (A20 > A20.shift(1)) * 1 + (e9 < e21) * 1 + ((C9 < C9.shift(1)) & (C9 < -100)) * 1 + ((K14 < K14.shift(1)) & (K14 < 50)) * 1
            s3L = (z < -1.5) * 1 + (ao > 0) * 1 + (sq > 0) * 1 + (R > rav) * 1 + (e9 > e21) * 1 + (hist12 > 0) * 1 + (stdt == 1) * 1
            s3S = (z > 1.5) * 1 + (ao < 0) * 1 + (sq < 0) * 1 + (R < rav) * 1 + (e9 < e21) * 1 + (hist12 < 0) * 1 + (stdt == -1) * 1
            vma = sma(d.volume, 20)
            vsp = d.volume > vma * vm
            mrL = (M > 60) & (M > M.shift(1)) & vsp
            mrS = (M < 40) & (M < M.shift(1)) & vsp
            s3L = s3L + pcL * 1 + mrL * 1 + (R > 50) * 1 + vsp * 1
            s3S = s3S + pcS * 1 + mrS * 1 + (R < 50) * 1 + vsp * 1
            return edge_event(t1L & (s2L >= t2) & (s3L >= t3)), edge_event(t1S & (s2S >= t2) & (s3S >= t3))

    # 15m combos (7, 9, 10, or 5, 8)
    d = d15
    A = p15["A"]
    R = p15["R"]
    M = p15["M"]
    ADX = p15["ADX"]
    volSMA = p15["volSMA"]
    body_ratio = p15["body_ratio"]
    valid = body_ratio > 0.75

    if cid == 7:
        bm = float(ent.get("body_atr", 1.6))
        vm = float(ent.get("volume_mult", 2.0))
        ef = ema(d.close, 21)
        es = ema(d.close, 55)
        adxr = ADX > ADX.shift(1)
        volume_surge = d.volume > d.volume.shift(1) * 1.8
        tu = d.close > p15["trend100"]
        td = d.close < p15["trend100"]
        strong_b = (d.close > d.open) & ((d.close - d.open) > A * bm)
        strong_s = (d.close < d.open) & ((d.open - d.close) > A * bm)
        volsp = d.volume > volSMA * vm
        L = volume_surge & (ef > es) & tu & adxr & (ADX > 25) & strong_b & volsp & valid & (R > 60) & (R < 85)
        S = volume_surge & (ef < es) & td & adxr & (ADX > 25) & strong_s & volsp & valid & (R < 40) & (R > 15)
        return edge_event(L), edge_event(S)

    if cid == 9:
        r_long = float(ent.get("rsi_long_max", 45.0))
        r_short = float(ent.get("rsi_short_min", 100.0 - r_long))
        fix_st = bool(ent.get("fix_short_supertrend", True))
        ema200 = p15["ema200"]
        stdir15 = p15["stdir"]
        mid = _nadaraya(d.close, 24, 3.0)
        nr = d.close.rolling(24, min_periods=24).std(ddof=0) * 2.3
        upper = mid + nr
        lower = mid - nr
        L = (d.close > ema200) & (R < r_long) & (d.close < lower) & (stdir15 == 1) & (body_ratio > 0.4)
        st_cond = (stdir15 == -1) if fix_st else (stdir15 == 1)
        S = (d.close < ema200) & (R > r_short) & (d.close > upper) & st_cond & (body_ratio > 0.4)
        return edge_event(L), edge_event(S)

    if cid == 10:
        adxth = float(ent.get("adx", 22.0))
        ao = sma((d.high + d.low) / 2, 5) - sma((d.high + d.low) / 2, 34)
        squeeze = ema(d.close, 20) - ema(d.close, 50)
        mean = sma(d.close, 20)
        sd = d.close.rolling(20, min_periods=20).std(ddof=0)
        z = (d.close - mean) / sd
        tu4 = d.close > p15["trend100"]
        td4 = d.close < p15["trend100"]
        upper_w = d.high - pd.concat([d.close, d.open], axis=1).max(axis=1)
        lower_w = pd.concat([d.close, d.open], axis=1).min(axis=1) - d.low
        body = (d.close - d.open).abs()
        bull_pin = (lower_w > body * 1.5) & (d.close > d.open)
        bear_pin = (upper_w > body * 1.5) & (d.close < d.open)
        L = (ao > 0) & (squeeze > 0) & (z < -1.5) & tu4 & (ADX > adxth) & bull_pin
        S = (ao < 0) & (squeeze < 0) & (z > 1.5) & td4 & (ADX > adxth) & bear_pin
        return edge_event(L), edge_event(S)

    raise KeyError(f"Unsupported custom FINAL14 combo {combo.combo_key}")


def _custom_new6_raw_signals(d1: pd.DataFrame, combo: ComboSpec) -> tuple[pd.Series, pd.Series]:
    """Evaluate NEW6 raw signals when a clone modifies entry parameters."""
    if _is_default_entry(combo):
        return _new6_raw_signals(d1, combo.combo_id)

    h = d1.high.to_numpy(float)
    l = d1.low.to_numpy(float)
    c = d1.close.to_numpy(float)
    cid = int(combo.combo_id)
    ent = combo.entry

    if cid == 101:
        n = int(ent.get("period", 60))
        long_min = float(ent.get("long_min", 0.75))
        short_max = float(ent.get("short_max", 0.25))
        z = _demarker(h, l, n)
        return _clean_bool(z > long_min, d1.index), _clean_bool(z < short_max, d1.index)
    if cid == 102:
        period = int(ent.get("period", 40))
        th = float(ent.get("threshold", 70.0))
        up, down = _aroon(h, l, period)
        return (
            _clean_bool(_cross_up(up, down) & (up >= th), d1.index),
            _clean_bool(_cross_up(down, up) & (down >= th), d1.index),
        )
    if cid == 103:
        length = int(ent.get("period", 40))
        th = float(ent.get("threshold", 40.0))
        z = _cmo(c, length)
        long = (z > th) & np.r_[False, z[:-1] <= th]
        short = (z < -th) & np.r_[False, z[:-1] >= -th]
        return _clean_bool(long, d1.index), _clean_bool(short, d1.index)
    if cid == 104:
        conv = int(ent.get("conv", 12))
        base = int(ent.get("base", 30))
        spanb = int(ent.get("spanb", 60))
        tenkan, kijun, span_a, span_b = _ichimoku(h, l, conv, base, spanb)
        top = np.maximum(span_a, span_b)
        bottom = np.minimum(span_a, span_b)
        return (
            _clean_bool(_cross_up(tenkan, kijun) & (c > top) & (span_a > span_b), d1.index),
            _clean_bool(_cross_down(tenkan, kijun) & (c < bottom) & (span_a < span_b), d1.index),
        )
    if cid == 105:
        er_len = int(ent.get("er_len", 40))
        fast_len = int(ent.get("fast_len", 2))
        slow_len = int(ent.get("slow_len", 40))
        er_min = float(ent.get("er_min", 0.4))
        kama, er = _kama_er(c, er_len, fast_len, slow_len)
        slope = np.r_[np.nan, np.diff(kama)]
        return (
            _clean_bool((c > kama) & (slope > 0) & (er > er_min), d1.index),
            _clean_bool((c < kama) & (slope < 0) & (er > er_min), d1.index),
        )
    if cid == 106:
        length = int(ent.get("period", 14))
        ratio = float(ent.get("ratio", 1.2))
        vi_plus, vi_minus = _vortex(h, l, c, length)
        return (
            _clean_bool(_cross_up(vi_plus, vi_minus) & (vi_plus > vi_minus * ratio), d1.index),
            _clean_bool(_cross_up(vi_minus, vi_plus) & (vi_minus > vi_plus * ratio), d1.index),
        )
    raise KeyError(f"Unknown NEW6 combo {cid}")


def _make_signal_event(
    sym: str,
    combo: ComboSpec,
    side_code: str,
    ot: pd.Timestamp,
    pos: int,
    sd: int,
    blocked: bool,
    ok: bool,
    d: pd.DataFrame,
    warmup_bars: int,
) -> SignalEvent:
    native = combo.native_timeframe
    last_open = ot + (pd.Timedelta(minutes=45) if native == "1h" else pd.Timedelta(0))
    if d.index[pos] != last_open:
        raise AssertionError("Cannot publish signal before native candle close")
    close_ms = int(d.index[pos].value // 1_000_000 + M15_MS - 1)
    native_open_ms = int(ot.value // 1_000_000)
    bar_open_ms = int(d.index[pos].value // 1_000_000)
    side_str = "BUY" if side_code == "L" else "SELL"
    sym_clean = sym.replace("-", "").replace("_", "")
    signal_id = f"SIG_{sym_clean}_{combo.combo_name}_{native}_{side_str}_{close_ms}"
    ref_price = float(d.iloc[pos]["close"])
    provider = str(d.iloc[pos].get("provider", "UNKNOWN"))

    return SignalEvent(
        signal_id=signal_id,
        combo_key=combo.combo_key,
        symbol=sym,
        side=side_str,
        native_open_ms=native_open_ms,
        signal_close_ms=close_ms,
        ref_price=ref_price,
        tags={
            "smc_dir": int(sd),
            "ob_blocked": bool(blocked),
            "approved": bool(ok),
            "warmup_ready": bool(pos >= warmup_bars - 1),
            "combo_id": int(combo.combo_id),
            "combo_name": combo.combo_name,
            "timeframe": native,
            "bar_position": int(pos),
            "bar_open_ms": bar_open_ms,
            "provider": provider,
            "is_new6": bool(combo.is_new6),
        },
    )


class Final20Strategy(Strategy):
    """Independent 20FINAL Strategy Core implementation."""

    def __init__(
        self,
        params_data: dict[str, Any] | None = None,
        params_path: Path | None = None,
        lock_path: Path | None = None,
        core_root: Path | None = None,
        enforce_lock: bool = True,
    ):
        self._params_path = Path(params_path) if params_path is not None else DEFAULT_PARAMS_PATH
        self._lock_path = Path(lock_path) if lock_path is not None else DEFAULT_LOCK_PATH
        self._core_root = Path(core_root) if core_root is not None else FINAL20_DIR.parents[1]

        if params_data is None:
            raw_text = normalize_lf(self._params_path.read_text(encoding="utf-8"))
            params_data = json.loads(raw_text)
        self._params_data = params_data

        strategy_id = str(params_data.get("strategy_id", "20FINAL"))
        version = str(params_data.get("version", "1"))
        warmup_bars = int(params_data.get("warmup_bars", MIN_LIVE_15M_BARS))
        timeframes = tuple(str(tf) for tf in params_data.get("timeframes", ["15m", "1h"]))
        parent = params_data.get("parent")

        hashes = compute_all_hashes(params_data, core_root=self._core_root)

        # Enforce baseline immutability lock when loading 20FINAL version 1
        if enforce_lock and strategy_id in ("20FINAL", "20FINAL_BASELINE") and version in ("1", "1.0.0"):
            if self._lock_path.exists():
                lock_data = json.loads(normalize_lf(self._lock_path.read_text(encoding="utf-8")))
                for h_key in ("signal_spec_hash", "exit_spec_hash", "spec_hash"):
                    expected_h = lock_data.get(h_key)
                    actual_h = hashes.get(h_key)
                    if expected_h != actual_h:
                        raise BaselineLockError(
                            f"Baseline 20FINAL@1 lock mismatch on {h_key}: "
                            f"expected={expected_h}, actual={actual_h}"
                        )

        combos = tuple(ComboSpec.from_dict(c) for c in params_data.get("combos", []))
        self._spec = StrategySpec(
            strategy_id=strategy_id,
            version=version,
            combos=combos,
            timeframes=timeframes,
            warmup_bars=warmup_bars,
            signal_spec_hash=hashes["signal_spec_hash"],
            exit_spec_hash=hashes["exit_spec_hash"],
            spec_hash=hashes["spec_hash"],
            parent=str(parent) if parent is not None else None,
        )

    def spec(self) -> StrategySpec:
        return self._spec

    def _generate_symbol(
        self,
        sym: str,
        d_in: pd.DataFrame,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[SignalEvent]:
        if not isinstance(d_in.index, pd.DatetimeIndex) or d_in.index.tz is None:
            raise ValueError("Need timestamp-indexed UTC M15 candles")
        d = d_in.tz_convert("UTC")
        t = d.index.asi8 // 1_000_000
        warmup_bars = self._spec.warmup_bars
        if (
            len(d) < warmup_bars
            or d.index.has_duplicates
            or not d.index.is_monotonic_increasing
            or (t % M15_MS).any()
            or (t[1:] - t[:-1] != M15_MS).any()
        ):
            raise ValueError("M15 gapped, misaligned or insufficient live warmup")

        sym_combos = [c for c in self._spec.combos if c.symbol.upper() == sym.upper()]
        if not sym_combos:
            return []

        final14_combos = sorted([c for c in sym_combos if not c.is_new6], key=lambda c: c.combo_id)
        new6_combos = sorted([c for c in sym_combos if c.is_new6], key=lambda c: c.combo_id)

        wanted: dict[str, set[str]] = {}
        for c in final14_combos:
            wanted.setdefault(c.combo_name, set()).add(c.entry_variant)

        pc = precompute(d, wanted if wanted else None)
        selected = entry_variants(pc, wanted) if wanted else {}
        d1 = pc["d1"]
        smc15 = smc_direction(d, 50, False)
        smc1 = smc_direction(d1, 50, False)
        obcfg = OBConfig(pivot_len=5, search_bars=12, max_age=80, danger_atr=0.5)
        ob15 = ob_context(d, obcfg)
        ob1 = ob_context(d1, obcfg)
        posmap = {ts: i for i, ts in enumerate(d.index)}

        events: list[SignalEvent] = []

        for c in final14_combos:
            native = c.native_timeframe
            if _is_default_entry(c):
                variants = [x for x in selected.get(c.combo_name, []) if x[0] == c.entry_variant]
                if len(variants) == 1:
                    _, L, S = variants[0]
                else:
                    L, S = _custom_final14_signals(pc, c)
            else:
                L, S = _custom_final14_signals(pc, c)

            rows = []
            for side_code, series in (("L", L), ("S", S)):
                for ot in series.index[series.fillna(False)]:
                    last = ot + (pd.Timedelta(minutes=45) if native == "1h" else pd.Timedelta(0))
                    if last not in posmap:
                        continue
                    sd = int((smc1 if native == "1h" else smc15).loc[ot])
                    ob = (ob1 if native == "1h" else ob15).loc[ot]
                    blocked = bool(ob["buy_blocked" if side_code == "L" else "sell_blocked"])
                    ok = bool(approved(side_code, sd, blocked, c.layer2))
                    rows.append((posmap[last], side_code, ot, sd, blocked, ok))
            rows.sort(key=lambda x: x[0])
            for p, side_code, ot, sd, blocked, ok in rows:
                ev = _make_signal_event(sym, c, side_code, ot, p, sd, blocked, ok, d, warmup_bars)
                if only_approved and not ev.tags["approved"]:
                    continue
                if only_warmup_ready and not ev.tags["warmup_ready"]:
                    continue
                events.append(ev)

        for c in new6_combos:
            L, S = _custom_new6_raw_signals(d1, c)
            for ot in d1.index:
                lng, shrt = bool(L.loc[ot]), bool(S.loc[ot])
                if lng == shrt:
                    continue
                last = ot + pd.Timedelta(minutes=45)
                if last not in posmap:
                    continue
                side_code = "L" if lng else "S"
                sd = int(smc1.loc[ot])
                blocked = bool(ob1.loc[ot, "buy_blocked" if lng else "sell_blocked"])
                ok = bool(approved(side_code, sd, blocked, c.layer2))
                ev = _make_signal_event(sym, c, side_code, ot, posmap[last], sd, blocked, ok, d, warmup_bars)
                if only_approved and not ev.tags["approved"]:
                    continue
                if only_warmup_ready and not ev.tags["warmup_ready"]:
                    continue
                events.append(ev)

        events.sort(key=lambda e: (e.signal_close_ms, int(e.tags["combo_id"]), e.side))
        return events

    def generate(
        self,
        bars: dict[str, pd.DataFrame] | pd.DataFrame,
        symbols: list[str] | None = None,
        only_approved: bool = True,
        only_warmup_ready: bool = True,
    ) -> list[SignalEvent]:
        """Batch signal generation across provided closed M15 candle frames."""
        if isinstance(bars, pd.DataFrame):
            sym = symbols[0] if symbols else "BTC-USDT"
            return self._generate_symbol(sym, bars, only_approved=only_approved, only_warmup_ready=only_warmup_ready)

        target_symbols = symbols or list(bars.keys())
        all_events: list[SignalEvent] = []
        for sym in target_symbols:
            if sym not in bars:
                continue
            sym_canonical = "BTC-USDT" if "BTC" in sym.upper() else ("ETH-USDT" if "ETH" in sym.upper() else sym)
            evs = self._generate_symbol(
                sym_canonical,
                bars[sym],
                only_approved=only_approved,
                only_warmup_ready=only_warmup_ready,
            )
            if sym != sym_canonical:
                evs = [
                    SignalEvent(
                        signal_id=e.signal_id,
                        combo_key=e.combo_key,
                        symbol=sym,
                        side=e.side,
                        native_open_ms=e.native_open_ms,
                        signal_close_ms=e.signal_close_ms,
                        ref_price=e.ref_price,
                        tags=e.tags,
                    )
                    for e in evs
                ]
            all_events.extend(evs)

        all_events.sort(key=lambda e: (e.signal_close_ms, int(e.tags["combo_id"]), e.side))
        return all_events

    def on_bar_close(
        self,
        state: Any,
        bar: Any,
    ) -> list[SignalEvent]:
        """Placeholder until Commit (4) adds incremental on_bar_close."""
        raise NotImplementedError("on_bar_close implemented in Commit (4)")
