"""Deterministic SHA256 hashing for Strategy Core specifications and source code (V3-11).

Computes three canonical hashes over sorted-key LF-normalized JSON:
1. signal_spec_hash: SHA256 over all .py source files in strategy_core (LF-normalized, sorted
   by relative POSIX path) + signal-affecting combo parameters (entry_variant, entry, layer2,
   native_timeframe, symbol, warmup).
2. exit_spec_hash: SHA256 over exit_policy of all combos.
3. spec_hash: SHA256 over signal_spec_hash + exit_spec_hash + strategy_id + version.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

STRATEGY_CORE_ROOT = Path(__file__).resolve().parent


def normalize_lf(text: str) -> str:
    """Normalize CRLF and CR line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def canonical_json_bytes(obj: Any) -> bytes:
    """Serialize object to canonical UTF-8 JSON bytes with sorted keys and LF line endings."""
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return normalize_lf(raw).encode("utf-8")


def canonical_json_sha256(obj: Any) -> str:
    """Compute SHA256 hex digest over canonical JSON representation."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def collect_strategy_core_source_hashes(
    core_root: Path | None = None,
    strategy_subdir: str | None = "strategies/final20",
) -> dict[str, str]:
    """Hash core shared .py files + indicators/ + the specific strategy's own .py files (LF-normalized).

    Does NOT hash unrelated sibling strategy plugins under strategies/<other>/ so adding a new
    independent plugin strategy does not alter 20FINAL@1's signal_spec_hash.
    """
    root = Path(core_root).resolve() if core_root is not None else STRATEGY_CORE_ROOT

    selected_files: list[Path] = []
    # 1. Shared core modules at strategy_core root
    for pf in root.glob("*.py"):
        if "__pycache__" not in pf.parts:
            selected_files.append(pf)

    # 2. Shared indicator library
    ind_dir = root / "indicators"
    if ind_dir.exists():
        for pf in ind_dir.rglob("*.py"):
            if "__pycache__" not in pf.parts:
                selected_files.append(pf)

    # 3. Strategies package init + target strategy subdir
    strat_init = root / "strategies" / "__init__.py"
    if strat_init.exists():
        selected_files.append(strat_init)

    if strategy_subdir:
        strat_dir = root / strategy_subdir
        if strat_dir.exists():
            for pf in strat_dir.rglob("*.py"):
                if "__pycache__" not in pf.parts:
                    selected_files.append(pf)

    py_files = sorted(set(selected_files), key=lambda p: p.relative_to(root).as_posix())
    out: dict[str, str] = {}
    for pf in py_files:
        rel = pf.relative_to(root).as_posix()
        norm_text = normalize_lf(pf.read_text(encoding="utf-8"))
        out[rel] = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()
    return out


def compute_signal_spec_hash(
    params_data: dict[str, Any],
    core_root: Path | None = None,
    strategy_subdir: str | None = "strategies/final20",
) -> str:
    """Compute signal_spec_hash from strategy_core .py sources and signal-affecting parameters."""
    eff_subdir = str(params_data.get("strategy_subdir", strategy_subdir)) if strategy_subdir is not None else None
    source_hashes = collect_strategy_core_source_hashes(core_root=core_root, strategy_subdir=eff_subdir)
    warmup = int(params_data.get("warmup_bars", 3400))

    raw_combos = params_data.get("combos", [])
    sorted_combos = sorted(
        raw_combos,
        key=lambda c: (str(c.get("symbol", "")), int(c.get("combo_id", 0)), str(c.get("combo_key", ""))),
    )

    signal_combos = []
    for c in sorted_combos:
        cid = int(c["combo_id"])
        signal_combos.append({
            "combo_key": str(c["combo_key"]),
            "symbol": str(c["symbol"]),
            "combo_id": cid,
            "combo_name": str(c.get("combo_name", "")),
            "native_timeframe": str(c["native_timeframe"]),
            "is_new6": bool(c.get("is_new6", cid >= 101)),
            "entry_variant": str(c["entry_variant"]),
            "entry": c["entry"],
            "layer2": str(c["layer2"]),
        })

    payload = {
        "source_files": source_hashes,
        "warmup": warmup,
        "signal_combos": signal_combos,
    }
    return canonical_json_sha256(payload)


def compute_exit_spec_hash(params_data: dict[str, Any]) -> str:
    """Compute exit_spec_hash from exit_policy of all combos."""
    raw_combos = params_data.get("combos", [])
    sorted_combos = sorted(
        raw_combos,
        key=lambda c: (str(c.get("symbol", "")), int(c.get("combo_id", 0)), str(c.get("combo_key", ""))),
    )

    exit_combos = []
    for c in sorted_combos:
        ep = c["exit_policy"]
        exit_combos.append({
            "combo_key": str(c["combo_key"]),
            "symbol": str(c["symbol"]),
            "combo_id": int(c["combo_id"]),
            "exit_policy": {
                "tp_pct": float(ep["tp_pct"]),
                "sl_pct": float(ep["sl_pct"]),
                "split_legs": bool(ep["split_legs"]),
                "leg_weights": [float(w) for w in ep["leg_weights"]],
                "trailing_act_pct": float(ep["trailing_act_pct"]),
                "trailing_cb_pct": float(ep["trailing_cb_pct"]),
                "max_bars_held": int(ep["max_bars_held"]) if ep.get("max_bars_held") is not None else None,
                "force_close_on_window_end": bool(ep.get("force_close_on_window_end", False)),
            },
        })

    return canonical_json_sha256({"exit_combos": exit_combos})


def compute_spec_hash(
    signal_spec_hash: str,
    exit_spec_hash: str,
    strategy_id: str,
    version: str,
) -> str:
    """Compute overall spec_hash combining signal_spec_hash, exit_spec_hash, strategy_id, and version."""
    payload = {
        "strategy_id": str(strategy_id),
        "version": str(version),
        "signal_spec_hash": str(signal_spec_hash),
        "exit_spec_hash": str(exit_spec_hash),
    }
    return canonical_json_sha256(payload)


def compute_all_hashes(
    params_data: dict[str, Any],
    core_root: Path | None = None,
) -> dict[str, str]:
    """Compute all three hashes (signal_spec_hash, exit_spec_hash, spec_hash) for a strategy spec."""
    strategy_id = str(params_data.get("strategy_id", "20FINAL"))
    version = str(params_data.get("version", "1"))
    sig_h = compute_signal_spec_hash(params_data, core_root=core_root)
    exit_h = compute_exit_spec_hash(params_data)
    spec_h = compute_spec_hash(sig_h, exit_h, strategy_id=strategy_id, version=version)
    return {
        "signal_spec_hash": sig_h,
        "exit_spec_hash": exit_h,
        "spec_hash": spec_h,
    }
