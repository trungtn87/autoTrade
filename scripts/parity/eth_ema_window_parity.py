"""Parity check: live-style M15 window (3400 / 12000) vs full-history reference, ETH M15 RUN17 CSV."""
import os, sys, json, time
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from autotrade_v3.live.lab_h1.eth_c1_h1 import compute_eth_c1_l2_features

CSV = os.environ["ETH_M15_CSV"]
N_SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 120
WINDOWS = (3400, 12000)
M15 = 900_000

raw = pd.read_csv(CSV)
raw["open_time"] = raw["open_ms"].astype("int64")
raw["close_time"] = raw["open_time"] + M15 - 1
df = raw[["open_time", "close_time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
assert (df.open_time.diff().dropna() == M15).all(), "gaps in M15 CSV"

t0 = time.time()
ref = compute_eth_c1_l2_features(df)
d1 = ref["d1"]
S_ref = ref["slope_10bar_1h"]; D_ref = ref["dist_ema150_4h_pct"]
print("ref features", round(time.time() - t0, 1), "s, H1 bars", len(d1), flush=True)

pos_of = {int(t): i for i, t in enumerate(df.open_time.values)}
start = pd.Timestamp("2023-11-01", tz="UTC")
cand = [ot for ot in d1.index if ot >= start and ot + pd.Timedelta(hours=1) <= pd.Timestamp(int(df.open_time.iloc[-1]), unit="ms", tz="UTC") and np.isfinite(S_ref.loc[ot]) and np.isfinite(D_ref.loc[ot])]
rng = np.random.default_rng(7)
near = [ot for ot in cand if abs(S_ref.loc[ot] - 1.5) < 0.05 or abs(D_ref.loc[ot] + 6.0) < 0.05]
rand = list(rng.choice(len(cand), size=min(N_SAMPLES, len(cand)), replace=False))
sample = {cand[i] for i in rand} | set(near[:60])
sample = sorted(sample)
print("samples", len(sample), "near-threshold", len(near), flush=True)

rows = []
for ot in sample:
    end_ms = int(ot.value // 1_000_000) + 45 * 60_000
    p = pos_of[end_ms]
    rec = {"ot": str(ot), "S_ref": float(S_ref.loc[ot]), "D_ref": float(D_ref.loc[ot])}
    for W in WINDOWS:
        if p - W + 1 < 0:
            rec[f"S_{W}"] = None; rec[f"D_{W}"] = None
            continue
        win = df.iloc[p - W + 1:p + 1].reset_index(drop=True)
        f = compute_eth_c1_l2_features(win)
        rec[f"S_{W}"] = float(f["slope_10bar_1h"].loc[ot]); rec[f"D_{W}"] = float(f["dist_ema150_4h_pct"].loc[ot])
    rows.append(rec)

out = pd.DataFrame(rows)
summary = {}
for W in WINDOWS:
    s = out.dropna(subset=[f"S_{W}"])
    if s.empty:
        summary[W] = "no data"; continue
    buy_flip = ((s.S_ref <= 1.5) != (s[f"S_{W}"] <= 1.5)).sum()
    sell_flip = ((s.D_ref >= -6.0) != (s[f"D_{W}"] >= -6.0)).sum()
    summary[W] = {
        "n": int(len(s)),
        "max_abs_dslope": float((s.S_ref - s[f"S_{W}"]).abs().max()),
        "max_abs_ddist": float((s.D_ref - s[f"D_{W}"]).abs().max()),
        "median_abs_ddist": float((s.D_ref - s[f"D_{W}"]).abs().median()),
        "buy_flips": int(buy_flip), "sell_flips": int(sell_flip),
    }
out.to_csv(os.environ.get("PARITY_OUT", "parity_samples.csv"), index=False)
print(json.dumps(summary, indent=1))
