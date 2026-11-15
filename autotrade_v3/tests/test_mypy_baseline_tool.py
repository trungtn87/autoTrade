"""Tests for mypy baseline regression checker (AT2-007)."""
from __future__ import annotations

from pathlib import Path

from autotrade_v3.tools.check_mypy_baseline import (
    REPO_ROOT,
    compare_against_baseline,
    parse_mypy_error_counts,
)


def test_parse_mypy_error_counts_normalizes_paths() -> None:
    lines = [
        r"autotrade_v3\validation\gates_b.py:10: error: Foo  [attr-defined]",
        "autotrade_v3/validation/gates_b.py:20: error: Bar  [arg-type]",
        "autotrade_v3/lab/run_manager.py:5: note: ignored note",
    ]
    counts = parse_mypy_error_counts(lines)
    assert counts == {"autotrade_v3/validation/gates_b.py": 2}


def test_compare_against_baseline_detects_increase_and_allows_equal_or_lower() -> None:
    baseline = parse_mypy_error_counts(["autotrade_v3/a.py:1: error: x", "autotrade_v3/b.py:2: error: y"])
    ok_same, reg_same = compare_against_baseline(baseline, baseline)
    assert ok_same is True
    assert reg_same == []

    worse = parse_mypy_error_counts([
        "autotrade_v3/a.py:1: error: x",
        "autotrade_v3/a.py:2: error: x2",
        "autotrade_v3/new.py:1: error: z",
    ])
    ok_worse, reg_worse = compare_against_baseline(worse, baseline)
    assert ok_worse is False
    assert len(reg_worse) == 2


def test_recorded_v315d_mypy_baseline_exists_and_has_final20_untouched() -> None:
    baseline_path = REPO_ROOT / "evidence" / "v3-15d" / "mypy_baseline.txt"
    assert baseline_path.exists()
    counts = parse_mypy_error_counts(baseline_path.read_text(encoding="utf-8").splitlines())
    assert counts["autotrade_v3/strategy_core/strategies/final20/logic.py"] == 8
    assert sum(counts.values()) >= 102
