from __future__ import annotations
import numpy as np
import pandas as pd

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
