"""Subprocess CLI helpers for cloned strategy parameter edits and strategy_core export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile
from typing import Any

from autotrade_v3.strategy_core.hashing import compute_all_hashes, normalize_lf
from autotrade_v3.strategy_core.registry import (
    PARAMS_DIR,
    StrategyCoreRegistry,
    compute_param_diff,
    parse_strategy_ref,
)


def edit_clone_params(
    *,
    strategy_ref: str,
    combo_key: str,
    updates: dict[str, Any],
    params_dir: Path | None = None,
) -> dict[str, Any]:
    """Edit exit_policy parameters on a cloned strategy (refuses baseline 20FINAL@1)."""
    sid, ver = parse_strategy_ref(strategy_ref)
    if (sid, ver) == ("20FINAL", "1"):
        raise PermissionError("Baseline 20FINAL@1 is locked and cannot be modified.")

    p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
    slug = f"{sid.lower()}_v{ver}"
    params_path = p_dir / slug / "params.json"
    if not params_path.exists():
        raise FileNotFoundError(f"Cloned strategy params not found: {params_path}")

    data = json.loads(normalize_lf(params_path.read_text(encoding="utf-8")))
    combos = data.get("combos", [])
    target = None
    for c in combos:
        if c.get("combo_key") == combo_key:
            target = c
            break
    if target is None:
        raise KeyError(f"Combo '{combo_key}' not found in {sid}@{ver}")

    ep = target.setdefault("exit_policy", {})
    allowed_ep_fields = {"tp_pct", "sl_pct", "trailing_act_pct", "trailing_cb_pct", "max_bars_held"}
    for k, v in updates.items():
        if k not in allowed_ep_fields:
            raise ValueError(f"Unsupported parameter field '{k}'; allowed: {sorted(allowed_ep_fields)}")
        if k == "max_bars_held":
            ep[k] = int(v) if v is not None else None
        else:
            ep[k] = float(v)

    parent_ref = data.get("parent")
    diff_obj: dict[str, Any] | None = None
    if parent_ref:
        parent_strat = StrategyCoreRegistry.get(str(parent_ref), params_dir=p_dir)
        diff_obj = compute_param_diff(parent_strat.spec().to_dict(), data)
        data["param_diff"] = {
            "added_combos": [c["combo_key"] for c in diff_obj["added_combos"]],
            "removed_combos": diff_obj["removed_combos"],
            "modified_combos": diff_obj["modified_combos"],
            "top_level_changes": diff_obj["top_level_changes"],
        }

    params_path.write_text(normalize_lf(json.dumps(data, indent=2) + "\n"), encoding="utf-8", newline="\n")
    diff_path = p_dir / slug / "param_diff.json"
    if diff_obj is not None:
        diff_path.write_text(normalize_lf(json.dumps(diff_obj, indent=2) + "\n"), encoding="utf-8", newline="\n")

    hashes = compute_all_hashes(data)
    return {"strategy_ref": f"{sid}@{ver}", "combo_key": combo_key, "hashes": hashes, "params_path": str(params_path)}


def export_strategy_bundle(
    *,
    strategy_ref: str,
    out_path: Path,
    params_dir: Path | None = None,
) -> Path:
    """Export a strategy_core version bundle as a zip archive."""
    p_dir = Path(params_dir) if params_dir is not None else PARAMS_DIR
    strat = StrategyCoreRegistry.get(strategy_ref, params_dir=p_dir)
    sp = strat.spec()
    sid, ver = parse_strategy_ref(sp.strategy_id, sp.version)
    slug = f"{sid.lower()}_v{ver}"

    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_p, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("params.json", normalize_lf(json.dumps(sp.to_dict(), indent=2) + "\n"))
        zf.writestr(
            "hashes.json",
            normalize_lf(
                json.dumps(
                    {
                        "strategy_id": sid,
                        "version": ver,
                        "signal_spec_hash": sp.signal_spec_hash,
                        "exit_spec_hash": sp.exit_spec_hash,
                        "spec_hash": sp.spec_hash,
                    },
                    indent=2,
                )
                + "\n"
            ),
        )
        diff_p = p_dir / slug / "param_diff.json"
        if diff_p.exists():
            zf.writestr("param_diff.json", normalize_lf(diff_p.read_text(encoding="utf-8")))
    return out_p


def main() -> None:
    parser = argparse.ArgumentParser(prog="autotrade_v3.dashboard._cli_ops")
    sub = parser.add_subparsers(dest="op", required=True)

    p_ed = sub.add_parser("edit-params")
    p_ed.add_argument("--strategy", required=True)
    p_ed.add_argument("--combo", required=True)
    p_ed.add_argument("--updates-json", required=True)
    p_ed.add_argument("--params-dir", default=None)

    p_ex = sub.add_parser("export-strategy")
    p_ex.add_argument("--strategy", required=True)
    p_ex.add_argument("--out", required=True)
    p_ex.add_argument("--params-dir", default=None)

    args = parser.parse_args()
    p_dir = Path(args.params_dir) if args.params_dir else None

    if args.op == "edit-params":
        try:
            res = edit_clone_params(
                strategy_ref=args.strategy,
                combo_key=args.combo,
                updates=json.loads(args.updates_json),
                params_dir=p_dir,
            )
            print(json.dumps(res, indent=2))
        except PermissionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
    elif args.op == "export-strategy":
        out = export_strategy_bundle(
            strategy_ref=args.strategy,
            out_path=Path(args.out),
            params_dir=p_dir,
        )
        print(json.dumps({"exported": str(out)}, indent=2))


if __name__ == "__main__":
    main()
