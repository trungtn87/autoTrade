"""Unified M1 execution simulator package for AutoTrade V3."""
from autotrade_v3.sim.types import (
    DecisionStatus,
    Event,
    ExitRules,
    LegOutcome,
    LegSpec,
    M1BarArrays,
    SignalDecision,
)
from autotrade_v3.sim.leg import extract_m1_arrays, simulate_leg

__all__ = [
    "DecisionStatus",
    "Event",
    "ExitRules",
    "LegOutcome",
    "LegSpec",
    "M1BarArrays",
    "SignalDecision",
    "extract_m1_arrays",
    "simulate_leg",
]
