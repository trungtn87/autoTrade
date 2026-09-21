from __future__ import annotations
import numpy as np
import pandas as pd
from indicators import (
    sma, ema, atr, rsi, mfi, cci, stochastic, crossover, crossunder,
    dmi, parabolic_sar, obv_variant, session_vwap, edge_event,
    pine_custom_supertrend_dir, pine_combo15_supertrend_dir,
)

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

def combo15_events(df15: pd.DataFrame, df4h: pd.DataFrame) -> pd.DataFrame:
    d=ensure_dt(df15); h4=ensure_dt(df4h); out=pd.DataFrame(index=d.index)
    ema50_4h=align_confirmed(ema(h4.close,50),d.index,'4h')
    trend_up=d.close>ema50_4h; trend_down=d.close<ema50_4h
    M=mfi(d,14); sar=parabolic_sar(d,0.05,0.1,0.2); sar_bull=d.close>sar; sar_bear=d.close<sar
    ema150=ema(d.close,150); ema200=ema(d.close,200); R=rsi(d.close,14); A=atr(d,14)
    dip,dim,ADX=dmi(d,14,14); adx_strong=(ADX>23)&(ADX>ADX.shift(1))
    body=(d.close-d.open).abs(); rng=d.high-d.low; body_ratio=body/rng.replace(0,np.nan); valid=body_ratio>0.75
    volSMA=sma(d.volume,20); volSpike=d.volume>volSMA*1.5
    stdir=pine_combo15_supertrend_dir(d)
    c5L=(stdir==1)&(ema150>ema200)&(ema150>ema150.shift(1))&volSpike&(R>45)&valid&adx_strong&(M>55)&sar_bull&trend_up
    c5S=(stdir==-1)&volSpike&(ema150<ema200)&(ema150<ema150.shift(1))&(R<55)&valid&adx_strong&(M<55)&sar_bear&trend_down

    ef=ema(d.close,21); es=ema(d.close,55); ema100_4h=align_confirmed(ema(h4.close,100),d.index,'4h')
    tu=d.close>ema100_4h; td=d.close<ema100_4h
    strong_bull=(d.close>d.open)&((d.close-d.open)>A*1.5); strong_bear=(d.close<d.open)&((d.open-d.close)>A*1.5)
    adx_r=ADX>ADX.shift(1); volume_surge=d.volume>d.volume.shift(1)*1.8
    c7L=volume_surge&(ef>es)&tu&adx_r&(ADX>25)&strong_bull&volSpike&valid&(R>60)&(R<85)
    c7S=volume_surge&(ef<es)&td&adx_r&(ADX>25)&strong_bear&volSpike&valid&(R<40)&(R>15)

    C=cci(d.close,d.high,d.low,10)
    longc=(C>100)&(C>C.shift(1))&(d.volume>volSMA*0.8)&(d.volume>d.volume.shift(1))
    shortc=(C<-100)&(C<C.shift(1))&(d.volume>volSMA*0.8)&(d.volume>d.volume.shift(1))
    macd=ema(d.close,12)-ema(d.close,26); sig=ema(macd,9); hist=macd-sig
    bull_eng=(d.close>d.open)&(d.close>d.open.shift(1))&(d.open<d.close.shift(1))
    bear_eng=(d.close<d.open)&(d.close<d.open.shift(1))&(d.open>d.close.shift(1))
    c8L=longc&(M>70)&(ADX>25)&(ADX>ADX.shift(1))&bull_eng&trend_up&(hist>0)&valid
    c8S=shortc&(M<30)&(ADX>25)&(ADX>ADX.shift(1))&bear_eng&trend_down&(hist<0)&valid

    mid=_nadaraya(d.close,24,3.0); nr=d.close.rolling(24,min_periods=24).std(ddof=0)*2.3
    upper=mid+nr; lower=mid-nr
    c9L=(d.close>ema200)&(R<40)&(d.close<lower)&(stdir==1)&(body_ratio>0.4)
    c9S=(d.close<ema200)&(R>60)&(d.close>upper)&(stdir==1)&(body_ratio>0.4)

    ao=sma((d.high+d.low)/2,5)-sma((d.high+d.low)/2,34)
    squeeze=ema(d.close,20)-ema(d.close,50); mean=sma(d.close,20); sd=d.close.rolling(20,min_periods=20).std(ddof=0); z=(d.close-mean)/sd
    trend100=align_confirmed(ema(h4.close,100),d.index,'4h'); tu4=d.close>trend100; td4=d.close<trend100
    upper_w=d.high-pd.concat([d.close,d.open],axis=1).max(axis=1); lower_w=pd.concat([d.close,d.open],axis=1).min(axis=1)-d.low
    bull_pin=(lower_w>body*1.5)&(d.close>d.open); bear_pin=(upper_w>body*1.5)&(d.close<d.open)
    c10L=(ao>0)&(squeeze>0)&(z<-1.5)&tu4&(ADX>20)&bull_pin
    c10S=(ao<0)&(squeeze<0)&(z>1.5)&td4&(ADX>20)&bear_pin
    for name,L,S in [('C5',c5L,c5S),('C7',c7L,c7S),('C8',c8L,c8S),('C9',c9L,c9S),('C10',c10L,c10S)]:
        out[name+'_L']=edge_event(L); out[name+'_S']=edge_event(S)
    out['ATR21']=atr(d,21); out['close']=d.close
    return out

def _atr_trailing_stop(src: pd.Series, atr14: pd.Series) -> pd.Series:
    trstop=1.5*atr14; x=src.to_numpy(float); ts=trstop.to_numpy(float); out=np.full(len(src),np.nan)
    for i in range(len(src)):
        prev=out[i-1] if i else np.nan; nz=0.0 if not np.isfinite(prev) else prev
        prevsrc=x[i-1] if i else np.nan
        if i and x[i]>nz and prevsrc>nz: out[i]=max(nz,x[i]-ts[i])
        elif i and x[i]<nz and prevsrc<nz: out[i]=min(nz,x[i]+ts[i])
        else: out[i]=x[i]-ts[i] if x[i]>nz else x[i]+ts[i]
    return pd.Series(out,index=src.index)

def combo60_events(df1h: pd.DataFrame, df4h: pd.DataFrame, df6h: pd.DataFrame|None=None) -> pd.DataFrame:
    d=ensure_dt(df1h); h4=ensure_dt(df4h); h6=ensure_dt(df6h) if df6h is not None else resample_ohlcv(d,'6h')
    out=pd.DataFrame(index=d.index)
    A8=atr(d,8); A14=atr(d,14); A21=atr(d,21)
    e10=ema(d.close,10); e25=ema(d.close,25)
    volma=sma(d.volume,20); volsp=d.volume>volma*1.5; R=rsi(d.close,14)
    macd=ema(d.close,8)-ema(d.close,21); sig=ema(macd,9); hist=macd-sig
    dip,dim,ADX=dmi(d,14,14); adxr=ADX>ADX.shift(1); M=mfi(d,14)
    sb=(d.close>d.open)&((d.close-d.open)>A21*0.8); ss=(d.close<d.open)&((d.open-d.close)>A21*0.8)
    ema1504=align_confirmed(ema(h4.close,150),d.index,'4h'); tu=d.close>ema1504; td=d.close<ema1504
    K=stochastic(d.close,d.high,d.low,11); D=sma(K,3); C=cci(d.close,d.high,d.low,9)
    longc=(K>30)&(K>D)&(C>80)&(C>C.shift(1)); shortc=(K<70)&(K<D)&(C>-80)&(C<C.shift(1))
    basis=sma(d.close,20); dev=1.8*d.close.rolling(20,min_periods=20).std(ddof=0); ub=basis+dev; lb=basis-dev
    bbup=d.close>ub; bbdn=d.close<lb
    ats=_atr_trailing_stop(d.close,A14); above=crossover(ema(d.close,1),ats); below=crossover(ats,ema(d.close,1))
    sar=parabolic_sar(d,0.05,0.2,0.3)
    bull_eng=(d.close>d.open)&(d.close.shift(1)<d.open.shift(1))&(d.close>d.open.shift(1))&(d.open<d.close.shift(1))
    bear_eng=(d.close<d.open)&(d.close.shift(1)>d.open.shift(1))&(d.close<d.open.shift(1))&(d.open>d.close.shift(1))
    stdir=pine_custom_supertrend_dir(d,8,4.0); O=obv_variant(d); oup=O>O.shift(1); odn=O<O.shift(1)
    ema50_6=align_confirmed(ema(h6.close,50),d.index,'6h')
    tfast=ema(d.close,21); tslow=ema(d.close,42); bt=tfast>tslow; br=tfast<tslow
    std20=d.close.rolling(20,min_periods=20).std(ddof=0); bbu=sma(d.close,20)+2*std20; bbl=sma(d.close,20)-2*std20
    ku=sma(d.close,20)+1.7*atr(d,20); kl=sma(d.close,20)-1.7*atr(d,20)
    breakout_b=crossover(d.close,bbu)&(d.close>ku); breakout_s=crossunder(d.close,bbl)&(d.close<kl)
    c1L=longc&(R>55)&(hist>0)&(M>65)&adxr&sb&tu&volsp
    c1S=shortc&(R<45)&(hist<0)&(M<45)&adxr&ss&td&volsp
    c2L=(d.close>ats)&above&(e10>e25)&(macd>sig)&(ADX>20)&bbup
    c2S=(d.close<ats)&below&(e10<e25)&(macd<sig)&(ADX>20)&bbdn
    c3L=(stdir==1)&(ADX>30)&(d.close>sar)&bull_eng&oup
    c3S=(stdir==-1)&(ADX>30)&(d.close<sar)&bear_eng&odn
    c4L=bt&breakout_b&(d.close>ema50_6)&(stdir==1)&sb&volsp&(ADX>23)
    c4S=br&breakout_s&(d.close<ema50_6)&(stdir==-1)&ss&volsp&(ADX>23)
    lc6=(R>30)&(K>30)&(K>D)&(C>100)&(C>C.shift(1)); sc6=(R<70)&(K<70)&(K<D)&(C>-100)&(C<C.shift(1))
    sb6=(d.close>d.open)&((d.close-d.open)>A21*1.2); ss6=(d.close<d.open)&((d.open-d.close)>A21*1.2); adxs=(ADX>20)&adxr
    c6L=sb6&breakout_b&lc6&adxs; c6S=ss6&breakout_s&sc6&adxs
    for name,L,S in [('C1',c1L,c1S),('C2',c2L,c2S),('C3',c3L,c3S),('C4',c4L,c4S),('C6',c6L,c6S)]:
        out[name+'_L']=edge_event(L); out[name+'_S']=edge_event(S)
    out['ATR21']=A21; out['close']=d.close
    return out

def tier60_events(df1h: pd.DataFrame, df4h: pd.DataFrame) -> pd.DataFrame:
    d=ensure_dt(df1h); h4=ensure_dt(df4h); out=pd.DataFrame(index=d.index)
    dip,dim,ADX=dmi(d,14,14); A=atr(d,20)
    e50=align_confirmed(ema(h4.close,50),d.index,'4h'); e150=align_confirmed(ema(h4.close,150),d.index,'4h'); e200=align_confirmed(ema(h4.close,200),d.index,'4h')
    tdL=(dip>dim)&(dip>22); tdS=(dip<dim)&(dim>22); R=rsi(d.close,14); V=session_vwap(d); vs=d.volume>sma(d.volume,20)*1.5
    tL=(e50>e200)&tdL&(d.close>V)&(e50>e50.shift(1)); tS=(e50<e200)&tdS&(d.close<V)&(e50<e50.shift(1))
    t1L=tL&vs&(ADX>25); t1S=tS&vs&(ADX>25)
    macd=ema(d.close,12)-ema(d.close,26); sig=ema(macd,9); hist=macd-sig; C=cci(d.close,d.high,d.low,9)
    K=stochastic(d.close,d.high,d.low,14); D=sma(K,3); e9=ema(d.close,9); e21=ema(d.close,21)
    sup=sma(d.close,8)+2*A; strong=(d.close-d.open).abs()>A*0.8; M=mfi(d,14)
    s2L=(hist>0)*2+(e150>e200)*2+(C>100)*1+(R>50)*1+(d.close>sup)*1+crossover(K,D)*0.5+(d.close>e21)*1+(strong&(d.close>d.open))*1
    s2S=(hist<0)*2+(e150<e200)*2+(C<-100)*1+(R<50)*1+(d.close<sup)*1+crossunder(K,D)*0.5+(d.close<e21)*1+(strong&(d.close<d.open))*1
    s2L=s2L+(M>55)*1+(A>A.shift(1))*1+(e9>e21)*1+((C>C.shift(1))&(C>100))*1+((K>K.shift(1))&(K>50))*1
    s2S=s2S+(M<45)*1+(A>A.shift(1))*1+(e9<e21)*1+((C<C.shift(1))&(C<-100))*1+((K<K.shift(1))&(K<50))*1
    p2L=s2L>=8; p2S=s2S>=8
    sd=d.close.rolling(20,min_periods=20).std(ddof=0); z=(d.close-sma(d.close,20))/sd
    ao=ema(d.close,5)-ema(d.close,34); sq=ema(d.close,20)-ema(d.close,50); rav=sma(R,14); rdL=R>rav; rdS=R<rav
    stdir=pine_custom_supertrend_dir(d,8,4.0)
    s3L=(z<-1.5)*1+(ao>0)*1+(sq>0)*1+rdL*1+(e9>e21)*1+(hist>0)*1+(stdir==1)*1
    s3S=(z>1.5)*1+(ao<0)*1+(sq<0)*1+rdS*1+(e9<e21)*1+(hist<0)*1+(stdir==-1)*1
    pcL=d.close>d.close.rolling(20,min_periods=20).max().shift(1); pcS=d.close<d.close.rolling(20,min_periods=20).min().shift(1)
    vma=sma(d.volume,20); vsp=d.volume>vma*1.5; mrL=(M>60)&(M>M.shift(1))&vsp; mrS=(M<40)&(M<M.shift(1))&vsp
    s3L=s3L+pcL*1+mrL*1+(R>50)*1+vsp*1; s3S=s3S+pcS*1+mrS*1+(R<50)*1+vsp*1
    entryL=t1L&p2L&(s3L>=6); entryS=t1S&p2S&(s3S>=6)
    out['TIER_L']=edge_event(entryL); out['TIER_S']=edge_event(entryS); out['close']=d.close; out['ATR21']=atr(d,21)
    return out

def build_all_signals(df15: pd.DataFrame, df1h: pd.DataFrame|None=None, df4h: pd.DataFrame|None=None, df6h: pd.DataFrame|None=None):
    d15=ensure_dt(df15)
    d1=ensure_dt(df1h) if df1h is not None else resample_ohlcv(d15,'1h')
    d4=ensure_dt(df4h) if df4h is not None else resample_ohlcv(d15,'4h')
    d6=ensure_dt(df6h) if df6h is not None else resample_ohlcv(d15,'6h')
    return {'15m':combo15_events(d15,d4),'1h':combo60_events(d1,d4,d6),'tier':tier60_events(d1,d4),'ohlcv15':d15,'ohlcv1h':d1,'ohlcv4h':d4,'ohlcv6h':d6}
