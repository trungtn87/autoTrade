"""Runner and orchestrator for AutoTrade V3 Independent Validation Gates (V3-10).

Executes all 12 validation gates on a persisted run directory, derives the overall
run validation status according to Section 4.4, and persists validation.json.

Status semantics (Section 4.4):
- VERIFIED: All 12 gates PASS, code_dirty is false, and real market data is used.
  (In PR V3-10, highest attainable status is PARTIALLY_VERIFIED due to B3).
- UNVERIFIED_DIRTY: Git working tree was dirty when the run was generated.
- SYNTHETIC: Synthetic dataset used.
- FAILED: At least one gate returned FAIL.
- PARTIALLY_VERIFIED: No gates FAIL, but one or more gates are INCONCLUSIVE (e.g. B3).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from autotrade_v3.validation.context import VerifyContext
from autotrade_v3.validation.gates_a import gate_a1, gate_a2
from autotrade_v3.validation.gates_b import gate_b1, gate_b2, gate_b3
from autotrade_v3.validation.gates_c import gate_c1, gate_c2, gate_c3, gate_c4, gate_c5
from autotrade_v3.validation.gates_d import gate_d1, gate_d2
from autotrade_v3.validation.types import FAIL, INCONCLUSIVE, PASS, GateResult

ALL_GATE_ORDER = [
    ("A1", gate_a1),
    ("A2", gate_a2),
    ("B1", gate_b1),
    ("B2", gate_b2),
    ("B3", gate_b3),
    ("C1", gate_c1),
    ("C2", gate_c2),
    ("C3", gate_c3),
    ("C4", gate_c4),
    ("C5", gate_c5),
    ("D1", gate_d1),
    ("D2", gate_d2),
]


@dataclass
class ValidationReport:
    run_id: str
    overall_status: str
    execution_mode: str
    is_real_data: bool
    code_dirty: bool
    gates: dict[str, dict[str, Any]]
    failure_reasons: list[str]
    total_duration_s: float
    seed: int
    n_cutoffs: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_overall_status(
    gate_results: dict[str, GateResult],
    is_synthetic: bool,
    code_dirty: bool,
) -> tuple[str, list[str]]:
    """Determine overall status per Section 4.4."""
    failures = []
    has_inconclusive = False

    for gid, res in gate_results.items():
        if res.status == FAIL:
            failures.append(f"{gid} ({res.name}): {res.reason or f'{res.violation_count} violations'}")
        elif res.status == INCONCLUSIVE:
            has_inconclusive = True

    if failures:
        return "FAILED", failures

    if code_dirty:
        return "UNVERIFIED_DIRTY", ["Working tree dirty at run execution time"]

    if is_synthetic:
        return "SYNTHETIC", []

    if has_inconclusive:
        return "PARTIALLY_VERIFIED", []

    # All 12 PASS, clean, real data
    return "VERIFIED", []


def run_validation(
    run_dir: str | Path,
    dataset_root: str | Path | None = None,
    repo_root: str | Path | None = None,
    reference_signals: str | Path | None = None,
    n_cutoffs: int = 20,
    seed: int = 20261110,
    skip_gates: list[str] | None = None,
    write_result: bool = True,
) -> ValidationReport:
    """Run all 12 validation gates on a run directory."""
    r_dir = Path(run_dir).resolve()
    rep_root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[2]
    d_root = Path(dataset_root).resolve() if dataset_root else rep_root
    ref_sig = Path(reference_signals).resolve() if reference_signals else None

    ctx = VerifyContext(
        run_dir=r_dir,
        dataset_root=d_root,
        repo_root=rep_root,
        reference_signals=ref_sig,
        n_cutoffs=n_cutoffs,
        seed=seed,
    )

    manifest = ctx.run.manifest or {}
    run_id = str(manifest.get("run_id", r_dir.name))
    mode = str(manifest.get("execution_mode", ""))
    code_dirty = bool(manifest.get("code_dirty", False))
    is_synthetic = ctx.is_synthetic

    skip = set(skip_gates or [])
    gate_results: dict[str, GateResult] = {}

    t_start = time.perf_counter()
    for gid, gate_fn in ALL_GATE_ORDER:
        if gid in skip:
            continue
        g_t0 = time.perf_counter()
        try:
            res = gate_fn(ctx)
        except Exception as e:
            res = GateResult(
                gate_id=gid,
                name=f"Gate {gid}",
                status=FAIL,
                rows_checked=0,
                rows_total=0,
                violation_count=1,
                violations=[{"kind": "CRASH", "error": str(e)}],
                reason=f"Gate crashed with exception: {e}",
            )
        res.duration_s = time.perf_counter() - g_t0
        gate_results[gid] = res

    total_duration = time.perf_counter() - t_start

    overall_status, failure_reasons = evaluate_overall_status(
        gate_results=gate_results,
        is_synthetic=is_synthetic,
        code_dirty=code_dirty,
    )

    report = ValidationReport(
        run_id=run_id,
        overall_status=overall_status,
        execution_mode=mode,
        is_real_data=not is_synthetic,
        code_dirty=code_dirty,
        gates={gid: res.to_dict() for gid, res in gate_results.items()},
        failure_reasons=failure_reasons,
        total_duration_s=round(total_duration, 2),
        seed=seed,
        n_cutoffs=n_cutoffs,
    )

    if write_result:
        val_path = r_dir / "validation.json"
        with open(val_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)

    return report
