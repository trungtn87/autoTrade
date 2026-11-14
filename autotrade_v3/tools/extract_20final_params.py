"""Programmatic parameter extractor for 20FINAL v1 from autotrade_v2/app/strategy/final14_config.py.

Reads V2 configuration via Python AST without importing autotrade_v2 or modifying sys.path,
generates autotrade_v3/strategy_core/strategies/final20/params/20final_v1.json, and prints
a full field-by-field verification table against V2 FINAL14_CASES and NEW6_CASES.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_V2_CONFIG = REPO_ROOT / "autotrade_v2" / "app" / "strategy" / "final14_config.py"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "autotrade_v3"
    / "strategy_core"
    / "strategies"
    / "final20"
    / "params"
    / "20final_v1.json"
)


def _eval_ast_node(node: ast.AST) -> Any:
    """Safely evaluate AST literals and simple binary expressions (such as 0.011 / 0.009)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Dict):
        return {
            _eval_ast_node(k): _eval_ast_node(v)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_ast_node(elt) for elt in node.elts]
    if isinstance(node, ast.Set):
        return {_eval_ast_node(elt) for elt in node.elts}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_ast_node(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return float(_eval_ast_node(node.left)) / float(_eval_ast_node(node.right))
    raise ValueError(f"Unsupported AST node in config extraction: {ast.dump(node)}")


def parse_v2_final14_config(v2_config_path: Path = DEFAULT_V2_CONFIG) -> dict[str, Any]:
    """Parse FINAL14_CASES, NEW6_CASES, and NATIVE_TIMEFRAME from V2 final14_config.py via AST."""
    source = v2_config_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(v2_config_path))

    extracted: dict[str, Any] = {}
    target_names = {"FINAL14_CASES", "NEW6_CASES", "NATIVE_TIMEFRAME", "DISABLED_CASES"}

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id in target_names:
                    extracted[t.id] = _eval_ast_node(stmt.value)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.target.id in target_names and stmt.value is not None:
                extracted[stmt.target.id] = _eval_ast_node(stmt.value)

    missing = {"FINAL14_CASES", "NEW6_CASES", "NATIVE_TIMEFRAME"} - set(extracted.keys())
    if missing:
        raise RuntimeError(f"Failed to extract {sorted(missing)} from {v2_config_path}")

    return extracted


def _parse_new6_entry_params(combo_id: int, indicator: str, variant: str) -> dict[str, Any]:
    """Derive structured entry parameters from NEW6 indicator and entry_variant string."""
    if combo_id == 101:
        # trend_n60_0.25_0.75
        m = re.match(r"^trend_n(\d+)_([0-9.]+)_([0-9.]+)$", variant)
        if not m:
            raise ValueError(f"Unexpected variant format for 101: {variant}")
        return {
            "indicator": indicator,
            "period": int(m.group(1)),
            "short_max": float(m.group(2)),
            "long_min": float(m.group(3)),
        }
    if combo_id == 102:
        # cross_n40_t70
        m = re.match(r"^cross_n(\d+)_t([0-9.]+)$", variant)
        if not m:
            raise ValueError(f"Unexpected variant format for 102: {variant}")
        return {
            "indicator": indicator,
            "period": int(m.group(1)),
            "threshold": float(m.group(2)),
        }
    if combo_id == 103:
        # momcross_n40_t40
        m = re.match(r"^momcross_n(\d+)_t([0-9.]+)$", variant)
        if not m:
            raise ValueError(f"Unexpected variant format for 103: {variant}")
        return {
            "indicator": indicator,
            "period": int(m.group(1)),
            "threshold": float(m.group(2)),
        }
    if combo_id == 104:
        # tkcross_12_30_60
        m = re.match(r"^tkcross_(\d+)_(\d+)_(\d+)$", variant)
        if not m:
            raise ValueError(f"Unexpected variant format for 104: {variant}")
        return {
            "indicator": indicator,
            "conv": int(m.group(1)),
            "base": int(m.group(2)),
            "spanb": int(m.group(3)),
        }
    if combo_id == 105:
        # state_e40_s40_t0.4
        m = re.match(r"^state_e(\d+)_s(\d+)_t([0-9.]+)$", variant)
        if not m:
            raise ValueError(f"Unexpected variant format for 105: {variant}")
        return {
            "indicator": indicator,
            "er_len": int(m.group(1)),
            "fast_len": 2,
            "slow_len": int(m.group(2)),
            "er_min": float(m.group(3)),
        }
    if combo_id == 106:
        # cross_n14_r1.2
        m = re.match(r"^cross_n(\d+)_r([0-9.]+)$", variant)
        if not m:
            raise ValueError(f"Unexpected variant format for 106: {variant}")
        return {
            "indicator": indicator,
            "period": int(m.group(1)),
            "ratio": float(m.group(2)),
        }
    raise ValueError(f"Unknown NEW6 combo_id: {combo_id}")


def _combo_name(combo_id: int, cfg: dict[str, Any]) -> str:
    if "name" in cfg:
        return str(cfg["name"])
    if 101 <= combo_id <= 106:
        return f"C{combo_id - 90}"
    return "TIER" if combo_id == 11 else f"C{combo_id}"


def build_20final_v1_params(v2_config_path: Path = DEFAULT_V2_CONFIG) -> dict[str, Any]:
    """Build the canonical 20final_v1.json parameter dictionary from V2 config."""
    raw = parse_v2_final14_config(v2_config_path)
    final14_cases: dict[str, dict[int, dict[str, Any]]] = raw["FINAL14_CASES"]
    new6_cases: dict[str, dict[int, dict[str, Any]]] = raw["NEW6_CASES"]
    native_tf_map: dict[int, str] = raw["NATIVE_TIMEFRAME"]

    combos: list[dict[str, Any]] = []

    for symbol in ("BTC-USDT", "ETH-USDT"):
        # 1. FINAL14 combos
        for combo_id, cfg in sorted(final14_cases.get(symbol, {}).items()):
            cid = int(combo_id)
            cname = _combo_name(cid, cfg)
            native_tf = str(native_tf_map[cid])
            combos.append({
                "combo_key": f"{symbol}/{cname}",
                "symbol": symbol,
                "combo_id": cid,
                "combo_name": cname,
                "native_timeframe": native_tf,
                "is_new6": False,
                "entry_variant": str(cfg["entry_variant"]),
                "entry": dict(cfg["entry"]),
                "layer2": str(cfg["layer2"]),
                "exit_policy": {
                    "tp_pct": float(cfg["tp_pct"]),
                    "sl_pct": float(cfg["sl_pct"]),
                    "split_legs": True,
                    "leg_weights": [0.5, 0.5],
                    "trailing_act_pct": 0.005,
                    "trailing_cb_pct": 0.005,
                    "max_bars_held": None,
                    "force_close_on_window_end": False,
                },
            })

        # 2. NEW6 combos
        for combo_id, cfg in sorted(new6_cases.get(symbol, {}).items()):
            cid = int(combo_id)
            cname = _combo_name(cid, cfg)
            indicator = str(cfg["indicator"])
            variant = str(cfg["entry_variant"])
            combos.append({
                "combo_key": f"{symbol}/{cname}",
                "symbol": symbol,
                "combo_id": cid,
                "combo_name": cname,
                "native_timeframe": "1h",
                "is_new6": True,
                "entry_variant": variant,
                "entry": _parse_new6_entry_params(cid, indicator, variant),
                "layer2": str(cfg["layer2"]),
                "exit_policy": {
                    "tp_pct": float(cfg["tp_pct"]),
                    "sl_pct": float(cfg["sl_pct"]),
                    "split_legs": False,
                    "leg_weights": [1.0],
                    "trailing_act_pct": 0.005,
                    "trailing_cb_pct": 0.005,
                    "max_bars_held": None,
                    "force_close_on_window_end": False,
                },
            })

    if len(combos) != 20:
        raise AssertionError(f"Expected 20 combos, extracted {len(combos)}")

    return {
        "strategy_id": "20FINAL",
        "version": "1",
        "parent": None,
        "warmup_bars": 3400,
        "timeframes": ["15m", "1h"],
        "trailing_assumption_note": (
            "trailing_act_pct=0.005 and trailing_cb_pct=0.005 match V3 baseline assumption "
            "and V2 default in autotrade_v2/app/config.py; live V2 runtime environment "
            "variables (TP_TRAILING_ACTIVATION_PCT, TP_TRAILING_CALLBACK_RATE) are unverified."
        ),
        "combos": combos,
    }


def verify_and_format_reconciliation(
    params_data: dict[str, Any],
    v2_config_path: Path = DEFAULT_V2_CONFIG,
) -> tuple[bool, str]:
    """Compare extracted 20final_v1.json against V2 FINAL14_CASES and NEW6_CASES field by field."""
    raw = parse_v2_final14_config(v2_config_path)
    final14_cases = raw["FINAL14_CASES"]
    new6_cases = raw["NEW6_CASES"]
    native_tf_map = raw["NATIVE_TIMEFRAME"]

    lines = [
        "# BẢNG ĐỐI CHIẾU THAM SỐ 20 COMBO (`20final_v1.json` vs V2 `final14_config.py`)",
        "",
        "| # | Combo Key | Symbol | ID | Name | TF (V2 / V3) | Entry Variant (V2 == V3) | Layer 2 (V2 == V3) | TP Pct (V2 == V3) | SL Pct (V2 == V3) | Split Legs | Trailing (Act/CB) | Match |",
        "|---:|:---|:---|---:|:---|:---:|:---|:---|---:|---:|:---:|:---:|:---:|",
    ]

    all_match = True
    for idx, c in enumerate(params_data["combos"], start=1):
        sym = c["symbol"]
        cid = c["combo_id"]
        is_new6 = cid >= 101
        v2_cfg = new6_cases[sym][cid] if is_new6 else final14_cases[sym][cid]
        v2_tf = "1h" if is_new6 else native_tf_map[cid]

        tf_ok = c["native_timeframe"] == v2_tf
        ev_ok = c["entry_variant"] == v2_cfg["entry_variant"]
        l2_ok = c["layer2"] == v2_cfg["layer2"]
        tp_ok = float(c["exit_policy"]["tp_pct"]) == float(v2_cfg["tp_pct"])
        sl_ok = float(c["exit_policy"]["sl_pct"]) == float(v2_cfg["sl_pct"])
        split_ok = c["exit_policy"]["split_legs"] == (not is_new6)
        entry_ok = True if is_new6 else (c["entry"] == v2_cfg["entry"])

        row_ok = tf_ok and ev_ok and l2_ok and tp_ok and sl_ok and split_ok and entry_ok
        if not row_ok:
            all_match = False

        ep = c["exit_policy"]
        lines.append(
            f"| {idx} | `{c['combo_key']}` | {sym} | {cid} | {c['combo_name']} | "
            f"{v2_tf} / {c['native_timeframe']} | `{c['entry_variant']}` | `{c['layer2']}` | "
            f"{ep['tp_pct']:.6f} | {ep['sl_pct']:.8f} | {ep['split_legs']} | "
            f"{ep['trailing_act_pct']}/{ep['trailing_cb_pct']} | {'MATCH' if row_ok else 'MISMATCH'} |"
        )

    lines.extend([
        "",
        f"- **Tổng số combo đối chiếu:** {len(params_data['combos'])} / 20",
        f"- **Kết quả đối chiếu từng trường:** `{'MATCH_ALL_20_COMBOS' if all_match else 'MISMATCH_FOUND'}`",
        "- **Ghi chú Trailing:** `trailing_act_pct=0.005` và `trailing_cb_pct=0.005` lấy theo giả định V3 hiện tại và giá trị mặc định trong `autotrade_v2/app/config.py` (dòng 53-54); cấu hình biến môi trường V2 đang chạy thật chưa đối chiếu.",
    ])
    return all_match, "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract 20FINAL v1 parameters from V2 final14_config.py")
    parser.add_argument("--v2-config", type=Path, default=DEFAULT_V2_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    params_data = build_20final_v1_params(args.v2_config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(params_data, indent=2, ensure_ascii=False) + "\n"
    args.output.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))

    ok, table_md = verify_and_format_reconciliation(params_data, args.v2_config)
    print(f"Extracted 20FINAL parameters to: {args.output}")
    print(table_md)
    if not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
