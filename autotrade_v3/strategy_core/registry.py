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


def compute_param_diff(
    source_params: dict[str, Any],
    target_params: dict[str, Any],
) -> dict[str, Any]:
    """Compute a deterministic parameter diff between source and target strategy parameter dicts."""
    src_combos = {str(c["combo_key"]): c for c in source_params.get("combos", [])}
    tgt_combos = {str(c["combo_key"]): c for c in target_params.get("combos", [])}

    added_combos = sorted(set(tgt_combos.keys()) - set(src_combos.keys()))
    removed_combos = sorted(set(src_combos.keys()) - set(tgt_combos.keys()))
    modified_combos: dict[str, dict[str, Any]] = {}

    for k in sorted(set(src_combos.keys()) & set(tgt_combos.keys())):
        sc = src_combos[k]
        tc = tgt_combos[k]
        if sc != tc:
            field_changes = {}
            for fk in sorted(set(sc.keys()) | set(tc.keys())):
                if sc.get(fk) != tc.get(fk):
                    field_changes[fk] = {"from": sc.get(fk), "to": tc.get(fk)}
            modified_combos[k] = field_changes

    top_changes = {}
    for fk in ("warmup_bars", "timeframes"):
        if source_params.get(fk) != target_params.get(fk):
            top_changes[fk] = {"from": source_params.get(fk), "to": target_params.get(fk)}

    return {
        "from_strategy": f"{source_params.get('strategy_id')}@{source_params.get('version')}",
        "to_strategy": f"{target_params.get('strategy_id')}@{target_params.get('version')}",
        "top_level_changes": top_changes,
        "added_combos": [tgt_combos[k] for k in added_combos],
        "removed_combos": removed_combos,
        "modified_combos": modified_combos,
    }


def clone_strategy_params(
    from_ref: str,
    to_ref: str,
    params_dir: Path | None = None,
    combo_overrides: dict[str, dict[str, Any]] | None = None,
    add_combos: list[dict[str, Any]] | None = None,
    remove_combos: list[str] | None = None,
) -> dict[str, Any]:
    """Clone an existing strategy parameter specification into a new versioned directory.

    Never modifies the baseline `20final_v1.json` or `20final_v1.lock`.
    Creates `<params_dir>/<to_id_lower>_v<to_ver>/params.json` and `param_diff.json`.
    """
    src_id, src_ver = parse_strategy_ref(from_ref)
    dst_id, dst_ver = parse_strategy_ref(to_ref)

    if (dst_id, dst_ver) == ("20FINAL", "1"):
        raise ValueError("Cannot clone into immutable baseline 20FINAL@1")
    if (dst_id, dst_ver) == (src_id, src_ver):
        raise ValueError(f"Cannot clone strategy onto itself: {dst_id}@{dst_ver}")

    p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
    dst_slug = f"{dst_id.lower()}_v{dst_ver}"
    out_dir = p_dir / dst_slug
    existing_candidates = [
        out_dir / "params.json",
        p_dir / f"{dst_slug}.json",
        p_dir / f"{dst_id}_v{dst_ver}.json",
    ]
    if out_dir.exists() or any(c.exists() for c in existing_candidates):
        raise FileExistsError(
            f"Target strategy '{dst_id}@{dst_ver}' already exists under {p_dir}; "
            "refusing to overwrite existing strategy parameters."
        )

    # Locate source params file
    if (src_id, src_ver) == ("20FINAL", "1"):
        src_path = p_dir / "20final_v1.json"
        if not src_path.exists():
            src_path = DEFAULT_PARAMS_PATH
    else:
        src_slug = f"{src_id.lower()}_v{src_ver}"
        src_path = p_dir / src_slug / "params.json"
        if not src_path.exists():
            src_path = p_dir / f"{src_slug}.json"
    if not src_path.exists():
        raise FileNotFoundError(f"Source strategy parameters not found for {src_id}@{src_ver}: {src_path}")

    src_data = json.loads(normalize_lf(src_path.read_text(encoding="utf-8")))
    dst_data = copy.deepcopy(src_data)
    dst_data["strategy_id"] = dst_id
    dst_data["version"] = dst_ver
    dst_data["parent"] = f"{src_id}@{src_ver}"

    combos_list: list[dict[str, Any]] = dst_data.get("combos", [])
    if remove_combos:
        rm_set = set(remove_combos)
        combos_list = [c for c in combos_list if c["combo_key"] not in rm_set]

    if combo_overrides:
        for c in combos_list:
            ck = c["combo_key"]
            if ck in combo_overrides:
                ov = combo_overrides[ck]
                for k, v in ov.items():
                    if k in ("entry", "exit_policy") and isinstance(v, dict) and isinstance(c.get(k), dict):
                        c[k].update(v)
                    else:
                        c[k] = v

    if add_combos:
        existing_keys = {c["combo_key"] for c in combos_list}
        for ac in add_combos:
            if ac["combo_key"] in existing_keys:
                raise ValueError(f"Duplicate combo_key when adding combo: {ac['combo_key']}")
            combos_list.append(copy.deepcopy(ac))
            existing_keys.add(ac["combo_key"])

    dst_data["combos"] = combos_list

    diff_obj = compute_param_diff(src_data, dst_data)
    dst_data["param_diff"] = {
        "added_combos": [c["combo_key"] for c in diff_obj["added_combos"]],
        "removed_combos": diff_obj["removed_combos"],
        "modified_combos": diff_obj["modified_combos"],
        "top_level_changes": diff_obj["top_level_changes"],
    }

    out_dir.mkdir(parents=True, exist_ok=False)
    params_out = out_dir / "params.json"
    diff_out = out_dir / "param_diff.json"

    params_out.write_text(normalize_lf(json.dumps(dst_data, indent=2) + "\n"), encoding="utf-8", newline="\n")
    diff_out.write_text(normalize_lf(json.dumps(diff_obj, indent=2) + "\n"), encoding="utf-8", newline="\n")

    hashes = compute_all_hashes(dst_data)
    return {
        "strategy_id": dst_id,
        "version": dst_ver,
        "parent": f"{src_id}@{src_ver}",
        "params_path": str(params_out),
        "diff_path": str(diff_out),
        "hashes": hashes,
        "diff": diff_obj,
    }


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

        p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
        if key == ("20FINAL", "1"):
            p_path = p_dir / "20final_v1.json"
            if not p_path.exists():
                p_path = DEFAULT_PARAMS_PATH
            l_path = p_dir / "20final_v1.lock"
            if not l_path.exists():
                l_path = DEFAULT_LOCK_PATH
            return Final20Strategy(
                params_path=p_path,
                lock_path=l_path,
                enforce_lock=True,
            )

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
    def clone(
        cls,
        from_ref: str,
        to_ref: str,
        params_dir: Path | None = None,
        combo_overrides: dict[str, dict[str, Any]] | None = None,
        add_combos: list[dict[str, Any]] | None = None,
        remove_combos: list[str] | None = None,
    ) -> Strategy:
        res = clone_strategy_params(
            from_ref=from_ref,
            to_ref=to_ref,
            params_dir=params_dir,
            combo_overrides=combo_overrides,
            add_combos=add_combos,
            remove_combos=remove_combos,
        )
        return cls.get(res["strategy_id"], res["version"], params_dir=params_dir)

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
