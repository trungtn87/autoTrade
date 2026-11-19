#!/usr/bin/env python3
"""End-to-end verification for V3_LAB_4@4.1.1 (Option A: BTC/C13 H4 + ETH/C14 A-R2-05)."""

from __future__ import annotations

import hashlib
import json
import pickle
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\AutoTrade")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from autotrade_v3.lab import (
    V3_LAB_4_ACTIVE_VERSION,
    V3_LAB_4_STRATEGY_ID,
    load_v3_lab_4_config,
    load_v3_lab_4_strategy,
)
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import RiskConfig, UnifiedSimulator
from round2_engine import Round2ResearchSimulator

R1_CACHE = ROOT / "runs" / "lab" / "v3_lab_4" / "weakest10_18variants_20261119_004500" / "cache" / "weakest10_immutable_cache_v1.pkl"
SEL_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "final_strategy_selection_20261119_103500"
OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_4" / "v4_1_1_integration_verification"

EVAL_START_MS = int(pd.Timestamp("2023-11-01 00:00:00", tz="UTC").timestamp() * 1000)
EVAL_END_MS = int(pd.Timestamp("2026-11-01 00:00:00", tz="UTC").timestamp() * 1000)
PROFILES = ["IDEAL", "BASE", "STRESS"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run_deterministic_child(out_pq: Path) -> None:
    with R1_CACHE.open("rb") as f:
        cache = pickle.load(f)
    strat = load_v3_lab_4_strategy()
    assert strat.version == "4.1.1"
    sigs = [s for s in strat.generate_signals(cache["m15_dict"]) if EVAL_START_MS <= int(s.signal_close_ms) < EVAL_END_MS]
    ctrl = strat.build_c15_h3_controller(cache["m15_dict"]["ETH-USDT"])
    cm = CostModel.from_profile_name("BASE", is_real_data=True)
    risk = RiskConfig(initial_equity=1000.0, notional_per_trade=100.0, cost_profile="BASE", cost_model=cm)
    sim = Round2ResearchSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE", c15_controller=ctrl)
    res = sim.run_with_arrays(mode="BINGX_PORTFOLIO", signals=sigs, arrays_by_sym=cache["m1_arrays_eval"])
    pd.DataFrame([asdict(t) for t in res.trades]).to_parquet(out_pq, index=False)


def main() -> None:
    if "--child" in sys.argv:
        i = sys.argv.index("--child")
        run_deterministic_child(Path(sys.argv[i + 1]))
        return

    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Verify all 4 versions rollback & config hashes
    rollback_doc = {"active_strategy_id": V3_LAB_4_STRATEGY_ID, "active_version": V3_LAB_4_ACTIVE_VERSION, "versions": {}}
    for ver in ("4.0.0", "4.0.1", "4.1.0", "4.1.1"):
        cfg = load_v3_lab_4_config(version=ver)
        st = load_v3_lab_4_strategy(version=ver)
        rollback_doc["versions"][ver] = {
            "config_hash": cfg.get("config_hash"),
            "signal_spec_hash": st.get_signal_spec_hash(),
            "spec_hash": st.get_spec_hash(),
            "btc_c13": cfg["upgraded_combos"].get("BTC-USDT/C13", "H0"),
            "eth_c14": cfg["upgraded_combos"].get("ETH-USDT/C14", "H0"),
        }
    (OUT_DIR / "ROLLBACK_HASHES.json").write_text(json.dumps(rollback_doc, indent=2), encoding="utf-8")

    # 2. Load immutable cache and run official V3_LAB_4@4.1.1 BINGX_PORTFOLIO (IDEAL, BASE, STRESS)
    with R1_CACHE.open("rb") as f:
        cache = pickle.load(f)
    m15_dict = cache["m15_dict"]
    m1_arrays = cache["m1_arrays_eval"]

    strat411 = load_v3_lab_4_strategy()
    assert strat411.version == "4.1.1"
    sigs411 = [s for s in strat411.generate_signals(m15_dict) if EVAL_START_MS <= int(s.signal_close_ms) < EVAL_END_MS]

    port_rows = []
    parity_rows = []
    for prof in PROFILES:
        ctrl = strat411.build_c15_h3_controller(m15_dict["ETH-USDT"])
        cm = CostModel.from_profile_name(prof, is_real_data=True)
        risk = RiskConfig(initial_equity=1000.0, notional_per_trade=100.0, cost_profile=prof, cost_model=cm)
        sim = Round2ResearchSimulator(risk_config=risk, cost_model=cm, ambiguity_policy="CONSERVATIVE", c15_controller=ctrl)
        res = sim.run_with_arrays(mode="BINGX_PORTFOLIO", signals=sigs411, arrays_by_sym=m1_arrays)

        df = pd.DataFrame([asdict(t) for t in res.trades]).sort_values(["entry_time_ms", "symbol", "combo_id", "side"]).reset_index(drop=True)
        net_pnl = float(df["net_pnl"].sum())
        m2m_dd = float(res.summary_dict.get("m2m_max_drawdown_dollars", 0.0))

        # Compare trade-by-trade vs P2 in final_strategy_selection_20261119_103500
        p2_df = pd.read_parquet(SEL_DIR / "ledgers" / f"P2__{prof}.parquet").sort_values(["entry_time_ms", "symbol", "combo_id", "side"]).reset_index(drop=True)
        assert len(df) == len(p2_df)
        max_diff = float(np.max(np.abs(df["net_pnl"].values - p2_df["net_pnl"].values)))
        assert max_diff < 1e-10

        port_rows.append({
            "version": "4.1.1",
            "cost_profile": prof,
            "trades": len(df),
            "net_pnl": round(net_pnl, 4),
            "m2m_max_dd_usd": round(m2m_dd, 4),
            "max_abs_diff_vs_p2_audit": max_diff,
            "status": "PASS",
        })

        # Individual combo parity for BTC-C13 (H4) and ETH-C14 (A-R2-05)
        for sym, cid, lbl in [("BTC-USDT", 103, "H4_SELL_ONLY_4H_TREND"), ("ETH-USDT", 104, "A-R2-05_ATR_SL_ADJUSTMENT")]:
            sub_411 = df[(df["symbol"] == sym) & (df["combo_id"] == cid)].reset_index(drop=True)
            sub_p2 = p2_df[(p2_df["symbol"] == sym) & (p2_df["combo_id"] == cid)].reset_index(drop=True)
            c_diff = float(np.max(np.abs(sub_411["pnl_own_entry"].values - sub_p2["pnl_own_entry"].values)))
            assert c_diff < 1e-10
            parity_rows.append({
                "combo": f"{sym}/C{cid-100}",
                "strategy": lbl,
                "cost_profile": prof,
                "trades": len(sub_411),
                "own_net_pnl": round(float(sub_411["pnl_own_entry"].sum()), 4),
                "max_abs_diff_vs_p2": c_diff,
                "status": "PASS",
            })

    pd.DataFrame(port_rows).to_csv(OUT_DIR / "PORTFOLIO_4_1_1.csv", index=False)
    pd.DataFrame(parity_rows).to_csv(OUT_DIR / "TRADE_PARITY_4_1_1.csv", index=False)

    # 3. Deterministic replay (2 clean subprocesses)
    pq1 = OUT_DIR / "_replay1.parquet"
    pq2 = OUT_DIR / "_replay2.parquet"
    subprocess.run([sys.executable, str(__file__), "--child", str(pq1)], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(__file__), "--child", str(pq2)], cwd=ROOT, check=True)
    h1, h2 = sha256_file(pq1), sha256_file(pq2)
    df1, df2 = pd.read_parquet(pq1), pd.read_parquet(pq2)
    r_diff = float(np.max(np.abs(df1["net_pnl"].values - df2["net_pnl"].values)))
    replay_doc = {
        "trades": len(df1),
        "run1_sha256": h1,
        "run2_sha256": h2,
        "exact_sha256_match": h1 == h2,
        "max_abs_pnl_diff": r_diff,
        "status": "PASS" if (h1 == h2 and r_diff == 0.0) else "FAIL",
    }
    (OUT_DIR / "DETERMINISTIC_REPLAY_4_1_1.json").write_text(json.dumps(replay_doc, indent=2), encoding="utf-8")
    pq1.unlink(missing_ok=True)
    pq2.unlink(missing_ok=True)

    port_md = "\n".join(
        f"| `V3_LAB_4@{r['version']}` | `{r['cost_profile']}` | `{r['trades']}` | **`${r['net_pnl']:+.4f}`** | `${r['m2m_max_dd_usd']:.4f}` | `{r['max_abs_diff_vs_p2_audit']:.1e}` | `{r['status']}` |"
        for r in port_rows
    )
    rb_md = "\n".join(
        f"| `{v}` | `{d['btc_c13']}` | `{d['eth_c14']}` | `{d['config_hash'][:16]}...` | `{d['signal_spec_hash'][:16]}...` |"
        for v, d in rollback_doc["versions"].items()
    )

    report_md = f"""# V3_LAB_4@4.1.1 INTEGRATION REPORT (OPTION A)

- **Trạng thái:** **`READY_TO_MERGE`** *(trên nhánh `lab/v3-lab-4-r2-integration`; chưa merge vào `v3_lab` / `main`)*
- **Active Default:** `V3_LAB_4@4.1.1` (`config_hash = {rollback_doc['versions']['4.1.1']['config_hash']}`)
- **Cảnh báo `ETH-USDT/C14` (`A-R2-05_ATR_SL_ADJUSTMENT`)**: Giữ nguyên **`AUDIT_VERIFIED_BUT_FRAGILE`**, `live_eligible = False`, `HOLDOUT_6M (-$4.3392 BASE)` và `Ex-Top2 (-$0.9697 BASE)` âm — không `LIVE_READY`.

## 1. Kết Quả `BINGX_PORTFOLIO` Từ Đường Thực Thi Chính Thức (`4.1.1`)

| Version | Profile | Trades | Net PnL | M2M MaxDD | Diff vs Audit P2 | Status |
|---|---|---:|---:|---:|---:|---|
{port_md}

## 2. Version Lineage & Rollback (`ROLLBACK_HASHES.json`)

| Version | BTC-USDT/C13 | ETH-USDT/C14 | Config Hash (prefix) | Signal Spec Hash (prefix) |
|---|---|---|---|---|
{rb_md}

## 3. Deterministic Replay (2 Tiến Trình Sạch)
- `trades = {replay_doc['trades']}`, `exact_sha256_match = {replay_doc['exact_sha256_match']}`, `max_abs_pnl_diff = {replay_doc['max_abs_pnl_diff']}` (`{replay_doc['status']}`).
"""
    (OUT_DIR / "V3_LAB_4_4_1_1_INTEGRATION_REPORT.md").write_text(report_md, encoding="utf-8")

    print(f"V4.1.1 VERIFICATION COMPLETE in {time.time() - t0:.2f}s")
    print(pd.DataFrame(port_rows).to_string(index=False))
    print("DETERMINISTIC REPLAY:", replay_doc)


if __name__ == "__main__":
    main()
