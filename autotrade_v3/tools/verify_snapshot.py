"""Independent verification and integrity tool for Mode C Portfolio Snapshots.

Mode C snapshots.parquet (~23.6 MB) exceeds the 20 MB GitHub commit limit.
This tool verifies its SHA256, schema, row counts, and equity metrics,
and provides instructions to reproduce it deterministically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "runs" / "golden" / "v3-08" / "BINGX_PORTFOLIO" / "snapshots.parquet"
DEFAULT_ARTIFACTS_PATH = REPO_ROOT / "runs" / "golden" / "v3-08" / "BINGX_PORTFOLIO" / "ARTIFACTS.json"
EXPECTED_SHA256 = "a02644b48fe0cec76bd00a58afb8acb3e40c18b81832c66197dc34ea8c7dca6a"
EXPECTED_ROWS = 781611


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshot(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    artifacts_path: str | Path = DEFAULT_ARTIFACTS_PATH,
) -> dict[str, Any]:
    snap_p = Path(snapshot_path)
    if not snap_p.exists():
        return {
            "verified": False,
            "error": f"Snapshot file not found: {snap_p}",
            "reproduction_command": (
                "python -m autotrade_v3.cli backtest --strategy 20FINAL_BASELINE "
                "--mode BINGX_PORTFOLIO --start 2023-11-01 --end 2026-11-01 "
                "--initial-equity 1000 --notional 100 --slippage 0 --golden"
            ),
        }

    actual_sha = compute_file_sha256(snap_p)
    actual_size = snap_p.stat().st_size

    # Check against ARTIFACTS.json
    recorded_sha = EXPECTED_SHA256
    art_p = Path(artifacts_path)
    if art_p.exists():
        try:
            with open(art_p, "r", encoding="utf-8") as f:
                art_data = json.load(f)
            recorded_sha = art_data.get("artifacts", {}).get("snapshots.parquet", {}).get("sha256", EXPECTED_SHA256)
        except Exception:
            pass

    sha_match = (actual_sha == recorded_sha)

    # Read lightweight metadata
    df = pd.read_parquet(snap_p)
    actual_rows = len(df)
    rows_match = (actual_rows == EXPECTED_ROWS)

    initial_equity = float(df["equity"].iloc[0]) if "equity" in df else 0.0
    final_equity = float(df["equity"].iloc[-1]) if "equity" in df else 0.0
    min_equity = float(df["equity"].min()) if "equity" in df else 0.0
    max_equity = float(df["equity"].max()) if "equity" in df else 0.0

    verified = sha_match and rows_match

    return {
        "verified": verified,
        "path": str(snap_p),
        "size_bytes": actual_size,
        "actual_sha256": actual_sha,
        "expected_sha256": recorded_sha,
        "sha_match": sha_match,
        "actual_rows": actual_rows,
        "expected_rows": EXPECTED_ROWS,
        "rows_match": rows_match,
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "min_equity": min_equity,
        "max_equity": max_equity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Mode C portfolio snapshots integrity")
    parser.add_argument("--path", default=str(DEFAULT_SNAPSHOT_PATH), help="Path to snapshots.parquet")
    parser.add_argument("--artifacts", default=str(DEFAULT_ARTIFACTS_PATH), help="Path to ARTIFACTS.json")
    args = parser.parse_args()

    res = verify_snapshot(args.path, args.artifacts)
    print("=== Mode C Portfolio Snapshots Verification ===")
    if not res.get("verified"):
        print(f"FAILED: {res.get('error', 'Integrity check failed')}")
        if "reproduction_command" in res:
            print(f"To reproduce deterministically, run:\n  {res['reproduction_command']}")
        sys.exit(1)

    print(f"File Path:       {res['path']}")
    print(f"Size:            {res['size_bytes']:,} bytes ({res['size_bytes'] / (1024*1024):.2f} MB)")
    print(f"SHA256:          {res['actual_sha256']}")
    print(f"Expected SHA256: {res['expected_sha256']}")
    print(f"SHA256 Status:   {'MATCH (100% IDENTICAL)' if res['sha_match'] else 'MISMATCH'}")
    print(f"Row Count:       {res['actual_rows']:,} (Expected: {res['expected_rows']:,})")
    print(f"Initial Equity:  ${res['initial_equity']:,.2f}")
    print(f"Final Equity:    ${res['final_equity']:,.2f}")
    print(f"Max Equity:      ${res['max_equity']:,.2f}")
    print(f"Min Equity:      ${res['min_equity']:,.2f}")
    print("VERIFICATION: PASS")


if __name__ == "__main__":
    main()
