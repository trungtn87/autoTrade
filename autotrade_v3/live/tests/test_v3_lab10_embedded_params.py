"""Live Lab 10 reads the frozen 20FINAL@1 params embedded in code, not a file.

The pinned hash is the canonical SHA-256 of the params as they were before embedding,
so any edit to the embedded values fails this test.
"""
from __future__ import annotations

from autotrade_v3.live import l2_lab10_flat as flat
from autotrade_v3.live.lab10_core_params import EMBEDDED_20FINAL_FIXED_PARAMS, EMBEDDED_20FINAL_PARAMS

FROZEN_20FINAL_PARAMS_SHA256 = "74fc9bd739fe4ca915f8ab5cca28ac47500400189dfd7736d98cf578fda335ed"
FROZEN_20FINAL_FIXED_PARAMS_SHA256 = "6317cfc35f8c2df396e4d3bcafbae539ef73de6414e392e5b7f60df88f5bf775"


def test_embedded_20final_params_match_frozen_values() -> None:
    assert flat.canonical_json_sha256(EMBEDDED_20FINAL_PARAMS) == FROZEN_20FINAL_PARAMS_SHA256


def test_embedded_20final_fixed_params_match_frozen_values() -> None:
    assert flat.canonical_json_sha256(EMBEDDED_20FINAL_FIXED_PARAMS) == FROZEN_20FINAL_FIXED_PARAMS_SHA256


def test_live_registry_loads_20final_without_params_file() -> None:
    spec = flat.StrategyCoreRegistry.get("20FINAL", "1").spec()
    assert len(spec.combos) == 20
    assert spec.warmup_bars == 3400
    assert tuple(spec.timeframes) == ("15m", "1h")
    assert len(flat.StrategyCoreRegistry.get("20FINAL_FIXED_V1", "1").spec().combos) == 20
