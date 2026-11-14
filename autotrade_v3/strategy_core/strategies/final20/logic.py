from __future__ import annotations

import numpy as np
import pandas as pd

from ...indicators.layer3_long import precompute, entry_variants, NATIVE
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
