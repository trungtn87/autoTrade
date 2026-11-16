"""Read-only discovery of local backtest runs across execution modes.

The V3-16 MT5 verifier is intentionally not reused for legacy/research modes:
a run discovered here is NOT automatically an MT5-verified run.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOTS_ENV = "AUTOTRADE_BACKTEST_RUN_ROOTS"
MAX_DEPTH = 5
MAX_JSON_BYTES = 8_000_000
METRIC_STATUSES = frozenset({"VERIFIED", "PARTIALLY_VERIFIED"})


@dataclass(frozen=True)
class CatalogRun:
    run_id: str
    run_dir: Path
    execution_mode: str
    strategy_ref: str
    cost_profile: str
    generated_at: str
    git_sha: str
    validation_status: str
    validation_present: bool
    code_dirty: bool
    symbols: tuple[str, ...]
    net_pnl: float | None
    roi_pct: float | None
    accepted_entries: int | None
    combo_rows: tuple[dict[str, Any], ...]
    report_path: Path | None
    metrics_note: str

    @property
    def identity(self) -> str:
        """Use the actual path: different runs may share an ID."""
        return str(self.run_dir.resolve())


def run_roots() -> list[Path]:
    """Include the canonical runs tree and explicitly configured extra roots."""
    roots = [Path("runs")]
    roots.extend(Path(value) for value in os.environ.get(ROOTS_ENV, "").split(os.pathsep) if value.strip())
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        canonical = root.resolve()
        if canonical not in seen:
            unique.append(root)
            seen.add(canonical)
    return unique


def _json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _manifest_paths(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: set[Path] = set()
    for depth in range(MAX_DEPTH + 1):
        pattern = "/".join(["*"] * depth + ["run_manifest.json"])
        for candidate in root.glob(pattern):
            if candidate.is_file() and not candidate.is_symlink():
                # No following symlinks out of the chosen root.
                try:
                    candidate.resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                found.add(candidate)
    return sorted(found)


def _analysis_integrity(run_dir: Path) -> tuple[bool, str]:
    """Reject a mismatching indexed analysis; do not invent a hash when absent."""
    index = _json(run_dir / "ARTIFACTS.json")
    if index is None:
        return True, "analysis.json chưa được đối chiếu checksum độc lập"
    entries = index.get("artifacts")
    if not isinstance(entries, dict):
        return True, "ARTIFACTS.json không khai báo checksum analysis"
    entry = entries.get("analysis.json")
    if not isinstance(entry, dict) or not entry.get("sha256"):
        return True, "ARTIFACTS.json không khai báo checksum analysis"
    try:
        digest = hashlib.sha256((run_dir / "analysis.json").read_bytes()).hexdigest()
    except OSError:
        return False, "Không đọc được analysis.json"
    if digest != str(entry["sha256"]):
        return False, "HASH_MISMATCH: analysis.json"
    return True, "analysis.json khớp checksum trong ARTIFACTS.json"


def _float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and abs(number) != float("inf") and number == number else None


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _load_run(manifest_path: Path) -> CatalogRun | None:
    manifest = _json(manifest_path)
    if manifest is None:
        return None
    run_dir = manifest_path.parent
    validation = _json(run_dir / "validation.json")
    # Never copy validation_status from run_manifest.json as proof.
    status = str((validation or {}).get("overall_status") or (validation or {}).get("overall_validation") or "UNVERIFIED")
    mode = str(manifest.get("execution_mode") or manifest.get("mode") or "UNKNOWN")
    strategy = str(manifest.get("strategy_id") or manifest.get("strategy_ref") or "UNKNOWN")
    version = str(manifest.get("strategy_version") or "")
    strategy_ref = f"{strategy}@{version}" if version and "@" not in strategy else strategy
    symbols_raw = manifest.get("symbols") or []
    symbols = tuple(str(s) for s in symbols_raw) if isinstance(symbols_raw, list) else ()
    report_path = run_dir / "report.md"
    if not report_path.is_file():
        report_path = None

    analysis = _json(run_dir / "analysis.json")
    integrity_ok, integrity_note = _analysis_integrity(run_dir) if analysis is not None else (False, "Thiếu analysis.json")
    visible = validation is not None and status in METRIC_STATUSES and integrity_ok and analysis is not None
    pnl = analysis.get("pnl") if visible else None
    accounting = analysis.get("accounting") if visible else None
    combos = analysis.get("combos") if visible else None
    pnl = pnl if isinstance(pnl, dict) else {}
    accounting = accounting if isinstance(accounting, dict) else {}
    combo_rows = tuple(item for item in combos if isinstance(item, dict)) if isinstance(combos, list) else ()
    note = integrity_note if visible else (
        "Chưa xác minh: không hiển thị KPI từ analysis.json"
        if not integrity_ok or validation is None or status not in METRIC_STATUSES else integrity_note
    )
    return CatalogRun(
        run_id=str(manifest.get("run_id") or run_dir.name),
        run_dir=run_dir,
        execution_mode=mode,
        strategy_ref=strategy_ref,
        cost_profile=str(manifest.get("cost_profile") or "UNKNOWN").upper(),
        generated_at=str(manifest.get("generated_at_utc") or manifest.get("created_at_utc") or ""),
        git_sha=str(manifest.get("git_sha") or manifest.get("code_sha") or ""),
        validation_status=status,
        validation_present=validation is not None,
        code_dirty=bool(manifest.get("code_dirty", False)),
        symbols=symbols,
        net_pnl=_float(pnl.get("net_pnl")) if visible else None,
        roi_pct=_float(pnl.get("roi_pct")) if visible else None,
        accepted_entries=_int(accounting.get("accepted_entries")) if visible else None,
        combo_rows=combo_rows if visible else (),
        report_path=report_path,
        metrics_note=note,
    )


def discover_runs(roots: list[Path] | None = None) -> list[CatalogRun]:
    """Rescan on every call; no cache, no writes, and no network requests."""
    paths: dict[Path, Path] = {}
    for root in run_roots() if roots is None else roots:
        for manifest in _manifest_paths(root):
            paths.setdefault(manifest.resolve(), manifest)
    rows = [_load_run(path) for path in paths.values()]
    return sorted(
        (row for row in rows if row is not None),
        key=lambda row: (row.generated_at, row.run_id, row.identity),
        reverse=True,
    )
