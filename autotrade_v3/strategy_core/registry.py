"""Versioned Strategy Registry for AutoTrade V3 Strategy Core (V3-11).

Supports lookup by (strategy_id, version) or '<strategy_id>@<version>', coexistence of
multiple versions of the same strategy_id, and cloning from an existing strategy spec.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from .api import Strategy
from .hashing import compute_all_hashes, normalize_lf
from .strategies.final20.logic import (
    DEFAULT_LOCK_PATH,
    DEFAULT_PARAMS_PATH,
    FINAL20_DIR,
    Final20Strategy,
)

PARAMS_DIR = FINAL20_DIR / "params"


def parse_strategy_ref(
    strategy_id_or_ref: str,
    version: str | None = None,
) -> tuple[str, str]:
    """Parse '20FINAL@1' or ('20FINAL', '1') into canonical (strategy_id, version)."""
    raw = str(strategy_id_or_ref).strip()
    if "@" in raw and version is None:
        sid, ver = raw.split("@", 1)
        sid = sid.strip()
        ver = ver.strip()
    else:
        sid = raw
        ver = str(version).strip() if version is not None else "1"

    if sid.upper() in ("20FINAL_BASELINE", "20FINAL"):
        sid = "20FINAL"
        if ver in ("1.0.0", "1.0", ""):
            ver = "1"
    return sid, ver


class StrategyCoreRegistry:
    """Registry indexing strategies by (strategy_id, version)."""

    _factories: dict[tuple[str, str], Callable[[], Strategy]] = {}
    _custom_instances: dict[tuple[str, str], Strategy] = {}

    @classmethod
    def register(
        cls,
        strategy: Strategy | Callable[[], Strategy],
        strategy_id: str | None = None,
        version: str | None = None,
    ) -> None:
        if isinstance(strategy, Strategy):
            sp = strategy.spec()
            key = (strategy_id or sp.strategy_id, version or sp.version)
            cls._custom_instances[key] = strategy
        else:
            if strategy_id is None or version is None:
                raise ValueError("strategy_id and version are required when registering a factory")
            cls._factories[(strategy_id, version)] = strategy

    @classmethod
    def get(
        cls,
        strategy_id_or_ref: str,
        version: str | None = None,
        params_dir: Path | None = None,
    ) -> Strategy:
        sid, ver = parse_strategy_ref(strategy_id_or_ref, version)
        key = (sid, ver)

        if key in cls._custom_instances:
            return cls._custom_instances[key]
        if key in cls._factories:
            return cls._factories[key]()

        # Check params directory for cloned or versioned strategy specs
        p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
        if key == ("20FINAL", "1"):
            return Final20Strategy(
                params_path=p_dir / "20final_v1.json",
                lock_path=p_dir / "20final_v1.lock",
                enforce_lock=True,
            )

        # Look for cloned parameter file under p_dir/<sid_lower>_v<ver>/params.json or <sid_lower>_v<ver>.json
        slug = f"{sid.lower()}_v{ver}"
        candidates = [
            p_dir / slug / "params.json",
            p_dir / f"{slug}.json",
            p_dir / f"{sid}_v{ver}.json",
        ]
        for cand in candidates:
            if cand.exists():
                return Final20Strategy(
                    params_path=cand,
                    enforce_lock=False,
                )

        raise KeyError(
            f"Strategy '{sid}@{ver}' not found in StrategyCoreRegistry. "
            f"Registered: {cls.list_keys(params_dir=p_dir)}"
        )

    @classmethod
    def list_keys(cls, params_dir: Path | None = None) -> list[str]:
        keys = {f"{sid}@{ver}" for sid, ver in cls._factories}
        keys |= {f"{sid}@{ver}" for sid, ver in cls._custom_instances}
        keys.add("20FINAL@1")
        p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
        if p_dir.exists():
            for sub in sorted(p_dir.iterdir()):
                if sub.is_dir() and (sub / "params.json").exists():
                    try:
                        d = json.loads(normalize_lf((sub / "params.json").read_text(encoding="utf-8")))
                        keys.add(f"{d['strategy_id']}@{d['version']}")
                    except Exception:
                        pass
        return sorted(keys)

    @classmethod
    def list_strategies(cls, params_dir: Path | None = None) -> list[dict[str, Any]]:
        out = []
        for ref in cls.list_keys(params_dir=params_dir):
            strat = cls.get(ref, params_dir=params_dir)
            out.append(strat.spec().to_dict())
        return out

    @classmethod
    def clear(cls) -> None:
        cls._factories.clear()
        cls._custom_instances.clear()
