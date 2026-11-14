"""Strategy and signal verification gates B1, B2, B3.

B1: Frozen signal parity against versioned reference signals.parquet (for 20FINAL@1: runs/golden/v3-08/ALL_SIGNALS/signals.parquet) and baseline lock verification.
B2: No-lookahead test using N cutoff points.
B3: Batch (`generate`) vs Live incremental (`on_bar_close`) equivalence and closed-candle enforcement.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autotrade_v3.strategy_core.hashing import normalize_lf
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry, parse_strategy_ref
from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.hashing import content_hash
from autotrade_v3.validation.types import (
    GateResult,
    ViolationCollector,
    failed,
    finalize,
    inconclusive,
)


def _resolve_reference_signals_by_version(
    repo_root: Path,
    strategy_id: str,
    strategy_version: str,
    explicit_ref: Path | None = None,
) -> Path | None:
    """Resolve reference signals.parquet path by (strategy_id, version)."""
    if explicit_ref is not None:
        return explicit_ref

    sid, ver = parse_strategy_ref(strategy_id, strategy_version)
    if (sid, ver) == ("20FINAL", "1"):
        cand = repo_root / "runs" / "golden" / "v3-08" / "ALL_SIGNALS" / "signals.parquet"
        if cand.exists():
            return cand

    # Versioned reference path lookup for other strategy versions if available
    slug = f"{sid.lower()}_v{ver}"
    for cand in (
        repo_root / "runs" / "golden" / slug / "ALL_SIGNALS" / "signals.parquet",
        repo_root / "autotrade_v3" / "strategy_core" / "strategies" / "final20" / "params" / slug / "reference_signals.parquet",
    ):
        if cand.exists():
            return cand
    return None


def gate_b1(ctx: VerifyContext) -> GateResult:
    """Compare the run's signals with the frozen reference signals for (strategy_id, version) & window."""
    name = "Frozen signal parity"
    manifest = ctx.run.manifest
    run_sigs = ctx.run.signals

    if manifest is None or run_sigs is None:
        return inconclusive("B1", name, f"required inputs unavailable: {sorted(ctx.run.errors)}")

    total = len(run_sigs)

    strat_id = str(manifest.get("strategy_id", ""))
    strat_ver = str(manifest.get("strategy_version", "1"))
    sid_canon, ver_canon = parse_strategy_ref(strat_id, strat_ver)

    tw = manifest.get("time_window", {})
    start = tw.get("start", "")
    end = tw.get("end", "")

    ref_path = _resolve_reference_signals_by_version(
        ctx.repo_root,
        sid_canon,
        ver_canon,
        ctx.reference_signals,
    )

    if (sid_canon, ver_canon) != ("20FINAL", "1") and ref_path is None:
        return inconclusive(
            "B1",
            name,
            f"Strategy ({sid_canon}@{ver_canon}) has no registered frozen reference baseline.",
            total=total,
        )

    if start != "2023-11-01" or end != "2026-11-01":
        return inconclusive(
            "B1",
            name,
            f"Window ({start} to {end}) does not match frozen 3-year reference window (2023-11-01 to 2026-11-01).",
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

    # Verify signal_spec_hash against baseline lock if present in manifest and lock file exists
    lock_file = (
        ctx.repo_root
        / "autotrade_v3"
        / "strategy_core"
        / "strategies"
        / "final20"
        / "params"
        / "20final_v1.lock"
    )
    lock_verified = False
    if (sid_canon, ver_canon) == ("20FINAL", "1"):
        if not lock_file.exists():
            viol.add({"kind": "LOCK_FILE_MISSING", "path": str(lock_file)})
        else:
            try:
                lock_data = json.loads(normalize_lf(lock_file.read_text(encoding="utf-8")))
                run_sig_hash = manifest.get("signal_spec_hash")
                if run_sig_hash:
                    if run_sig_hash != lock_data.get("signal_spec_hash"):
                        viol.add({
                            "kind": "SIGNAL_SPEC_HASH_MISMATCH",
                            "expected": lock_data.get("signal_spec_hash"),
                            "actual": run_sig_hash,
                        })
                    else:
                        lock_verified = True
                else:
                    # Legacy manifests (e.g. V3-08 golden) predate signal_spec_hash; never mark lock_verified=True
                    lock_verified = False
            except Exception as e:
                viol.add({"kind": "LOCK_READ_ERROR", "error": str(e)})

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
            "strategy_ref": f"{sid_canon}@{ver_canon}",
            "reference_file": str(ref_path),
            "lock_verified": lock_verified,
            "per_combo": per_combo,
            "total_signals": total,
        },
    )


_REPLAY_CACHE: dict[tuple[Any, ...], list[Any]] = {}
_B3_CACHE: dict[tuple[Any, ...], tuple[int, int, list[dict[str, Any]], dict[str, Any]]] = {}


def clear_replay_cache() -> None:
    """Clear in-memory strategy replay caches for Gates B2 and B3."""
    global _REPLAY_CACHE, _B3_CACHE
    _REPLAY_CACHE.clear()
    _B3_CACHE.clear()


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
    strat_ver = str(manifest.get("strategy_version", "1"))
    try:
        from autotrade_v3.strategy.registry import StrategyRegistry
        try:
            strat = StrategyRegistry.get(f"{strat_id}@{strat_ver}")
        except KeyError:
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
        buckets = np.array_split(all_close_ms, n_cutoffs)
        cutoffs = np.array([int(rng.choice(b)) for b in buckets], dtype=np.int64)
        cutoffs.sort()

    symbols = manifest.get("symbols", ["BTC-USDT", "ETH-USDT"])
    cutoff_results = []

    checked_points = 0
    total_points = len(cutoffs)

    from autotrade_v3.data_layer import DataLayer
    dl = DataLayer(catalog_path=ctx.repo_root / "catalog" / "research.duckdb", data_dir=ctx.data_root / "data" / "m1_raw")

    tw = manifest.get("time_window", {})
    start_date = tw.get("start", "2023-11-01")
    end_date = tw.get("end", "2026-11-01")

    config_hash = str(
        manifest.get("spec_hash")
        or manifest.get("signal_spec_hash")
        or manifest.get("config_hash")
        or ""
    )
    if not config_hash:
        cfg_repr = f"{strat_id}_{getattr(strat, 'version', '1.0')}"
        config_hash = hashlib.sha256(cfg_repr.encode("utf-8")).hexdigest()

    dataset_hash = str(manifest.get("dataset_hash") or (dm.get("dataset_hash") if dm else "") or "")
    symbols_tuple = tuple(sorted(symbols))
    run_cache = ctx.cache.setdefault("b2_replay_cache", {})

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

    def _eval_single_cutoff(cutoff_t: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        cutoff_dt = pd.Timestamp(cutoff_t, unit="ms", tz="UTC")
        cutoff_dt_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

        expected_subset = run_sigs[run_sigs["signal_close_ms"] <= cutoff_t]
        expected_sig_dict = {
            str(s["signal_id"]): {
                "combo_id": int(s["combo_id"]),
                "side": str(s["side"]),
                "signal_close_ms": int(s["signal_close_ms"]),
                "entry_price": float(s["entry_price"]),
                "tp_price": float(s["tp_price"]),
                "sl_price": float(s["sl_price"]),
            }
            for _, s in expected_subset.iterrows()
        }
        expected_ids = set(expected_sig_dict.keys())

        sub_data = {sym: full_data[sym][full_data[sym].index < cutoff_dt] for sym in symbols}

        cutoff_viols: list[dict[str, Any]] = []
        cache_key = (
            strat_id,
            config_hash,
            dataset_hash,
            symbols_tuple,
            start_date,
            end_date,
            int(cutoff_t),
        )
        if cache_key in run_cache:
            replayed_sigs = run_cache[cache_key]
        elif cache_key in _REPLAY_CACHE:
            replayed_sigs = _REPLAY_CACHE[cache_key]
            run_cache[cache_key] = replayed_sigs
        else:
            try:
                replayed_sigs = strat.generate_signals(
                    data=sub_data,
                    symbols=symbols,
                    only_approved=True,
                    only_warmup_ready=True,
                )
                run_cache[cache_key] = replayed_sigs
                _REPLAY_CACHE[cache_key] = replayed_sigs
            except Exception as e:
                cutoff_viols.append({
                    "cutoff_ms": int(cutoff_t),
                    "kind": "STRATEGY_REPLAY_FAILED",
                    "error": str(e),
                })
                return {
                    "cutoff_ms": int(cutoff_t),
                    "cutoff_dt": cutoff_dt_str,
                    "expected_count": len(expected_ids),
                    "replayed_count": 0,
                    "content_matched": False,
                    "pass": False,
                }, cutoff_viols

        replayed_subset = [s for s in replayed_sigs if s.signal_close_ms <= cutoff_t]
        replayed_ids = {str(s.signal_id) for s in replayed_subset}

        missing = expected_ids - replayed_ids
        extra = replayed_ids - expected_ids

        content_mismatches = []
        for s in replayed_subset:
            sid = str(s.signal_id)
            if sid in expected_sig_dict:
                exp = expected_sig_dict[sid]
                if (
                    s.combo_id != exp["combo_id"]
                    or s.side != exp["side"]
                    or s.signal_close_ms != exp["signal_close_ms"]
                    or abs(float(s.entry_price) - exp["entry_price"]) > 1e-6
                    or abs(float(s.tp_price) - exp["tp_price"]) > 1e-6
                    or abs(float(s.sl_price) - exp["sl_price"]) > 1e-6
                ):
                    content_mismatches.append(sid)

        is_passed = (len(missing) == 0 and len(extra) == 0 and len(content_mismatches) == 0)

        if not is_passed:
            cutoff_viols.append({
                "cutoff_ms": int(cutoff_t),
                "cutoff_dt": cutoff_dt_str,
                "kind": "LOOKAHEAD_DETECTED",
                "missing_signals": sorted(missing)[:5],
                "extra_signals": sorted(extra)[:5],
                "content_mismatches": sorted(content_mismatches)[:5],
            })

        return {
            "cutoff_ms": int(cutoff_t),
            "cutoff_dt": cutoff_dt_str,
            "expected_count": len(expected_ids),
            "replayed_count": len(replayed_ids),
            "content_matched": len(content_mismatches) == 0,
            "pass": is_passed,
        }, cutoff_viols

    workers = min(8, n_cutoffs)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            raw_results = list(pool.map(_eval_single_cutoff, cutoffs))
    else:
        raw_results = [_eval_single_cutoff(c) for c in cutoffs]

    for c_info, c_viols in raw_results:
        checked_points += 1
        for v in c_viols:
            viol.add(v)
        cutoff_results.append(c_info)

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
            "exit_code": 0 if viol.count == 0 else 1,
            "sampling_policy": "uniform_buckets_random_jitter",
            "boundary_policy": "strictly past closed bars (bar_open_index < cutoff_dt)",
        },
    )


def gate_b3(ctx: VerifyContext) -> GateResult:
    """Batch vs Live incremental equivalence test (enabled in V3-11).

    Verifies that:
    1. Incremental `on_bar_close(state, bar)` rejects unclosed candles (`is_closed=False`).
    2. Across the run's M15 dataset, `generate(bars)` (batch) and `on_bar_close(state, bar)`
       (bar-by-bar incremental O(1)) produce the exact same SignalEvent set and identical fields/tags.
    3. The run's persisted `signals.parquet` matches the evaluation-window signals from `on_bar_close`.
    """
    name = "Batch-Live equivalence"
    manifest = ctx.run.manifest
    run_sigs = ctx.run.signals

    if manifest is None or run_sigs is None:
        return inconclusive("B3", name, f"required inputs unavailable: {sorted(ctx.run.errors)}")

    total = len(run_sigs)
    if ctx.is_synthetic:
        return inconclusive(
            "B3",
            name,
            "Synthetic dataset run; Batch-Live full M15 equivalence requires real dataset.",
            total=total,
        )

    dm = ctx.run.dataset_manifest
    if not dm:
        return inconclusive("B3", name, "dataset_manifest.json missing", total=total)
    parts = dm.get("partitions", [])
    if parts and not any((ctx.data_root / p["relative_path"]).exists() for p in parts):
        return inconclusive("B3", name, f"BLOCKED: M1 data not present under {ctx.data_root}", total=total)

    strat_id = str(manifest.get("strategy_id", "20FINAL"))
    strat_ver = str(manifest.get("strategy_version", "1"))
    try:
        core_strat = StrategyCoreRegistry.get(strat_id, strat_ver)
    except Exception as e:
        return inconclusive("B3", name, f"Cannot load core strategy {strat_id}@{strat_ver}: {e}", total=total)

    viol = ViolationCollector()
    t0 = time.perf_counter()

    # 1. Verify closed-candle enforcement: passing is_closed=False must raise ValueError
    unclosed_rejected = False
    try:
        probe_state: dict[str, Any] = {}
        core_strat.on_bar_close(
            probe_state,
            {
                "symbol": "BTC-USDT",
                "open_ms": 1698796800000,
                "open": 35000.0,
                "high": 35100.0,
                "low": 34900.0,
                "close": 35050.0,
                "volume": 100.0,
                "is_closed": False,
            },
        )
    except ValueError:
        unclosed_rejected = True
    except Exception as e:
        viol.add({"kind": "UNCLOSED_BAR_UNEXPECTED_ERROR", "error": str(e)})

    if not unclosed_rejected:
        viol.add({"kind": "UNCLOSED_BAR_ACCEPTED", "detail": "on_bar_close did not reject is_closed=False"})

    # 2. Check batch vs live equivalence over the M15 window
    symbols = manifest.get("symbols", ["BTC-USDT", "ETH-USDT"])
    tw = manifest.get("time_window", {})
    start_date = tw.get("start", "2023-11-01")
    end_date = tw.get("end", "2026-11-01")
    start_ms = int(pd.Timestamp(start_date, tz="UTC").value // 1_000_000)
    end_ms = int(pd.Timestamp(end_date, tz="UTC").value // 1_000_000)

    spec = core_strat.spec()
    dataset_hash = str(manifest.get("dataset_hash") or (dm.get("dataset_hash") if dm else "") or "")
    cache_key = (
        spec.strategy_id,
        spec.version,
        spec.signal_spec_hash,
        dataset_hash,
        tuple(sorted(symbols)),
        start_date,
        end_date,
    )

    if cache_key in _B3_CACHE:
        cached_checked, _, cached_viols, cached_details = _B3_CACHE[cache_key]
        for v in cached_viols:
            viol.add(v)
        live_eval_records = list(cached_details.get("live_eval_records", []))
        per_symbol_stats = cached_details.get("per_symbol", {})
    else:
        from autotrade_v3.data_layer import DataLayer
        dl = DataLayer(
            catalog_path=ctx.repo_root / "catalog" / "research.duckdb",
            data_dir=ctx.data_root / "data" / "m1_raw",
        )
        per_symbol_stats = {}
        live_eval_records: list[dict[str, Any]] = []
        replay_viols: list[dict[str, Any]] = []

        for sym in symbols:
            try:
                df = dl.query_timeframe(sym, "15m", start_date, end_date, include_warmup=True)
            except Exception as e:
                return inconclusive("B3", name, f"Failed to load M15 data for {sym}: {e}", total=total)

            batch_evs = core_strat.generate(df, symbols=[sym], only_approved=True, only_warmup_ready=True)

            live_state: dict[str, Any] = {}
            t_arr = df.index.asi8 // 1_000_000
            o_arr = df["open"].to_numpy(float)
            h_arr = df["high"].to_numpy(float)
            l_arr = df["low"].to_numpy(float)
            c_arr = df["close"].to_numpy(float)
            v_arr = df["volume"].to_numpy(float)
            p_arr = df["provider"].astype(str).to_numpy() if "provider" in df.columns else np.full(len(df), "UNKNOWN")

            live_evs = []
            for i in range(len(df)):
                bar_dict = {
                    "symbol": sym,
                    "open_ms": int(t_arr[i]),
                    "open": float(o_arr[i]),
                    "high": float(h_arr[i]),
                    "low": float(l_arr[i]),
                    "close": float(c_arr[i]),
                    "volume": float(v_arr[i]),
                    "provider": str(p_arr[i]),
                    "is_closed": True,
                }
                evs = core_strat.on_bar_close(live_state, bar_dict, only_approved=True, only_warmup_ready=True)
                live_evs.extend(evs)

            b_map = {e.signal_id: e for e in batch_evs}
            l_map = {e.signal_id: e for e in live_evs}

            only_batch = sorted(set(b_map.keys()) - set(l_map.keys()))
            only_live = sorted(set(l_map.keys()) - set(b_map.keys()))
            field_diffs = []
            for sid in sorted(set(b_map.keys()) & set(l_map.keys())):
                be = b_map[sid]
                le = l_map[sid]
                if (
                    be.combo_key != le.combo_key
                    or be.symbol != le.symbol
                    or be.side != le.side
                    or be.native_open_ms != le.native_open_ms
                    or be.signal_close_ms != le.signal_close_ms
                    or abs(be.ref_price - le.ref_price) > 1e-12
                    or be.tags != le.tags
                ):
                    field_diffs.append(sid)

            if only_batch:
                replay_viols.append({
                    "symbol": sym,
                    "kind": "ONLY_IN_BATCH",
                    "count": len(only_batch),
                    "sample": only_batch[:10],
                })
            if only_live:
                replay_viols.append({
                    "symbol": sym,
                    "kind": "ONLY_IN_LIVE",
                    "count": len(only_live),
                    "sample": only_live[:10],
                })
            if field_diffs:
                replay_viols.append({
                    "symbol": sym,
                    "kind": "FIELD_MISMATCH_BATCH_VS_LIVE",
                    "count": len(field_diffs),
                    "sample": field_diffs[:10],
                })

            for e in live_evs:
                if start_ms <= e.signal_close_ms + 1 < end_ms:
                    live_eval_records.append({
                        "signal_id": str(e.signal_id),
                        "symbol": str(e.symbol),
                        "combo_id": int(e.tags["combo_id"]),
                        "side": str(e.side),
                        "signal_close_ms": int(e.signal_close_ms),
                        "ref_price": float(e.ref_price),
                    })

            per_symbol_stats[sym] = {
                "bars_processed": len(df),
                "batch_signals": len(batch_evs),
                "live_signals": len(live_evs),
                "only_in_batch": len(only_batch),
                "only_in_live": len(only_live),
                "field_mismatches": len(field_diffs),
            }

        for v in replay_viols:
            viol.add(v)

        _B3_CACHE[cache_key] = (
            total,
            total,
            replay_viols,
            {
                "live_eval_records": live_eval_records,
                "per_symbol": per_symbol_stats,
            },
        )

    # 3. Verify run's signals.parquet against live_eval_records (field-level + multiplicity)
    from collections import Counter

    live_by_sid: dict[str, dict[str, Any]] = {}
    live_sid_counts: Counter[str] = Counter()
    for rec in live_eval_records:
        sid = rec["signal_id"]
        live_sid_counts[sid] += 1
        live_by_sid[sid] = rec

    run_sid_counts: Counter[str] = Counter()
    run_field_mismatches: list[str] = []
    for _, row in run_sigs.iterrows():
        sid = str(row["signal_id"])
        run_sid_counts[sid] += 1
        if sid in live_by_sid:
            exp = live_by_sid[sid]
            r_sym = str(row["symbol"])
            r_cid = int(row["combo_id"])
            r_side = str(row["side"])
            r_close = int(row["signal_close_ms"])
            r_price = float(row["entry_price"])
            if (
                r_sym != exp["symbol"]
                or r_cid != exp["combo_id"]
                or r_side != exp["side"]
                or r_close != exp["signal_close_ms"]
                or abs(r_price - exp["ref_price"]) > 1e-8
            ):
                run_field_mismatches.append(sid)

    missing_from_run = sorted(set(live_sid_counts.keys()) - set(run_sid_counts.keys()))
    extra_in_run = sorted(set(run_sid_counts.keys()) - set(live_sid_counts.keys()))
    multiplicity_mismatches = sorted(
        sid for sid in (set(live_sid_counts.keys()) & set(run_sid_counts.keys()))
        if run_sid_counts[sid] != live_sid_counts[sid]
    )

    if missing_from_run:
        viol.add({
            "kind": "RUN_SIGNALS_MISSING_FROM_LIVE",
            "count": len(missing_from_run),
            "sample": missing_from_run[:10],
        })
    if extra_in_run:
        viol.add({
            "kind": "RUN_SIGNALS_EXTRA_VS_LIVE",
            "count": len(extra_in_run),
            "sample": extra_in_run[:10],
        })
    if multiplicity_mismatches:
        viol.add({
            "kind": "DUPLICATE_SIGNAL_IN_RUN",
            "count": len(multiplicity_mismatches),
            "sample": [
                {"signal_id": sid, "run_count": run_sid_counts[sid], "live_count": live_sid_counts[sid]}
                for sid in multiplicity_mismatches[:10]
            ],
        })
    if run_field_mismatches:
        viol.add({
            "kind": "RUN_SIGNAL_FIELD_MISMATCH",
            "count": len(run_field_mismatches),
            "sample": sorted(set(run_field_mismatches))[:10],
        })

    duration = time.perf_counter() - t0
    return finalize(
        "B3",
        name,
        checked=total,
        total=total,
        viol=viol,
        details={
            "strategy_ref": f"{spec.strategy_id}@{spec.version}",
            "signal_spec_hash": spec.signal_spec_hash,
            "unclosed_bar_rejected": unclosed_rejected,
            "per_symbol": per_symbol_stats,
            "duration_s": round(duration, 2),
        },
    )
