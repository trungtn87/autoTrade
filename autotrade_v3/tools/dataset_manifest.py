"""Dataset Manifest generator for AutoTrade V3.

Reads (read-only) all M1 Parquet partitions and the DuckDB catalog,
producing a dataset_manifest.json with exact partition provenance,
row counts, candle ranges per symbol, and a canonical dataset_hash.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import duckdb
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = REPO_ROOT / "catalog" / "research.duckdb"
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "m1_raw"


def compute_file_sha256(path: Path) -> str:
    """Compute SHA256 checksum of a file in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def generate_dataset_manifest(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    base_dir: str | Path = REPO_ROOT,
) -> dict[str, Any]:
    """Inspect M1 dataset partitions and catalog to build provenance manifest."""
    catalog_path = Path(catalog_path)
    data_dir = Path(data_dir)
    base_dir = Path(base_dir)

    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    parquet_files = sorted(data_dir.rglob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in: {data_dir}")

    partitions: list[dict[str, Any]] = []
    symbol_partition_map: dict[str, list[dict[str, Any]]] = {}

    for pf in parquet_files:
        rel_path = pf.relative_to(base_dir).as_posix()
        size_bytes = pf.stat().st_size
        meta = pq.read_metadata(pf)
        num_rows = meta.num_rows
        file_sha256 = compute_file_sha256(pf)

        p_info = {
            "relative_path": rel_path,
            "size_bytes": size_bytes,
            "num_rows": num_rows,
            "sha256": file_sha256,
        }
        partitions.append(p_info)

    # Sort partitions by relative path
    partitions.sort(key=lambda p: p["relative_path"])

    # Compute canonical dataset_hash: SHA256 of sorted partition entries
    hash_lines = [
        f"{p['relative_path']}:{p['size_bytes']}:{p['num_rows']}:{p['sha256']}"
        for p in partitions
    ]
    dataset_hash = hashlib.sha256("\n".join(hash_lines).encode("utf-8")).hexdigest()

    # Query candle stats by symbol from catalog or directly from partitions
    symbol_stats: dict[str, dict[str, Any]] = {}
    if catalog_path.exists():
        con = duckdb.connect(str(catalog_path), read_only=True)
        try:
            # Check available tables
            tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
            tbl_name = "v3_m1" if "v3_m1" in tables else ("m1_candles" if "m1_candles" in tables else None)
            if tbl_name:
                res = con.execute(f"""
                    SELECT 
                        symbol,
                        COUNT(*) as count,
                        MIN(open_ms) as min_ts,
                        MAX(open_ms) as max_ts
                    FROM {tbl_name}
                    GROUP BY symbol
                    ORDER BY symbol
                """).fetchall()
                for sym, cnt, min_ts, max_ts in res:
                    min_ms = int(min_ts)
                    max_ms = int(max_ts)
                    symbol_stats[str(sym)] = {
                        "total_candles": int(cnt),
                        "min_open_ms": min_ms,
                        "max_open_ms": max_ms,
                        "min_open_utc": datetime.fromtimestamp(min_ms / 1000, tz=timezone.utc).isoformat(),
                        "max_open_utc": datetime.fromtimestamp(max_ms / 1000, tz=timezone.utc).isoformat(),
                    }
        finally:
            con.close()

    total_candles = sum(s["total_candles"] for s in symbol_stats.values()) if symbol_stats else sum(p["num_rows"] for p in partitions)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "catalog_path": str(catalog_path.relative_to(base_dir).as_posix()) if catalog_path.is_relative_to(base_dir) else str(catalog_path),
        "data_dir": str(data_dir.relative_to(base_dir).as_posix()) if data_dir.is_relative_to(base_dir) else str(data_dir),
        "total_partitions": len(partitions),
        "total_candles": total_candles,
        "symbol_stats": symbol_stats,
        "dataset_hash": dataset_hash,
        "partitions": partitions,
    }
    return manifest


def verify_dataset_integrity(
    manifest_path: str | Path = REPO_ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json",
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    base_dir: str | Path | None = None,
) -> tuple[bool, str, str]:
    """Verify live dataset hash against saved manifest.

    Returns (is_valid, live_dataset_hash, error_message).
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return False, "", f"Saved dataset manifest not found: {manifest_path}"

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        saved_hash = saved_data.get("dataset_hash", "")
    except Exception as e:
        return False, "", f"Failed to read saved dataset manifest: {e}"

    effective_base = Path(base_dir) if base_dir is not None else REPO_ROOT
    try:
        live_manifest = generate_dataset_manifest(
            catalog_path=catalog_path,
            data_dir=data_dir,
            base_dir=effective_base,
        )
        live_hash = live_manifest.get("dataset_hash", "")
    except Exception as e:
        return False, "", f"Failed to compute live dataset hash from actual files: {e}"

    if not saved_hash or not live_hash or saved_hash != live_hash:
        return False, live_hash, f"Dataset hash mismatch: saved={saved_hash} vs live={live_hash}"

    return True, live_hash, ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate dataset manifest for AutoTrade V3")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG_PATH), help="DuckDB catalog path")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="M1 raw data directory")
    parser.add_argument("--output", "-o", default=None, help="Output JSON file path")
    args = parser.parse_args()

    manifest = generate_dataset_manifest(
        catalog_path=args.catalog,
        data_dir=args.data_dir,
    )

    out_json = json.dumps(manifest, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_json, encoding="utf-8")
        print(f"Dataset manifest written to {out_path}")
        print(f"Total partitions: {manifest['total_partitions']}")
        print(f"Total candles:    {manifest['total_candles']:,}")
        print(f"Dataset hash:     {manifest['dataset_hash']}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
