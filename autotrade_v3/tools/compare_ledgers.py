"""Compare two trade/execution ledgers.

Compares two ledgers (Parquet or CSV) joined by a specified or auto-detected key
(default: signal_id, leg_type), reporting:
- Rows only in A
- Rows only in B
- Columns unique to A or B (e.g. signal_id in post vs pre)
- Value differences per common column with maximum absolute difference
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence
import numpy as np
import pandas as pd


def load_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if p.suffix.lower() == ".parquet":
        return pd.read_parquet(p)
    return pd.read_csv(p)


def compare_ledgers(
    path_a: str | Path,
    path_b: str | Path,
    key_cols: Sequence[str] | None = None,
    tolerance: float = 1e-9,
) -> dict:
    df_a = load_table(path_a)
    df_b = load_table(path_b)

    # Auto-resolve join keys
    if key_cols is None:
        if "signal_id" in df_a.columns and "signal_id" in df_b.columns:
            if "leg_type" in df_a.columns and "leg_type" in df_b.columns:
                # If there are duplicates on (signal_id, leg_type), add leg_id or entry_time_ms
                if "leg_id" in df_a.columns and "leg_id" in df_b.columns:
                    key_cols = ["signal_id", "leg_id"]
                else:
                    key_cols = ["signal_id", "leg_type"]
            else:
                key_cols = ["signal_id"]
        elif "leg_id" in df_a.columns and "leg_id" in df_b.columns:
            key_cols = ["leg_id"]
        elif all(c in df_a.columns and c in df_b.columns for c in ["symbol", "combo_id", "side", "entry_time_ms", "leg_type"]):
            key_cols = ["symbol", "combo_id", "side", "entry_time_ms", "leg_type"]
        else:
            raise ValueError("Could not auto-determine join keys between tables.")

    key_list = list(key_cols)

    dup_a = int(df_a.duplicated(subset=key_list).sum())
    dup_b = int(df_b.duplicated(subset=key_list).sum())

    cols_a = set(df_a.columns)
    cols_b = set(df_b.columns)
    common_cols = [c for c in df_a.columns if c in cols_b and c not in key_list]
    only_in_a_cols = sorted(cols_a - cols_b)
    only_in_b_cols = sorted(cols_b - cols_a)

    # Perform outer join
    merged = pd.merge(
        df_a,
        df_b,
        on=key_list,
        how="outer",
        suffixes=("_A", "_B"),
        indicator=True,
    )

    only_in_a = merged[merged["_merge"] == "left_only"]
    only_in_b = merged[merged["_merge"] == "right_only"]
    matched = merged[merged["_merge"] == "both"]

    col_diffs: dict[str, dict] = {}
    for col in common_cols:
        col_a = f"{col}_A"
        col_b = f"{col}_B"

        s_a = matched[col_a]
        s_b = matched[col_b]

        null_mismatch = (s_a.isna() ^ s_b.isna()).to_numpy()
        both_valid = (~s_a.isna() & ~s_b.isna()).to_numpy()
        diff_mask = null_mismatch.copy()

        # Check numeric difference (excluding booleans)
        if (
            pd.api.types.is_numeric_dtype(s_a)
            and pd.api.types.is_numeric_dtype(s_b)
            and not pd.api.types.is_bool_dtype(s_a)
            and not pd.api.types.is_bool_dtype(s_b)
        ):
            if both_valid.any():
                diff_mask[both_valid] = ((s_a.iloc[both_valid] - s_b.iloc[both_valid]).abs() > tolerance).to_numpy()
            diff_count = int(diff_mask.sum())
            valid_diffs = (s_a.iloc[both_valid] - s_b.iloc[both_valid]).abs() if both_valid.any() else pd.Series(dtype=float)
            max_diff = float(valid_diffs.max()) if len(valid_diffs) > 0 and not valid_diffs.isna().all() else 0.0
            col_diffs[col] = {
                "type": "numeric",
                "diff_count": diff_count,
                "null_mismatches": int(null_mismatch.sum()),
                "max_abs_diff": max_diff,
                "identical": diff_count == 0,
            }
        else:
            # String / categorical comparison
            if both_valid.any():
                diff_mask[both_valid] = (s_a.iloc[both_valid].astype(str) != s_b.iloc[both_valid].astype(str)).to_numpy()
            diff_count = int(diff_mask.sum())
            col_diffs[col] = {
                "type": "categorical",
                "diff_count": diff_count,
                "null_mismatches": int(null_mismatch.sum()),
                "max_abs_diff": None,
                "identical": diff_count == 0,
            }

    return {
        "path_a": str(path_a),
        "path_b": str(path_b),
        "rows_a": len(df_a),
        "rows_b": len(df_b),
        "key_cols": key_list,
        "duplicates_in_a": dup_a,
        "duplicates_in_b": dup_b,
        "key_uniqueness_pass": dup_a == 0 and dup_b == 0,
        "rows_only_in_a": len(only_in_a),
        "rows_only_in_b": len(only_in_b),
        "rows_matched": len(matched),
        "cols_only_in_a": only_in_a_cols,
        "cols_only_in_b": only_in_b_cols,
        "col_diffs": col_diffs,
    }


def print_comparison_report(result: dict) -> None:
    print("=== AutoTrade V3 Ledger Comparison Report ===")
    print(f"Table A: {result['path_a']} ({result['rows_a']:,} rows)")
    print(f"Table B: {result['path_b']} ({result['rows_b']:,} rows)")
    print(f"Join Keys: {', '.join(result['key_cols'])}")
    if not result["key_uniqueness_pass"]:
        print(f"WARNING: KEY UNIQUENESS VIOLATION! Duplicates in A: {result['duplicates_in_a']}, Duplicates in B: {result['duplicates_in_b']}")
    print(f"Matched Rows: {result['rows_matched']:,}")
    print(f"Rows only in A: {result['rows_only_in_a']:,}")
    print(f"Rows only in B: {result['rows_only_in_b']:,}")

    if result["cols_only_in_a"]:
        print(f"Columns only in A: {', '.join(result['cols_only_in_a'])}")
    if result["cols_only_in_b"]:
        print(f"Columns only in B (New columns): {', '.join(result['cols_only_in_b'])}")

    print("\n--- Column-by-Column Divergence Analysis ---")
    fmt = "{:<32} {:<12} {:<12} {:<20}"
    print(fmt.format("Column", "Status", "Diff Count", "Max Abs Diff"))
    print("-" * 80)

    any_diff = False
    for col, d in result["col_diffs"].items():
        status = "IDENTICAL" if d["identical"] else "DIFFERENT"
        if not d["identical"]:
            any_diff = True
        max_d_str = f"{d['max_abs_diff']:.10g}" if d["max_abs_diff"] is not None else "-"
        print(fmt.format(col, status, f"{d['diff_count']:,}", max_d_str))

    print("-" * 80)
    if (
        not any_diff
        and result["rows_only_in_a"] == 0
        and result["rows_only_in_b"] == 0
        and result["key_uniqueness_pass"]
    ):
        print("RESULT: ALL MATCHED ROWS AND COMMON COLUMNS ARE 100% IDENTICAL.")
    else:
        print("RESULT: DIVERGENCE DETECTED.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two execution ledgers")
    parser.add_argument("path_a", help="Path to Ledger A (e.g. pre)")
    parser.add_argument("path_b", help="Path to Ledger B (e.g. post-1)")
    parser.add_argument("--key", default=None, help="Comma-separated join key columns")
    parser.add_argument("--tolerance", type=float, default=1e-9, help="Numerical diff tolerance")
    args = parser.parse_args()

    keys = [k.strip() for k in args.key.split(",")] if args.key else None
    res = compare_ledgers(args.path_a, args.path_b, key_cols=keys, tolerance=args.tolerance)
    print_comparison_report(res)


if __name__ == "__main__":
    main()
