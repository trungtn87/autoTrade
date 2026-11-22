"""Strategy registry for dynamic strategy discovery and versioned selection."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from autotrade_v3.strategy.base import BaseStrategy
from autotrade_v3.strategy.baseline_20final import Baseline20FinalStrategy
from autotrade_v3.strategy.eth_c1_h1_both_1 import EthC1H1Both1Strategy
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry, parse_strategy_ref


class StrategyRegistry:
    """Registry maintaining available research strategies indexed by (strategy_id, version)."""

    _strategies: dict[tuple[str, str], BaseStrategy] = {}

    @classmethod
    def register(cls, strategy: BaseStrategy, version: str | None = None) -> None:
        ver = str(version) if version is not None else str(strategy.version)
        cls._strategies[(strategy.strategy_id, ver)] = strategy

    @classmethod
    def get(
        cls,
        strategy_id: str,
        version: str | None = None,
        params_dir: Path | None = None,
    ) -> BaseStrategy:
        raw = str(strategy_id).strip()
        if "@" in raw and version is None:
            sid, ver = raw.split("@", 1)
            sid = sid.strip()
            ver = ver.strip()
        else:
            sid = raw
            ver = str(version).strip() if version is not None else None

        # 1. Exact (sid, ver) match in registered instances
        if ver is not None and (sid, ver) in cls._strategies:
            return cls._strategies[(sid, ver)]

        # 2. If version not specified, match latest/first registered for sid
        if ver is None:
            for (k_sid, k_ver), inst in cls._strategies.items():
                if k_sid.lower() == sid.lower():
                    return inst

        # 3. Delegate to StrategyCoreRegistry for '20FINAL@1' or cloned strategies
        try:
            core_strat = StrategyCoreRegistry.get(sid, ver, params_dir=params_dir)
            sp = core_strat.spec()
            if sid.upper() == "20FINAL_BASELINE":
                return Baseline20FinalStrategy(core_strategy=core_strat)
            return Baseline20FinalStrategy(
                core_strategy=core_strat,
                strategy_id_override=sp.strategy_id,
                version_override=sp.version,
            )
        except KeyError:
            pass

        raise KeyError(
            f"Strategy '{strategy_id}' (version={version}) not found. "
            f"Registered: {[f'{k[0]}@{k[1]}' for k in cls._strategies.keys()]}"
        )

    @classmethod
    def list_strategies(cls, params_dir: Path | None = None) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for (sid, ver), strat in cls._strategies.items():
            seen.add((sid, ver))
            out.append(strat.to_dict())
        for ref in StrategyCoreRegistry.list_keys(params_dir=params_dir):
            c_sid, c_ver = parse_strategy_ref(ref)
            if (c_sid, c_ver) not in seen and not (c_sid == "20FINAL" and c_ver == "1" and ("20FINAL_BASELINE", "1.0.0") in seen):
                st = cls.get(f"{c_sid}@{c_ver}", params_dir=params_dir)
                out.append(st.to_dict())
        return out

    @classmethod
    def clear(cls) -> None:
        cls._strategies.clear()
        cls._register_defaults()

    @classmethod
    def _register_defaults(cls) -> None:
        from autotrade_v3.strategy.v3_lab_2 import V3Lab2Strategy
        from autotrade_v3.strategy.v3_lab_4 import V3Lab4Strategy
        from autotrade_v3.strategy.v3_lab_5 import V3Lab5Strategy
        from autotrade_v3.strategy.v3_lab_6 import V3Lab6Strategy
        from autotrade_v3.strategy.v3_lab_7 import V3Lab7Strategy
        from autotrade_v3.strategy.v3_lab_8 import V3Lab8Strategy
        from autotrade_v3.strategy.v3_lab_9 import V3Lab9Strategy
        from autotrade_v3.strategy.v3_lab_10 import V3Lab10Strategy
        from autotrade_v3.strategy.v3_lab_11 import V3Lab11Strategy
        from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy

        cls.register(Baseline20FinalStrategy())
        cls.register(EthC1H1Both1Strategy())
        cls.register(V3Lab2Strategy())
        cls.register(V3Lab4Strategy(version="4.0.0"))
        cls.register(V3Lab4Strategy(version="4.0.1"))
        cls.register(V3Lab4Strategy(version="4.1.0"))
        cls.register(V3Lab4Strategy(version="4.1.1"))
        cls.register(V3Lab5Strategy(version="5.0.0"))
        cls.register(V3Lab6Strategy(version="6.0.0"))
        cls.register(V3Lab7Strategy(version="7.0.0"))
        cls.register(V3Lab8Strategy(version="8.0.0"))
        cls.register(V3Lab9Strategy(version="9.0.0"))
        cls.register(V3Lab10Strategy(version="10.0.0"))
        cls.register(V3Lab11Strategy(version="11.0.0"))
        cls.register(V3Lab11Prune23Strategy(version="11.0.0"))


# Initialize defaults
StrategyRegistry._register_defaults()
