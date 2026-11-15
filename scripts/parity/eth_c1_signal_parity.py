"""Signal-level parity: live L2 ETH C1 path (12000-bar window) vs Lab-style full-history generator, same bars."""
import os, sys, json, time
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from autotrade_v3.live.l2_strategy import L2Strategy
from autotrade_v3.live.lab_h1.eth_c1_h1 import EthC1H1Both1Strategy, compute_eth_c1_l2_features, M15_MS
from autotrade_v3.live.tests.fakes import MemoryStore
from autotrade_v2.app.contracts import MarketSnapshot

N_RANDOM = int(sys.argv[1]) if len(sys.argv) > 1 else 40
BARS = 12_000
raw = pd.read_csv(os.environ["ETH_M15_CSV"])
raw["open_time"] = raw["open_ms"].astype("int64")
raw["close_time"] = raw["open_time"] + M15_MS - 1
df = raw[["open_time", "close_time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

t0 = time.time()
gen = EthC1H1Both1Strategy(enable_eth_c1_h1_l2=True)
feats = compute_eth_c1_l2_features(df)
ref = gen._generate_eth_c1_side(feats, side="BUY") + gen._generate_eth_c1_side(feats, side="SELL")
ref_set = {(int(s.signal_close_ms), s.side) for s in ref}
ref_rej = {(int(r["signal_close_ms"]), r["side"]) for r in gen.last_l2_rejections}
print("ref signals", len(ref_set), "ref H1 rejections", len(ref_rej), round(time.time()-t0,1), "s", flush=True)

ref_closes = sorted({c for c, _ in ref_set})
start_ms = int(pd.Timestamp("2023-11-01", tz="UTC").value // 1_000_000)
ref_closes_recent = [c for c in ref_closes if c >= start_ms]
rng = np.random.default_rng(11)
pool = [int(t) + M15_MS - 1 for t in df.open_time.values[(df.open_time.values >= start_ms) & (df.open_time.values % 3_600_000 == 2_700_000)]]
rand = list(rng.choice(pool, size=min(N_RANDOM, len(pool)), replace=False))
rej_closes = sorted({c for c, _ in ref_rej if c >= start_ms})
rej_pick = [int(x) for x in rng.choice(rej_closes, size=min(60, len(rej_closes)), replace=False)] if rej_closes else []
samples = sorted(set(ref_closes_recent[:60]) | {int(x) for x in rand} | set(rej_pick))
print("samples", len(samples), "ref-signal bars", len(set(ref_closes_recent[:60])), flush=True)

pos_of = {int(t): i for i, t in enumerate(df.open_time.values)}
l2 = L2Strategy(MemoryStore(), control=None, eth_c1_h1_enabled=True)
rows = []
for close_ms in samples:
    last_open = close_ms - M15_MS + 1
    p = pos_of[last_open]
    win = df.iloc[p - BARS + 1:p + 1].reset_index(drop=True)
    snap = MarketSnapshot("ETH-USDT", "15m", win.tail(3400).reset_index(drop=True), int(win.open_time.iloc[-1]), int(win.close_time.iloc[-1]), {})
    out = l2._eth_c1_latest(snap, win)
    live = {s.side for s in out}
    for side in ("BUY", "SELL"):
        rows.append({"close_ms": close_ms, "side": side, "ref": (close_ms, side) in ref_set,
                     "ref_rejected_h1": (close_ms, side) in ref_rej, "live": side in live})

res = pd.DataFrame(rows)
mismatch = res[res.ref != res.live]
summary = {"rejected_h1_pairs": int(res.ref_rejected_h1.sum()), "pairs": int(len(res)), "ref_true": int(res.ref.sum()), "live_true": int(res.live.sum()),
           "mismatch": int(len(mismatch)), "agree_rate": float((res.ref == res.live).mean()),
           "seconds": round(time.time() - t0, 1)}
res.to_csv(os.environ.get("PARITY_OUT", "parity_signals.csv"), index=False)
print(json.dumps(summary, indent=1))
print(mismatch.head(10).to_string() if len(mismatch) else "no mismatches")
