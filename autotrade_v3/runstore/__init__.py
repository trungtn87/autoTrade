"""AutoTrade V3 RunStore package: deterministic run_id, immutable store, DuckDB index."""
from __future__ import annotations

from autotrade_v3.runstore.equity import build_equity_df
from autotrade_v3.runstore.hashing import (
    artifact_hashes,
    canonical_json,
    compute_run_id,
    compute_sim_config_hash,
    df_content_hash,
    sha256_file,
    sha256_text,
)
from autotrade_v3.runstore.index import StoreIndex
from autotrade_v3.runstore.store import STORE_ARTIFACTS, NonReproducibleError, RunStore, StoreSaveResult

__all__ = [
    "NonReproducibleError",
    "RunStore",
    "STORE_ARTIFACTS",
    "StoreIndex",
    "StoreSaveResult",
    "artifact_hashes",
    "build_equity_df",
    "canonical_json",
    "compute_run_id",
    "compute_sim_config_hash",
    "df_content_hash",
    "sha256_file",
    "sha256_text",
]
