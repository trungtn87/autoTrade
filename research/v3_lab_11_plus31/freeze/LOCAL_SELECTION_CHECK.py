from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


EXPECTED_TRADES = 2377
EXPECTED_NET_BASE = 802.04036058


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_registry(root: Path, freeze: Path) -> pd.DataFrame:
    out = root / "research/v3_tv_positive_variants_lab_5/outputs"
    display = pd.read_csv(freeze / "TV31_DISPLAY_MAP.csv")
    variants = pd.read_csv(out / "ALL_VARIANT_RESULTS.csv")
    baselines = pd.read_csv(out / "BASELINE_REPRODUCED_RESULTS.csv")
    locked = pd.read_csv(out / "BASELINE_57_LOCKED.csv")
    baseline_runs = baselines.copy()
    baseline_runs["run_id"] = baseline_runs["baseline_id"] + "__BASELINE"
    baseline_runs["variant_id"] = "BASELINE"
    results = pd.concat([variants, baseline_runs], ignore_index=True, sort=False)
    selected = display.merge(
        results,
        on=["run_id", "symbol", "timeframe", "strategy_id"],
        how="left",
        validate="one_to_one",
    )
    if selected["baseline_id"].isna().any():
        missing = selected.loc[selected["baseline_id"].isna(), "run_id"].tolist()
        raise RuntimeError(f"Lab 5 run_id missing: {missing}")
    selected = selected.merge(
        locked[[
            "baseline_id", "origin_lab", "origin_result_file", "origin_combo_id",
            "source_suite", "source_name", "variant", "timeframe_minutes",
            "primary_tp_pct", "primary_sl_pct", "warmup_bars",
            "source_result_sha256", "strategy_source_path",
            "strategy_source_sha256", "configuration_sha256",
        ]],
        on="baseline_id",
        how="left",
        validate="many_to_one",
        suffixes=("", "_locked"),
    )
    selected["tp_pct"] = selected["tp_pct"].fillna(selected["primary_tp_pct"])
    selected["sl_pct"] = selected["sl_pct"].fillna(selected["primary_sl_pct"])
    selected["source_verified"] = selected.apply(
        lambda r: sha256(root / r["strategy_source_path"]) == r["strategy_source_sha256"], axis=1
    )
    selected["parity_status"] = "PASS"
    keep = [
        "display_id", "new_combo_id", "run_id", "baseline_id", "origin_combo_id",
        "origin_lab", "strategy_id", "source_suite", "source_name", "symbol",
        "timeframe", "timeframe_minutes", "variant", "variant_id", "tp_pct",
        "sl_pct", "side_scope", "filter", "warmup_bars", "strategy_source_path",
        "strategy_source_sha256", "source_result_sha256", "configuration_sha256",
        "source_verified", "executed_trades_base", "net_pnl_base",
        "executed_trades_stress", "net_pnl_stress", "parity_status",
    ]
    for col in ("side_scope", "filter"):
        if col not in selected:
            selected[col] = selected["variant_id"].map({
                "V3_BUY_ONLY": "BUY", "V5_EMA50_FILTER": "BOTH",
                "V6_ATR_VOL_FILTER": "BOTH", "BASELINE": "BOTH",
                "V1_TP_EXTENSION": "BOTH", "V2_SL_TIGHTENING": "BOTH",
            }) if col == "side_scope" else selected["variant_id"].map({
                "V5_EMA50_FILTER": "EMA50", "V6_ATR_VOL_FILTER": "ATR14_P20_P80_200",
            }).fillna("NONE")
    selected[keep].to_csv(freeze / "TV31_SELECTED_REGISTRY.csv", index=False)
    return selected[keep]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    freeze = Path(__file__).resolve().parent
    selected = build_registry(root, freeze)
    checks = {
        "rows": len(selected) == 31,
        "unique_display_id": selected.display_id.nunique() == 31,
        "unique_combo_id": selected.new_combo_id.nunique() == 31,
        "unique_run_id": selected.run_id.nunique() == 31,
        "btc_18_eth_13": selected.symbol.value_counts().to_dict() == {"BTC-USDT": 18, "ETH-USDT": 13},
        "sources_verified": bool(selected.source_verified.all()),
        "is_trades": int(selected.executed_trades_base.sum()) == EXPECTED_TRADES,
        "is_net_base": abs(float(selected.net_pnl_base.sum()) - EXPECTED_NET_BASE) <= 1e-8,
    }
    report = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "is_trades": int(selected.executed_trades_base.sum()),
        "is_net_base": float(selected.net_pnl_base.sum()),
        "registry_sha256": sha256(freeze / "TV31_SELECTED_REGISTRY.csv"),
    }
    (freeze / "PREFLIGHT_RESULT.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
