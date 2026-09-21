from __future__ import annotations

import pandas as pd

from .final14_config import FINAL14_CASES, enabled_combos, get_case, snapshot as final14_snapshot
from .strategy import Signal
from .final14_research.layer3_long import precompute, entry_variants, NATIVE
from .final14_research.signals import ensure_dt, resample_ohlcv
from .final14_research.smc_structure import smc_direction
from .final14_research.smc_ob import ob_context, OBConfig
from .final14_research.two_trail_layer12 import approved

MIN_LIVE_15M_BARS = 12000


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
    """Return locked FINAL14 hard TP/SL from decimal-fraction config values."""
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


def _selected_variants(pc, symbol:str):
    allv=entry_variants(pc)
    selected={}
    for cname,cfg in _symbol_configs(symbol).items():
        wanted=cfg["entry_variant"]
        matches=[x for x in allv[cname] if x[0]==wanted]
        if len(matches)!=1:
            raise RuntimeError(f"FINAL14 variant mismatch {symbol} {cname} {wanted}: {len(matches)}")
        selected[cname]=matches[0]
    return selected


def combo_readiness(
    m15:pd.DataFrame,
    h1:pd.DataFrame|None=None,
    h4:pd.DataFrame|None=None,
    h6:pd.DataFrame|None=None,
    smc_swing_len:int=50,
    symbol:str|None=None,
)->dict[int,dict]:
    symbol=(symbol or "").upper()
    count=int(len(m15))
    ready=count>=MIN_LIVE_15M_BARS
    return {
        combo:{
            "ready":ready,
            "timeframe":"15m" if NATIVE[_combo_name(combo)]=="15m" else "1h",
            "requirements":{"15m":MIN_LIVE_15M_BARS},
            "available":{"15m":count},
            "missing":{} if ready else {"15m":{"required":MIN_LIVE_15M_BARS,"available":count}},
        }
        for combo in enabled_combos(symbol)
    }


def strategy_static_snapshot()->dict:
    return {
        **final14_snapshot(),
        "signal_engine":"vendored_exact_research",
        "min_live_15m_bars":MIN_LIVE_15M_BARS,
        "native_timeframes":{
            _combo_name(k):NATIVE[_combo_name(k)]
            for s in FINAL14_CASES.values() for k in s
        },
        "smc":{"swing_len":50,"confluence":False,"same_bar":True},
        "ob":{"pivot_len":5,"search_bars":12,"max_age":80,"danger_atr":0.5},
    }


def scan_latest(
    symbol:str,
    m15:pd.DataFrame,
    h1:pd.DataFrame|None=None,
    h4:pd.DataFrame|None=None,
    h6:pd.DataFrame|None=None,
    smc_mode:str="Veto Only",
    smc_swing_len:int=50,
    smc_confluence:bool=False,
    sl_pct:float=0.009,
    tp_pct:float=0.011,
    include_1h:bool=True,
)->list[Signal]:
    symbol=symbol.upper()
    if len(m15)<MIN_LIVE_15M_BARS:
        return []

    d=_research_frame(m15)
    pc=precompute(d)
    chosen=_selected_variants(pc,symbol)

    d1=resample_ohlcv(d,"1h")
    smc15=smc_direction(d,50,False)
    smc1=smc_direction(d1,50,False)
    obcfg=OBConfig(pivot_len=5,search_bars=12,max_age=80,danger_atr=0.5)
    ob15=ob_context(d,obcfg)
    ob1=ob_context(d1,obcfg)

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
            if not include_1h:
                continue
            ot=latest-pd.Timedelta("45min")
            # 1H signals are actionable only on the final 15m candle of the hour.
            if latest.minute%60!=45 or ot not in L.index:
                continue
            sd=int(smc1.loc[ot]) if ot in smc1.index else 0
            obrow=ob1.loc[ot] if ot in ob1.index else None
        else:
            ot=latest
            if ot not in L.index:
                continue
            sd=int(smc15.loc[ot]) if ot in smc15.index else 0
            obrow=ob15.loc[ot] if ot in ob15.index else None

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

        # Config values are decimal fractions (0.02 == 2%).
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
    return out
