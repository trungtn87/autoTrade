"""CLI chạy toàn bộ nghiên cứu V3-18b trên cơ sở Lab 11 chuẩn và xác minh minh chứng."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from autotrade_v3.research_v3_18.regime_features import load_research_config
from autotrade_v3.research_v3_18b import DEFAULT_CONFIG_PATH
from autotrade_v3.research_v3_18b.experiments_v3_18b import (
    analyze_lab11_live_23_combos_by_regime,
    run_v3_18b_portfolio_experiments,
)
from autotrade_v3.research_v3_18b.lab11_baseline import (
    load_v3_18b_context,
    verify_v3_16_baseline_parity,
)
from autotrade_v3.research_v3_18b.report_vi import DEFAULT_EVIDENCE_DIR, write_v3_18b_artifacts
from autotrade_v3.research_v3_18b.verify import verify_v3_18b_evidence


def _ensure_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def run_pipeline(
    *,
    config_path: Path | None = None,
    evidence_dir: Path | None = None,
) -> int:
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    out_dir = evidence_dir or DEFAULT_EVIDENCE_DIR
    cfg = load_research_config(cfg_path)
    ctx = load_v3_18b_context(config=cfg)
    parity_res = verify_v3_16_baseline_parity(ctx)
    if not parity_res["parity_ok"]:
        raise RuntimeError(f"Đối khớp cơ sở V3-16 thất bại: {parity_res['reproduced_full_bingx']}")

    baseline_sim = parity_res["baseline_3y_sim_result"]
    combo_analysis = analyze_lab11_live_23_combos_by_regime(ctx=ctx, baseline_3y_sim=baseline_sim)
    experiments_res = run_v3_18b_portfolio_experiments(ctx=ctx, baseline_3y_sim=baseline_sim)
    written = write_v3_18b_artifacts(
        ctx=ctx,
        parity_res=parity_res,
        combo_analysis=combo_analysis,
        experiments_res=experiments_res,
        evidence_dir=out_dir,
        config_path=cfg_path,
    )
    verify_res = verify_v3_18b_evidence(
        evidence_dir=out_dir,
        config_path=cfg_path,
        verify_data_hash=True,
        write_result=True,
    )
    print(
        json.dumps(
            {
                "written": written,
                "verify_status": verify_res["status"],
                "parity_ok": parity_res["parity_ok"],
                "total_trials_m": experiments_res["total_trials_m"],
                "candidate_count": len(experiments_res["candidate_watchlist"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if verify_res["status"] == "PASS" else 1


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8()
    parser = argparse.ArgumentParser(
        prog="python -m autotrade_v3.research_v3_18b",
        description="V3-18b: Nghiên cứu thêm/bớt/lọc mã lấy ĐÚNG Lab 11 live làm cơ sở",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Chạy toàn bộ nghiên cứu V3-18b và xuất evidence/v3-18b")
    p_run.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p_run.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)

    p_ver = sub.add_parser("verify", help="Xác minh toàn bộ minh chứng V3-18b")
    p_ver.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    p_ver.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    p_ver.add_argument("--skip-data-hash", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "run":
        return run_pipeline(config_path=args.config, evidence_dir=args.evidence_dir)
    if args.command == "verify":
        res = verify_v3_18b_evidence(
            evidence_dir=args.evidence_dir,
            config_path=args.config,
            verify_data_hash=not bool(args.skip_data_hash),
            write_result=True,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "PASS" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
