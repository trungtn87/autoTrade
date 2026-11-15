"""Check mypy errors against the recorded V3-15d baseline (AT2-007).

Fails if any file has more mypy errors than in ``evidence/v3-15d/mypy_baseline.txt``
or if total errors exceed the baseline. Does not require modifying legacy/final20 code.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = REPO_ROOT / "evidence" / "v3-15d" / "mypy_baseline.txt"
_ERROR_RE = re.compile(r"^(.+?):(\d+):\s+error:\s+(.+)$")


def parse_mypy_error_counts(lines: Sequence[str]) -> Counter[str]:
    """Return per-file error counts with normalized forward-slash paths."""
    counts: Counter[str] = Counter()
    for line in lines:
        m = _ERROR_RE.match(line.strip())
        if m:
            norm_path = m.group(1).replace("\\", "/")
            counts[norm_path] += 1
    return counts


def compare_against_baseline(
    current: Counter[str],
    baseline: Counter[str],
) -> tuple[bool, list[str]]:
    """Return (ok, regressions) where ok is True iff no file exceeds baseline."""
    regressions: list[str] = []
    for path, count in sorted(current.items()):
        base_count = baseline.get(path, 0)
        if count > base_count:
            regressions.append(f"{path}: {count} > baseline {base_count}")
    return len(regressions) == 0, regressions


def _tracked_autotrade_v3_files(repo_root: Path) -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files", "autotrade_v3/*.py", "autotrade_v3/**/*.py"],
        cwd=repo_root,
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check mypy error counts against V3-15d baseline.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--mypy-bin", default="mypy")
    args = parser.parse_args(argv)

    if not args.baseline.exists():
        print(f"ERROR: baseline file not found: {args.baseline}", file=sys.stderr)
        return 2

    baseline_counts = parse_mypy_error_counts(
        args.baseline.read_text(encoding="utf-8").splitlines()
    )
    files = _tracked_autotrade_v3_files(REPO_ROOT)
    proc = subprocess.run(
        [args.mypy_bin, "--no-error-summary", *files],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    current_counts = parse_mypy_error_counts(proc.stdout.splitlines())
    ok, regressions = compare_against_baseline(current_counts, baseline_counts)
    total_base = sum(baseline_counts.values())
    total_cur = sum(current_counts.values())
    print(f"mypy baseline check: current={total_cur}, baseline={total_base}")
    if not ok:
        print("Mypy regressions detected:", file=sys.stderr)
        for reg in regressions:
            print(f"  - {reg}", file=sys.stderr)
        return 1
    print("OK: no mypy error increase over baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
