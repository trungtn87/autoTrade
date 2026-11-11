"""Strategy and signal verification gates B1, B2, B3.

B1: Frozen signal parity against reference signals.parquet (for 20FINAL baseline: runs/golden/v3-08/ALL_SIGNALS/signals.parquet).
B2: No-lookahead test using N cutoff points.
B3: Batch vs live equivalence (returns INCONCLUSIVE until V3-11).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.hashing import content_hash
from autotrade_v3.validation.types import (
    GateResult,
    ViolationCollector,
    failed,
    finalize,
    inconclusive,
)


def gate_b1(ctx: VerifyContext) -> GateResult:
    """Compare the run's signals with the frozen reference signals for the strategy & window."""
    name = "Frozen signal parity"
    manifest = ctx.run.manifest
    run_sigs = ctx.run.signals

    if manifest is None or run_sigs is None:
        return inconclusive("B1", name, f"required inputs unavailable: {sorted(ctx.run.errors)}")

    total = len(run_sigs)

    # Determine reference signals file
    ref_path = ctx.reference_signals
    if ref_path is None:
        # Default baseline reference for 20FINAL 3Y window
        cand = ctx.repo_root / "runs" / "golden" / "v3-08" / "ALL_SIGNALS" / "signals.parquet"
        if cand.exists():
            ref_path = cand

    strat_id = str(manifest.get("strategy_id", ""))
    tw = manifest.get("time_window", {})
    start = tw.get("start", "")
    end = tw.get("end", "")

    # B1 is only applicable if using 20FINAL_BASELINE on 2023-11-01 -> 2026-11-01
    if strat_id != "20FINAL_BASELINE" or start != "2023-11-01" or end != "2026-11-01":
        return inconclusive(
            "B1",
            name,
            f"Strategy ({strat_id}) or window ({start} to {end}) does not match frozen reference baseline.",
            total=total,
        )

    if ctx.is_synthetic:
        return inconclusive(
            "B1",
            name,
            "Synthetic dataset run; frozen reference parity applies only to real baseline.",
            total=total,
        )

    syms = set(manifest.get("symbols", []))
    if syms and syms != {"BTC-USDT", "ETH-USDT"}:
        return inconclusive(
            "B1",
            name,
            f"Subset of symbols ({sorted(syms)}); frozen reference parity requires full symbol universe.",
            total=total,
        )

    if ref_path is None or not ref_path.exists():
        return inconclusive(
            "B1",
            name,
            f"Reference signals file not found at {ref_path}",
            total=total,
        )

    try:
        ref_sigs = pd.read_parquet(ref_path)
    except Exception as e:
        return failed("B1", name, f"Cannot read reference signals: {e}", total=total)

    viol = ViolationCollector()

    ref_ids = set(ref_sigs["signal_id"].astype(str))
    run_ids = set(run_sigs["signal_id"].astype(str))

    missing = sorted(ref_ids - run_ids)
    extra = sorted(run_ids - ref_ids)

    if missing:
        viol.add_many(len(missing), [{"signal_id": s, "kind": "MISSING_FROM_RUN"} for s in missing[:20]])
    if extra:
        viol.add_many(len(extra), [{"signal_id": s, "kind": "EXTRA_IN_RUN"} for s in extra[:20]])

    # Per (symbol, combo) breakdown & content hash
    per_combo: dict[str, Any] = {}
    run_grouped = run_sigs.groupby(["symbol", "combo_id"])
    ref_grouped = ref_sigs.groupby(["symbol", "combo_id"])

    all_keys = sorted(set(run_grouped.groups.keys()) | set(ref_grouped.groups.keys()))
    for sym, cid in all_keys:
        k_str = f"{sym}_C{cid}"
        r_df = run_grouped.get_group((sym, cid)) if (sym, cid) in run_grouped.groups else pd.DataFrame()
        ref_df = ref_grouped.get_group((sym, cid)) if (sym, cid) in ref_grouped.groups else pd.DataFrame()

        r_cnt = len(r_df)
        ref_cnt = len(ref_df)
        r_hash = content_hash(r_df) if r_cnt else ""
        ref_hash = content_hash(ref_df) if ref_cnt else ""

        match = (r_cnt == ref_cnt) and (r_hash == ref_hash)
        per_combo[k_str] = {
            "run_count": r_cnt,
            "ref_count": ref_cnt,
            "run_content_hash": r_hash,
            "ref_content_hash": ref_hash,
            "match": match,
        }
        if not match:
            viol.add({
                "combo_key": k_str,
                "kind": "COMBO_CONTENT_MISMATCH",
                "run_count": r_cnt,
                "ref_count": ref_cnt,
            })

    # Total count vs expected
    if len(run_sigs) != len(ref_sigs):
        viol.add({
            "kind": "TOTAL_COUNT_MISMATCH",
            "expected": len(ref_sigs),
            "actual": len(run_sigs),
        })

    return finalize(
        "B1",
        name,
        checked=total,
        total=total,
        viol=viol,
        details={
            "reference_file": str(ref_path),
            "per_combo": per_combo,
            "total_signals": total,
        },
    )


def gate_b2(ctx: VerifyContext) -> GateResult:
    """No-lookahead verification: select N cutoff points T.
    
    Re-generate signals only from M1 data before T and ensure that signals
    with signal_close_ms <= T match exactly the corresponding subset of the full run.
    """
    name = "No-lookahead validation"
    manifest = ctx.run.manifest
    run_sigs = ctx.run.signals

    if manifest is None or run_sigs is None:
        return inconclusive("B2", name, f"required inputs unavailable: {sorted(ctx.run.errors)}")

    if len(run_sigs) == 0:
        return finalize("B2", name, 0, 0, ViolationCollector(), details={"note": "no signals in run"})

    # M1 check
    dm = ctx.run.dataset_manifest
    if not dm:
        return inconclusive("B2", name, "dataset_manifest.json missing")
    parts = dm.get("partitions", [])
    if parts and not any((ctx.data_root / p["relative_path"]).exists() for p in parts):
        return inconclusive("B2", name, f"BLOCKED: M1 data not present under {ctx.data_root}")

    strat_id = str(manifest.get("strategy_id", ""))
    try:
        from autotrade_v3.strategy.registry import StrategyRegistry
        strat = StrategyRegistry.get(strat_id)
    except Exception as e:
        return inconclusive("B2", name, f"Cannot load strategy {strat_id}: {e}")

    store = ctx.m1_store()
    if store is None:
        return inconclusive("B2", name, "M1 store could not be initialized")

    t0 = time.perf_counter()
    viol = ViolationCollector()

    # Determine cutoff timestamps
    n_cutoffs = ctx.n_cutoffs
    seed = ctx.seed
    rng = np.random.default_rng(seed)

    all_close_ms = np.sort(run_sigs["signal_close_ms"].unique())
    if len(all_close_ms) <= n_cutoffs:
        cutoffs = all_close_ms
    else:
        # Uniformly spaced + random jitter
        indices = np.linspace(len(all_close_ms) // 10, len(all_close_ms) - 1, n_cutoffs, dtype=int)
        cutoffs = all_close_ms[indices]

    symbols = manifest.get("symbols", ["BTC-USDT", "ETH-USDT"])
    cutoff_results = []

    # Map full signals by id
    full_sig_map = run_sigs.set_index("signal_id")

    checked_points = 0
    total_points = len(cutoffs)

    from autotrade_v3.data_layer import DataLayer
    dl = DataLayer(catalog_path=ctx.repo_root / "catalog" / "research.duckdb", data_dir=ctx.data_root / "data" / "m1_raw")

    tw = manifest.get("time_window", {})
    start_date = tw.get("start", "2023-11-01")
    end_date = tw.get("end", "2026-11-01")

    # Optimization: Query full M15 series once per symbol with warmup, then slice per cutoff
    full_data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            full_data[sym] = dl.query_timeframe(
                symbol=sym,
                timeframe="15m",
                start=start_date,
                end=end_date,
                include_warmup=True,
            )
        except Exception as e:
            return inconclusive("B2", name, f"Failed to load M15 data for {sym}: {e}")

    for cutoff_t in cutoffs:
        checked_points += 1
        cutoff_dt = pd.Timestamp(cutoff_t, unit="ms", tz="UTC")
        cutoff_dt_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Full run signals up to cutoff_t
        expected_subset = run_sigs[run_sigs["signal_close_ms"] <= cutoff_t]
        expected_ids = set(expected_subset["signal_id"])

        # Slice data strictly up to cutoff_dt (candles that closed at or before cutoff_t)
        sub_data = {sym: full_data[sym][full_data[sym].index < cutoff_dt] for sym in symbols}

        # Re-run strategy
        try:
            replayed_sigs = strat.generate_signals(
                data=sub_data,
                symbols=symbols,
                only_approved=True,
                only_warmup_ready=True,
            )
        except Exception as e:
            viol.add({
                "cutoff_ms": int(cutoff_t),
                "kind": "STRATEGY_REPLAY_FAILED",
                "error": str(e),
            })
            continue

        replayed_ids = {s.signal_id for s in replayed_sigs if s.signal_close_ms <= cutoff_t}

        missing = expected_ids - replayed_ids
        extra = replayed_ids - expected_ids

        if missing or extra:
            viol.add({
                "cutoff_ms": int(cutoff_t),
                "cutoff_dt": cutoff_dt_str,
                "kind": "LOOKAHEAD_DETECTED",
                "missing_signals": sorted(missing)[:5],
                "extra_signals": sorted(extra)[:5],
            })

        cutoff_results.append({
            "cutoff_ms": int(cutoff_t),
            "cutoff_dt": cutoff_dt_str,
            "expected_count": len(expected_ids),
            "replayed_count": len(replayed_ids),
            "pass": len(missing) == 0 and len(extra) == 0,
        })

    duration = time.perf_counter() - t0

    return finalize(
        "B2",
        name,
        checked=checked_points,
        total=total_points,
        viol=viol,
        details={
            "seed": seed,
            "n_cutoffs": n_cutoffs,
            "duration_s": round(duration, 2),
            "cutoffs_tested": cutoff_results,
        },
    )


def gate_b3(ctx: VerifyContext) -> GateResult:
    """Batch vs live equivalence test.
    
    Must return INCONCLUSIVE until V3-11 introduces on_bar_close.
    Never returns PASS.
    """
    name = "Batch-Live equivalence"
    return inconclusive(
        "B3",
        name,
        reason="on_bar_close chưa tồn tại",
        total=len(ctx.run.signals) if ctx.run.signals is not None else 0,
        details={
            "spec_section": "4.3 Gate B3",
            "blocked_until": "V3-11",
        },
    )
