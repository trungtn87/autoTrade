"""Bộ xác minh tính toàn vẹn dữ liệu, hàm băm SHA-256, quy tắc mẫu và kết quả V3-18."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autotrade_v3.research_v3_18.combo_regime_analyzer import SPLIT_ORDER
from autotrade_v3.research_v3_18.combos_30 import (
    CrossSymbol30FixedStrategy,
    get_live_23_pairs,
    get_non_live_7_pairs,
)
from autotrade_v3.research_v3_18.labels_vi import find_english_label_leaks
from autotrade_v3.research_v3_18.portfolio_experiments import evaluate_portfolio_candidate_flag
from autotrade_v3.research_v3_18.regime_features import DEFAULT_CONFIG_PATH, load_research_config
from autotrade_v3.strategy_core.hashing import canonical_json_sha256
from autotrade_v3.tools.dataset_manifest import generate_dataset_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_DIR = PROJECT_ROOT / "evidence" / "v3-18"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha256_matches_eol(path: Path, expected: Any) -> bool:
    """So khớp SHA-256 tệp văn bản bất kể LF/CRLF do Git autocrlf chuyển đổi trên Windows."""
    raw = path.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    variants = {
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(lf).hexdigest(),
        hashlib.sha256(crlf).hexdigest(),
    }
    return isinstance(expected, str) and expected in variants


def verify_evidence_dir(
    evidence_dir: Path | None = None,
    *,
    config_path: Path | None = None,
    verify_data_hash: bool = True,
    write_result: bool = True,
) -> dict[str, Any]:
    """Xác minh toàn bộ minh chứng V3-18 và trả về kết quả PASS/FAIL có cấu trúc."""
    out_dir = evidence_dir or DEFAULT_EVIDENCE_DIR
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    manifest_path = out_dir / "manifest.json"
    report_path = out_dir / "regime_report.json"
    html_path = out_dir / "regime_report.html"

    missing_files = [p.name for p in (manifest_path, report_path, html_path) if not p.exists()]
    record("files_exist", len(missing_files) == 0, f"missing={missing_files}")
    if missing_files:
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
    cfg_file_ok = text_sha256_matches_eol(cfg_path, manifest.get("config_file_sha256"))
    record(
        "config_hashes",
        cfg_canon == manifest.get("config_canonical_sha256") and cfg_file_ok,
        f"manifest_cfg={manifest.get('config_canonical_sha256')} actual={cfg_canon}"
        + ("" if cfg_file_ok else " file_ok=False"),
    )

    rep_sha_recorded = report.get("report_sha256")
    rep_body = {k: v for k, v in report.items() if k != "report_sha256"}
    rep_canon_actual = canonical_json_sha256(rep_body)
    rep_file_ok = text_sha256_matches_eol(report_path, manifest.get("report_file_sha256"))
    html_file_ok = text_sha256_matches_eol(html_path, manifest.get("html_file_sha256"))
    record(
        "report_and_html_hashes",
        rep_sha_recorded == manifest.get("report_canonical_sha256")
        and rep_canon_actual == rep_sha_recorded
        and rep_file_ok
        and html_file_ok,
        f"canon_ok={rep_canon_actual == rep_sha_recorded} file_ok={rep_file_ok} html_ok={html_file_ok}",
    )

    if verify_data_hash:
        ds = generate_dataset_manifest()
        ds_match = ds.get("dataset_hash") == manifest.get("dataset", {}).get("dataset_hash")
        record(
            "dataset_hash",
            bool(ds_match),
            f"manifest={manifest.get('dataset', {}).get('dataset_hash')} actual={ds.get('dataset_hash')}",
        )

    strat = CrossSymbol30FixedStrategy()
    spec_hash = strat.get_spec_hash()
    record(
        "combos_spec_hash",
        spec_hash == manifest.get("combos_spec_hash"),
        f"manifest={manifest.get('combos_spec_hash')} actual={spec_hash}",
    )

    combo_ana = report.get("combo_regime_analysis", {})
    per_combo = combo_ana.get("per_combo", [])
    expected_live = get_live_23_pairs()
    expected_non_live = {(s, cid) for s, cid, _ in get_non_live_7_pairs()}
    found_pairs = {
        (str(row["catalog"]["symbol"]), int(row["catalog"]["combo_id"])): bool(row["catalog"]["is_live_23"])
        for row in per_combo
    }
    live_pairs_found = {k for k, is_l in found_pairs.items() if is_l}
    non_live_pairs_found = {k for k, is_l in found_pairs.items() if not is_l}
    pos20_count = sum(1 for row in per_combo if row["catalog"].get("audit_20_status") == "DƯƠNG_20")
    record(
        "catalog_30_23_7_exact",
        len(per_combo) == 30
        and live_pairs_found == expected_live
        and non_live_pairs_found == expected_non_live
        and pos20_count == 20,
        f"count={len(per_combo)} live={len(live_pairs_found)} non_live={len(non_live_pairs_found)} pos20={pos20_count}",
    )

    min_n = int(cfg["statistics"]["min_sample_size"])
    insuff_lbl = str(cfg["statistics"]["insufficient_sample_label"])

    accounting_errors: list[str] = []
    sample_rule_errors: list[str] = []

    for row in per_combo:
        ck = str(row["catalog"]["combo_symbol"])
        o_all = row["overall_all"]
        o_sp = row["overall_by_split"]
        by_reg = row["by_regime"]

        reg_cnt_sum = sum(int(c["so_lenh"]) for c in by_reg.values())
        reg_pnl_sum = sum(float(c["net_pnl_usd"]) for c in by_reg.values())
        if reg_cnt_sum != int(o_all["so_lenh"]) or abs(reg_pnl_sum - float(o_all["net_pnl_usd"])) > 0.02:
            accounting_errors.append(
                f"{ck}: regime_sum=({reg_cnt_sum}, {reg_pnl_sum:.4f}) vs overall=({o_all['so_lenh']}, {o_all['net_pnl_usd']:.4f})"
            )

        sp_cnt_sum = sum(int(o_sp[sp]["so_lenh"]) for sp in SPLIT_ORDER)
        sp_pnl_sum = sum(float(o_sp[sp]["net_pnl_usd"]) for sp in SPLIT_ORDER)
        if sp_cnt_sum != int(o_all["so_lenh"]) or abs(sp_pnl_sum - float(o_all["net_pnl_usd"])) > 0.02:
            accounting_errors.append(
                f"{ck}: split_sum=({sp_cnt_sum}, {sp_pnl_sum:.4f}) vs overall=({o_all['so_lenh']}, {o_all['net_pnl_usd']:.4f})"
            )

        cells_to_check: list[tuple[str, dict[str, Any]]] = [(f"{ck}/OVERALL", o_all)]
        cells_to_check.extend((f"{ck}/{r}", c) for r, c in by_reg.items())
        for sp_name, sp_dict in row.get("by_regime_splits", {}).items():
            cells_to_check.extend((f"{ck}/{sp_name}/{r}", c) for r, c in sp_dict.items())

        for cell_label, cell in cells_to_check:
            n_trades = int(cell["so_lenh"])
            if n_trades < min_n:
                if cell.get("status") != insuff_lbl or cell.get("conclusive") is not False:
                    sample_rule_errors.append(f"{cell_label}: n={n_trades} nhung status={cell.get('status')}")
            else:
                if cell.get("status") != "ĐỦ_MẪU" or cell.get("conclusive") is not True:
                    sample_rule_errors.append(f"{cell_label}: n={n_trades} nhung status={cell.get('status')}")

    for dim_rows in combo_ana.get("dimension_summaries", {}).values():
        for entry in dim_rows:
            for sp_k in ("all",) + SPLIT_ORDER:
                cell = entry[sp_k]
                n_trades = int(cell["so_lenh"])
                if n_trades < min_n and (cell.get("status") != insuff_lbl or cell.get("conclusive") is not False):
                    sample_rule_errors.append(f"DIM/{entry['dimension']}/{entry['label']}/{sp_k}: n={n_trades}")

    total_trades_expected = int(combo_ana.get("total_trades_all_signals", 0))
    total_trades_by_combo = sum(int(row["overall_all"]["so_lenh"]) for row in per_combo)
    zero_trade_combos = [
        str(row["catalog"]["combo_symbol"]) for row in per_combo if int(row["overall_all"]["so_lenh"]) <= 0
    ]
    if total_trades_expected <= 0 or total_trades_by_combo != total_trades_expected or zero_trade_combos:
        accounting_errors.append(
            f"total_trades_by_combo={total_trades_by_combo} vs total={total_trades_expected}, zero_combos={zero_trade_combos}"
        )

    record(
        "combo_regime_accounting",
        len(accounting_errors) == 0,
        f"errors={accounting_errors[:3]}",
    )
    record(
        "min_sample_size_rule",
        len(sample_rule_errors) == 0,
        f"errors={sample_rule_errors[:3]}",
    )

    port_res = report.get("portfolio_experiments", {})
    portfolios = port_res.get("portfolios", [])
    baselines = [p for p in portfolios if p.get("is_baseline")]
    non_baselines = [p for p in portfolios if not p.get("is_baseline")]
    port_errors: list[str] = []

    if len(baselines) != 1:
        port_errors.append(f"expected 1 baseline, got {len(baselines)}")
    if int(port_res.get("total_trials_m", -1)) != len(non_baselines):
        port_errors.append(f"total_trials_m={port_res.get('total_trials_m')} != {len(non_baselines)}")

    if len(baselines) == 1:
        b = baselines[0]
        base_val_net = float(b["metrics"]["VALIDATION"]["net_pnl_usd"])
        base_hold_net = float(b["metrics"]["HOLDOUT"]["net_pnl_usd"])
        base_val_dd = float(b["metrics"]["VALIDATION"]["m2m_max_dd_usd"])
        base_hold_dd = float(b["metrics"]["HOLDOUT"]["m2m_max_dd_usd"])
        dd_mult = float(cfg["portfolio_selection_rules"]["max_dd_multiplier_vs_baseline"])

        expected_cand_ids: list[str] = []
        for p in non_baselines:
            tid = str(p["trial_id"])
            val_net = float(p["metrics"]["VALIDATION"]["net_pnl_usd"])
            hold_net = float(p["metrics"]["HOLDOUT"]["net_pnl_usd"])
            val_dd = float(p["metrics"]["VALIDATION"]["m2m_max_dd_usd"])
            hold_dd = float(p["metrics"]["HOLDOUT"]["m2m_max_dd_usd"])
            val_delta = round(val_net - base_val_net, 4)
            hold_delta = round(hold_net - base_hold_net, 4)
            dt = p["delta_vs_baseline"]
            if abs(val_delta - float(dt["VALIDATION_net_pnl_usd"])) > 0.02 or abs(
                hold_delta - float(dt["HOLDOUT_net_pnl_usd"])
            ) > 0.02:
                port_errors.append(f"{tid}: delta mismatch")

            exp_flag = evaluate_portfolio_candidate_flag(
                val_delta=val_delta,
                hold_delta=hold_delta,
                val_dd=val_dd,
                hold_dd=hold_dd,
                base_val_dd=base_val_dd,
                base_hold_dd=base_hold_dd,
                dd_multiplier=dd_mult,
            )
            if bool(p.get("is_candidate_improved")) != exp_flag:
                port_errors.append(f"{tid}: is_candidate_improved mismatch")
            if exp_flag:
                expected_cand_ids.append(tid)

        actual_cand_ids = [str(c["trial_id"]) for c in port_res.get("candidate_watchlist", [])]
        if actual_cand_ids != expected_cand_ids:
            port_errors.append(f"watchlist={actual_cand_ids} != expected={expected_cand_ids}")

    audit20_rows = [p for p in portfolios if p.get("trial_id") == "DANH_MỤC_20_MÃ_DƯƠNG_AUDIT"]
    if len(audit20_rows) != 1 or int(audit20_rows[0].get("signal_count", 0)) <= 0:
        port_errors.append("DANH_MỤC_20_MÃ_DƯƠNG_AUDIT missing or empty")

    record("portfolio_consistency_and_gating", len(port_errors) == 0, f"errors={port_errors[:3]}")

    leaks = find_english_label_leaks(html_text)
    record("vietnamese_only_html", len(leaks) == 0, f"leaks={leaks}")

    splits_cfg = cfg["splits"]
    dev_e = int(splits_cfg["DEVELOPMENT"]["end_ms"])
    val_s = int(splits_cfg["VALIDATION"]["start_ms"])
    val_e = int(splits_cfg["VALIDATION"]["end_ms"])
    hold_s = int(splits_cfg["HOLDOUT"]["start_ms"])
    record(
        "splits_contiguous_disjoint",
        dev_e + 1 == val_s and val_e + 1 == hold_s,
        f"dev_end+1={dev_e + 1} val_start={val_s} val_end+1={val_e + 1} hold_start={hold_s}",
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
