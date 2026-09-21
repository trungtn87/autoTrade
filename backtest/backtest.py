from __future__ import annotations
from dataclasses import dataclass,asdict
import numpy as np
import pandas as pd

@dataclass
class Trade:
    combo:str; side:str; entry_time:pd.Timestamp; exit_time:pd.Timestamp|None; entry:float; exit:float|None
    tp:float; sl:float; qty:float; margin:float; leverage:float; notional:float
    pnl:float|None=None; gross_pnl:float|None=None; fees:float|None=None; reason:str|None=None

ATR_RISK={'C1','C3','C6'}
TF1H={'C1','C2','C3','C4','C6','TIER'}
ALL=['C1','C2','C3','C4','C5','C6','C7','C8','C9','C10','TIER']

def _tv_exit(row,side,tp,sl,policy='tv_heuristic'):
    h,l,o=float(row.high),float(row.low),float(row.open)
    hit_tp=(h>=tp) if side=='L' else (l<=tp)
    hit_sl=(l<=sl) if side=='L' else (h>=sl)
    if not(hit_tp or hit_sl): return None,None
    if hit_tp and not hit_sl:return tp,'TP'
    if hit_sl and not hit_tp:return sl,'SL'
    if policy=='stop_first':return sl,'SL'
    if policy=='tp_first':return tp,'TP'
    high_first=abs(o-h)<abs(o-l)
    if side=='L': return (tp,'TP') if high_first else (sl,'SL')
    return (sl,'SL') if high_first else (tp,'TP')

def backtest_parallel(df15:pd.DataFrame, signals:dict, enabled=None, sl_pct=.009,tp_pct=.011,
                      initial_equity=1000.0, margin_per_trade=1.0, leverage=100.0,
                      commission_pct=0.05, policy='tv_heuristic',
                      ob15:pd.DataFrame|None=None, ob1h:pd.DataFrame|None=None):
    if initial_equity<=0 or margin_per_trade<=0 or leverage<=0:
        raise ValueError('initial_equity, margin_per_trade and leverage must be > 0')
    if commission_pct<0:
        raise ValueError('commission_pct must be >= 0')

    enabled=set(enabled or ALL)
    d=df15.copy().sort_index(); sig15=signals['15m']; sig1=signals['1h']; tier=signals['tier']
    events={t:[] for t in d.index}
    for combo in ['C5','C7','C8','C9','C10']:
        for side in ['L','S']:
            col=f'{combo}_{side}'
            for t in sig15.index[sig15[col].fillna(False)]: events.setdefault(t,[]).append((combo,side,t))
    for combo in ['C1','C2','C3','C4','C6']:
        for side in ['L','S']:
            col=f'{combo}_{side}'
            for ot in sig1.index[sig1[col].fillna(False)]:
                ct=ot+pd.Timedelta('1h')-pd.Timedelta('15m')
                events.setdefault(ct,[]).append((combo,side,ot))
    for side in ['L','S']:
        for ot in tier.index[tier[f'TIER_{side}'].fillna(False)]:
            ct=ot+pd.Timedelta('1h')-pd.Timedelta('15m')
            events.setdefault(ct,[]).append(('TIER',side,ot))

    active={}; trades=[]; equity=float(initial_equity); curve=[]; veto=[]; skipped_margin=0
    fixed_notional=float(margin_per_trade)*float(leverage)

    for t,row in d.iterrows():
        for combo,tr in list(active.items()):
            px,why=_tv_exit(row,tr.side,tr.tp,tr.sl,policy)
            if px is not None:
                gross=(px-tr.entry)*tr.qty*(1 if tr.side=='L' else -1)
                entry_fee=tr.entry*tr.qty*commission_pct/100.0
                exit_fee=px*tr.qty*commission_pct/100.0
                fees=entry_fee+exit_fee
                net=gross-fees
                tr.exit_time=t; tr.exit=px; tr.gross_pnl=gross; tr.fees=fees
                tr.pnl=net; tr.reason=why
                equity+=net; trades.append(tr); del active[combo]

        for combo,side,native_ot in events.get(t,[]):
            if combo not in enabled or combo in active: continue
            blocked=False
            if combo in TF1H and ob1h is not None and native_ot in ob1h.index:
                blocked=bool(ob1h.loc[native_ot,'buy_blocked' if side=='L' else 'sell_blocked'])
            elif combo not in TF1H and ob15 is not None and t in ob15.index:
                blocked=bool(ob15.loc[t,'buy_blocked' if side=='L' else 'sell_blocked'])
            if blocked:
                veto.append({'time':t,'combo':combo,'side':side}); continue

            entry=float(row.close)
            entry_fee=fixed_notional*commission_pct/100.0
            if equity < margin_per_trade + entry_fee:
                skipped_margin+=1
                continue

            native_atr=float(sig1.loc[native_ot,'ATR21']) if combo in TF1H and native_ot in sig1.index else float(sig15.loc[t,'ATR21'])
            if combo in ATR_RISK:
                if side=='L': tp=entry+native_atr*1.6; sl=entry-native_atr*1.4
                else: tp=entry-native_atr*1.6; sl=entry+native_atr*1.4
            else:
                if side=='L': tp=entry*(1+tp_pct); sl=entry*(1-sl_pct)
                else: tp=entry*(1-tp_pct); sl=entry*(1+sl_pct)

            qty=fixed_notional/entry
            active[combo]=Trade(combo,side,t,None,entry,None,tp,sl,qty,
                                float(margin_per_trade),float(leverage),fixed_notional)
        curve.append((t,equity))

    tdf=pd.DataFrame([asdict(x) for x in trades])
    ec=pd.DataFrame(curve,columns=['time','equity']).set_index('time')
    if len(ec):
        peak=ec.equity.cummax(); dd=peak-ec.equity; maxdd=float(dd.max())
    else:
        maxdd=0.0

    common={'initial_equity':float(initial_equity),'margin_per_trade':float(margin_per_trade),
            'leverage':float(leverage),'notional_per_trade':fixed_notional,
            'commission_pct_per_side':float(commission_pct),'skipped_margin':int(skipped_margin)}
    if tdf.empty:
        stats={**common,'trades':0,'wins':0,'losses':0,'win_rate':np.nan,'net_pnl':0.0,'gross_pnl':0.0,
               'fees':0.0,'profit_factor':np.nan,'max_drawdown':maxdd,'ending_equity':equity,'vetoes':len(veto)}
    else:
        gp=float(tdf.loc[tdf.pnl>0,'pnl'].sum()); gl=float(-tdf.loc[tdf.pnl<0,'pnl'].sum())
        wins=int((tdf.pnl>0).sum()); losses=int((tdf.pnl<0).sum())
        stats={**common,'trades':len(tdf),'wins':wins,'losses':losses,'win_rate':wins/len(tdf)*100,
               'net_pnl':float(tdf.pnl.sum()),'gross_pnl':float(tdf.gross_pnl.sum()),'fees':float(tdf.fees.sum()),
               'profit_factor':gp/gl if gl else np.inf,'max_drawdown':maxdd,'ending_equity':equity,'vetoes':len(veto)}
    return {'stats':stats,'trades':tdf,'equity':ec,'vetoes':pd.DataFrame(veto),'open_trades':active}
