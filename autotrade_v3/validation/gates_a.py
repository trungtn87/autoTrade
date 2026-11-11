"""Gate A1 (data hash) and A2 (M1 integrity)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from autotrade_v3.validation.context import M1_MS, VerifyContext, canon_symbol
from autotrade_v3.validation.hashing import dataset_hash_from_partitions, sha256_file
from autotrade_v3.validation.types import GateResult, ViolationCollector, finalize, inconclusive


def gate_a1(ctx: VerifyContext) -> GateResult:
    """Recompute SHA256 of every Parquet partition and compare with the run's dataset manifest."""
    name = "Dataset hash"
    dm = ctx.run.dataset_manifest
    if not dm:
        return inconclusive("A1", name, "dataset_manifest.json missing or unreadable: " + ctx.run.errors.get("dataset_manifest.json", ""))
    parts: list[dict[str, Any]] = list(dm.get("partitions", []))
    total = len(parts)
    root = ctx.data_root
    if total and not any((root / p["relative_path"]).exists() for p in parts):
        return inconclusive(
            "A1", name, f"BLOCKED: no partition of the manifest exists under dataset root {root}", total=total
        )

    viol = ViolationCollector()
    checked = 0
    measured: list[dict[str, Any]] = []
    for p in parts:
        rel = p["relative_path"]
        path = root / rel
        checked += 1
        if not path.exists():
            viol.add({"partition": rel, "kind": "MISSING_FILE"})
            continue
        size = path.stat().st_size
        sha = sha256_file(path)
        try:
            rows = pq.read_metadata(path).num_rows
        except Exception as e:  # noqa: BLE001
            viol.add({"partition": rel, "kind": "UNREADABLE", "error": str(e)})
            continue
        measured.append({"relative_path": rel, "size_bytes": size, "num_rows": rows, "sha256": sha})
        if sha != p.get("sha256"):
            viol.add({"partition": rel, "kind": "SHA256_MISMATCH", "expected": p.get("sha256"), "actual": sha})
        if size != p.get("size_bytes"):
            viol.add({"partition": rel, "kind": "SIZE_MISMATCH", "expected": p.get("size_bytes"), "actual": size})
        if rows != p.get("num_rows"):
            viol.add({"partition": rel, "kind": "ROWS_MISMATCH", "expected": p.get("num_rows"), "actual": rows})

    # Extra partitions present in the data directory but absent from the manifest.
    data_dir = dm.get("data_dir")
    if data_dir and (root / data_dir).exists():
        listed = {p["relative_path"] for p in parts}
        for f in sorted((root / data_dir).rglob("*.parquet")):
            rel = f.relative_to(root).as_posix()
            if rel not in listed:
                viol.add({"partition": rel, "kind": "UNLISTED_PARTITION"})

    if dm.get("total_partitions") != total:
        viol.add({"kind": "MANIFEST_PARTITION_COUNT", "expected": dm.get("total_partitions"), "actual": total})
    if dm.get("total_candles") != sum(int(p.get("num_rows", 0)) for p in parts):
        viol.add({"kind": "MANIFEST_CANDLE_TOTAL", "expected": dm.get("total_candles"),
                  "actual": sum(int(p.get("num_rows", 0)) for p in parts)})

    recomputed_declared = dataset_hash_from_partitions(parts)
    recomputed_measured = dataset_hash_from_partitions(measured) if len(measured) == total else ""
    if recomputed_declared != dm.get("dataset_hash"):
        viol.add({"kind": "DATASET_HASH_FORMULA", "expected": dm.get("dataset_hash"), "actual": recomputed_declared})
    if recomputed_measured and recomputed_measured != dm.get("dataset_hash"):
        viol.add({"kind": "DATASET_HASH_MEASURED", "expected": dm.get("dataset_hash"), "actual": recomputed_measured})
    run_hash = (ctx.run.manifest or {}).get("dataset_hash")
    if run_hash != dm.get("dataset_hash"):
        viol.add({"kind": "RUN_MANIFEST_DATASET_HASH", "run_manifest": run_hash, "dataset_manifest": dm.get("dataset_hash")})

    return finalize(
        "A1", name, checked, total, viol,
        details={"dataset_hash_declared": dm.get("dataset_hash"), "dataset_hash_measured": recomputed_measured,
                 "data_root": str(root)},
    )


def gate_a2(ctx: VerifyContext) -> GateResult:
    """Per-symbol M1 integrity: gaps, duplicates, misaligned minutes, absurd OHLC, negative volume."""
    name = "M1 integrity"
    dm = ctx.run.dataset_manifest
    store = ctx.m1_store()
    if not dm or store is None:
        return inconclusive("A2", name, "dataset_manifest.json missing or unreadable")
    stats: dict[str, Any] = dm.get("symbol_stats") or {}
    if not stats:
        return inconclusive("A2", name, "dataset_manifest.json has no symbol_stats")
    parts = list(dm.get("partitions", []))
    if parts and not any((ctx.data_root / p["relative_path"]).exists() for p in parts):
        return inconclusive("A2", name, f"BLOCKED: M1 data not present under {ctx.data_root}")

    viol = ViolationCollector()
    checked = 0
    total = sum(int(s["total_candles"]) for s in stats.values())
    per_symbol: dict[str, Any] = {}
    t0 = time.perf_counter()
    for sym_key, st in sorted(stats.items()):
        try:
            s = store.get(sym_key)
        except FileNotFoundError:
            viol.add({"symbol": sym_key, "kind": "NO_PARTITIONS"})
            continue
        n = len(s.t)
        checked += n
        min_ms, max_ms = int(st["min_open_ms"]), int(st["max_open_ms"])
        expected = (max_ms - min_ms) // M1_MS + 1
        unique_t = np.unique(s.t)
        dups = n - len(unique_t)
        misaligned = int(np.count_nonzero(s.t % M1_MS != 0))
        gaps = np.diff(unique_t)
        gap_idx = np.nonzero(gaps > M1_MS)[0]
        missing_in_range = int(((gaps[gap_idx] // M1_MS) - 1).sum()) if len(gap_idx) else 0
        edge_missing = 0
        if len(unique_t):
            if unique_t[0] > min_ms:
                edge_missing += int((unique_t[0] - min_ms) // M1_MS)
            if unique_t[-1] < max_ms:
                edge_missing += int((max_ms - unique_t[-1]) // M1_MS)
        missing = missing_in_range + edge_missing
        with np.errstate(invalid="ignore"):
            bad_num = ~(np.isfinite(s.o) & np.isfinite(s.h) & np.isfinite(s.l) & np.isfinite(s.c) & np.isfinite(s.v))
            bad_price = (s.o <= 0) | (s.h <= 0) | (s.l <= 0) | (s.c <= 0)
            bad_ohlc = (s.h < s.l) | (s.h < s.o) | (s.h < s.c) | (s.l > s.o) | (s.l > s.c)
            neg_vol = s.v < 0
        counts = {
            "rows": n, "expected_rows_from_manifest_range": expected, "manifest_total_candles": int(st["total_candles"]),
            "missing": missing, "duplicates": dups, "misaligned": misaligned,
            "nonfinite": int(bad_num.sum()), "nonpositive_price": int(bad_price.sum()),
            "ohlc_inconsistent": int(bad_ohlc.sum()), "negative_volume": int(neg_vol.sum()),
        }
        per_symbol[sym_key] = counts
        if n != int(st["total_candles"]):
            viol.add({"symbol": sym_key, "kind": "COUNT_VS_MANIFEST", "expected": int(st["total_candles"]), "actual": n})
        if n and (int(unique_t[0]) != min_ms or int(unique_t[-1]) != max_ms):
            viol.add({"symbol": sym_key, "kind": "RANGE_VS_MANIFEST",
                      "expected": [min_ms, max_ms], "actual": [int(unique_t[0]), int(unique_t[-1])]})
        if int(st["total_candles"]) != expected:
            viol.add({"symbol": sym_key, "kind": "MANIFEST_COUNT_VS_RANGE",
                      "manifest_total_candles": int(st["total_candles"]), "expected_from_range": expected})
        if missing:
            ex = [{"symbol": sym_key, "kind": "MISSING_CANDLE", "after_open_ms": int(unique_t[i]),
                   "missing_count": int(gaps[i] // M1_MS - 1)} for i in gap_idx[:5]]
            viol.add_many(missing, ex)
        if dups:
            d_ms = unique_t[np.nonzero(np.bincount(np.searchsorted(unique_t, s.t)) > 1)[0][:5]]
            viol.add_many(dups, [{"symbol": sym_key, "kind": "DUPLICATE_CANDLE", "open_ms": int(x)} for x in d_ms])
        for key, mask, kind in (
            ("misaligned", s.t % M1_MS != 0, "MISALIGNED_MINUTE"),
            ("nonfinite", bad_num, "NON_FINITE_VALUE"),
            ("nonpositive_price", bad_price, "NON_POSITIVE_PRICE"),
            ("ohlc_inconsistent", bad_ohlc, "OHLC_INCONSISTENT"),
            ("negative_volume", neg_vol, "NEGATIVE_VOLUME"),
        ):
            cnt = int(mask.sum())
            if cnt:
                idx = np.nonzero(mask)[0][:5]
                viol.add_many(cnt, [{"symbol": sym_key, "kind": kind, "open_ms": int(s.t[i])} for i in idx])
    res = finalize("A2", name, checked, total, viol, details={"per_symbol": per_symbol,
                                                              "scan_seconds": round(time.perf_counter() - t0, 3)})
    return res


__all__ = ["gate_a1", "gate_a2", "canon_symbol", "Path"]
