import sys, json
repo = sys.argv[1]; out = sys.argv[2]  # usage: python baseline_20final_regression.py <repo_root> <out.json>
sys.path.insert(0, repo)
import numpy as np, pandas as pd
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
rng = np.random.default_rng(20261119)
def make(n, start, p0):
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC").as_unit("ns")
    r = rng.normal(0, 0.0025, n)
    c = p0 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0012, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0012, n)))
    v = rng.lognormal(3, 0.5, n)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)
data = {"BTC-USDT": make(20000, "2024-01-01", 60000.0), "ETH-USDT": make(20000, "2024-01-01", 3000.0)}
strat = Baseline20FinalStrategy()
sigs = strat.generate_signals(data, symbols=["BTC-USDT", "ETH-USDT"], only_approved=False, only_warmup_ready=False)
rows = []
for s in sigs:
    rows.append([s.symbol, int(s.combo_id), s.side, int(s.signal_close_ms), round(float(s.entry_price), 8),
                 round(float(s.tp_pct), 10), round(float(s.sl_pct), 10), round(float(s.tp_price), 6),
                 round(float(s.sl_price), 6), bool(s.approved)])
rows.sort()
json.dump({"n": len(rows), "rows": rows}, open(out, "w"))
print("n", len(rows))
