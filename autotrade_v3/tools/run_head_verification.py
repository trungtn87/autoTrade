"""Deterministic Golden Verification Tool for AutoTrade V3-08.

Executes all three modes (ALL_SIGNALS, ONE_TRADE_PER_COMBO, BINGX_PORTFOLIO)
on real 3-year M1 data into an independent directory, and performs full
bit-for-bit reconciliation (signals, trades, pnl, skipped/rejected, and snapshots)
against the locked Golden Baseline without modifying reference artifacts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, "") and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.lab.experiment import ExperimentRunner
from autotrade_v3.lab.run_manager import RunManager, get_git_state
from autotrade_v3.tools.artifact_hash import compute_content_hash, compute_file_sha256
from autotrade_v3.tools.compare_ledgers import compare_ledgers
from autotrade_v3.tools.dataset_manifest import verify_dataset_integrity
from autotrade_v3.tools.verify_snapshot import verify_snapshot


def run_head_verification(
    out_dir: Path,
    golden_root: Path = REPO_ROOT / "runs" / "golden" / "v3-08",
    catalog_path: Path = REPO_ROOT / "catalog" / "research.duckdb",
    data_dir: Path = REPO_ROOT / "data" / "m1_raw",
    expected_commit_prefix: str = "f1f7f5b",
) -> list[dict[str, Any]]:
    print("=" * 80)
    print(f"AUTOTRADE V3-08: GOLDEN VERIFICATION RUN ON COMMIT HEAD {expected_commit_prefix}")
    print("=" * 80)

    # 1. Verify Git State
    sha, dirty, diff_hash = get_git_state(REPO_ROOT)
    print(f"Git HEAD SHA: {sha}")
    print(f"Git Dirty:    {dirty}")
    print(f"Diff Hash:    '{diff_hash}'")
    if dirty:
        print("WARNING: Git working tree is dirty, golden integrity check requires clean tree.")
    if not sha.startswith(expected_commit_prefix):
        print(f"Note: Current HEAD is {sha}, expected commit prefix was {expected_commit_prefix}")

    # 2. Verify Live Dataset Integrity
    print("\nVerifying live dataset integrity against physical partitions...")
    is_valid, live_hash, err = verify_dataset_integrity(
        manifest_path=golden_root / "dataset_manifest.json",
        catalog_path=catalog_path,
        data_dir=data_dir,
    )
    print(f"Dataset Valid: {is_valid}")
    print(f"Dataset Hash:  {live_hash}")
    if not is_valid:
        print(f"ERROR: {err}")
        sys.exit(1)

    # 3. Setup Independent Output Directory
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nTarget Verification Output: {out_dir}")

    dl = DataLayer(catalog_path=catalog_path, data_dir=data_dir)
    rm = RunManager(runs_dir=out_dir)
    runner = ExperimentRunner(data_layer=dl, run_manager=rm)

    modes = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
    results = {}

    for mode in modes:
        t0 = time.time()
        print(f"\n>>> Running Mode {mode} on REAL M1 DATA (2023-11-01 to 2026-11-01)...")
        res = runner.run_experiment(
            strategy_id="20FINAL_BASELINE",
            mode=mode,
            symbols=["BTC-USDT", "ETH-USDT"],
            start="2023-11-01",
            end="2026-11-01",
            initial_equity=1000.0,
            notional_per_trade=100.0,
            slippage_rate=0.0,
            smoke_test=False,
            is_golden=True,
        )
        elapsed = time.time() - t0
        print(f"Finished {mode} in {elapsed:.1f}s. Saved to: {res.run_dir}")
        results[mode] = res

    # 4. Comprehensive Comparison against Locked Golden Baseline
    print("\n" + "=" * 80)
    print("RECONCILIATION: NEW HEAD RUN vs LOCKED GOLDEN BASELINE")
    print("=" * 80)

    comparison_summary = []

    for mode in modes:
        gold_dir = golden_root / mode
        new_dir = out_dir / mode

        print(f"\n--- Comparing Mode: {mode} ---")

        # Compare signals
        gold_sig = pd.read_parquet(gold_dir / "signals.parquet")
        new_sig = pd.read_parquet(new_dir / "signals.parquet")
        sig_rows_gold = len(gold_sig)
        sig_rows_new = len(new_sig)
        sig_dups_new = int(new_sig.duplicated(subset=["signal_id"]).sum())
        sig_h_gold = compute_content_hash(gold_sig, key_cols=["signal_id"])
        sig_h_new = compute_content_hash(new_sig, key_cols=["signal_id"])
        sig_match = (sig_rows_gold == sig_rows_new) and (sig_h_gold == sig_h_new)
        print(f"  signals.parquet:       Rows={sig_rows_new} (gold={sig_rows_gold}), Dups={sig_dups_new}, Hash Match={sig_match}")

        # Compare trades
        gold_trades = pd.read_parquet(gold_dir / "trades.parquet")
        new_trades = pd.read_parquet(new_dir / "trades.parquet")
        trades_rows_gold = len(gold_trades)
        trades_rows_new = len(new_trades)
        trades_dups_new = int(new_trades.duplicated(subset=["signal_id", "leg_id"]).sum())
        trades_h_gold = compute_content_hash(gold_trades, key_cols=["signal_id", "leg_id"])
        trades_h_new = compute_content_hash(new_trades, key_cols=["signal_id", "leg_id"])
        trades_match = (trades_rows_gold == trades_rows_new) and (trades_h_gold == trades_h_new)

        # Ledger comparison
        ledger_cmp = compare_ledgers(gold_dir / "trades.parquet", new_dir / "trades.parquet", key_cols=["signal_id", "leg_id"])
        diff_count = sum(c.get("diff_count", 0) for c in ledger_cmp.get("col_diffs", {}).values())
        print(f"  trades.parquet:        Rows={trades_rows_new} (gold={trades_rows_gold}), Dups={trades_dups_new}, Hash Match={trades_match}, Ledger Diffs={diff_count}")

        # Compare skipped
        gold_skip = pd.read_parquet(gold_dir / "skipped_signals.parquet")
        new_skip = pd.read_parquet(new_dir / "skipped_signals.parquet")
        skip_h_gold = compute_content_hash(gold_skip, key_cols=["signal_id"])
        skip_h_new = compute_content_hash(new_skip, key_cols=["signal_id"])
        skip_match = (len(gold_skip) == len(new_skip)) and (skip_h_gold == skip_h_new)
        print(f"  skipped_signals:       Rows={len(new_skip)} (gold={len(gold_skip)}), Hash Match={skip_match}")

        # Compare rejected
        gold_rej = pd.read_parquet(gold_dir / "rejected_signals.parquet")
        new_rej = pd.read_parquet(new_dir / "rejected_signals.parquet")
        rej_h_gold = compute_content_hash(gold_rej, key_cols=["signal_id"])
        rej_h_new = compute_content_hash(new_rej, key_cols=["signal_id"])
        rej_match = (len(gold_rej) == len(new_rej)) and (rej_h_gold == rej_h_new)
        print(f"  rejected_signals:      Rows={len(new_rej)} (gold={len(gold_rej)}), Hash Match={rej_match}")

        # Compare analysis
        with open(gold_dir / "analysis.json", encoding="utf-8") as f:
            gold_analysis = json.load(f)
        with open(new_dir / "analysis.json", encoding="utf-8") as f:
            new_analysis = json.load(f)

        gold_pnl = float(gold_analysis["pnl"]["net_pnl"])
        new_pnl = float(new_analysis["pnl"]["net_pnl"])
        gold_roi = float(gold_analysis["pnl"]["roi_pct"])
        new_roi = float(new_analysis["pnl"]["roi_pct"])
        pnl_diff = abs(gold_pnl - new_pnl)
        print(f"  Net PnL:               ${new_pnl:.4f} (gold=${gold_pnl:.4f}, diff={pnl_diff})")
        print(f"  ROI:                   {new_roi:+.2f}% (gold={gold_roi:+.2f}%)")

        # Snapshot check for Mode C
        snap_status = "N/A"
        if mode == "BINGX_PORTFOLIO":
            snap_res = verify_snapshot(new_dir / "snapshots.parquet", gold_dir / "ARTIFACTS.json")
            snap_status = "PASS" if snap_res.get("verified") else "FAIL"
            print(f"  snapshots.parquet:     Verified={snap_res.get('verified')}, SHA Match={snap_res.get('sha_match')}, Rows={snap_res.get('actual_rows')}")

        comparison_summary.append({
            "mode": mode,
            "signals": sig_rows_new,
            "signals_hash_match": sig_match,
            "trades": trades_rows_new,
            "trades_hash_match": trades_match,
            "net_pnl": new_pnl,
            "net_pnl_gold": gold_pnl,
            "pnl_diff": pnl_diff,
            "skipped": len(new_skip),
            "skipped_hash_match": skip_match,
            "rejected": len(new_rej),
            "rejected_hash_match": rej_match,
            "snapshot_status": snap_status,
        })

    return comparison_summary


def main():
    parser = argparse.ArgumentParser(description="Run full golden verification on HEAD commit")
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "runs" / "golden_verify_head"),
        help="Target output directory for verification artifacts",
    )
    parser.add_argument(
        "--expected-commit",
        default="f1f7f5b",
        help="Expected Git commit prefix",
    )
    args = parser.parse_args()

    out_p = Path(args.out_dir)
    res = run_head_verification(out_dir=out_p, expected_commit_prefix=args.expected_commit)
    print("\nVerification summary:")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
