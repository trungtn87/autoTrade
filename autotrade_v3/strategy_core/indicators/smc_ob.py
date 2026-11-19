from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .indicators import atr

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
