"""Result types shared by all independent validation gates (V3-10)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

MAX_EXAMPLES = 20

GATE_IDS = (
    "A1", "A2", "B1", "B2", "B3", "C1", "C2", "C3", "C4", "C5", "D1", "D2",
)


@dataclass
class GateResult:
    """Outcome of one gate.

    PASS is only possible when ``rows_checked == rows_total`` and there are no violations.
    """

    gate_id: str
    name: str
    status: str
    rows_checked: int
    rows_total: int
    violation_count: int = 0
    violations: list[Any] = field(default_factory=list)
    duration_s: float = 0.0
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "name": self.name,
            "status": self.status,
            "rows_checked": int(self.rows_checked),
            "rows_total": int(self.rows_total),
            "violation_count": int(self.violation_count),
            "violations": list(self.violations[:MAX_EXAMPLES]),
            "duration_s": round(self.duration_s, 3),
            "reason": self.reason,
            "details": self.details,
        }


class ViolationCollector:
    """Counts every violation but keeps at most ``MAX_EXAMPLES`` examples."""

    def __init__(self) -> None:
        self.count = 0
        self.examples: list[Any] = []

    def add(self, example: Any) -> None:
        self.count += 1
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append(example)

    def add_many(self, n: int, examples: list[Any]) -> None:
        self.count += int(n)
        for e in examples:
            if len(self.examples) >= MAX_EXAMPLES:
                break
            self.examples.append(e)


def finalize(
    gate_id: str,
    name: str,
    checked: int,
    total: int,
    viol: ViolationCollector,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> GateResult:
    """Build a result applying the PASS rule of spec 4.2."""
    if viol.count > 0:
        status = FAIL
    elif checked == total:
        status = PASS
    else:
        status = INCONCLUSIVE
        reason = reason or "rows_checked < rows_total"
    return GateResult(
        gate_id=gate_id,
        name=name,
        status=status,
        rows_checked=checked,
        rows_total=total,
        violation_count=viol.count,
        violations=list(viol.examples),
        reason=reason,
        details=details or {},
    )


def inconclusive(gate_id: str, name: str, reason: str, total: int = 0, details: dict[str, Any] | None = None) -> GateResult:
    return GateResult(
        gate_id=gate_id, name=name, status=INCONCLUSIVE, rows_checked=0, rows_total=total,
        reason=reason, details=details or {},
    )


def failed(gate_id: str, name: str, reason: str, examples: list[Any] | None = None, total: int = 0) -> GateResult:
    ex = examples or [reason]
    return GateResult(
        gate_id=gate_id, name=name, status=FAIL, rows_checked=0, rows_total=total,
        violation_count=len(ex), violations=list(ex[:MAX_EXAMPLES]), reason=reason,
    )


GateFn = Callable[[Any], GateResult]
