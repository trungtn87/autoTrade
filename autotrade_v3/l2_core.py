"""L2 strategy core: the one implementation used by live and by the lab.

Single module on purpose: indicator, signal and NEW6 functions are copied
verbatim from the final14 research code. Only definitions reachable from the
live L2 path are kept; research harness code (CLI, pickles, simulators) is not
part of this module. Change logic here only with a parity run.
"""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import numpy as np
import pandas as pd

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    x = s.astype(float).to_numpy()
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return pd.Series(out, index=s.index)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        if np.isfinite(w).all():
            out[i] = w.mean()
            start = i + 1
            break
    else:
        return pd.Series(out, index=s.index)
    for i in range(start, len(x)):
        if np.isfinite(x[i]) and np.isfinite(out[i - 1]):
            out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return pd.Series(out, index=s.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df['close'].shift(1)
    return pd.concat([df['high'] - df['low'],(df['high'] - prev_close).abs(),(df['low'] - prev_close).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return rma(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    au = rma(up, n); ad = rma(dn, n)
    rs = au / ad.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(ad != 0, 100.0)
    out = out.where(au != 0, 0.0)
    return out


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    mf = tp * df['volume']; d = tp.diff()
    pos = mf.where(d > 0, 0.0); neg = mf.where(d < 0, 0.0)
    ps = pos.rolling(n, min_periods=n).sum(); ns = neg.rolling(n, min_periods=n).sum()
    ratio = ps / ns.replace(0, np.nan)
    out = 100 - 100 / (1 + ratio)
    out = out.where(ns != 0, 100.0)
    return out


def cci(close_or_source: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    src = close_or_source; ma = sma(src, n)
    md = src.rolling(n, min_periods=n).apply(lambda a: np.mean(np.abs(a - np.mean(a))), raw=True)
    return (src - ma) / (0.015 * md.replace(0, np.nan))


def stochastic(close: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    lo = low.rolling(n, min_periods=n).min(); hi = high.rolling(n, min_periods=n).max()
    return 100 * (close - lo) / (hi - lo).replace(0, np.nan)


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def dmi(df: pd.DataFrame, di_len: int = 14, adx_len: int = 14):
    up = df['high'].diff(); down = -df['low'].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr_rma = rma(true_range(df), di_len)
    plus = 100 * rma(plus_dm, di_len) / tr_rma.replace(0, np.nan)
    minus = 100 * rma(minus_dm, di_len) / tr_rma.replace(0, np.nan)
    dx = 100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan)
    adx = rma(dx, adx_len)
    return plus, minus, adx


def parabolic_sar(df: pd.DataFrame, start: float = 0.02, inc: float = 0.02, max_af: float = 0.2) -> pd.Series:
    h = df['high'].to_numpy(float); l = df['low'].to_numpy(float); c = df['close'].to_numpy(float)
    n = len(df); out = np.full(n, np.nan)
    if n < 2: return pd.Series(out, index=df.index)
    bull = c[1] >= c[0]; af = start; ep = h[0] if bull else l[0]; sar = l[0] if bull else h[0]; out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, l[i-1], l[i-2]) if i >= 2 else min(sar, l[i-1])
            if l[i] < sar: bull = False; sar = ep; ep = l[i]; af = start
            elif h[i] > ep: ep = h[i]; af = min(max_af, af + inc)
        else:
            sar = max(sar, h[i-1], h[i-2]) if i >= 2 else max(sar, h[i-1])
            if h[i] > sar: bull = True; sar = ep; ep = h[i]; af = start
            elif l[i] < ep: ep = l[i]; af = min(max_af, af + inc)
        out[i] = sar
    return pd.Series(out, index=df.index)


def obv_variant(df: pd.DataFrame) -> pd.Series:
    return (df['close'].diff() * df['volume']).cumsum()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError('DataFrame index must be DatetimeIndex')
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    pv = tp * df['volume']; day = df.index.floor('D')
    return pv.groupby(day).cumsum() / df['volume'].groupby(day).cumsum().replace(0, np.nan)


def edge_event(cond: pd.Series) -> pd.Series:
    current = cond.astype("boolean").fillna(False)
    previous = current.shift(1, fill_value=False)
    return (current & ~previous).astype(bool)


def pine_custom_supertrend_dir(df: pd.DataFrame, atr_n: int, factor: float) -> pd.Series:
    a = atr(df, atr_n); hl2 = (df['high'] + df['low']) / 2
    upper = hl2 + factor * a; lower = hl2 - factor * a
    trend = np.full(len(df), np.nan)
    close = df['close'].to_numpy(float); up = upper.to_numpy(float); lo = lower.to_numpy(float); mid = hl2.to_numpy(float)
    for i in range(len(df)):
        if i == 0 or not np.isfinite(trend[i-1]):
            trend[i] = mid[i]; continue
        if close[i-1] > trend[i-1]:
            trend[i] = max(lo[i], trend[i-1]) if np.isfinite(lo[i]) else trend[i-1]
        else:
            trend[i] = min(up[i], trend[i-1]) if np.isfinite(up[i]) else trend[i-1]
    t = pd.Series(trend, index=df.index)
    return pd.Series(np.where(df['close'] > t, 1, -1), index=df.index)


def pine_combo15_supertrend_dir(df: pd.DataFrame) -> pd.Series:
    a = atr(df, 14); base = sma(df['close'], 8)
    upper = base + 2*a; lower = base - 2*a
    out = np.ones(len(df), dtype=int)
    c = df['close'].to_numpy(float); u=upper.to_numpy(float); l=lower.to_numpy(float)
    for i in range(len(df)):
        prev = out[i-1] if i else 1
        out[i] = 1 if (np.isfinite(l[i]) and c[i] > l[i]) else (-1 if (np.isfinite(u[i]) and c[i] < u[i]) else prev)
    return pd.Series(out, index=df.index)


def ensure_dt(df: pd.DataFrame) -> pd.DataFrame:
    x=df.copy()
    if not isinstance(x.index,pd.DatetimeIndex):
        if 'open_time' in x: x.index=pd.to_datetime(x['open_time'],unit='ms',utc=True)
        else: raise ValueError('OHLCV needs DatetimeIndex or open_time')
    if x.index.tz is None: x.index=x.index.tz_localize('UTC')
    else: x.index=x.index.tz_convert('UTC')
    return x.sort_index()


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x=ensure_dt(df)
    out=x[['open','high','low','close','volume']].resample(rule,label='left',closed='left').agg({
        'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    return out.dropna(subset=['open','high','low','close'])


def align_confirmed(series: pd.Series, target_index: pd.DatetimeIndex, tf: str) -> pd.Series:
    delta=pd.Timedelta(tf)
    s=series.copy()
    src_idx=(pd.DatetimeIndex(s.index)+delta).as_unit('ns')
    tgt_idx=pd.DatetimeIndex(target_index).as_unit('ns')
    # Normalize BOTH indexes to nanoseconds before integer merge. Pandas 3 may
    # preserve ms/us resolution in .asi8, so raw .asi8 values can differ by
    # powers of 1000 even for the same timestamp.
    left=pd.DataFrame({'t':tgt_idx.asi8}).sort_values('t')
    right=pd.DataFrame({'t':src_idx.asi8,'v':s.values}).sort_values('t')
    out=pd.merge_asof(left,right,on='t',direction='backward')['v'].to_numpy()
    return pd.Series(out,index=target_index)


def _nadaraya(close: pd.Series, length=24, smooth=3.0) -> pd.Series:
    denom=0.0; num=pd.Series(0.0,index=close.index)
    for i in range(length+1):
        w=np.exp(-((i/length*smooth)**2))
        num=num+close.shift(i)*w; denom+=w
    return num/denom


NATIVE={"C1":"1h","C2":"1h","C3":"1h","C4":"1h","C5":"15m","C6":"1h","C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h"}


def precompute(d15, selected_names=None):
    d15=ensure_dt(d15); d1=resample_ohlcv(d15,"1h"); d4=resample_ohlcv(d15,"4h"); d6=resample_ohlcv(d15,"6h")
    pc={"15":{},"1":{},"d15":d15,"d1":d1,"d4":d4,"d6":d6}
    # 15m shared
    d=d15; h4=d4
    A=atr(d,14); R=rsi(d.close,14); M=mfi(d,14); dip,dim,ADX=dmi(d,14,14)
    volSMA=sma(d.volume,20); body=(d.close-d.open).abs(); rng=d.high-d.low; body_ratio=body/rng.replace(0,np.nan)
    trend100=align_confirmed(ema(h4.close,100),d.index,"4h")
    pc["15"].update(A=A,R=R,M=M,ADX=ADX,volSMA=volSMA,body_ratio=body_ratio,trend100=trend100)

    want_c5 = selected_names is None or "C5" in selected_names
    want_c8 = selected_names is None or "C8" in selected_names
    want_c9 = selected_names is None or "C9" in selected_names
    if want_c5:
        pc["15"]["ema150"]=ema(d.close,150)
    if want_c5 or want_c9:
        pc["15"]["ema200"]=ema(d.close,200)
        pc["15"]["stdir"]=pine_combo15_supertrend_dir(d)
    if want_c5 or want_c8:
        ema50_4h=align_confirmed(ema(h4.close,50),d.index,"4h")
        pc["15"]["trend50"]=d.close>ema50_4h
        pc["15"]["trend50s"]=d.close<ema50_4h

    # 1h shared
    d=d1; h4=d4; h6=d6
    A21h=atr(d,21); R1=rsi(d.close,14); M1=mfi(d,14); dip1,dim1,ADX1=dmi(d,14,14)
    volma1=sma(d.volume,20); K=stochastic(d.close,d.high,d.low,11); D=sma(K,3); C=cci(d.close,d.high,d.low,9)
    ema1504=align_confirmed(ema(h4.close,150),d.index,"4h")
    pc["1"].update(A21=A21h,R=R1,M=M1,ADX=ADX1,dip=dip1,dim=dim1,volma=volma1,K=K,D=D,C=C,ema1504=ema1504)
    if selected_names is None or "C2" in selected_names:
        pc["1"]["A14"]=atr(d,14)
    return pc


def _want_variant(selected_names, combo, name):
    if selected_names is None:
        return True
    return name in selected_names.get(combo, set())


def _want_combo(selected_names, combo):
    return selected_names is None or combo in selected_names


def entry_variants(pc, selected_names=None):
    """Build research variants, optionally only the exact locked production names.

    selected_names is a mapping like {"C1": {"MFI_L50_S50"}, "TIER": {...}}.
    Passing None preserves the original research/backtest behavior exactly.
    """
    d15=pc["d15"]; d1=pc["d1"]; d4=pc["d4"]; d6=pc["d6"]; p15=pc["15"]; p1=pc["1"]
    out={}

    # ----- 1H base components -----
    d=d1; A21=p1["A21"]; R=p1["R"]; M=p1["M"]; ADX=p1["ADX"]; K=p1["K"]; D=p1["D"]; C=p1["C"]
    macd=ema(d.close,8)-ema(d.close,21); sig=ema(macd,9); hist=macd-sig
    volma=p1["volma"]; volsp=d.volume>volma*1.5; adxr=ADX>ADX.shift(1)
    sb08=(d.close>d.open)&((d.close-d.open)>A21*0.8); ss08=(d.close<d.open)&((d.open-d.close)>A21*0.8)
    tu=d.close>p1["ema1504"]; td=d.close<p1["ema1504"]
    longc=(K>30)&(K>D)&(C>80)&(C>C.shift(1)); shortc=(K<70)&(K<D)&(C>-80)&(C<C.shift(1))
    # ATR trailing stop/Bollinger components are C2-only.
    if _want_combo(selected_names,"C2"):
        e10=ema(d.close,10); e25=ema(d.close,25)
        basis=sma(d.close,20); dev=1.8*d.close.rolling(20,min_periods=20).std(ddof=0); ub=basis+dev; lb=basis-dev
        bbup=d.close>ub; bbdn=d.close<lb
        trstop=1.5*p1["A14"]; x=d.close.to_numpy(float); ts=trstop.to_numpy(float); arr=np.full(len(d),np.nan)
        for i in range(len(d)):
            prev=arr[i-1] if i else np.nan; nz=0 if not np.isfinite(prev) else prev; prevsrc=x[i-1] if i else np.nan
            if i and x[i]>nz and prevsrc>nz: arr[i]=max(nz,x[i]-ts[i])
            elif i and x[i]<nz and prevsrc<nz: arr[i]=min(nz,x[i]+ts[i])
            else: arr[i]=x[i]-ts[i] if x[i]>nz else x[i]+ts[i]
        ats=pd.Series(arr,index=d.index)
        above=crossover(ema(d.close,1),ats); below=crossover(ats,ema(d.close,1))

    stdir1=pine_custom_supertrend_dir(d,8,4.0)
    if _want_combo(selected_names,"C3"):
        sar=parabolic_sar(d,0.05,0.2,0.3)
        bull_eng=(d.close>d.open)&(d.close.shift(1)<d.open.shift(1))&(d.close>d.open.shift(1))&(d.open<d.close.shift(1))
        bear_eng=(d.close<d.open)&(d.close.shift(1)>d.open.shift(1))&(d.close<d.open.shift(1))&(d.open>d.close.shift(1))
        O=obv_variant(d); oup=O>O.shift(1); odn=O<O.shift(1)
    e50_6=align_confirmed(ema(d6.close,50),d.index,"6h"); tfast=ema(d.close,21); tslow=ema(d.close,42); bt=tfast>tslow; br=tfast<tslow
    std20=d.close.rolling(20,min_periods=20).std(ddof=0); bbu=sma(d.close,20)+2*std20; bbl=sma(d.close,20)-2*std20; ku=sma(d.close,20)+1.7*atr(d,20); kl=sma(d.close,20)-1.7*atr(d,20)
    breakout_b=crossover(d.close,bbu)&(d.close>ku); breakout_s=crossunder(d.close,bbl)&(d.close<kl)

    # C1: preserve the asymmetric original BASE exactly:
    # Long MFI > 65, Short MFI < 45.
    # Include the short-window 50/55 seed plus a small neighborhood.
    c1_pairs=[
        (65,45,"BASE"),
        (60,45,"L60_S45"),
        (60,50,"L60_S50"),
        (55,45,"L55_S45"),
        (55,50,"L55_S50"),
        (55,55,"L55_S55"),
        (50,50,"L50_S50"),
        (50,55,"L50_S55_SEED"),
    ]
    for lth,sth,label in c1_pairs:
        name=f"MFI_{label}"
        if not _want_variant(selected_names,"C1",name): continue
        L=longc&(R>55)&(hist>0)&(M>lth)&adxr&sb08&tu&volsp
        S=shortc&(R<45)&(hist<0)&(M<sth)&adxr&ss08&td&volsp
        out.setdefault("C1",[]).append((name,edge_event(L),edge_event(S)))

    # C2: base ADX20 and nearby; no historical entry candidate => narrow local test
    for adxth in [18,20,22,25,28]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C2",name): continue
        L=(d.close>ats)&above&(e10>e25)&(macd>sig)&(ADX>adxth)&bbup
        S=(d.close<ats)&below&(e10<e25)&(macd<sig)&(ADX>adxth)&bbdn
        out.setdefault("C2",[]).append((name,edge_event(L),edge_event(S)))

    # C3: ADX around base 30
    for adxth in [26,28,30,32,34]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C3",name): continue
        L=(stdir1==1)&(ADX>adxth)&(d.close>sar)&bull_eng&oup
        S=(stdir1==-1)&(ADX>adxth)&(d.close<sar)&bear_eng&odn
        out.setdefault("C3",[]).append((name,edge_event(L),edge_event(S)))

    # C4: prior short-data candidate ADX 26/30; include base 23 + neighbors
    for adxth in [23,26,28,30,32]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C4",name): continue
        L=bt&breakout_b&(d.close>e50_6)&(stdir1==1)&sb08&volsp&(ADX>adxth)
        S=br&breakout_s&(d.close<e50_6)&(stdir1==-1)&ss08&volsp&(ADX>adxth)
        out.setdefault("C4",[]).append((name,edge_event(L),edge_event(S)))

    # C6: prior candidate body >1.6 ATR vs base 1.2
    lc6=(R>30)&(K>30)&(K>D)&(C>100)&(C>C.shift(1)); sc6=(R<70)&(K<70)&(K<D)&(C>-100)&(C<C.shift(1)); adxs=(ADX>20)&adxr
    for bm in [1.0,1.2,1.4,1.6,1.8]:
        name=f"BODY_{bm:.1f}"
        if not _want_variant(selected_names,"C6",name): continue
        sb=(d.close>d.open)&((d.close-d.open)>A21*bm); ss=(d.close<d.open)&((d.open-d.close)>A21*bm)
        out.setdefault("C6",[]).append((name,edge_event(sb&breakout_b&lc6&adxs),edge_event(ss&breakout_s&sc6&adxs)))

    # ----- 15m components -----
    d=d15; A=p15["A"]; R=p15["R"]; M=p15["M"]; ADX=p15["ADX"]; volSMA=p15["volSMA"]; body_ratio=p15["body_ratio"]; valid=body_ratio>0.75
    ef=ema(d.close,21); es=ema(d.close,55)
    if _want_combo(selected_names,"C5"):
        ema150=p15["ema150"]; ema200=p15["ema200"]; stdir15=p15["stdir"]
        sar15=parabolic_sar(d,0.05,0.1,0.2)
    # C5 tune volume multiplier around prior 2.0 candidate
    for vm in [1.3,1.5,1.8,2.0,2.2]:
        name=f"VOL_{vm:.1f}"
        if not _want_variant(selected_names,"C5",name): continue
        volSpike=d.volume>volSMA*vm; adx_strong=(ADX>23)&(ADX>ADX.shift(1))
        L=(stdir15==1)&(ema150>ema200)&(ema150>ema150.shift(1))&volSpike&(R>45)&valid&adx_strong&(M>55)&(d.close>sar15)&p15["trend50"]
        S=(stdir15==-1)&volSpike&(ema150<ema200)&(ema150<ema150.shift(1))&(R<55)&valid&adx_strong&(M<55)&(d.close<sar15)&p15["trend50s"]
        out.setdefault("C5",[]).append((name,edge_event(L),edge_event(S)))

    # C7 tune strong candle ATR and volSpike SMA multiplier; keep volume_surge prev*1.8
    adxr=ADX>ADX.shift(1); volume_surge=d.volume>d.volume.shift(1)*1.8; tu=d.close>p15["trend100"]; td=d.close<p15["trend100"]
    for bm,vm in itertools.product([1.3,1.5,1.6,1.8],[1.3,1.5,1.8,2.0]):
        name=f"BODY{bm:.1f}_VOL{vm:.1f}"
        if not _want_variant(selected_names,"C7",name): continue
        strong_b=(d.close>d.open)&((d.close-d.open)>A*bm); strong_s=(d.close<d.open)&((d.open-d.close)>A*bm)
        volsp=d.volume>volSMA*vm
        L=volume_surge&(ef>es)&tu&adxr&(ADX>25)&strong_b&volsp&valid&(R>60)&(R<85)
        S=volume_surge&(ef<es)&td&adxr&(ADX>25)&strong_s&volsp&valid&(R<40)&(R>15)
        out.setdefault("C7",[]).append((name,edge_event(L),edge_event(S)))

    # C8 prior candidate ADX30 vs base25
    if _want_combo(selected_names,"C8"):
        C8=cci(d.close,d.high,d.low,10); longc8=(C8>100)&(C8>C8.shift(1))&(d.volume>volSMA*0.8)&(d.volume>d.volume.shift(1)); shortc8=(C8<-100)&(C8<C8.shift(1))&(d.volume>volSMA*0.8)&(d.volume>d.volume.shift(1))
        macd8=ema(d.close,12)-ema(d.close,26); sig8=ema(macd8,9); hist8=macd8-sig8; bull=(d.close>d.open)&(d.close>d.open.shift(1))&(d.open<d.close.shift(1)); bear=(d.close<d.open)&(d.close<d.open.shift(1))&(d.open>d.close.shift(1))
        for adxth in [23,25,28,30,32,35]:
            name=f"ADX_{adxth}"
            if not _want_variant(selected_names,"C8",name): continue
            L=longc8&(M>70)&(ADX>adxth)&(ADX>ADX.shift(1))&bull&p15["trend50"]&(hist8>0)&valid
            S=shortc8&(M<30)&(ADX>adxth)&(ADX>ADX.shift(1))&bear&p15["trend50s"]&(hist8<0)&valid
            out.setdefault("C8",[]).append((name,edge_event(L),edge_event(S)))

    # C9 original plus corrected-short-ST branch and RSI neighborhoods
    if _want_combo(selected_names,"C9"):
        ema200=p15["ema200"]; stdir15=p15["stdir"]
        mid=_nadaraya(d.close,24,3.0); nr=d.close.rolling(24,min_periods=24).std(ddof=0)*2.3; upper=mid+nr; lower=mid-nr
        for rth in [35,40,45]:
            name0=f"RSI{rth}_ORIGST"; name1=f"RSI{rth}_FIXST"
            if not (_want_variant(selected_names,"C9",name0) or _want_variant(selected_names,"C9",name1)): continue
            L=(d.close>ema200)&(R<rth)&(d.close<lower)&(stdir15==1)&(body_ratio>0.4)
            S0=(d.close<ema200)&(R>(100-rth))&(d.close>upper)&(stdir15==1)&(body_ratio>0.4)
            S1=(d.close<ema200)&(R>(100-rth))&(d.close>upper)&(stdir15==-1)&(body_ratio>0.4)
            if _want_variant(selected_names,"C9",name0):
                out.setdefault("C9",[]).append((name0,edge_event(L),edge_event(S0)))
            if _want_variant(selected_names,"C9",name1):
                out.setdefault("C9",[]).append((name1,edge_event(L),edge_event(S1)))

    # C10 ADX neighborhood
    ao=sma((d.high+d.low)/2,5)-sma((d.high+d.low)/2,34); squeeze=ema(d.close,20)-ema(d.close,50); mean=sma(d.close,20); sd=d.close.rolling(20,min_periods=20).std(ddof=0); z=(d.close-mean)/sd
    tu4=d.close>p15["trend100"]; td4=d.close<p15["trend100"]; upper_w=d.high-pd.concat([d.close,d.open],axis=1).max(axis=1); lower_w=pd.concat([d.close,d.open],axis=1).min(axis=1)-d.low; body=(d.close-d.open).abs(); bull_pin=(lower_w>body*1.5)&(d.close>d.open); bear_pin=(upper_w>body*1.5)&(d.close<d.open)
    for adxth in [18,20,22,25,28]:
        name=f"ADX_{adxth}"
        if not _want_variant(selected_names,"C10",name): continue
        L=(ao>0)&(squeeze>0)&(z<-1.5)&tu4&(ADX>adxth)&bull_pin
        S=(ao<0)&(squeeze<0)&(z>1.5)&td4&(ADX>adxth)&bear_pin
        out.setdefault("C10",[]).append((name,edge_event(L),edge_event(S)))

    # TIER: historical candidate around ADX26, T2=8,T3=6; local grid.
    if _want_combo(selected_names,"TIER"):
        d=d1; dip=p1["dip"]; dim=p1["dim"]; ADX=p1["ADX"]; R=p1["R"]; M=p1["M"]; A20=atr(d,20)
        e50=align_confirmed(ema(d4.close,50),d.index,"4h"); e150=align_confirmed(ema(d4.close,150),d.index,"4h"); e200=align_confirmed(ema(d4.close,200),d.index,"4h"); V=session_vwap(d); macd=ema(d.close,12)-ema(d.close,26); sig=ema(macd,9); hist=macd-sig; C9=cci(d.close,d.high,d.low,9); K14=stochastic(d.close,d.high,d.low,14); D14=sma(K14,3); e9=ema(d.close,9); e21=ema(d.close,21); sup=sma(d.close,8)+2*A20; strong=(d.close-d.open).abs()>A20*0.8
        sd=d.close.rolling(20,min_periods=20).std(ddof=0); z=(d.close-sma(d.close,20))/sd; ao=ema(d.close,5)-ema(d.close,34); sq=ema(d.close,20)-ema(d.close,50); rav=sma(R,14); stdt=pine_custom_supertrend_dir(d,8,4.0); pcL=d.close>d.close.rolling(20,min_periods=20).max().shift(1); pcS=d.close<d.close.rolling(20,min_periods=20).min().shift(1)
        for adxth,t2,t3,vm in itertools.product([23,25,26,28,30],[7,8,9],[5,6,7],[1.3,1.5,1.8]):
            name=f"ADX{adxth}_T2{t2}_T3{t3}_V{vm:.1f}"
            if not _want_variant(selected_names,"TIER",name): continue
            vs=d.volume>sma(d.volume,20)*vm; tdL=(dip>dim)&(dip>22); tdS=(dip<dim)&(dim>22); trL=(e50>e200)&tdL&(d.close>V)&(e50>e50.shift(1)); trS=(e50<e200)&tdS&(d.close<V)&(e50<e50.shift(1)); t1L=trL&vs&(ADX>adxth); t1S=trS&vs&(ADX>adxth)
            s2L=(hist>0)*2+(e150>e200)*2+(C9>100)*1+(R>50)*1+(d.close>sup)*1+crossover(K14,D14)*0.5+(d.close>e21)*1+(strong&(d.close>d.open))*1
            s2S=(hist<0)*2+(e150<e200)*2+(C9<-100)*1+(R<50)*1+(d.close<sup)*1+crossunder(K14,D14)*0.5+(d.close<e21)*1+(strong&(d.close<d.open))*1
            s2L=s2L+(M>55)*1+(A20>A20.shift(1))*1+(e9>e21)*1+((C9>C9.shift(1))&(C9>100))*1+((K14>K14.shift(1))&(K14>50))*1
            s2S=s2S+(M<45)*1+(A20>A20.shift(1))*1+(e9<e21)*1+((C9<C9.shift(1))&(C9<-100))*1+((K14<K14.shift(1))&(K14<50))*1
            s3L=(z<-1.5)*1+(ao>0)*1+(sq>0)*1+(R>rav)*1+(e9>e21)*1+(hist>0)*1+(stdt==1)*1
            s3S=(z>1.5)*1+(ao<0)*1+(sq<0)*1+(R<rav)*1+(e9<e21)*1+(hist<0)*1+(stdt==-1)*1
            vma=sma(d.volume,20); vsp=d.volume>vma*vm; mrL=(M>60)&(M>M.shift(1))&vsp; mrS=(M<40)&(M<M.shift(1))&vsp
            s3L=s3L+pcL*1+mrL*1+(R>50)*1+vsp*1; s3S=s3S+pcS*1+mrS*1+(R<50)*1+vsp*1
            L=t1L&(s2L>=t2)&(s3L>=t3); S=t1S&(s2S>=t2)&(s3S>=t3)
            out.setdefault("TIER",[]).append((name,edge_event(L),edge_event(S)))
    return out


@dataclass
class OBConfig:
    pivot_len:int=5
    search_bars:int=12
    max_age:int=80
    danger_atr:float=0.0


def _confirmed_pivots(d:pd.DataFrame,n:int):
    h=d.high.to_numpy(float); l=d.low.to_numpy(float); ph=np.full(len(d),np.nan); pl=np.full(len(d),np.nan)
    for j in range(n,len(d)-n):
        if h[j] > np.max(np.r_[h[j-n:j],h[j+1:j+n+1]]): ph[j+n]=h[j]
        if l[j] < np.min(np.r_[l[j-n:j],l[j+1:j+n+1]]): pl[j+n]=l[j]
    return ph,pl


def ob_context(df:pd.DataFrame,cfg:OBConfig=OBConfig())->pd.DataFrame:
    d=df.copy(); A=atr(d,14).to_numpy(float); ph,pl=_confirmed_pivots(d,cfg.pivot_len)
    o=d.open.to_numpy(float); h=d.high.to_numpy(float); l=d.low.to_numpy(float); c=d.close.to_numpy(float)
    n=len(d); buy=np.zeros(n,bool); sell=np.zeros(n,bool)
    bull_lo=np.full(n,np.nan); bull_hi=np.full(n,np.nan); bear_lo=np.full(n,np.nan); bear_hi=np.full(n,np.nan)
    sh=sl=np.nan; sh_broken=sl_broken=False; blo=bhi=np.nan; bro=-1; elo=ehi=np.nan; ero=-1
    for i in range(n):
        if np.isfinite(ph[i]): sh=ph[i]; sh_broken=False
        if np.isfinite(pl[i]): sl=pl[i]; sl_broken=False
        bull_bos=np.isfinite(sh) and not sh_broken and c[i]>sh
        bear_bos=np.isfinite(sl) and not sl_broken and c[i]<sl
        if bull_bos:
            sh_broken=True
            for j in range(1,min(cfg.search_bars,i)+1):
                k=i-j
                if c[k]<o[k]: blo,bhi,bro=l[k],h[k],k; break
        if bear_bos:
            sl_broken=True
            for j in range(1,min(cfg.search_bars,i)+1):
                k=i-j
                if c[k]>o[k]: elo,ehi,ero=l[k],h[k],k; break
        if np.isfinite(blo) and ((i-bro)>cfg.max_age or c[i]<blo): blo=bhi=np.nan; bro=-1
        if np.isfinite(ehi) and ((i-ero)>cfg.max_age or c[i]>ehi): elo=ehi=np.nan; ero=-1
        danger=(A[i]*cfg.danger_atr) if np.isfinite(A[i]) else 0.0
        buy[i]=np.isfinite(elo) and c[i]<=ehi and (elo-c[i])<=danger
        sell[i]=np.isfinite(bhi) and c[i]>=blo and (c[i]-bhi)<=danger
        bull_lo[i]=blo; bull_hi[i]=bhi; bear_lo[i]=elo; bear_hi[i]=ehi
    return pd.DataFrame({'buy_blocked':buy,'sell_blocked':sell,'bull_ob_low':bull_lo,'bull_ob_high':bull_hi,'bear_ob_low':bear_lo,'bear_ob_high':bear_hi},index=d.index)


def smc_direction(df: pd.DataFrame, swing_len: int = 50, confluence: bool = False) -> pd.Series:
    """Python port of Pine f_smc_dir() from SMC Same-Bar v11.
    Returns +1 strong bull, -1 strong bear, 0 mixed/neutral.
    """
    d=df.copy()
    h=d.high.to_numpy(float); l=d.low.to_numpy(float)
    o=d.open.to_numpy(float); c=d.close.to_numpy(float)
    n=len(d); out=np.zeros(n,dtype=np.int8)

    swing_leg=0; internal_leg=0
    swing_hi=np.nan; swing_lo=np.nan
    internal_hi=np.nan; internal_lo=np.nan
    swing_hi_crossed=False; swing_lo_crossed=False
    internal_hi_crossed=False; internal_lo_crossed=False
    swing_bias=0; internal_bias=0

    prev_swing_hi=np.nan; prev_swing_lo=np.nan
    prev_internal_hi=np.nan; prev_internal_lo=np.nan

    def crossover(i, level, prev_level):
        return i>0 and np.isfinite(level) and np.isfinite(prev_level) and c[i]>level and c[i-1]<=prev_level
    def crossunder(i, level, prev_level):
        return i>0 and np.isfinite(level) and np.isfinite(prev_level) and c[i]<level and c[i-1]>=prev_level

    for i in range(n):
        # Pine: high[L] > ta.highest(L), where highest covers current..L-1 bars.
        swing_new_high = i>=swing_len and h[i-swing_len] > np.max(h[i-swing_len+1:i+1])
        swing_new_low  = i>=swing_len and l[i-swing_len] < np.min(l[i-swing_len+1:i+1])
        old=swing_leg
        if swing_new_high: swing_leg=0
        elif swing_new_low: swing_leg=1
        swing_change=swing_leg-old

        ilen=5
        int_new_high = i>=ilen and h[i-ilen] > np.max(h[i-ilen+1:i+1])
        int_new_low  = i>=ilen and l[i-ilen] < np.min(l[i-ilen+1:i+1])
        oldi=internal_leg
        if int_new_high: internal_leg=0
        elif int_new_low: internal_leg=1
        int_change=internal_leg-oldi

        prev_swing_hi=swing_hi; prev_swing_lo=swing_lo
        prev_internal_hi=internal_hi; prev_internal_lo=internal_lo

        if swing_change==1:
            swing_lo=l[i-swing_len]; swing_lo_crossed=False
        elif swing_change==-1:
            swing_hi=h[i-swing_len]; swing_hi_crossed=False

        if int_change==1:
            internal_lo=l[i-ilen]; internal_lo_crossed=False
        elif int_change==-1:
            internal_hi=h[i-ilen]; internal_hi_crossed=False

        # ta.crossover uses previous value of the level series; if a level was just
        # initialized from na, crossover is false on that bar.
        bullbar=True; bearbar=True
        if confluence:
            upper=h[i]-max(c[i],o[i])
            # Pine expression preserved semantically as written.
            rhs=min(c[i], o[i]-l[i])
            bullbar=upper>rhs; bearbar=upper<rhs

        internal_bull_extra=(np.isfinite(internal_hi) and np.isfinite(swing_hi)
                             and internal_hi!=swing_hi and bullbar)
        if (not internal_hi_crossed and internal_bull_extra
            and crossover(i,internal_hi,prev_internal_hi)):
            internal_hi_crossed=True; internal_bias=1

        internal_bear_extra=(np.isfinite(internal_lo) and np.isfinite(swing_lo)
                             and internal_lo!=swing_lo and bearbar)
        if (not internal_lo_crossed and internal_bear_extra
            and crossunder(i,internal_lo,prev_internal_lo)):
            internal_lo_crossed=True; internal_bias=-1

        if (not swing_hi_crossed and crossover(i,swing_hi,prev_swing_hi)):
            swing_hi_crossed=True; swing_bias=1
        if (not swing_lo_crossed and crossunder(i,swing_lo,prev_swing_lo)):
            swing_lo_crossed=True; swing_bias=-1

        out[i]=1 if internal_bias==1 and swing_bias==1 else (-1 if internal_bias==-1 and swing_bias==-1 else 0)

    return pd.Series(out,index=d.index,name="smc_dir")


def approved(side,smcdir,obblocked,variant):
    edir=1 if side=="L" else -1
    if "STRICT" in variant:
        smc_ok=(smcdir==edir)
    elif "VETO" in variant:
        smc_ok=(smcdir!=-edir)
    else:
        smc_ok=True
    ob_ok=(not obblocked) if ("OB" in variant) else True
    return smc_ok and ob_ok


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


def hard_tp_sl(entry:float, direction:int, cfg:dict)->tuple[float,float]:
    """Return locked hard TP/SL from decimal-fraction config values."""
    tpp=float(cfg["tp_pct"])
    slp=float(cfg["sl_pct"])
    if direction==1:
        return entry*(1+tpp), entry*(1-slp)
    if direction==-1:
        return entry*(1-tpp), entry*(1+slp)
    raise ValueError("direction must be +1 or -1")


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


def _new6_latest_action(
    d15:pd.DataFrame,
    d1:pd.DataFrame,
    combo:int,
    cfg:dict,
    smc1:pd.Series,
    ob1:pd.DataFrame|None,
)->tuple[int,int]:
    """Return (direction, current_smc_dir) only when latest bar opens a NEW trade.

    This reconstructs the single-position-per-strategy state with the exact hard
    TP/SL semantics used in research: exits first, stop first on ambiguous bars,
    entry at the 15m close, and no exit on the entry candle.
    """
    latest=d15.index[-1]
    if latest.minute%60!=45:
        return 0,0
    ot=latest-pd.Timedelta("45min")
    if ot not in d1.index:
        return 0,0

    long1,short1=_new6_filtered_signals(d1,combo,cfg,smc1,ob1)
    current_long=bool(long1.loc[ot])
    current_short=bool(short1.loc[ot])
    if current_long==current_short:
        return 0,int(smc1.loc[ot]) if ot in smc1.index else 0

    event_index=d1.index+pd.Timedelta("45min")
    long15=pd.Series(long1.to_numpy(bool),index=event_index).reindex(
        d15.index,fill_value=False
    ).to_numpy(bool)
    short15=pd.Series(short1.to_numpy(bool),index=event_index).reindex(
        d15.index,fill_value=False
    ).to_numpy(bool)

    high=d15.high.to_numpy(float)
    low=d15.low.to_numpy(float)
    close=d15.close.to_numpy(float)
    tp_pct=float(cfg["tp_pct"])
    sl_pct=float(cfg["sl_pct"])

    pos=0
    tp=0.0
    sl=0.0
    last=len(d15)-1
    for i in range(len(d15)):
        if pos==1:
            hit_sl=low[i]<=sl
            hit_tp=high[i]>=tp
            if hit_sl or hit_tp:
                pos=0
        elif pos==-1:
            hit_sl=high[i]>=sl
            hit_tp=low[i]<=tp
            if hit_sl or hit_tp:
                pos=0

        if pos!=0:
            continue

        is_long=bool(long15[i])
        is_short=bool(short15[i])
        if is_long==is_short:
            continue

        if i==last:
            return (1 if is_long else -1),int(smc1.loc[ot]) if ot in smc1.index else 0

        entry=float(close[i])
        if is_long:
            pos=1
            tp=entry*(1+tp_pct)
            sl=entry*(1-sl_pct)
        else:
            pos=-1
            tp=entry*(1-tp_pct)
            sl=entry*(1+sl_pct)

    return 0,int(smc1.loc[ot]) if ot in smc1.index else 0
