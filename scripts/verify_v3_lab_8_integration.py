"""Full 3-year dataset integration verification for V3_LAB_8@8.0.0 vs V3_LAB_7@7.0.0."""
from __future__ import annotations

from dataclasses import asdict
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.risk_manager import RiskConfig
from autotrade_v3.lab import load_v3_lab_7_strategy, load_v3_lab_8_strategy
from autotrade_v3.sim.costs import CostModel
from autotrade_v3.sim.engine import UnifiedSimulator

START_DATE = "2023-11-01"
END_DATE = "2026-11-01"
OUT_DIR = ROOT / "runs" / "lab" / "v3_lab_8" / "v8_0_0_integration"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def calc_stats(df: pd.DataFrame) -> dict:
    net = df["net_pnl"].values
    wins = net[net > 0]
    losses = net[net <= 0]
    pf = float(wins.sum()) / float(abs(losses.sum())) if len(losses) else float("inf")
    eq = 1000.0 + np.cumsum(net)
    peak = np.maximum.accumulate(np.insert(eq, 0, 1000.0))[1:]
    dd = (peak - eq) / peak * 100.0
    return {
        "trades": int(len(df)),
        "buy": int((df["side"] == "BUY").sum()),
        "sell": int((df["side"] == "SELL").sum()),
        "win_rate": round(float((net > 0).mean() * 100.0), 2),
        "net_pnl": round(float(net.sum()), 4),
        "pf": round(pf, 4),
        "max_dd_pct": round(float(dd.max()), 2),
    }


def main() -> None:
    dl = DataLayer()
    s7 = load_v3_lab_7_strategy()
    s8 = load_v3_lab_8_strategy()

    all_symbols = sorted({c["symbol"] for c in s8.config["combos"]})
    m1_all = {sym: dl.query_m1(sym, START_DATE, END_DATE, include_warmup=True) for sym in all_symbols}
    m15_all = {sym: dl.query_timeframe(sym, "15m", START_DATE, END_DATE, include_warmup=True) for sym in all_symbols}

    sigs7 = s7.generate_signals(m15_all, symbols=all_symbols, only_approved=True, only_warmup_ready=True)
    sigs8 = s8.generate_signals(m15_all, symbols=all_symbols, only_approved=True, only_warmup_ready=True)

    # 1. Check 29 other combos exact parity
    other7 = [(s.symbol, int(s.combo_id), s.side, int(s.signal_close_ms)) for s in sigs7 if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 6)]
    other8 = [(s.symbol, int(s.combo_id), s.side, int(s.signal_close_ms)) for s in sigs8 if not (s.symbol == "ETH-USDT" and int(s.combo_id) == 6)]
    assert other7 == other8, f"29-combo parity FAILED: len7={len(other7)} vs len8={len(other8)}"
    print(f"[PARITY 29 COMBOS] PASS ({len(other8)} signals identical)")

    # 2. Check ETH-C6 signals
    c6_sigs = [s for s in sigs8 if s.symbol == "ETH-USDT" and int(s.combo_id) == 6]
    c6_buy = sum(1 for s in c6_sigs if s.side == "BUY")
    c6_sell = sum(1 for s in c6_sigs if s.side == "SELL")
    print(f"[ETH-C6 SIGNALS] total={len(c6_sigs)} (BUY={c6_buy}, SELL={c6_sell})")
    assert c6_buy == 36 and c6_sell == 81, f"Expected 36 BUY / 81 SELL, got {c6_buy} BUY / {c6_sell} SELL"

    # 3. Own ALL_SIGNALS & FULL30 BINGX_PORTFOLIO
    risk = RiskConfig()
    own_base = calc_stats(pd.DataFrame([asdict(t) for t in UnifiedSimulator(cost_model=CostModel.from_profile_name("BASE"), risk_config=risk).run("ALL_SIGNALS", c6_sigs, {"ETH-USDT": m1_all["ETH-USDT"]}).trades]))
    own_stress = calc_stats(pd.DataFrame([asdict(t) for t in UnifiedSimulator(cost_model=CostModel.from_profile_name("STRESS"), risk_config=risk).run("ALL_SIGNALS", c6_sigs, {"ETH-USDT": m1_all["ETH-USDT"]}).trades]))
    full_base = calc_stats(pd.DataFrame([asdict(t) for t in UnifiedSimulator(cost_model=CostModel.from_profile_name("BASE"), risk_config=risk).run("BINGX_PORTFOLIO", sigs8, m1_all).trades]))
    full_stress = calc_stats(pd.DataFrame([asdict(t) for t in UnifiedSimulator(cost_model=CostModel.from_profile_name("STRESS"), risk_config=risk).run("BINGX_PORTFOLIO", sigs8, m1_all).trades]))

    print(f"[OWN BASE] {own_base}")
    print(f"[OWN STRESS] {own_stress}")
    print(f"[FULL30 BASE] {full_base}")
    print(f"[FULL30 STRESS] {full_stress}")

    assert abs(own_base["net_pnl"] - 25.2905) < 0.05
    assert abs(own_stress["net_pnl"] - 16.4402) < 0.05
    assert abs(full_base["net_pnl"] - 353.2449) < 0.05
    assert abs(full_stress["net_pnl"] - 30.8373) < 0.05

    summary = {
        "strategy_id": s8.strategy_id,
        "version": s8.version,
        "parity_29_combos": "PASS",
        "other_29_signals": len(other8),
        "eth_c6_buy": c6_buy,
        "eth_c6_sell": c6_sell,
        "own_base": own_base,
        "own_stress": own_stress,
        "full30_base": full_base,
        "full30_stress": full_stress,
    }
    (OUT_DIR / "V3_LAB_8_VERIFICATION.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("[VERIFICATION] ALL CHECKS PASSED!")


if __name__ == "__main__":
    main()
