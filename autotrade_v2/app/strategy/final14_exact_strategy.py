from __future__ import annotations

import numpy as np
import pandas as pd

from .final14_config import (
    FINAL14_CASES,
    NEW6_CASES,
    enabled_combos,
    enabled_new6_combos,
    get_case,
    new6_snapshot,
    snapshot as final14_snapshot,
)
from .strategy import Signal
from .final14_research.layer3_long import precompute, entry_variants, NATIVE
from .final14_research.smc_structure import smc_direction
from .final14_research.smc_ob import ob_context, OBConfig
from .final14_research.two_trail_layer12 import approved

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


def hard_tp_sl(entry:float, direction:int, cfg:dict)->tuple[float,float]:
    """Return locked hard TP/SL from decimal-fraction config values."""
    tpp=float(cfg["tp_pct"])
    slp=float(cfg["sl_pct"])
    if direction==1:
        return entry*(1+tpp), entry*(1-slp)
    if direction==-1:
        return entry*(1-tpp), entry*(1+slp)
    raise ValueError("direction must be +1 or -1")


def _symbol_configs(symbol:str)->dict[str,dict]:
    out={}
    for combo,cfg in FINAL14_CASES.get(symbol.upper(),{}).items():
        out[_combo_name(combo)]=cfg
    return out


def _selected_variants(pc, symbol:str, wanted:dict[str,set[str]]):
    configs=_symbol_configs(symbol)
    allv=entry_variants(pc,wanted)
    selected={}
    for cname,cfg in configs.items():
        variant=cfg["entry_variant"]
        matches=[x for x in allv[cname] if x[0]==variant]
        if len(matches)!=1:
            raise RuntimeError(f"FINAL14 variant mismatch {symbol} {cname} {variant}: {len(matches)}")
        selected[cname]=matches[0]
    return selected


def _layer2_requirements(symbol:str)->dict[str,bool]:
    req={"smc15":False,"smc1":False,"ob15":False,"ob1":False}
    for combo,cfg in FINAL14_CASES.get(symbol.upper(),{}).items():
        native=NATIVE[_combo_name(combo)]
        layer2=str(cfg["layer2"])
        if "STRICT" in layer2 or "VETO" in layer2:
            req["smc1" if native=="1h" else "smc15"]=True
        if "OB" in layer2:
            req["ob1" if native=="1h" else "ob15"]=True
    for cfg in NEW6_CASES.get(symbol.upper(),{}).values():
        layer2=str(cfg["layer2"])
        if "STRICT" in layer2 or "VETO" in layer2:
            req["smc1"]=True
        if "OB" in layer2:
            req["ob1"]=True
    return req


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


def combo_readiness(
    m15:pd.DataFrame,
    symbol:str|None=None,
)->dict[int,dict]:
    symbol=(symbol or "").upper()
    count=int(len(m15))
    ready=count>=MIN_LIVE_15M_BARS
    ids=tuple(enabled_combos(symbol))+tuple(enabled_new6_combos(symbol))
    return {
        combo:{
            "ready":ready,
            "timeframe":(
                "1h"
                if combo>=101 or NATIVE[_combo_name(combo)]=="1h"
                else "15m"
            ),
            "requirements":{"15m":MIN_LIVE_15M_BARS},
            "available":{"15m":count},
            "missing":{} if ready else {"15m":{"required":MIN_LIVE_15M_BARS,"available":count}},
        }
        for combo in ids
    }


def strategy_static_snapshot()->dict:
    return {
        **final14_snapshot(),
        "signal_engine":"vendored_exact_research_plus_new6",
        "min_live_15m_bars":MIN_LIVE_15M_BARS,
        "native_timeframes":{
            _combo_name(k):NATIVE[_combo_name(k)]
            for cases in FINAL14_CASES.values() for k in cases
        },
        "new6":new6_snapshot(),
        "smc":{"swing_len":50,"confluence":False,"same_bar":True},
        "ob":{"pivot_len":5,"search_bars":12,"max_age":80,"danger_atr":0.5},
    }


def scan_latest(
    symbol:str,
    m15:pd.DataFrame,
)->list[Signal]:
    symbol=symbol.upper()
    if len(m15)<MIN_LIVE_15M_BARS:
        return []

    d=_research_frame(m15)
    configs=_symbol_configs(symbol)
    wanted={cname:{cfg["entry_variant"]} for cname,cfg in configs.items()}
    pc=precompute(d,wanted)
    chosen=_selected_variants(pc,symbol,wanted)

    d1=pc["d1"]
    layer2_req=_layer2_requirements(symbol)
    smc15=smc_direction(d,50,False)
    smc1=smc_direction(d1,50,False)
    obcfg=OBConfig(pivot_len=5,search_bars=12,max_age=80,danger_atr=0.5)
    ob15=ob_context(d,obcfg) if layer2_req["ob15"] else None
    ob1=ob_context(d1,obcfg) if layer2_req["ob1"] else None

    latest=d.index[-1]
    latest_close_ms=(
        int(m15.iloc[-1]["close_time"])
        if "close_time" in m15.columns
        else int(latest.value//1_000_000 + 15*60_000-1)
    )
    entry=float(d.iloc[-1]["close"])
    out=[]

    for combo in enabled_combos(symbol):
        cname=_combo_name(combo)
        cfg=get_case(symbol,combo)
        name,L,S=chosen[cname]
        native=NATIVE[cname]
        if native=="1h":
            ot=latest-pd.Timedelta("45min")
            if latest.minute%60!=45 or ot not in L.index:
                continue
            sd=int(smc1.loc[ot]) if ot in smc1.index else 0
            obrow=ob1.loc[ot] if ob1 is not None and ot in ob1.index else None
        else:
            ot=latest
            if ot not in L.index:
                continue
            sd=int(smc15.loc[ot]) if ot in smc15.index else 0
            obrow=ob15.loc[ot] if ob15 is not None and ot in ob15.index else None

        direction=0
        side_code=""
        blocked=False
        if bool(L.loc[ot]):
            direction=1; side_code="L"
            blocked=bool(obrow["buy_blocked"]) if obrow is not None else False
        elif bool(S.loc[ot]):
            direction=-1; side_code="S"
            blocked=bool(obrow["sell_blocked"]) if obrow is not None else False
        if not direction:
            continue
        if not approved(side_code,sd,blocked,cfg["layer2"]):
            continue

        tp,sl=hard_tp_sl(entry,direction,cfg)
        out.append(Signal(
            symbol=symbol,
            combo=int(combo),
            side="BUY" if direction==1 else "SELL",
            timeframe="1h" if native=="1h" else "15m",
            close_time=latest_close_ms,
            entry=entry,
            tp=tp,
            sl=sl,
            smc_dir=sd,
        ))

    for combo in enabled_new6_combos(symbol):
        cfg=get_case(symbol,combo)
        direction,sd=_new6_latest_action(d,d1,combo,cfg,smc1,ob1)
        if not direction:
            continue
        tp,sl=hard_tp_sl(entry,direction,cfg)
        out.append(Signal(
            symbol=symbol,
            combo=int(combo),
            side="BUY" if direction==1 else "SELL",
            timeframe="1h",
            close_time=latest_close_ms,
            entry=entry,
            tp=tp,
            sl=sl,
            smc_dir=sd,
        ))

    return out
