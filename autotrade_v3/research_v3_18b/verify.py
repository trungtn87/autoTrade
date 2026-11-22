"""Bộ xác minh tính toàn vẹn dữ liệu, đối khớp Lab 11, hàm băm SHA-256 và quy tắc mẫu V3-18b."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autotrade_v3.reconcile_v3.era_engines import ensure_fixed_v1_registered
from autotrade_v3.research_v3_18.combo_regime_analyzer import SPLIT_ORDER
from autotrade_v3.research_v3_18.combos_30 import get_live_23_pairs
from autotrade_v3.research_v3_18.portfolio_experiments import evaluate_portfolio_candidate_flag
from autotrade_v3.research_v3_18.regime_features import load_research_config
from autotrade_v3.research_v3_18b import DEFAULT_CONFIG_PATH
from autotrade_v3.research_v3_18b.labels_vi import find_english_label_leaks_v3_18b
from autotrade_v3.strategy.v3_lab_11 import V3Lab11Strategy
from autotrade_v3.strategy.v3_lab_11_prune23 import V3Lab11Prune23Strategy
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.tools.dataset_manifest import generate_dataset_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_DIR = PROJECT_ROOT / "evidence" / "v3-18b"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_v3_18b_evidence(
    evidence_dir: Path | None = None,
    *,
    config_path: Path | None = None,
    verify_data_hash: bool = True,
    write_result: bool = True,
) -> dict[str, Any]:
    """Xác minh toàn bộ minh chứng V3-18b và trả về báo cáo PASS/FAIL."""
    out_dir = evidence_dir or DEFAULT_EVIDENCE_DIR
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    manifest_path = out_dir / "manifest.json"
    report_path = out_dir / "lab11_report.json"
    html_path = out_dir / "lab11_report.html"

    missing = [p.name for p in (manifest_path, report_path, html_path) if not p.exists()]
    record("files_exist", len(missing) == 0, f"missing={missing}")
    if missing:
        result = {"status": "FAIL", "evidence_dir": str(out_dir), "checks": checks, "failures": failures}
        if write_result and out_dir.exists():
            (out_dir / "verify_result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return result

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    html_text = html_path.read_text(encoding="utf-8")
    cfg = load_research_config(cfg_path)

    cfg_canon = canonical_json_sha256(cfg)
    cfg_file = _file_sha256(cfg_path)
    record(
        "config_hashes",
        cfg_canon == manifest.get("config_canonical_sha256") and cfg_file == manifest.get("config_file_sha256"),
        f"manifest_cfg={manifest.get('config_canonical_sha256')} actual={cfg_canon}",
    )

    rep_recorded = report.get("report_sha256")
    rep_body = {k: v for k, v in report.items() if k != "report_sha256"}
    rep_canon = canonical_json_sha256(rep_body)
    rep_file = _file_sha256(report_path)
    html_file = _file_sha256(html_path)
    record(
        "report_and_html_hashes",
        rep_recorded == manifest.get("report_canonical_sha256")
        and rep_canon == rep_recorded
        and rep_file == manifest.get("report_file_sha256")
        and html_file == manifest.get("html_file_sha256"),
        f"canon_ok={rep_canon == rep_recorded} rep_file_ok={rep_file == manifest.get('report_file_sha256')} html_ok={html_file == manifest.get('html_file_sha256')}",
    )

    if verify_data_hash:
        ds = generate_dataset_manifest()
        record(
            "dataset_hash",
            ds.get("dataset_hash") == manifest.get("dataset", {}).get("dataset_hash"),
            f"manifest={manifest.get('dataset', {}).get('dataset_hash')} actual={ds.get('dataset_hash')}",
        )

    ensure_fixed_v1_registered()
    prune23_spec = V3Lab11Prune23Strategy().get_spec_hash()
    all30_spec = V3Lab11Strategy().get_spec_hash()
    record(
        "lab11_spec_hashes",
        prune23_spec == manifest.get("lab11_prune23_spec_hash")
        and all30_spec == manifest.get("lab11_all30_spec_hash"),
        f"prune23_ok={prune23_spec == manifest.get('lab11_prune23_spec_hash')} all30_ok={all30_spec == manifest.get('lab11_all30_spec_hash')}",
    )

    parity = report.get("parity_v3_16", {})
    repro = parity.get("reproduced_full_bingx", {})
    ref = parity.get("v3_16_ref_summary", {})
    parity_ok = (
        bool(parity.get("parity_ok"))
        and bool(repro.get("exact_signal_set_match"))
        and int(repro.get("total_signals", -1)) == int(ref.get("total_signals", -2))
        and int(repro.get("accepted_entries", -1)) == int(ref.get("accepted_entries", -2))
        and float(repro.get("net_pnl_diff_usd", 999.0)) < 0.01
    )
    record(
        "v3_16_prune23_base_parity",
        parity_ok,
        f"sigs={repro.get('total_signals')}/{ref.get('total_signals')} trades={repro.get('accepted_entries')}/{ref.get('accepted_entries')} diff={repro.get('net_pnl_diff_usd')}",
    )

    combo_ana = report.get("combo_regime_analysis_23", {})
    per_combo = combo_ana.get("per_combo", [])
    live23_pairs = get_live_23_pairs()
    found_pairs = {(str(r["catalog"]["symbol"]), int(r["catalog"]["combo_id"])) for r in per_combo}
    total_acc = int(combo_ana.get("total_accepted_trades_3y", 0))
    sum_trades = sum(int(r["overall_all"]["so_lenh"]) for r in per_combo)
    min_n = int(cfg["statistics"]["min_sample_size"])
    insuff_lbl = str(cfg["statistics"]["insufficient_sample_label"])

    acc_errs: list[str] = []
    sample_errs: list[str] = []
    if len(per_combo) != 23 or found_pairs != live23_pairs:
        acc_errs.append(f"combos_23={len(per_combo)} match={found_pairs == live23_pairs}")
    if total_acc <= 0 or sum_trades != total_acc:
        acc_errs.append(f"sum_trades={sum_trades} != total_acc={total_acc}")

    for row in per_combo:
        ck = str(row["catalog"]["combo_symbol"])
        o_all = row["overall_all"]
        o_sp = row["overall_by_split"]
        by_reg = row["by_regime"]
        if sum(int(c["so_lenh"]) for c in by_reg.values()) != int(o_all["so_lenh"]):
            acc_errs.append(f"{ck}: by_regime count mismatch")
        if abs(sum(float(c["net_pnl_usd"]) for c in by_reg.values()) - float(o_all["net_pnl_usd"])) > 0.02:
            acc_errs.append(f"{ck}: by_regime pnl mismatch")
        if sum(int(o_sp[sp]["so_lenh"]) for sp in SPLIT_ORDER) != int(o_all["so_lenh"]):
            acc_errs.append(f"{ck}: split count mismatch")
        if abs(sum(float(o_sp[sp]["net_pnl_usd"]) for sp in SPLIT_ORDER) - float(o_all["net_pnl_usd"])) > 0.02:
            acc_errs.append(f"{ck}: split pnl mismatch")

        cells: list[tuple[str, dict[str, Any]]] = [(f"{ck}/ALL", o_all)]
        cells.extend((f"{ck}/{r}", c) for r, c in by_reg.items())
        for sp_name, sp_dict in row.get("by_regime_splits", {}).items():
            cells.extend((f"{ck}/{sp_name}/{r}", c) for r, c in sp_dict.items())
        for lbl, cell in cells:
            n = int(cell["so_lenh"])
            if n < min_n and (cell.get("status") != insuff_lbl or cell.get("conclusive") is not False):
                sample_errs.append(f"{lbl}: n={n} status={cell.get('status')}")
            if n >= min_n and (cell.get("status") != "ĐỦ_MẪU" or cell.get("conclusive") is not True):
                sample_errs.append(f"{lbl}: n={n} status={cell.get('status')}")

    record("combos_23_accounting", len(acc_errs) == 0, f"errors={acc_errs[:3]}")
    record("min_sample_size_rule", len(sample_errs) == 0, f"errors={sample_errs[:3]}")

    exps = report.get("experiments", {})
    trials = exps.get("trials", [])
    baselines = [t for t in trials if t.get("is_baseline")]
    non_base = [t for t in trials if not t.get("is_baseline")]
    exp_errs: list[str] = []
    if len(baselines) != 1:
        exp_errs.append(f"baselines={len(baselines)}")
    add_n = sum(1 for t in non_base if t.get("group_type") == "THEM_7")
    drop_n = sum(1 for t in non_base if t.get("group_type") == "BOT_23")
    flt_n = sum(1 for t in non_base if t.get("group_type") == "LOC_3")
    if (add_n, drop_n, flt_n) != (7, 23, 3) or int(exps.get("total_trials_m", -1)) != 33:
        exp_errs.append(f"counts=({add_n},{drop_n},{flt_n}) total={exps.get('total_trials_m')}")

    if len(baselines) == 1:
        b = baselines[0]
        if int(b["metrics"]["ALL"]["so_lenh"]) != total_acc:
            exp_errs.append(f"baseline trades={b['metrics']['ALL']['so_lenh']} != {total_acc}")
        b_val_net = float(b["metrics"]["VALIDATION"]["net_pnl_usd"])
        b_hold_net = float(b["metrics"]["HOLDOUT"]["net_pnl_usd"])
        b_val_dd = float(b["metrics"]["VALIDATION"]["m2m_max_dd_usd"])
        b_hold_dd = float(b["metrics"]["HOLDOUT"]["m2m_max_dd_usd"])
        dd_mult = float(cfg["portfolio_selection_rules"]["max_dd_multiplier_vs_baseline"])
        exp_cands: list[str] = []
        for t in non_base:
            tid = str(t["trial_id"])
            v_net = float(t["metrics"]["VALIDATION"]["net_pnl_usd"])
            h_net = float(t["metrics"]["HOLDOUT"]["net_pnl_usd"])
            v_dd = float(t["metrics"]["VALIDATION"]["m2m_max_dd_usd"])
            h_dd = float(t["metrics"]["HOLDOUT"]["m2m_max_dd_usd"])
            v_dt = round(v_net - b_val_net, 4)
            h_dt = round(h_net - b_hold_net, 4)
            dt = t["delta_vs_baseline"]
            if abs(v_dt - float(dt["VALIDATION_net_pnl_usd"])) > 0.02 or abs(
                h_dt - float(dt["HOLDOUT_net_pnl_usd"])
            ) > 0.02:
                exp_errs.append(f"{tid}: delta mismatch")
            flag = evaluate_portfolio_candidate_flag(
                val_delta=v_dt,
                hold_delta=h_dt,
                val_dd=v_dd,
                hold_dd=h_dd,
                base_val_dd=b_val_dd,
                base_hold_dd=b_hold_dd,
                dd_multiplier=dd_mult,
            )
            if bool(t.get("is_candidate_improved")) != flag:
                exp_errs.append(f"{tid}: is_candidate_improved mismatch")
            if flag:
                exp_cands.append(tid)
        actual_cands = [str(c["trial_id"]) for c in exps.get("candidate_watchlist", [])]
        if actual_cands != exp_cands:
            exp_errs.append(f"watchlist={actual_cands} != {exp_cands}")

    record("experiments_33_and_gating", len(exp_errs) == 0, f"errors={exp_errs[:3]}")

    leaks = find_english_label_leaks_v3_18b(html_text)
    record("vietnamese_only_html", len(leaks) == 0, f"leaks={leaks}")

    live_ph = report.get("live_comparison_placeholder", {})
    record(
        "live_placeholder_compare_from",
        live_ph.get("compare_from_utc") == cfg["dataset"]["live_compare_from_utc"],
        f"compare_from={live_ph.get('compare_from_utc')}",
    )

    overall_ok = len(failures) == 0
    result = {
        "status": "PASS" if overall_ok else "FAIL",
        "evidence_dir": str(out_dir),
        "checks": checks,
        "failures": failures,
    }
    if write_result:
        (out_dir / "verify_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result
