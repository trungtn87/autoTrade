"""Independent hashing helpers for validation gates.

These re-implement the artifact content-hash and ExitPolicy-hash definitions from their written
specification (canonical, format independent) without importing any production module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def content_hash(df: pd.DataFrame, key_cols: Sequence[str] | None = None) -> str:
    """Canonical content hash: sorted columns, deterministic row order, 17-digit floats."""
    if df.empty:
        return hashlib.sha256((",".join(sorted(df.columns)) + "\n").encode("utf-8")).hexdigest()

    cols = sorted(df.columns)
    d = df[cols].copy()
    if key_cols is not None:
        keys = [c for c in key_cols if c in d.columns]
    elif "signal_id" in d.columns and "leg_id" in d.columns:
        keys = ["signal_id", "leg_id"]
    elif "signal_id" in d.columns and "event_id" in d.columns:
        keys = ["signal_id", "event_id"]
    elif "signal_id" in d.columns:
        keys = ["signal_id"]
    elif "leg_id" in d.columns:
        keys = ["leg_id"]
    elif "event_id" in d.columns:
        keys = ["event_id"]
    elif "trade_id" in d.columns:
        keys = ["trade_id"]
    else:
        keys = cols[:1]
    sort_by = list(keys) + [c for c in cols if c not in keys]
    d = d.sort_values(by=sort_by, na_position="first").reset_index(drop=True)

    lines = [",".join(cols)]
    for row in d.itertuples(index=False):
        fields: list[str] = []
        for v in row:
            if pd.isna(v):
                fields.append("")
            elif isinstance(v, (float, np.floating)):
                fields.append(f"{float(v):.17g}")
            elif isinstance(v, (bool, np.bool_)):
                fields.append(str(bool(v)))
            elif isinstance(v, (int, np.integer)):
                fields.append(str(int(v)))
            else:
                fields.append(str(v))
        lines.append(",".join(fields))
    return hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def artifact_content_hash(path: Path) -> tuple[str, str, int | None]:
    """Return (sha256, content_hash, row_count) following the ARTIFACTS.json convention."""
    sha = sha256_file(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df = pd.read_parquet(path)
        return sha, content_hash(df), len(df)
    if suffix == ".csv":
        df = pd.read_csv(path)
        return sha, content_hash(df), len(df)
    return sha, sha, None


_POLICY_FIELDS = (
    "tp_pct", "sl_pct", "split_legs", "trailing_act_pct", "trailing_cb_pct",
    "leg_weights", "max_bars_held", "force_close_on_window_end",
)


def policy_hash(policy: dict[str, Any]) -> str:
    """SHA256 of the canonical JSON of the ExitPolicy parameter dict (sorted keys)."""
    data = {k: policy[k] for k in _POLICY_FIELDS}
    data["leg_weights"] = list(data["leg_weights"])
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def dataset_hash_from_partitions(partitions: list[dict[str, Any]]) -> str:
    """dataset_hash = SHA256 over sorted ``relative_path:size:rows:sha256`` lines."""
    parts = sorted(partitions, key=lambda p: p["relative_path"])
    lines = [f"{p['relative_path']}:{p['size_bytes']}:{p['num_rows']}:{p['sha256']}" for p in parts]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
