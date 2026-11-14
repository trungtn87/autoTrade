"""Canonical hashing and deterministic run_id computation for RunStore."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


def canonical_json(obj: Any) -> str:
    """Serialize *obj* to deterministic canonical JSON."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def df_content_hash(df: pd.DataFrame, key_cols: Sequence[str] | None = None) -> str:
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
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def artifact_hashes(path: str | Path) -> dict[str, Any]:
    """Compute size_bytes, sha256, content_hash, and row_count for an artifact file."""
    p = Path(path)
    size_bytes = p.stat().st_size
    sha = sha256_file(p)
    if p.suffix == ".parquet":
        df = pd.read_parquet(p)
        chash = df_content_hash(df)
        rows: int | None = int(len(df))
    elif p.suffix == ".json":
        obj = json.loads(p.read_text(encoding="utf-8"))
        chash = sha256_text(canonical_json(obj))
        rows = len(obj) if isinstance(obj, list) else None
    else:
        chash = sha
        rows = None
    return {
        "file_name": p.name,
        "size_bytes": size_bytes,
        "sha256": sha,
        "content_hash": chash,
        "row_count": rows,
    }


def compute_sim_config_hash(
    *,
    initial_equity: float,
    notional_per_trade: float,
    final14_exit_mode: str = "50TP_50TRAIL",
    trailing_act_pct: float = 0.005,
    trailing_cb_pct: float = 0.005,
    leverage_by_symbol: dict[str, float] | None = None,
    mmr_by_symbol: dict[str, float] | None = None,
) -> str:
    """Compute canonical SHA256 over simulator configuration parameters."""
    payload = {
        "initial_equity": float(initial_equity),
        "notional_per_trade": float(notional_per_trade),
        "final14_exit_mode": str(final14_exit_mode),
        "trailing_act_pct": float(trailing_act_pct),
        "trailing_cb_pct": float(trailing_cb_pct),
        "leverage_by_symbol": {str(k): float(v) for k, v in sorted((leverage_by_symbol or {}).items())},
        "mmr_by_symbol": {str(k): float(v) for k, v in sorted((mmr_by_symbol or {}).items())},
    }
    return sha256_text(canonical_json(payload))


def compute_run_id(
    *,
    dataset_hash: str,
    spec_hash: str,
    mode: str,
    time_window: dict[str, str],
    symbols: list[str] | tuple[str, ...],
    sim_config_hash: str,
    cost_profile_hash: str,
    ambiguity_policy: str,
    filters: list[dict[str, str]] | None,
    code_sha: str,
) -> str:
    """Deterministic run_id = 'R_' + first 16 hex chars of SHA256 over canonical input spec."""
    norm_filters = [
        {"name": str(f.get("name", "")), "config_hash": str(f.get("config_hash", ""))}
        for f in (filters or [])
    ]
    payload = {
        "dataset_hash": str(dataset_hash),
        "spec_hash": str(spec_hash),
        "mode": str(mode),
        "time_window": {
            "start": str(time_window.get("start", "")),
            "end": str(time_window.get("end", "")),
        },
        "symbols": sorted(str(s) for s in symbols),
        "sim_config_hash": str(sim_config_hash),
        "cost_profile_hash": str(cost_profile_hash),
        "ambiguity_policy": str(ambiguity_policy),
        "filters": norm_filters,
        "code_sha": str(code_sha),
    }
    return "R_" + sha256_text(canonical_json(payload))[:16]
