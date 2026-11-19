"""Annotation-backed signal filter using causal `as_of_ms <= signal_close_ms` lookup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autotrade_v3.filters.base import FilterContext, FilterDecision, hash_filter_config


@dataclass
class AnnotationGuardFilter:
    """Drops signals when the latest causal annotation has a veto verdict."""

    source: str
    name: str = "annotation_guard"
    payload_key: str = "verdict"
    drop_values: tuple[str, ...] = ("DROP", "BLOCK", "VETO")

    def config_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "payload_key": self.payload_key,
            "drop_values": list(self.drop_values),
        }

    def config_hash(self) -> str:
        return hash_filter_config(self.config_dict())

    def evaluate(self, signal: Any, ctx: FilterContext) -> FilterDecision:
        q = ctx.get_annotations(
            str(signal.symbol),
            int(signal.signal_close_ms),
            source=self.source,
        )
        if not q.records:
            return FilterDecision(action="KEEP")
        latest = q.records[-1]
        val = str(latest.payload.get(self.payload_key, ""))
        if val in self.drop_values:
            return FilterDecision(action="DROP", reason=self.name, label=f"{self.source}:{val}")
        return FilterDecision(action="KEEP", label=f"{self.source}:{val}" if val else "")
