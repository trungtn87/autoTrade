"""Tests for Validation Runner overall status evaluation and mandatory gate enforcement."""
from __future__ import annotations

import unittest
from pathlib import Path

from autotrade_v3.validation.runner import evaluate_overall_status, run_validation
from autotrade_v3.validation.types import (
    FAIL,
    GATE_IDS,
    INCONCLUSIVE,
    PASS,
    GateResult,
)


def _make_dummy_gate_results(
    overrides: dict[str, GateResult] | None = None,
    exclude: set[str] | None = None,
) -> dict[str, GateResult]:
    res = {}
    for gid in GATE_IDS:
        if exclude and gid in exclude:
            continue
        res[gid] = GateResult(
            gate_id=gid,
            name=f"Gate {gid}",
            status=PASS,
            rows_checked=10,
            rows_total=10,
            violation_count=0,
            violations=[],
        )
    if overrides:
        res.update(overrides)
    return res


class TestValidationRunner(unittest.TestCase):
    """Test validation runner evaluation logic."""

    def test_runner_verified_only_all_pass(self):
        """All 12 PASS, clean tree, real data -> VERIFIED."""
        results = _make_dummy_gate_results()
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=False)
        self.assertEqual(status, "VERIFIED")
        self.assertEqual(len(failures), 0)

    def test_runner_rejects_missing_gates(self):
        """Missing even one gate out of 12 -> FAILED."""
        results = _make_dummy_gate_results(exclude={"A1"})
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=False)
        self.assertEqual(status, "FAILED")
        self.assertTrue(any("Missing mandatory gate: A1" in f for f in failures))

    def test_runner_rejects_skipped_gate(self):
        """Skipped gate must be registered as FAIL and overall status must be FAILED."""
        # Test evaluate_overall_status directly when a gate failed due to being skipped
        skipped_b3 = GateResult(
            gate_id="B3",
            name="Gate B3",
            status=FAIL,
            rows_checked=0,
            rows_total=0,
            violation_count=1,
            violations=[{"kind": "SKIPPED", "gate": "B3"}],
            reason="Gate B3 was skipped by caller option",
        )
        results = _make_dummy_gate_results(overrides={"B3": skipped_b3})
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=False)
        self.assertEqual(status, "FAILED")
        self.assertTrue(any("B3 was skipped" in f for f in failures))

    def test_runner_partial_when_inconclusive_only(self):
        """11 PASS + 1 INCONCLUSIVE -> PARTIALLY_VERIFIED."""
        inconclusive_b3 = GateResult(
            gate_id="B3",
            name="Gate B3",
            status=INCONCLUSIVE,
            rows_checked=10,
            rows_total=10,
            violation_count=0,
            violations=[],
            reason="B3 inconclusive (waiting for V3-11)",
        )
        results = _make_dummy_gate_results(overrides={"B3": inconclusive_b3})
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=False)
        self.assertEqual(status, "PARTIALLY_VERIFIED")
        self.assertEqual(len(failures), 0)

    def test_runner_fails_on_any_gate_failure(self):
        """11 PASS + 1 FAIL -> FAILED."""
        fail_c2 = GateResult(
            gate_id="C2",
            name="Gate C2",
            status=FAIL,
            rows_checked=10,
            rows_total=10,
            violation_count=2,
            violations=[{"trade_id": "T1"}],
            reason="2 causality violations",
        )
        results = _make_dummy_gate_results(overrides={"C2": fail_c2})
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=False)
        self.assertEqual(status, "FAILED")
        self.assertTrue(any("C2" in f for f in failures))

    def test_runner_synthetic_flag(self):
        """12 PASS, clean, but is_synthetic=True -> SYNTHETIC."""
        results = _make_dummy_gate_results()
        status, failures = evaluate_overall_status(results, is_synthetic=True, code_dirty=False)
        self.assertEqual(status, "SYNTHETIC")
        self.assertEqual(len(failures), 0)

    def test_runner_dirty_flag(self):
        """12 PASS, real, but code_dirty=True -> UNVERIFIED_DIRTY."""
        results = _make_dummy_gate_results()
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=True)
        self.assertEqual(status, "UNVERIFIED_DIRTY")
        self.assertTrue(any("dirty" in f.lower() for f in failures))

    def test_runner_pass_with_incomplete_rows_rejected(self):
        """A gate claiming PASS with rows_checked != rows_total must fail."""
        fake_pass = GateResult(
            gate_id="C1",
            name="Gate C1",
            status=PASS,
            rows_checked=5,
            rows_total=10,
            violation_count=0,
            violations=[],
        )
        results = _make_dummy_gate_results(overrides={"C1": fake_pass})
        status, failures = evaluate_overall_status(results, is_synthetic=False, code_dirty=False)
        self.assertEqual(status, "FAILED")
        self.assertTrue(any("invalid PASS" in f for f in failures))


if __name__ == "__main__":
    unittest.main()
