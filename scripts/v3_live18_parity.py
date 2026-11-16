"""Signal parity: a live L2 registry (default V3_LIVE_20_POSITIVE_FIXED_V1) vs a lab signals.csv.

Used for V3_LIVE_18_H1_V2 vs V3_18_COMBO_H1_BASELINE_V1 and for V3_LIVE_20_POSITIVE_FIXED_V1 vs
audit AUDIT_20_POSITIVE_FIXED_TPSL_20261116 (lab rows outside the registry are dropped first).

Offline only (no BingX, no DB). Inputs are the Owner's local lab artifacts:
  --m15-btc / --m15-eth : closed M15 candles (parquet, UTC DatetimeIndex) built by the lab
                          DataLayer from data/m1_raw (same frames the baseline used)
  --lab-signals         : runs/v3_18_baseline_v2/RUN_..._ALL_SIGNALS_.../signals.csv
Modes:
  batch   : one call over the full history (algorithm parity, same as the lab batch).
  rolling : the exact live call (V18Engine.calculate on the newest 3400 bars) at EVERY
            closed 15m bar (live semantics incl. 3400-bar window truncation).
Compares symbol, combo, side, signal close timestamp, entry/TP/SL price and duplicates.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import multiprocessing as mp
from pathlib import Path

import pandas as pd

from autotrade_v3.live.strategy_registry import REGISTRY_V20, load_registry
from autotrade_v3.live.strategy_v18 import V18Engine

WINDOW = 3400
KEY = ["symbol", "combo_id", "side", "signal_close_ms"]


def _rows(sigs) -> list[dict]:
    return [{"symbol": s.symbol, "combo_id": s.combo, "side": s.side, "signal_close_ms": s.close_time,
             "timeframe": s.timeframe, "entry": s.entry, "tp": s.tp, "sl": s.sl, "event_id": s.event_id,
             "spec_hash": s.spec_hash, "registry_version": s.registry_version} for s in sigs]


REGISTRY = REGISTRY_V20


def batch(paths: dict[str, str]) -> pd.DataFrame:
    eng = V18Engine(load_registry(REGISTRY))
    out = []
    for sym, p in paths.items():
        d = pd.read_parquet(p)
        first_ok = int(d.index[WINDOW - 1].value // 1_000_000)   # lab warmup: bar_position >= 3399
        out += [r for r in _rows(eng.frame_signals(sym, d, latest_only=False)) if r["signal_close_ms"] - 899_999 >= first_ok]
    return pd.DataFrame(out)


def _rolling_chunk(args) -> list[dict]:
    sym, path, lo, hi, version = args
    reg = load_registry(version)
    full = V18Engine(reg)
    # 1H combos can only fire on the bar closing at HH:59; other bars only need the 15m combos.
    m15_only = V18Engine(dataclasses.replace(reg, combos=tuple(c for c in reg.combos if c.native_timeframe == "15m")))
    d = pd.read_parquet(path)
    out: list[dict] = []
    for i in range(lo, hi):
        w = d.iloc[i - WINDOW + 1:i + 1].copy()
        w["open_time"] = w.index.asi8 // 1_000_000
        w["close_time"] = w["open_time"] + 899_999
        w = w.reset_index(drop=True)
        eng = full if d.index[i].minute == 45 else m15_only
        out += _rows(eng.calculate(sym, w))
    return out


def rolling(paths: dict[str, str], workers: int) -> pd.DataFrame:
    tasks = []
    for sym, p in paths.items():
        n = len(pd.read_parquet(p))
        tasks += [(sym, p, lo, min(lo + 2000, n), REGISTRY) for lo in range(WINDOW - 1, n, 2000)]
    out: list[dict] = []
    with mp.Pool(workers) as pool:
        for part in pool.imap_unordered(_rolling_chunk, tasks):
            out += part
    return pd.DataFrame(out)


def compare(lab: pd.DataFrame, live: pd.DataFrame) -> dict:
    m = lab.merge(live, on=KEY, how="outer", indicator=True)
    both = m[m["_merge"] == "both"]
    per = []
    for (sym, cid), g in m.groupby(["symbol", "combo_id"]):
        per.append({"symbol": sym, "combo_id": int(cid), "lab": int((g["_merge"] != "right_only").sum()),
                    "live": int((g["_merge"] != "left_only").sum()), "matched": int((g["_merge"] == "both").sum()),
                    "lab_only": int((g["_merge"] == "left_only").sum()), "live_only": int((g["_merge"] == "right_only").sum())})
    return {
        "lab_signals": int(len(lab)), "live_signals": int(len(live)), "matched": int(len(both)),
        "lab_only": int((m["_merge"] == "left_only").sum()), "live_only": int((m["_merge"] == "right_only").sum()),
        "max_abs_entry_diff": float((both["entry"] - both["entry_price"]).abs().max() or 0.0),
        "max_abs_tp_diff": float((both["tp"] - both["tp_price"]).abs().max() or 0.0),
        "max_abs_sl_diff": float((both["sl"] - both["sl_price"]).abs().max() or 0.0),
        "timeframe_mismatch": int((both["timeframe_x"] != both["timeframe_y"]).sum()) if "timeframe_x" in both else 0,
        "live_duplicates": int(live.duplicated(KEY).sum()), "lab_duplicates": int(lab.duplicated(KEY).sum()),
        "per_combo": per,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--m15-btc", required=True)
    ap.add_argument("--m15-eth", required=True)
    ap.add_argument("--lab-signals", required=True)
    ap.add_argument("--mode", choices=["batch", "rolling"], default="batch")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", required=True)
    ap.add_argument("--registry", default=REGISTRY_V20)
    a = ap.parse_args()
    global REGISTRY
    REGISTRY = a.registry.upper()
    paths = {"BTC-USDT": a.m15_btc, "ETH-USDT": a.m15_eth}
    lab = pd.read_csv(a.lab_signals)
    allowed = load_registry(REGISTRY).allowed()
    lab = lab[[(s, int(c)) in allowed for s, c in zip(lab["symbol"], lab["combo_id"])]].reset_index(drop=True)
    live = batch(paths) if a.mode == "batch" else rolling(paths, a.workers)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    live.sort_values(KEY).to_csv(out / f"live_signals_{a.mode}.csv", index=False)
    rep = {"mode": a.mode, "lab_signals_file": Path(a.lab_signals).name, "registry": load_registry(REGISTRY).summary(),
           **compare(lab, live)}
    (out / f"parity_{a.mode}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("per_combo", "registry")}, indent=1))


if __name__ == "__main__":
    main()
