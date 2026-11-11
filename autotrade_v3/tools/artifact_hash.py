"""Artifact Content Hash utility for AutoTrade V3.

Computes a deterministic, format-independent content hash for tabular artifacts
(Parquet/CSV):
- Sorts columns alphabetically
- Sorts rows deterministically by key columns (default: signal_id, leg_id)
- Formats floating point numbers with full 17-digit precision
- Uses standard LF line endings
- Hashes the canonical UTF-8 representation with SHA256
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any, Sequence
import numpy as np
import pandas as pd


def compute_file_sha256(path: str | Path) -> str:
    """Compute SHA256 of raw file bytes."""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def compute_content_hash(
    df: pd.DataFrame,
    key_cols: Sequence[str] | None = None,
) -> str:
    """Compute deterministic content hash of a DataFrame."""
    if df.empty:
        # Canonical hash for empty table with sorted columns
        cols_line = ",".join(sorted(df.columns)) + "\n"
        return hashlib.sha256(cols_line.encode("utf-8")).hexdigest()

    sorted_cols = sorted(df.columns)
    df_sorted = df[sorted_cols].copy()

    # Determine sort keys
    if key_cols is not None:
        effective_keys = [c for c in key_cols if c in df_sorted.columns]
    else:
        # Default key resolution
        if "signal_id" in df_sorted.columns and "leg_id" in df_sorted.columns:
            effective_keys = ["signal_id", "leg_id"]
        elif "signal_id" in df_sorted.columns and "event_id" in df_sorted.columns:
            effective_keys = ["signal_id", "event_id"]
        elif "signal_id" in df_sorted.columns:
            effective_keys = ["signal_id"]
        elif "leg_id" in df_sorted.columns:
            effective_keys = ["leg_id"]
        elif "event_id" in df_sorted.columns:
            effective_keys = ["event_id"]
        elif "trade_id" in df_sorted.columns:
            effective_keys = ["trade_id"]
        else:
            effective_keys = sorted_cols[:1]

    # Add all columns as tie-breakers to ensure absolute deterministic sorting
    sort_by = list(effective_keys)
    for c in sorted_cols:
        if c not in sort_by:
            sort_by.append(c)

    # Sort DataFrame rows
    df_sorted = df_sorted.sort_values(by=sort_by, na_position="first").reset_index(drop=True)

    lines: list[str] = [",".join(sorted_cols)]
    for row in df_sorted.itertuples(index=False):
        row_fields = []
        for val in row:
            if pd.isna(val):
                row_fields.append("")
            elif isinstance(val, (float, np.floating)):
                row_fields.append(f"{float(val):.17g}")
            elif isinstance(val, (bool, np.bool_)):
                row_fields.append(str(bool(val)))
            elif isinstance(val, (int, np.integer)):
                row_fields.append(str(int(val)))
            else:
                row_fields.append(str(val))
        lines.append(",".join(row_fields))

    canonical_text = "\n".join(lines) + "\n"
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def compute_artifact_hashes(
    path: str | Path,
    key_cols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Compute both file SHA256 and tabular content hash for an artifact."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Artifact file not found: {p}")

    file_sha256 = compute_file_sha256(p)
    file_size = p.stat().st_size

    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
        content_hash = compute_content_hash(df, key_cols=key_cols)
        row_count = len(df)
    elif p.suffix.lower() == ".csv":
        df = pd.read_csv(p)
        content_hash = compute_content_hash(df, key_cols=key_cols)
        row_count = len(df)
    elif p.suffix.lower() == ".json":
        content_hash = file_sha256
        row_count = None
    else:
        content_hash = file_sha256
        row_count = None

    return {
        "file_name": p.name,
        "path": str(p),
        "size_bytes": file_size,
        "sha256": file_sha256,
        "content_hash": content_hash,
        "row_count": row_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate content hash for an artifact table")
    parser.add_argument("path", help="Path to Parquet or CSV file")
    parser.add_argument("--key", default=None, help="Comma-separated key columns for sorting")
    args = parser.parse_args()

    keys = [k.strip() for k in args.key.split(",")] if args.key else None
    res = compute_artifact_hashes(args.path, key_cols=keys)
    print(f"File:         {res['file_name']}")
    print(f"Size:         {res['size_bytes']:,} bytes")
    print(f"File SHA256:  {res['sha256']}")
    print(f"Content Hash: {res['content_hash']}")
    if res['row_count'] is not None:
        print(f"Rows:         {res['row_count']:,}")


if __name__ == "__main__":
    main()
