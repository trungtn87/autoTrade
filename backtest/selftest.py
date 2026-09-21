import numpy as np,pandas as pd
from signals import build_all_signals,resample_ohlcv
from backtest import backtest_parallel
from smc_ob import ob_context,OBConfig
rng=np.random.default_rng(42); idx=pd.date_range('2023-01-01',periods=5000,freq='15min',tz='UTC')
c=20000+np.cumsum(rng.normal(0,30,len(idx))); o=np.r_[c[0],c[:-1]]
h=np.maximum(o,c)+rng.uniform(0,20,len(idx)); l=np.minimum(o,c)-rng.uniform(0,20,len(idx)); v=rng.lognormal(5,1,len(idx))
d=pd.DataFrame({'open':o,'high':h,'low':l,'close':c,'volume':v},index=idx)
s=build_all_signals(d)
ob15=ob_context(d,OBConfig()); ob1=ob_context(resample_ohlcv(d,'1h'),OBConfig())
a=backtest_parallel(d,s); b=backtest_parallel(d,s,ob15=ob15,ob1h=ob1)
print('RAW',a['stats']); print('OB',b['stats']); print('signal counts',sum(int(s[k][c].sum()) for k in ['15m','1h','tier'] for c in s[k].columns if c.endswith(('_L','_S'))))
