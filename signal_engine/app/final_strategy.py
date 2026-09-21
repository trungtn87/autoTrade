from __future__ import annotations

import numpy as np
import pandas as pd

from .final_config import FINAL_CASES, enabled_combos, get_case, snapshot as final_config_snapshot
from .indicators import (
    atr, cci, combo15_supertrend_dir, combo3_custom_trend, crossover, crossunder,
    dmi, ema, mfi, nadaraya_mid, parabolic_sar, rsi, sma, stdev, stoch,
    ut_atr_trailing_stop,
)
from .strategy import Signal, smc_direction


FIFTEEN_MIN_COMBOS = {5, 7, 8, 9, 10}
ONE_HOUR_COMBOS = {1, 2, 3, 4, 6, 11}


def _event(cond: pd.Series) -> pd.Series:
    x = cond.fillna(False).astype(bool)
    return x & ~x.shift(1, fill_value=False)


def _hl2(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"]) / 2.0


def _map_htf(target: pd.DataFrame, higher: pd.DataFrame, values: pd.Series) -> pd.Series:
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


def _confirmed_pivots(df: pd.DataFrame, n: int = 5):
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    ph = np.full(len(df), np.nan)
    pl = np.full(len(df), np.nan)
    for j in range(n, len(df) - n):
        if h[j] > np.max(np.r_[h[j-n:j], h[j+1:j+n+1]]):
            ph[j+n] = h[j]
        if l[j] < np.min(np.r_[l[j-n:j], l[j+1:j+n+1]]):
            pl[j+n] = l[j]
    return ph, pl


def order_block_context(
    df: pd.DataFrame,
    pivot_len: int = 5,
    search_bars: int = 12,
    max_age: int = 80,
    danger_atr: float = 0.5,
) -> pd.DataFrame:
    """Port of the validated backtest OB veto. OB is location-only, not trend."""
    a = atr(df, 14).to_numpy(float)
    ph, pl = _confirmed_pivots(df, pivot_len)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(df)
    buy = np.zeros(n, bool)
    sell = np.zeros(n, bool)
    sh = sl = np.nan
    sh_broken = sl_broken = False
    blo = bhi = np.nan
    bro = -1
    elo = ehi = np.nan
    ero = -1
    for i in range(n):
        if np.isfinite(ph[i]):
            sh = ph[i]
            sh_broken = False
        if np.isfinite(pl[i]):
            sl = pl[i]
            sl_broken = False
        bull_bos = np.isfinite(sh) and not sh_broken and c[i] > sh
        bear_bos = np.isfinite(sl) and not sl_broken and c[i] < sl
        if bull_bos:
            sh_broken = True
            for j in range(1, min(search_bars, i) + 1):
                k = i - j
                if c[k] < o[k]:
                    blo, bhi, bro = l[k], h[k], k
                    break
        if bear_bos:
            sl_broken = True
            for j in range(1, min(search_bars, i) + 1):
                k = i - j
                if c[k] > o[k]:
                    elo, ehi, ero = l[k], h[k], k
                    break
        if np.isfinite(blo) and ((i - bro) > max_age or c[i] < blo):
            blo = bhi = np.nan
            bro = -1
        if np.isfinite(ehi) and ((i - ero) > max_age or c[i] > ehi):
            elo = ehi = np.nan
            ero = -1
        danger = a[i] * danger_atr if np.isfinite(a[i]) else 0.0
        buy[i] = np.isfinite(elo) and c[i] <= ehi and (elo - c[i]) <= danger
        sell[i] = np.isfinite(bhi) and c[i] >= blo and (c[i] - bhi) <= danger
    return pd.DataFrame({"buy_blocked": buy, "sell_blocked": sell}, index=df.index)


def combo_readiness(
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    h6: pd.DataFrame,
    smc_swing_len: int = 50,
    symbol: str | None = None,
) -> dict[int, dict]:
    available = {"15m": len(m15), "1h": len(h1), "4h": len(h4), "6h": len(h6)}
    smc15 = int(smc_swing_len) + 1
    smc1h = int(smc_swing_len) + 1
    requirements = {
        1: {"1h": max(27, smc1h), "4h": 150},
        2: {"1h": max(27, smc1h)},
        3: {"1h": max(27, smc1h)},
        4: {"1h": max(42, smc1h), "6h": 50},
        5: {"15m": max(200, smc15), "4h": 50},
        6: {"1h": max(27, smc1h)},
        7: {"15m": smc15, "4h": 100},
        8: {"15m": smc15, "4h": 50},
        9: {"15m": max(200, smc15)},
        10: {"15m": smc15, "4h": 100},
        11: {"1h": max(50, smc1h), "4h": 200},
    }
    wanted = set(requirements)
    if symbol:
        wanted &= set(enabled_combos(symbol))
    out = {}
    for combo in sorted(wanted):
        req = requirements[combo]
        missing = {
            tf: {"required": need, "available": available[tf]}
            for tf, need in req.items()
            if available[tf] < need
        }
        out[combo] = {
            "ready": not missing,
            "timeframe": "15m" if combo in FIFTEEN_MIN_COMBOS else "1h",
            "requirements": req,
            "available": {tf: available[tf] for tf in req},
            "missing": missing,
        }
    return out


def strategy_static_snapshot() -> dict:
    return {
        **final_config_snapshot(),
        "fifteen_min_combos": sorted(FIFTEEN_MIN_COMBOS),
        "one_hour_combos": sorted(ONE_HOUR_COMBOS),
        "ob": {"pivot_len": 5, "search_bars": 12, "max_age": 80, "danger_atr": 0.5},
        "smc": {"internal_swing_len": 5, "swing_len": 50, "confluence": False},
    }


def _combo15_events(symbol: str, df: pd.DataFrame, h4: pd.DataFrame):
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    a14 = atr(df, 14)
    _, _, adx = dmi(df, 14, 14)
    r14 = rsi(c, 14)
    m14 = mfi(df, 14)
    vol_sma = sma(v, 20)
    body = (c-o).abs()
    rng = h-l
    body_ratio = body / rng.replace(0, np.nan)
    valid = body_ratio > 0.75
    ema50_h4 = _map_htf(df, h4, ema(h4["close"], 50))
    ema100_h4 = _map_htf(df, h4, ema(h4["close"], 100))
    trend_up = c > ema50_h4
    trend_down = c < ema50_h4
    ema150 = ema(c, 150)
    ema200 = ema(c, 200)
    st_dir = combo15_supertrend_dir(df, a14)
    sar = parabolic_sar(df, 0.05, 0.1, 0.2)

    out = {}
    cfgs = FINAL_CASES.get(symbol, {})

    if 5 in cfgs:
        vm = float(cfgs[5]["entry"].get("volume_mult", 1.5))
        vol_spike = v > vol_sma * vm
        adx_strong = (adx > 23) & (adx > adx.shift(1))
        L = (st_dir==1)&(ema150>ema200)&(ema150>ema150.shift(1))&vol_spike&(r14>45)&valid&adx_strong&(m14>55)&(c>sar)&trend_up
        S = (st_dir==-1)&vol_spike&(ema150<ema200)&(ema150<ema150.shift(1))&(r14<55)&valid&adx_strong&(m14<55)&(c<sar)&trend_down
        out[5] = (_event(L), _event(S))

    if 7 in cfgs:
        p = cfgs[7]["entry"]
        bm = float(p.get("body_atr", 1.5))
        vm = float(p.get("volume_mult", 1.5))
        ef, es = ema(c,21), ema(c,55)
        strong_b = (c>o)&((c-o)>a14*bm)
        strong_s = (c<o)&((o-c)>a14*bm)
        vol_surge = v > v.shift(1)*1.8
        vol_spike = v > vol_sma*vm
        adx_r = adx > adx.shift(1)
        L = vol_surge&(ef>es)&(c>ema100_h4)&adx_r&(adx>25)&strong_b&vol_spike&valid&(r14>60)&(r14<85)
        S = vol_surge&(ef<es)&(c<ema100_h4)&adx_r&(adx>25)&strong_s&vol_spike&valid&(r14<40)&(r14>15)
        out[7] = (_event(L), _event(S))

    if 8 in cfgs:
        ath = float(cfgs[8]["entry"].get("adx",25))
        cc = cci(c,10)
        longc=(cc>100)&(cc>cc.shift(1))&(v>vol_sma*0.8)&(v>v.shift(1))
        shortc=(cc<-100)&(cc<cc.shift(1))&(v>vol_sma*0.8)&(v>v.shift(1))
        macd=ema(c,12)-ema(c,26); sig=ema(macd,9); hist=macd-sig
        bull=(c>o)&(c>o.shift(1))&(o<c.shift(1))
        bear=(c<o)&(c<o.shift(1))&(o>c.shift(1))
        L=longc&(m14>70)&(adx>ath)&(adx>adx.shift(1))&bull&trend_up&(hist>0)&valid
        S=shortc&(m14<30)&(adx>ath)&(adx>adx.shift(1))&bear&trend_down&(hist<0)&valid
        out[8]=(_event(L),_event(S))

    if 9 in cfgs:
        p=cfgs[9]["entry"]
        rlong=float(p.get("rsi_long_max",40))
        rshort=float(p.get("rsi_short_min",60))
        mid=nadaraya_mid(c,24,3); nr=stdev(c,24)*2.3
        upper=mid+nr; lower=mid-nr
        L=(c>ema200)&(r14<rlong)&(c<lower)&(st_dir==1)&(body_ratio>0.4)
        short_st=-1 if bool(p.get("fix_short_supertrend",False)) else 1
        S=(c<ema200)&(r14>rshort)&(c>upper)&(st_dir==short_st)&(body_ratio>0.4)
        out[9]=(_event(L),_event(S))

    if 10 in cfgs:
        ath=float(cfgs[10]["entry"].get("adx",20))
        ao=sma(_hl2(df),5)-sma(_hl2(df),34)
        squeeze=ema(c,20)-ema(c,50)
        mean=sma(c,20); sd=stdev(c,20); z=(c-mean)/sd
        upper_w=h-pd.concat([c,o],axis=1).max(axis=1)
        lower_w=pd.concat([c,o],axis=1).min(axis=1)-l
        bull=(lower_w>body*1.5)&(c>o); bear=(upper_w>body*1.5)&(c<o)
        L=(ao>0)&(squeeze>0)&(z<-1.5)&(c>ema100_h4)&(adx>ath)&bull
        S=(ao<0)&(squeeze<0)&(z>1.5)&(c<ema100_h4)&(adx>ath)&bear
        out[10]=(_event(L),_event(S))
    return out


def _combo60_events(symbol: str, df: pd.DataFrame, h4: pd.DataFrame, h6: pd.DataFrame):
    c,h,l,o,v=df["close"],df["high"],df["low"],df["open"],df["volume"]
    a8,a14,a21=atr(df,8),atr(df,14),atr(df,21)
    r14=mfi14=None
    r14=rsi(c,14); mfi14=mfi(df,14)
    dip,dim,adx=dmi(df,14,14)
    adxr=adx>adx.shift(1)
    volma=sma(v,20)
    K=stoch(c,h,l,11); D=sma(K,3); C=cci(c,9)
    ema150_h4=_map_htf(df,h4,ema(h4["close"],150))
    e50h6=_map_htf(df,h6,ema(h6["close"],50))
    out={}; cfgs=FINAL_CASES.get(symbol,{})

    # shared C1
    if 1 in cfgs:
        p=cfgs[1]["entry"]; ml=float(p.get("mfi_long",65)); ms=float(p.get("mfi_short",45))
        macd=ema(c,8)-ema(c,21); sig=ema(macd,9); hist=macd-sig
        longc=(K>30)&(K>D)&(C>80)&(C>C.shift(1)); shortc=(K<70)&(K<D)&(C>-80)&(C<C.shift(1))
        sb=(c>o)&((c-o)>a21*0.8); ss=(c<o)&((o-c)>a21*0.8); vs=v>volma*1.5
        L=longc&(r14>55)&(hist>0)&(mfi14>ml)&adxr&sb&(c>ema150_h4)&vs
        S=shortc&(r14<45)&(hist<0)&(mfi14<ms)&adxr&ss&(c<ema150_h4)&vs
        out[1]=(_event(L),_event(S))

    if 2 in cfgs:
        ath=float(cfgs[2]["entry"].get("adx",20))
        e10,e25=ema(c,10),ema(c,25); macd=ema(c,8)-ema(c,21); sig=ema(macd,9)
        trailing=ut_atr_trailing_stop(c,a14,1.5); above=crossover(c,trailing); below=crossover(trailing,c)
        basis=sma(c,20); dev=1.8*stdev(c,20); ub=basis+dev; lb=basis-dev
        L=(c>trailing)&above&(e10>e25)&(macd>sig)&(adx>ath)&(c>ub)
        S=(c<trailing)&below&(e10<e25)&(macd<sig)&(adx>ath)&(c<lb)
        out[2]=(_event(L),_event(S))

    trend=combo3_custom_trend(df,a8,4.0)
    stdir=pd.Series(np.where(c>trend,1,-1),index=df.index)
    sar=parabolic_sar(df,0.05,0.2,0.3)
    ch=c.diff(); obv=(ch.fillna(0)*v).cumsum()
    if 3 in cfgs:
        ath=float(cfgs[3]["entry"].get("adx",30))
        bull=(c>o)&(c.shift(1)<o.shift(1))&(c>o.shift(1))&(o<c.shift(1))
        bear=(c<o)&(c.shift(1)>o.shift(1))&(c<o.shift(1))&(o>c.shift(1))
        L=(stdir==1)&(adx>ath)&(c>sar)&bull&(obv>obv.shift(1))
        S=(stdir==-1)&(adx>ath)&(c<sar)&bear&(obv<obv.shift(1))
        out[3]=(_event(L),_event(S))

    std20=stdev(c,20); bbu=sma(c,20)+2*std20; bbl=sma(c,20)-2*std20
    ku=sma(c,20)+1.7*atr(df,20); kl=sma(c,20)-1.7*atr(df,20)
    breakout_b=crossover(c,bbu)&(c>ku); breakout_s=crossunder(c,bbl)&(c<kl)
    if 4 in cfgs:
        ath=float(cfgs[4]["entry"].get("adx",23))
        fast,slow=ema(c,21),ema(c,42); sb=(c>o)&((c-o)>a21*0.8); ss=(c<o)&((o-c)>a21*0.8); vs=v>volma*1.5
        L=(fast>slow)&breakout_b&(c>e50h6)&(stdir==1)&sb&vs&(adx>ath)
        S=(fast<slow)&breakout_s&(c<e50h6)&(stdir==-1)&ss&vs&(adx>ath)
        out[4]=(_event(L),_event(S))

    if 6 in cfgs:
        bm=float(cfgs[6]["entry"].get("body_atr",1.2))
        lc=(r14>30)&(K>30)&(K>D)&(C>100)&(C>C.shift(1))
        sc=(r14<70)&(K<70)&(K<D)&(C>-100)&(C<C.shift(1))
        sb=(c>o)&((c-o)>a21*bm); ss=(c<o)&((o-c)>a21*bm); ads=(adx>20)&adxr
        out[6]=(_event(sb&breakout_b&lc&ads),_event(ss&breakout_s&sc&ads))
    return out


def _tier_events(symbol: str, d: pd.DataFrame, h4: pd.DataFrame):
    cfg = FINAL_CASES.get(symbol, {}).get(11)
    if not cfg:
        return None
    p=cfg["entry"]
    adxth=float(p["adx"]); t2=float(p["tier2_score"]); t3=float(p["tier3_score"]); vm=float(p["volume_mult"])
    c,h,l,o,v=d["close"],d["high"],d["low"],d["open"],d["volume"]
    dip,dim,adx=dmi(d,14,14); A=atr(d,20)
    e50=_map_htf(d,h4,ema(h4["close"],50)); e150=_map_htf(d,h4,ema(h4["close"],150)); e200=_map_htf(d,h4,ema(h4["close"],200))
    # session VWAP parity with research: reset at UTC day boundary.
    day=pd.to_datetime(d["open_time"],unit="ms",utc=True).dt.floor("D")
    tp=((h+l+c)/3)*v
    V=tp.groupby(day).cumsum()/v.groupby(day).cumsum().replace(0,np.nan)
    R=rsi(c,14); M=mfi(d,14); vs=v>sma(v,20)*vm
    tdL=(dip>dim)&(dip>22); tdS=(dip<dim)&(dim>22)
    t1L=(e50>e200)&tdL&(c>V)&(e50>e50.shift(1))&vs&(adx>adxth)
    t1S=(e50<e200)&tdS&(c<V)&(e50<e50.shift(1))&vs&(adx>adxth)
    macd=ema(c,12)-ema(c,26); sig=ema(macd,9); hist=macd-sig
    C=cci(c,9); K=stoch(c,h,l,14); D=sma(K,3); e9=ema(c,9); e21=ema(c,21)
    sup=sma(c,8)+2*A; strong=(c-o).abs()>A*0.8
    s2L=(hist>0)*2+(e150>e200)*2+(C>100)*1+(R>50)*1+(c>sup)*1+crossover(K,D)*0.5+(c>e21)*1+(strong&(c>o))*1
    s2S=(hist<0)*2+(e150<e200)*2+(C<-100)*1+(R<50)*1+(c<sup)*1+crossunder(K,D)*0.5+(c<e21)*1+(strong&(c<o))*1
    s2L=s2L+(M>55)*1+(A>A.shift(1))*1+(e9>e21)*1+((C>C.shift(1))&(C>100))*1+((K>K.shift(1))&(K>50))*1
    s2S=s2S+(M<45)*1+(A>A.shift(1))*1+(e9<e21)*1+((C<C.shift(1))&(C<-100))*1+((K<K.shift(1))&(K<50))*1
    sd=stdev(c,20); z=(c-sma(c,20))/sd; ao=ema(c,5)-ema(c,34); sq=ema(c,20)-ema(c,50); rav=sma(R,14)
    trend=combo3_custom_trend(d,atr(d,8),4.0); std=pd.Series(np.where(c>trend,1,-1),index=d.index)
    s3L=(z<-1.5)*1+(ao>0)*1+(sq>0)*1+(R>rav)*1+(e9>e21)*1+(hist>0)*1+(std==1)*1
    s3S=(z>1.5)*1+(ao<0)*1+(sq<0)*1+(R<rav)*1+(e9<e21)*1+(hist<0)*1+(std==-1)*1
    pcL=c>c.rolling(20,min_periods=20).max().shift(1); pcS=c<c.rolling(20,min_periods=20).min().shift(1)
    vsp=v>sma(v,20)*vm; mrL=(M>60)&(M>M.shift(1))&vsp; mrS=(M<40)&(M<M.shift(1))&vsp
    s3L=s3L+pcL*1+mrL*1+(R>50)*1+vsp*1
    s3S=s3S+pcS*1+mrS*1+(R<50)*1+vsp*1
    return _event(t1L&(s2L>=t2)&(s3L>=t3)), _event(t1S&(s2S>=t2)&(s3S>=t3))


def _layer2_approved(mode: str, smc_dir: int, direction: int, blocked: bool) -> bool:
    m=(mode or "OFF").upper()
    if m in {"OB","VETO_OB","STRICT_OB"} and blocked:
        return False
    if m in {"STRICT","STRICT_OB"}:
        return smc_dir == direction
    if m in {"VETO","VETO_OB"}:
        return smc_dir != -direction
    return True


def _signal(symbol: str, combo: int, direction: int, timeframe: str, row: pd.Series, smc_dir: int) -> Signal:
    cfg=get_case(symbol,combo)
    entry=float(row["close"])
    slp=float(cfg["sl_pct"])
    sl=entry*(1-slp if direction==1 else 1+slp)
    # TP is retained only as a compatibility field. FINAL18 execution ignores it
    # and uses two trailing legs with no fixed TP.
    tp=entry*(1+0.05 if direction==1 else 1-0.05)
    return Signal(symbol=symbol,combo=combo,side="BUY" if direction==1 else "SELL",timeframe=timeframe,close_time=int(row["close_time"]),entry=entry,tp=tp,sl=sl,smc_dir=int(smc_dir))


def scan_latest(
    symbol: str,
    m15: pd.DataFrame,
    h1: pd.DataFrame,
    h4: pd.DataFrame,
    h6: pd.DataFrame,
    smc_mode: str = "Veto Only",
    smc_swing_len: int = 50,
    smc_confluence: bool = False,
    sl_pct: float = 0.009,
    tp_pct: float = 0.011,
    include_1h: bool = True,
) -> list[Signal]:
    symbol=symbol.upper()
    signals=[]
    readiness=combo_readiness(m15,h1,h4,h6,smc_swing_len,symbol=symbol)
    smc15=smc_direction(m15,smc_swing_len,smc_confluence); ob15=order_block_context(m15)
    ev15=_combo15_events(symbol,m15,h4)
    if len(m15):
        i=len(m15)-1
        for combo in sorted(set(enabled_combos(symbol)) & FIFTEEN_MIN_COMBOS):
            if combo not in readiness or not readiness[combo]["ready"] or combo not in ev15: continue
            L,S=ev15[combo]; direction=1 if bool(L.iloc[i]) else -1 if bool(S.iloc[i]) else 0
            if not direction: continue
            blocked=bool(ob15.iloc[i]["buy_blocked" if direction==1 else "sell_blocked"])
            cfg=get_case(symbol,combo)
            if _layer2_approved(cfg["layer2"],int(smc15.iloc[i]),direction,blocked):
                signals.append(_signal(symbol,combo,direction,"15m",m15.iloc[i],int(smc15.iloc[i])))

    if include_1h and len(h1):
        smc1=smc_direction(h1,smc_swing_len,smc_confluence); ob1=order_block_context(h1)
        ev1=_combo60_events(symbol,h1,h4,h6)
        tier=_tier_events(symbol,h1,h4)
        if tier is not None: ev1[11]=tier
        i=len(h1)-1
        for combo in sorted(set(enabled_combos(symbol)) & ONE_HOUR_COMBOS):
            if combo not in readiness or not readiness[combo]["ready"] or combo not in ev1: continue
            L,S=ev1[combo]; direction=1 if bool(L.iloc[i]) else -1 if bool(S.iloc[i]) else 0
            if not direction: continue
            blocked=bool(ob1.iloc[i]["buy_blocked" if direction==1 else "sell_blocked"])
            cfg=get_case(symbol,combo)
            if _layer2_approved(cfg["layer2"],int(smc1.iloc[i]),direction,blocked):
                signals.append(_signal(symbol,combo,direction,"1h",h1.iloc[i],int(smc1.iloc[i])))
    return signals
