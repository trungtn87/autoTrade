from __future__ import annotations
import argparse, json, itertools
from pathlib import Path
import numpy as np
import pandas as pd

from .signals import ensure_dt, resample_ohlcv, align_confirmed
from .indicators import sma, ema, atr, rsi, mfi, cci, stochastic, crossover, crossunder, dmi, parabolic_sar, obv_variant, session_vwap, edge_event, pine_custom_supertrend_dir, pine_combo15_supertrend_dir
from .smc_structure import smc_direction
from .smc_ob import ob_context, OBConfig
from .two_trail_layer12 import run_one_trade, approved, WINDOWS, END

COMBOS=["C1","C2","C3","C4","C5","C6","C7","C8","C9","C10","TIER"]
NATIVE={"C1":"1h","C2":"1h","C3":"1h","C4":"1h","C5":"15m","C6":"1h","C7":"15m","C8":"15m","C9":"15m","C10":"15m","TIER":"1h"}

def load(path):
    d=pd.read_pickle(path)
    if not isinstance(d.index,pd.DatetimeIndex):
        d.index=pd.to_datetime(d["open_time"],unit="ms",utc=True)
    elif d.index.tz is None: d.index=d.index.tz_localize("UTC")
    else: d.index=d.index.tz_convert("UTC")
    return d.sort_index()

def _nadaraya(close,length=24,smooth=3.0):
    denom=0.0; num=pd.Series(0.0,index=close.index)
    for i in range(length+1):
        w=np.exp(-((i/length*smooth)**2)); num=num+close.shift(i)*w; denom+=w
    return num/denom

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

def load_lock(path):
    return json.loads(Path(path).read_text())

def build_context(d, lock_sym, variants):
    d1=resample_ohlcv(d,"1h")
    smc15=smc_direction(d,50,False); smc1=smc_direction(d1,50,False)
    cfg=OBConfig(pivot_len=5,search_bars=12,max_age=80,danger_atr=0.5)
    ob15=ob_context(d,cfg); ob1=ob_context(d1,cfg)
    pos={t:i for i,t in enumerate(d.index)}
    events={}
    for combo, lst in variants.items():
        events[combo]={}
        for name,L,S in lst:
            ev=[]
            frame_index=L.index
            for side,ser in [("L",L),("S",S)]:
                for ot in ser.index[ser.fillna(False)]:
                    et=ot+pd.Timedelta("45min") if NATIVE[combo]=="1h" else ot
                    if et not in pos or et>=END: continue
                    if NATIVE[combo]=="1h":
                        sd=int(smc1.loc[ot]) if ot in smc1.index else 0
                        blocked=bool(ob1.loc[ot,"buy_blocked" if side=="L" else "sell_blocked"]) if ot in ob1.index else False
                    else:
                        sd=int(smc15.loc[ot]) if ot in smc15.index else 0
                        blocked=bool(ob15.loc[ot,"buy_blocked" if side=="L" else "sell_blocked"]) if ot in ob15.index else False
                    ev.append((pos[et],side,ot,sd,blocked))
            events[combo][name]=sorted(ev,key=lambda x:x[0])
    return events

def simulate(d,events,start,cfg,l2variant):
    idx=d.index; a=int(idx.searchsorted(start)); b=int(idx.searchsorted(END)); H=d.high.to_numpy(float); L=d.low.to_numpy(float); C=d.close.to_numpy(float)
    ev=[e for e in events if a<=e[0]<b]
    sl=cfg["sl_pct"]/100; t1cb=cfg["t1_callback_pct"]/100; t2act=cfg["t2_activation_pct"]/100; t2cb=cfg["t2_callback_pct"]/100; protect=cfg["protect_pct"]/100
    p=0; net=0; pos=0; neg=0; wins=losses=trades=veto=0; eq=1000.; peak=1000.; dd=0
    while p<len(ev):
        ei,side,ot,sd,blocked=ev[p]
        if not approved(side,sd,blocked,l2variant):
            veto+=1;p+=1;continue
        xi,pnl,fees,reasons,done=run_one_trade(H,L,C,ei,b,side,sl,t1cb,t2act,t2cb,protect)
        if not done: break
        trades+=1; net+=pnl; eq+=pnl; peak=max(peak,eq); dd=max(dd,peak-eq)
        if pnl>0:wins+=1;pos+=pnl
        elif pnl<0:losses+=1;neg+=-pnl
        p+=1
        while p<len(ev) and ev[p][0]<xi:p+=1
    return {"trades":trades,"wins":wins,"losses":losses,"win_rate":wins/trades*100 if trades else np.nan,"profit_factor":pos/neg if neg else (np.inf if pos else np.nan),"net_pnl":net,"max_drawdown":dd,"vetoes":veto}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--btc",required=True);ap.add_argument("--eth",required=True);ap.add_argument("--lock",default="layer3_locked_run17.json");ap.add_argument("--out-dir",default="results/layer3_long")
    args=ap.parse_args(); outdir=Path(args.out_dir);outdir.mkdir(parents=True,exist_ok=True); locks=load_lock(args.lock); allrank=[]
    for sym,path in [("BTCUSDT",args.btc),("ETHUSDT",args.eth)]:
        d=load(path); pc=precompute(d); vars=entry_variants(pc); evs=build_context(d,locks[sym],vars)
        rows=[]
        for combo in COMBOS:
            cfg=locks[sym][combo]; l2=cfg["layer2_variant"]
            for vname in evs[combo]:
                for wn,start in WINDOWS.items():
                    rows.append({"symbol":sym,"combo":combo,"entry_variant":vname,"window":wn,"layer2_variant":l2,**simulate(d,evs[combo][vname],start,cfg,l2)})
        broad=pd.DataFrame(rows); broad.to_csv(outdir/f"{sym}_L3_broad.csv",index=False)
        ag=[]
        for (combo,vname),g in broad.groupby(["combo","entry_variant"]):
            by={r.window:r for r in g.itertuples()}; rec=[by["6M"].net_pnl,by["1Y"].net_pnl,by["3Y"].net_pnl]
            ag.append({"symbol":sym,"combo":combo,"entry_variant":vname,"all_recent_positive":all(x>0 for x in rec),"recent_positive":sum(x>0 for x in rec),
                       "pnl_6M":by["6M"].net_pnl,"pnl_1Y":by["1Y"].net_pnl,"pnl_3Y":by["3Y"].net_pnl,"pnl_FULL":by["FULL"].net_pnl,
                       "pf_3Y":by["3Y"].profit_factor,"dd_3Y":by["3Y"].max_drawdown,"trades_3Y":by["3Y"].trades})
        r=pd.DataFrame(ag).sort_values(["combo","all_recent_positive","recent_positive","pnl_3Y","pf_3Y","dd_3Y"],ascending=[True,False,False,False,False,True]); r["layer3_rank_within_combo"]=r.groupby("combo").cumcount()+1
        r.to_csv(outdir/f"{sym}_L3_ranked.csv",index=False); allrank.append(r)
        print("\n",sym,"L3 WINNERS",flush=True); print(r[r.layer3_rank_within_combo==1].to_string(index=False),flush=True)
    pd.concat(allrank,ignore_index=True).to_csv(outdir/"L3_RANKED_ALL.csv",index=False)
    (outdir/"run_meta.json").write_text(json.dumps({"source_run":17,"selection":"3Y primary; 1Y/6M confirmation; FULL stress","layer1_2_locked":args.lock},indent=2))

if __name__=="__main__": main()
